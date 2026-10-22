"""Private loopback application and OAuth callback. Not an internet-facing server."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import jwt
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from . import chatgpt
from .advisor import analyze, current_report
from .conversation import turn as conversation_turn
from .location import listing_map
from .analysis import assess
from .avito import SourceUnavailable
from .config import load_config, load_env
from .details import read_detail
from .models import CATEGORIES, utc_now, timestamp
from .network import RemoteError, photo_bytes
from .presentation import ranked_feed, price_label
from .sources import source_name
from .storage import Store
from .workspace import STAGES, VIEWS, validate_profile, active_profile, filter_results, watched
from dataclasses import replace

PORT = 8766
ORIGIN = f"http://127.0.0.1:{PORT}"
ROOT = Path(__file__).resolve().parent.parent
SESSIONS = {}
PENDING_SIGN_INS = {}
SIGN_IN_LOCK = threading.Lock()
ANALYSIS_LOCK = threading.Lock()
DETAIL_WORKERS = ThreadPoolExecutor(max_workers=3, thread_name_prefix='listing-details')
DETAIL_PENDING = set()
DETAIL_LOCK = threading.Lock()


def quick_detail(item, store):
    """Serve known facts immediately; a slow source must not block opening a card."""
    try: cached = json.loads(store.get_state('detail:'+item.id,'null'))
    except ValueError: cached = None
    if cached:
        try:
            if (utc_now()-timestamp(cached['fetched_at'])).total_seconds()<21600: return cached
        except (ValueError,KeyError): pass
    key = (str(store.path),item.id)
    with DETAIL_LOCK:
        if key not in DETAIL_PENDING:
            DETAIL_PENDING.add(key)
            def fetch():
                try: read_detail(item,store)
                except (SourceUnavailable,OSError,ValueError): pass
                finally:
                    with DETAIL_LOCK: DETAIL_PENDING.discard(key)
            DETAIL_WORKERS.submit(fetch)
    return cached or {'description':item.description,'params':item.attributes.get('visible_params',''),
        'photos':[item.photo_url] if item.photo_url else [],'scope':'catalog','pending':True,
        'error':'Полная страница загружается в фоне. Сейчас показаны сведения каталога.'}


def register_sign_in(pending):
    """Loopback OAuth binds to state and PKCE, independently of browser cookies."""
    with SIGN_IN_LOCK:
        expired = [key for key, value in PENDING_SIGN_INS.items() if time.time() - value['time'] > 600]
        for key in expired:
            PENDING_SIGN_INS.pop(key, None)
        PENDING_SIGN_INS[pending['state']] = pending


def consume_sign_in(state):
    with SIGN_IN_LOCK:
        return PENDING_SIGN_INS.pop(state, None)


def sign_in_problem(error):
    if isinstance(error, RemoteError):
        status = error.status
        return (f"OpenAI не завершил обмен кодом входа (HTTP {status}). Начни новый вход." if status
                else "Не удалось связаться с OpenAI при обмене кодом входа. Проверь доступ к сервису и начни новый вход.")
    if isinstance(error, ValueError):
        return str(error)[:300]
    if isinstance(error, jwt.PyJWTError):
        return "Не удалось проверить подпись ответа OpenAI. Начни новый вход."
    return "Не удалось сохранить подключение в защищённом хранилище Windows. Начни новый вход."


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass  # OAuth query strings must never be logged.

    def session(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        sid = cookie.get("radar_session")
        return SESSIONS.get(sid.value) if sid else None

    def reply(self, value, code=200, kind="application/json; charset=utf-8", cookie=""):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        if cookie: self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(raw)

    def item(self, ident):
        item = next((x for x in self.server.store.all() if x.id == ident), None)
        if item is None: raise ValueError("Объект не найден")
        return item

    def serialize(self, item, result=None):
        value = item.to_dict()
        value["source"] = source_name(item)
        value['map'] = listing_map(item)
        value["comparison"] = price_label(result) if result else ""
        value['price_analysis'] = ({'discount':result.discount,'status':result.status,
            'comparables':len(result.comparable_ids),'reference_price':result.reference_price,
            'scope':result.comparison_scope,'reason':result.reason} if result else None)
        meta = self.server.store.listing_meta(self.user_id(),item.id)
        value['meta'] = {**meta,'watching':watched(meta)}
        value["favorite"] = bool(meta['favorite'])
        change = self.server.store.price_change(item.id)
        value['price_change'] = change if change and change['current']==item.price_rub else None
        return value

    def user_id(self):
        return self.server.store.get_state('owner_chat_id','local')

    def do_GET(self):
        if self.headers.get("Host") != f"127.0.0.1:{PORT}":
            self.reply({"error": "Локальный доступ"}, 403)
            return
        path = urlsplit(self.path)
        args = {k: v[0] for k, v in parse_qs(path.query,keep_blank_values=True).items()}
        session = self.session()
        if path.path == "/":
            cookie = ""
            if session is None:
                sid = secrets.token_urlsafe(32)
                SESSIONS[sid] = {"csrf": secrets.token_urlsafe(32), "pending": None}
                cookie = f"radar_session={sid}; HttpOnly; SameSite=Lax; Path=/"
            self.reply((ROOT / "garage_radar" / "ui.html").read_bytes(), kind="text/html; charset=utf-8", cookie=cookie)
            return
        if path.path == "/auth/callback":
            # The system browser may differ from the browser that started the flow.
            # Consume the unpredictable state once; finish also verifies state, age and PKCE.
            pending = consume_sign_in(args.get("state", ""))
            cookie = ""
            if session is None:
                sid = secrets.token_urlsafe(32)
                session = {"csrf": secrets.token_urlsafe(32)}
                SESSIONS[sid] = session
                cookie = f"radar_session={sid}; HttpOnly; SameSite=Lax; Path=/"
            store = self.server.store
            try:
                if pending is None:
                    raise ValueError("Сеанс входа истёк или приложение было перезапущено. Начни вход заново в этом окне.")
                chatgpt.finish(args, pending)
                notice = "ChatGPT подключён. Выбери модель в настройках."
                stage = "connected"
            except (ValueError, RemoteError, jwt.PyJWTError, OSError) as error:
                notice = sign_in_problem(error)
                stage = "failed"
            session["notice"] = notice
            store.set_state("chatgpt_auth_progress", json.dumps({"stage": stage, "message": notice, "at": int(time.time())}, ensure_ascii=False))
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            if cookie: self.send_header("Set-Cookie", cookie)
            self.end_headers()
            return
        if not session:
            self.reply({"error": "Открой приложение заново"}, 401)
            return
        try:
            store, config = self.server.store, self.server.config
            if path.path == "/api/state":
                self.reply({"csrf": session["csrf"], "chatgpt": chatgpt.status(), "model": store.get_state("chatgpt_model"),
                            "auth_progress": json.loads(store.get_state("chatgpt_auth_progress", "{}")),
                            'stages':STAGES,'views':VIEWS,'profiles':store.profiles(self.user_id()),
                            'active_profile':store.get_state(f'active_search:{self.user_id()}'),
                            'view':store.get_state(f'view:{self.user_id()}','all'),
                            'sort':store.get_state(f'sort:{self.user_id()}','price_asc'),
                            'category':store.category(self.user_id(),config.search.category),
                            "categories": CATEGORIES, "notice": session.pop("notice", ""),
                            "schedule": store.get_state("schedule:" + store.get_state("owner_chat_id"), "off")})
            elif path.path in {"/ui.css", "/ui.js", '/ui-workspace.js', '/ui-chat.js'}:
                kind = "text/css; charset=utf-8" if path.path.endswith('.css') else "text/javascript; charset=utf-8"
                self.reply((ROOT / 'garage_radar' / path.path[1:]).read_bytes(), kind=kind)
            elif path.path == "/api/models":
                available = chatgpt.models()
                if available and not store.get_state("chatgpt_model"):
                    store.set_state("chatgpt_model", available[0]["id"])
                self.reply({"models": available, "selected": store.get_state("chatgpt_model")})
            elif path.path == "/api/list":
                category = args.get("category", "garage")
                if category not in CATEGORIES: raise ValueError("Неизвестная категория")
                search = replace(config.search, category=category)
                results = ranked_feed(store.all(), search, recent_days=7, sort_mode=args.get("sort", "price_asc"))
                profile = active_profile(store,self.user_id())
                if any(k in args for k in ('min_price','max_price','locality','include','exclude')):
                    profile = validate_profile({**args,'category':category,'name':'Текущий поиск'})
                results = filter_results(store,self.user_id(),results,view=args.get('view'),profile=profile)
                self.reply({"items": [self.serialize(r.listing, r) for r in results[:150]], "count": len(results)})
            elif path.path == "/api/item":
                item = self.item(args.get("id"))
                detail = quick_detail(item, store)
                cached = json.loads(store.get_state("advisor:" + item.id, "null"))
                if cached and cached.get('listing_price') != item.price_rub: cached = None
                history, previous = [], None
                for observed,price in store.history(item.id):
                    if price != previous: history.append({'at':observed,'price':price})
                    previous = price
                result = assess(item,store.all(),replace(config.search,category=item.category))
                if not current_report(cached,result): cached = None
                self.reply({"item": self.serialize(item,result), "detail": detail,"analysis": cached,'history':history[-30:]})
            elif path.path == "/photo":
                item = self.item(args.get("id"))
                index = min(5, max(0, int(args.get("index", 0))))
                cached = json.loads(store.get_state("detail:" + item.id, "{}"))
                photos = cached.get("photos") or [item.photo_url]
                if index >= len(photos): raise ValueError("Фото не найдено")
                data, kind = photo_bytes(photos[index])
                self.reply(data, kind=kind)
            else: self.reply({"error": "Не найдено"}, 404)
        except (ValueError, RemoteError, SourceUnavailable):
            self.reply({"error": "Источник недоступен или запрос не удалось завершить. Данные не выдумываются."}, 400)

    def do_POST(self):
        session = self.session()
        if (self.headers.get("Host") != f"127.0.0.1:{PORT}" or self.headers.get("Origin") != ORIGIN or not session
                or not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), session["csrf"])):
            self.reply({"error": "Сеанс не подтверждён"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not 0 < length < 8192: raise ValueError("Неверный запрос")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict): raise ValueError("Неверный запрос")
            store, config = self.server.store, self.server.config
            if self.path == "/api/connect":
                url, pending = chatgpt.begin(ORIGIN + "/auth/callback")
                register_sign_in(pending)
                store.set_state("chatgpt_auth_progress", json.dumps({"stage": "waiting", "message": "Вход начат. Ожидаю возврата со страницы OpenAI.", "at": int(time.time())}, ensure_ascii=False))
                self.reply({"url": url})
            elif self.path == "/api/disconnect":
                confirmed = chatgpt.disconnect()
                store.set_state("chatgpt_model", "")
                self.reply({"ok": True, "remote_confirmed": confirmed})
            elif self.path == "/api/model":
                available = {row["id"] for row in chatgpt.models()}
                if data.get("model") not in available: raise ValueError("Модель не доступна аккаунту")
                store.set_state("chatgpt_model", data["model"])
                self.reply({"ok": True})
            elif self.path == "/api/favorite":
                item = self.item(data.get("id"))
                meta = store.listing_meta(self.user_id(),item.id)
                meta = store.set_listing_meta(self.user_id(),item.id,favorite=not meta['favorite'])
                self.reply({"favorite":bool(meta['favorite']),'meta':{**meta,'watching':watched(meta)}})
            elif self.path == '/api/meta':
                item = self.item(data.get('id'))
                changes = data.get('changes')
                if not isinstance(changes,dict): raise ValueError('Некорректное действие')
                meta = store.set_listing_meta(self.user_id(),item.id,**changes)
                self.reply({'meta':{**meta,'watching':watched(meta)}})
            elif self.path == '/api/preferences':
                if 'view' in data:
                    if data['view'] not in VIEWS: raise ValueError('Неизвестный режим')
                    store.set_state(f'view:{self.user_id()}',data['view'])
                if 'sort' in data:
                    if data['sort'] not in {'price_asc','price_desc','newest'}: raise ValueError('Неизвестный порядок')
                    store.set_state(f'sort:{self.user_id()}',data['sort'])
                self.reply({'ok':True})
            elif self.path == '/api/search/save':
                profile = validate_profile(data)
                ident = data.get('id') or secrets.token_hex(6)
                if not isinstance(ident,str) or len(ident)>24: raise ValueError('Некорректный поиск')
                if data.get('id') and not any(p['id']==ident for p in store.profiles(self.user_id())):
                    raise ValueError('Поиск не найден')
                if not data.get('id') and len(store.profiles(self.user_id()))>=30: raise ValueError('Можно сохранить до 30 поисков')
                store.save_profile(self.user_id(),ident,profile)
                store.set_state(f'active_search:{self.user_id()}',ident)
                store.set_category(self.user_id(),profile['category'])
                self.reply({'profiles':store.profiles(self.user_id()),'selected':ident})
            elif self.path == '/api/search/activate':
                ident = data.get('id','')
                profile = next((p for p in store.profiles(self.user_id()) if p['id']==ident),None)
                if ident and not profile: raise ValueError('Поиск не найден')
                if not ident and 'category' in data and data['category'] not in CATEGORIES:
                    raise ValueError('Неизвестная категория')
                store.set_state(f'active_search:{self.user_id()}',ident)
                if profile: store.set_category(self.user_id(),profile['category'])
                elif 'category' in data: store.set_category(self.user_id(),data['category'])
                self.reply({'ok':True})
            elif self.path == '/api/search/delete':
                store.delete_profile(self.user_id(),str(data.get('id','')))
                self.reply({'profiles':store.profiles(self.user_id())})
            elif self.path == '/api/chat':
                ident = data.get('selected_id')
                if ident is not None and (not isinstance(ident, str) or len(ident) > 80):
                    raise ValueError('Некорректный объект')
                reply = conversation_turn(store, config, self.user_id(), data.get('text'), selected_id=ident)
                self.reply({'answer': reply['answer'], 'category': reply['category'], 'search_url': reply.get('search_url', ''),
                    'items': [self.serialize(r.listing, r) for r in reply['results']]})
            elif self.path == "/api/analyze":
                item = self.item(data.get("id"))
                if not ANALYSIS_LOCK.acquire(blocking=False):
                    self.reply({"error": "Разбор уже выполняется. Дождись результата."}, 409)
                    return
                try:
                    result = assess(item, store.all(), replace(config.search, category=item.category))
                    self.reply(analyze(result, store))
                finally: ANALYSIS_LOCK.release()
            else: self.reply({"error": "Не найдено"}, 404)
        except ValueError as error:
            self.reply({"error": str(error)[:300]}, 400)
        except (RemoteError, SourceUnavailable, jwt.PyJWTError):
            self.reply({"error": "Сервис не ответил. Проверь подключение ChatGPT и доступность источника."}, 400)


def main():
    load_env(ROOT / ".env", override=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.store = Store(ROOT / "data" / "radar.sqlite3")
    server.config = load_config(ROOT / "config.toml")
    server.serve_forever()


if __name__ == "__main__": main()
