from __future__ import annotations

import logging
import json
import secrets
import hmac
import os
import re
import time
from urllib.parse import urlsplit
from html import escape
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import threading

from .ai import summarize
from . import chatgpt
from .advisor import analyze, current_report
from .conversation import turn as conversation_turn
from .details import read_detail
from .avito import PublicAvito, SourceUnavailable
from .analysis import assess, render, shortlist
from .config import Config
from .demo import demo_listings
from .models import CATEGORIES, utc_now, timestamp
from .presentation import caption, ranked_feed, recent, full_card
from .network import RemoteError, post_json, photo_bytes, post_photos
from .sources import PublicCatalog, source_name, AKULA_PATHS
from .schedule import SCHEDULE_LABELS, due_slot, MOSCOW
from .storage import Store, load_jsonl
from .workspace import STAGES, VIEWS, active_profile, filter_results, matches_profile, validate_profile, watched

LOG = logging.getLogger(__name__)
HELP = (
    "Найди интересное предложение в Ростове и области.\n\n"
    "Выбери категорию ниже: покажу фотографии, цены, местоположение и сравнение с похожими объявлениями."
)
CATEGORY_KEYBOARD = {"keyboard": [["🔎 Категории", "📬 Сводка 10 объектов"],
                                   ["⚙️ Мои сводки", "📊 Источники"]],
                     "resize_keyboard": True, "is_persistent": True}
CHOICES = list(CATEGORIES.items())
CATEGORY_CHOICES = {"inline_keyboard": [
    [{"text": label, "callback_data": "category:" + key} for key, label in CHOICES[i:i + 2]]
    for i in range(0, len(CHOICES), 2)]}


def miniapp_url():
    value = os.getenv('MINI_APP_URL', '')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        return ''
    return value


def card_keyboard(item) -> dict:
    buttons = [[{"text": "Открыть · " + source_name(item), "url": item.url}]]
    if len(item.id.encode()) <= 55:
        buttons.append([{"text": "Анализ", "callback_data": "ai:" + item.id},
                        {"text": "Аналоги", "callback_data": "peers:" + item.id}])
        buttons.append([{"text": "Вся информация", "callback_data": "details:" + item.id},
                        {"text": "☆ Сохранить", "callback_data": "favorite:" + item.id}])
        buttons.append([{'text':'Мои действия','callback_data':'actions:'+item.id}])
    return {"inline_keyboard": buttons}


def post_caption(result, report=None):
    text = caption(result)
    if report:
        text = text.replace("\n<i>Характеристики, текст и разбор — в кнопках ниже</i>", "")
        summary = re.sub(r"[#*_]", "", report.get("text", "")).strip()[:200]
        summary = re.sub(r"^Краткий вывод\s*[:\n]*", "", summary, flags=re.I).strip()
        addition = "\n\n<b>ИИ · краткий вывод</b>\n" + escape(summary)
        while len(text + addition) > 1000 and len(summary) > 40:
            summary = summary[:-10]
            addition = "\n\n<b>ИИ · краткий вывод</b>\n" + escape(summary) + "…"
        if len(text + addition) <= 1000: text += addition
    return text


class Telegram:
    def __init__(self, token: str):
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
            raise ValueError("Задайте TELEGRAM_BOT_TOKEN в .env")
        self._base = "https://api.telegram.org/bot" + token + "/"
        self._last_send: dict[int, float] = {}
        self._photo_cache: dict[str, str] = {}

    def call(self, method: str, payload: dict) -> object:
        response = post_json(self._base + method, payload)
        if not response.get("ok"):
            raise RemoteError(int(response.get("error_code", 0)))
        return response.get("result")

    def send(self, chat_id: int, text: str, keyboard: dict | None = None) -> None:
        for offset in range(0, len(text), 3500):
            delay = 1.1 - (time.monotonic() - self._last_send.get(chat_id, 0))
            if delay > 0:
                time.sleep(delay)
            payload = {"chat_id": chat_id, "text": text[offset:offset + 3500],
                       "link_preview_options": {"is_disabled": True}}
            if keyboard:
                payload["reply_markup"] = keyboard
            self.call("sendMessage", payload)
            self._last_send[chat_id] = time.monotonic()

    def send_card(self, chat_id: int, result, keyboard: dict, report=None):
        delay = 1.1 - (time.monotonic() - self._last_send.get(chat_id, 0))
        if delay > 0:
            time.sleep(delay)
        payload = {"chat_id": chat_id, "parse_mode": "HTML", "reply_markup": keyboard}
        text = post_caption(result, report)
        photo = result.listing.photo_url
        if photo:
            try:
                response = self.call("sendPhoto", {**payload, "photo": self._photo_cache.get(photo, photo), "caption": text})
                if isinstance(response, dict) and response.get("photo"):
                    self._photo_cache[photo] = response["photo"][-1]["file_id"]
                self._last_send[chat_id] = time.monotonic()
                return response
            except RemoteError as error:
                if error.status != 400:
                    raise
                try:
                    response = post_photos(self._base + "sendPhoto", {**payload, "caption": text}, {"photo": photo_bytes(photo)})
                    self._photo_cache[photo] = response["result"]["photo"][-1]["file_id"]
                    self._last_send[chat_id] = time.monotonic()
                    return response["result"]
                except RemoteError as upload_error:
                    if upload_error.status != 400:
                        raise
                    LOG.warning("Photo unavailable; sending useful text card")
        response = self.call("sendMessage", {**payload, "text": text, "link_preview_options": {"is_disabled": True}})
        self._last_send[chat_id] = time.monotonic()
        return response

    def send_album(self, chat_id: int, results: list) -> None:
        if not 1 <= len(results) <= 10:
            raise ValueError("В альбоме должно быть от 1 до 10 объектов")
        if len(results) == 1:
            self.send_card(chat_id, results[0], card_keyboard(results[0].listing))
            return
        delay = 1.1 - (time.monotonic() - self._last_send.get(chat_id, 0))
        if delay > 0: time.sleep(delay)
        media = [{"type": "photo", "media": self._photo_cache.get(r.listing.photo_url, r.listing.photo_url),
                  "caption": caption(r) + '\n\n<a href="' + escape(r.listing.url, quote=True) + '">Открыть · ' + source_name(r.listing) + '</a>',
                  "parse_mode": "HTML"} for r in results]
        payload = {"chat_id": chat_id, "media": media}
        try:
            response = self.call("sendMediaGroup", payload)
        except RemoteError as error:
            if error.status != 400: raise
            files = {}
            try:
                for i, result in enumerate(results):
                    files[f"photo{i}"] = photo_bytes(result.listing.photo_url)
                    media[i]["media"] = f"attach://photo{i}"
                response = post_photos(self._base + "sendMediaGroup", payload, files)["result"]
            except RemoteError as upload_error:
                if upload_error.status != 400: raise
                for result in results:
                    self.send_card(chat_id, result, card_keyboard(result.listing))
                return
        if isinstance(response, list):
            for result, message in zip(results, response):
                if message.get("photo"):
                    self._photo_cache[result.listing.photo_url] = message["photo"][-1]["file_id"]
        self._last_send[chat_id] = time.monotonic()


class Bot:
    def __init__(self, config: Config, store: Store, telegram: Telegram, allowed: set[int], model: str = "",
                 pairing_code: str = ""):
        owner = store.get_state("owner_chat_id")
        allowed = set(allowed)
        if owner:
            allowed.add(int(owner))
        if (not allowed and len(pairing_code) < 20) or any(value <= 0 for value in allowed):
            raise ValueError("Нужны TELEGRAM_ALLOWED_CHAT_IDS или код привязки длиной от 20 символов")
        self.config, self.store, self.telegram = config, store, telegram
        self.allowed, self.model = allowed, model
        for chat_id in allowed: self.store.migrate_deliveries(chat_id)
        self.pairing_code = pairing_code
        self.advisor_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="listing-advisor")
        self.advisor_pending = set()
        self.advisor_lock = threading.Lock()
        self.delivery_lock = threading.Lock()
        self.chat_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="listing-chat")
        self.chat_queued = 0
        self.source = PublicAvito(config.source_refresh_seconds) if config.source in {"avito_public", "public_catalogs"} else None
        self.sources = [("Авито", self.source)] if self.source else []
        if config.source == "public_catalogs":
            self.sources += [("Яндекс Недвижимость", PublicCatalog("yandex", config.source_refresh_seconds)),
                             ("Дром", PublicCatalog("drom", config.source_refresh_seconds)),
                             ("Акула", PublicCatalog("akula", config.source_refresh_seconds))]

    def chat_message(self, chat_id, text):
        with self.advisor_lock:
            if self.chat_queued >= 3:
                self.telegram.send(chat_id, "Дождись ответа ИИ: в очереди уже есть сообщения.")
                return
            self.chat_queued += 1
        def complete():
            try:
                self.telegram.call('sendChatAction', {'chat_id': chat_id, 'action': 'typing'})
                reply = conversation_turn(self.store, self.config, chat_id, text)
                keyboard = {'inline_keyboard': [[{'text': 'Открыть поиск на Авито', 'url': reply['search_url']}]]} if reply.get('search_url') else None
                self.telegram.send(chat_id, reply['answer'], keyboard)
                for result in reply['results']:
                    self.publish(chat_id, result, card_keyboard(result.listing))
            except ValueError as error:
                self.telegram.send(chat_id, str(error)[:400])
            except (RemoteError, SourceUnavailable):
                self.telegram.send(chat_id, 'ИИ или источник не ответил. Попробуй запрос ещё раз; объявления не выдумываются.')
            finally:
                with self.advisor_lock: self.chat_queued -= 1
        self.chat_worker.submit(complete)

    def publish(self, chat_id, result, keyboard):
        # Scheduled scans and Telegram commands can select the same object concurrently.
        with self.delivery_lock:
            if result.listing.id in self.store.delivered_ids(chat_id, [result.listing]):
                return None
            return self._publish(chat_id, result, keyboard)

    def _publish(self, chat_id, result, keyboard):
        def remember(message):
            self.store.mark_delivered(chat_id,result.listing)
            if isinstance(message,dict) and message.get('message_id'):
                self.store.set_state(f"card_keyboard:{chat_id}:{message['message_id']}",json.dumps(keyboard,ensure_ascii=False))
            return message
        meta = self.store.listing_meta(chat_id,result.listing.id)
        for row in keyboard.get('inline_keyboard',[]):
            for button in row:
                if button.get('callback_data')=='favorite:'+result.listing.id:
                    button['text']='★ Сохранено' if meta['favorite'] else '☆ Сохранить'
        if not chatgpt.status()["connected"]:
            return remember(self.telegram.send_card(chat_id, result, keyboard))
        cached = json.loads(self.store.get_state("advisor:" + result.listing.id, "null"))
        if not current_report(cached,result): cached = None
        message = self.telegram.send_card(chat_id, result, keyboard, cached)
        remember(message)
        key = (chat_id, result.listing.id, result.listing.price_rub)
        if not cached:
            with self.advisor_lock:
                if key in self.advisor_pending: return message
                self.advisor_pending.add(key)
            def complete():
                try:
                    report = analyze(result, self.store)
                    if (isinstance(message, dict) and message.get("message_id")
                            and self.store.get_state(f"card_view:{chat_id}:{message['message_id']}", "summary").startswith("summary")):
                        field = "caption" if message.get("photo") else "text"
                        self.telegram.call("editMessageCaption" if field == "caption" else "editMessageText",
                            {"chat_id": chat_id, "message_id": message["message_id"], field: post_caption(result, report),
                             "parse_mode": "HTML", "reply_markup": keyboard})
                except (RemoteError, ValueError, SourceUnavailable):
                    LOG.warning("Background listing analysis unavailable; no result fabricated")
                finally:
                    with self.advisor_lock: self.advisor_pending.discard(key)
            self.advisor_worker.submit(complete)
        return message

    def edit_card_view(self, message, item, view, page=0):
        """Buttons replace the current post, including long reports in pages."""
        from .card_views import section_card
        chat_id, message_id = message['chat']['id'], message['message_id']
        view_key = f'card_view:{chat_id}:{message_id}'
        revision = ('summary' if view == 's' else view) + ':' + secrets.token_hex(6)
        self.store.set_state(view_key, revision)
        pool = self.store.all()
        result = assess(item, pool, replace(self.search_for(chat_id), category=item.category))
        meta = self.store.set_listing_meta(chat_id,item.id,seen=True)
        saved = bool(meta['favorite'])
        report = json.loads(self.store.get_state('advisor:' + item.id, 'null'))
        if not current_report(report,result):
            report = None

        def update(text, keyboard):
            if self.store.get_state(view_key) != revision:
                return
            field = 'caption' if message.get('photo') else 'text'
            if message.get(field) == text and message.get('reply_markup') == keyboard:
                return
            payload = {'chat_id': chat_id, 'message_id': message_id, field: text,
                       'parse_mode': 'HTML', 'reply_markup': keyboard}
            if field == 'text': payload['link_preview_options'] = {'is_disabled': True}
            self.telegram.call('editMessageCaption' if field == 'caption' else 'editMessageText', payload)

        def show(text):
            body, keyboard = section_card(item, text, view, page, photo=bool(message.get('photo')), favorite=saved)
            update(body, keyboard)

        if view == 's':
            keyboard = json.loads(self.store.get_state(f'card_keyboard:{chat_id}:{message_id}','null')) or card_keyboard(item)
            for row in keyboard['inline_keyboard']:
                for button in row:
                    if button.get('callback_data') == 'favorite:' + item.id:
                        button['text'] = '★ Сохранено' if saved else '☆ Сохранить'
            update(post_caption(result, report), keyboard)
        elif view == 'd':
            try:
                detail = read_detail(item, self.store)
            except SourceUnavailable as error:
                detail = {'description': item.description, 'params': item.attributes.get('visible_params', ''), 'error': str(error)}
            show(full_card(result, detail))
        elif view == 'p':
            peers = [other for other in pool if other.id in result.comparable_ids]
            show('\n\n'.join(f'{peer.title}\n{peer.price_rub:,.0f} ₽ · {peer.locality}\n{peer.url}' for peer in peers)
                 or 'Пока недостаточно похожих объявлений. Разница в цене не присваивается.')
        elif view == 'c':
            show(render(result))
        elif view == 'h':
            history = self.store.history(item.id)
            changes, previous = [], None
            for observed,price in history:
                if price != previous:
                    changes.append(f'{timestamp(observed).astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")} МСК: {price:,.0f} ₽')
                previous = price
            show('История наблюдений ботом, не вся история объявления:\n\n'+'\n'.join(changes))
        elif view == 'a':
            def report_text(value):
                text = re.sub(r'(?m)^#{1,4}\s*', '', value['text']).replace('**', '')
                sources = '\n'.join(c['url'] for c in value.get('citations', []) if c.get('url', '').startswith(('https://', 'http://')))
                return f"Фото изучено: {value.get('photos_analyzed', 0)}\n\n" + text + ('\n\nИсточники:\n' + sources if sources else '\n\nНезависимые источники не получены.')
            if report:
                show(report_text(report))
            elif not chatgpt.status()['connected']:
                show('Подключи ChatGPT в приложении Радар. Разбор фотографий и района пока не выполнен.')
            else:
                show('Читаю описание и изучаю доступные фотографии. Разбор появится в этой карточке автоматически.')
                def complete():
                    try:
                        value = analyze(result, self.store)
                        show(report_text(value))
                    except (RemoteError, ValueError, SourceUnavailable):
                        show('Не удалось завершить разбор. Нажми «Анализ», чтобы повторить. Исходное объявление доступно по кнопке.')
                self.advisor_worker.submit(complete)

    def digest_categories(self, chat_id: int) -> list[str]:
        profile = active_profile(self.store,chat_id)
        if profile: return [profile['category']]
        saved = self.store.get_state(f"digest_categories:{chat_id}")
        return [c for c in saved.split(",") if c in CATEGORIES] if saved else [self.search_for(chat_id).category]

    def show_settings(self, chat_id: int) -> None:
        mode = self.store.get_state(f"schedule:{chat_id}", "off")
        categories = self.digest_categories(chat_id)
        category_buttons = [{"text": ("✓ " if c in categories else "○ ") + label, "callback_data": "prefcat:" + c}
                            for c, label in CATEGORIES.items()]
        keyboard = {"inline_keyboard": [category_buttons[i:i + 2] for i in range(0, len(category_buttons), 2)] + [
            [{"text": label, "callback_data": "schedule:" + key}] for key, label in SCHEDULE_LABELS.items()
        ] + [[{'text':'Мои поиски','callback_data':'mysearches'},{'text':'Режим выдачи','callback_data':'myviews'}],
             [{"text": "Получить 10 объектов сейчас", "callback_data": "digestnow"}]]}
        text = ("📬 Твои сводки\n\n" + SCHEDULE_LABELS.get(mode, SCHEDULE_LABELS["off"]) + " · МСК\n"
                "До 10 объектов в каждой сводке\n\nКатегории: " + ", ".join(CATEGORIES[c] for c in categories) +
                "\n\nВыдача по кнопкам работает отдельно. Здесь можно выбрать несколько категорий для автосводки."
                "\nЗа последние 7 дней по дате сайта. По умолчанию сначала дешёвые; порядок можно изменить кнопками под сводкой.")
        self.telegram.send(chat_id, text, keyboard)

    def sort_mode(self, chat_id: int) -> str:
        mode = self.store.get_state(f"sort:{chat_id}", "price_asc")
        return mode if mode in {"newest", "price_asc", "price_desc"} else "price_asc"

    def sorting_keyboard(self, prefix: str = "sort") -> list:
        return [{"text": label, "callback_data": prefix + ":" + value} for value, label in
                (("newest", "🕒 Новые"), ("price_asc", "Цена ↑"), ("price_desc", "Цена ↓"))]

    def send_digest(self, chat_id: int, *, automatic: bool = False, offset: int = 0, token: str = "") -> int:
        pool = self.store.all()
        delivered = self.store.delivered_ids(chat_id,pool)
        state_key = f"digest_snapshot:{chat_id}"
        context = [active_profile(self.store,chat_id),self.store.get_state(f'view:{chat_id}','all'),self.sort_mode(chat_id)]
        if token:
            try:
                snapshot = json.loads(self.store.get_state(state_key))
                if snapshot["token"] != token or snapshot.get('context',context)!=context: raise ValueError()
            except (ValueError, KeyError, TypeError):
                self.telegram.send(chat_id, "Подборка уже обновилась. Нажми «Сводка 10 объектов» для свежих результатов.")
                return 0
            by_id = {r.listing.id: r for c in self.digest_categories(chat_id)
                     for r in self.personal_results(chat_id, ranked_feed(pool, replace(self.search_for(chat_id), category=c), recent_days=7,
                                          sort_mode=self.sort_mode(chat_id)))}
            ids = snapshot["ids"]
        else:
            combined = [r for c in self.digest_categories(chat_id)
                        for r in self.personal_results(chat_id, ranked_feed(pool, replace(self.search_for(chat_id), category=c), recent_days=7,
                                             sort_mode=self.sort_mode(chat_id)))]
            mode = self.sort_mode(chat_id)
            if mode == "newest":
                combined.sort(key=lambda r: timestamp(r.listing.attributes["site_date_at"]), reverse=True)
            else:
                combined.sort(key=lambda r: r.listing.price_rub, reverse=mode == "price_desc")
            by_id = {r.listing.id: r for r in combined}
            ids = list(by_id)
            token = secrets.token_hex(6)
            self.store.set_state(state_key, json.dumps({"token": token, "ids": ids,'context':context}))
        # Keep snapshot offsets stable while skipping entries that expired since
        # the preceding page. Fill up to ten current listings when available.
        position, results = offset, []
        while position < len(ids) and len(results) < 10:
            item = by_id.get(ids[position])
            position += 1
            if item is not None and item.listing.id not in delivered:
                results.append(item)
        if not results:
            if not automatic:
                self.telegram.send(chat_id, "Новых подходящих объявлений пока нет. Уже отправленные не повторяю; старые и без даты исключены. Все объекты можно открыть в приложении.", CATEGORY_KEYBOARD)
            return 0
        rated = sum(r.discount is not None and r.status != "insufficient" for r in results)
        self.telegram.send(chat_id, f"📬 {'Автосводка' if automatic else 'Твоя сводка'} · {len(results)} объектов\n"
                           f"За 7 дней по дате на сайте · с оценкой: {rated}\n"
                           "Дата загрузки ботом не используется. Поднятие объявления может менять дату на сайте.\n"
                           "Открой фото: там цена, адрес и ссылка. Проценты предварительные.", CATEGORY_KEYBOARD)
        for result in results:
            self.publish(chat_id, result, card_keyboard(result.listing))
        keyboard = {"inline_keyboard": [self.sorting_keyboard("digestsort")]}
        if any(key in by_id and key not in delivered for key in ids[position:]):
            keyboard["inline_keyboard"].insert(0, [{"text": "Ещё 10 объявлений", "callback_data": f"digestmore:{token}:{position}"}])
        self.telegram.send(chat_id, "Следующая порция и порядок объявлений:", keyboard)
        return len(results)

    def scheduled_digests(self) -> None:
        for chat_id in self.store.subscribers():
            if chat_id not in self.allowed: continue
            slot = due_slot(self.store.get_state(f"schedule:{chat_id}", "off"), utc_now())
            if not slot: continue
            key = f"digest_slot:{chat_id}:{slot}"
            if not self.store.claim_state(key, "sending"): continue
            try:
                count = self.send_digest(chat_id, automatic=True)
                self.store.set_state(key, f"sent:{count}")
                self.store.set_state(f"digest_last:{chat_id}", f"{slot}: {count} объектов")
            except RemoteError as error:
                self.store.set_state(key, "failed")
                self.store.set_state(f"digest_last:{chat_id}", f"{slot}: доставка не завершена")
                if error.status == 403: self.store.subscribe(chat_id, False)
                raise

    def search_for(self, chat_id: int):
        threshold = float(self.store.get_state(f"discount:{chat_id}", str(self.config.search.discount)))
        profile = active_profile(self.store,chat_id)
        return replace(self.config.search, category=profile['category'] if profile else self.store.category(chat_id, self.config.search.category), discount=threshold)

    def personal_results(self, chat_id, results):
        return filter_results(self.store,chat_id,results,profile=active_profile(self.store,chat_id))

    def show_searches(self, chat_id):
        profiles = self.store.profiles(chat_id)
        keyboard = {'inline_keyboard':[[{'text':p['name'],'callback_data':'profile:'+p['id']}] for p in profiles]
                    + [[{'text':'Без сохранённого поиска','callback_data':'profile:clear'}]]}
        self.telegram.send(chat_id, 'Мои поиски\n\nВыбери сохранённый поиск. Создать и изменить фильтры можно в приложении.\n'
                           'В боте можно создать поиск командой:\n/save_search Название | Город или район | Максимальная цена | Исключить слова через запятую\n'
                           'Пример: /save_search Гараж до 700 тысяч | Северный | 700000 | аренда, доля\n'
                           'Категория берётся из текущего выбора. Снижения цен по поискам отслеживаются автоматически.', keyboard)

    def show_views(self, chat_id):
        selected = self.store.get_state(f'view:{chat_id}','all')
        self.telegram.send(chat_id,'Режим просмотра. Все варианты сортируются сначала по самой низкой цене.',
            {'inline_keyboard':[[{'text':('✓ ' if k==selected else '')+label,'callback_data':'view:'+k}] for k,label in VIEWS.items()]})

    def action_keyboard(self, chat_id, item):
        meta = self.store.listing_meta(chat_id,item.id)
        return {'inline_keyboard':[
            [{'text':'Вернуть в выдачу' if meta['hidden'] else 'Скрыть','callback_data':'m:hide:'+item.id},
             {'text':'Отметить непросмотренным' if meta['seen'] else 'Просмотрено','callback_data':'m:seen:'+item.id}],
            [{'text':'Не следить за ценой' if watched(meta) else 'Следить за ценой','callback_data':'m:watch:'+item.id}],
            *[[{'text':('✓ ' if meta['stage']==stage else '')+label,'callback_data':'s:'+code+':'+item.id}] for code,stage,label in
              [('n','','Без статуса'),('i','interested','Интересен'),('c','contacted','Связался'),('v','inspection','Осмотр'),('r','rejected','Отказ')]],
            [{'text':'История цены','callback_data':'history:'+item.id}],
            [{'text':'К карточке','callback_data':'card:'+item.id}]]}

    def price_alerts(self):
        by_id = {item.id:item for item in self.store.all()}
        for chat_id in self.store.subscribers():
            if chat_id not in self.allowed: continue
            profiles = [p for p in self.store.profiles(chat_id) if p.get('alerts')]
            sent = 0
            for event in self.store.pending_price_drops(chat_id):
                item = by_id.get(event['listing_id'])
                meta = self.store.listing_meta(chat_id,event['listing_id'])
                eligible = (item and item.active and recent(item,utc_now(),7) and item.price_rub==event['current']
                            and (utc_now()-timestamp(event['observed_at'])).total_seconds()<=86400
                            and not meta['hidden'] and meta['stage']!='rejected' and meta['watch']!=0
                            and (watched(meta) or any(matches_profile(item,p) for p in profiles)))
                if eligible:
                    if sent>=5: break
                    self.telegram.send(chat_id,f'Цена снизилась\n\n{item.title}\n{item.locality}\n'
                        f"{event['previous']:,.0f} ₽ → {event['current']:,.0f} ₽\n"
                        f"Снижение: {event['previous']-event['current']:,.0f} ₽\n"
                        'Изменение замечено ботом, наличие уточни у продавца.',card_keyboard(item))
                    sent+=1
                self.store.finish_price_drop(chat_id,event['id'])

    def show_feed(self, chat_id: int, offset: int = 0, minimum_discount: float | None = None, token: str = '') -> None:
        search = self.search_for(chat_id)
        results = self.personal_results(chat_id,ranked_feed(self.store.all(), search, minimum_discount, recent_days=7, sort_mode=self.sort_mode(chat_id)))
        delivered = self.store.delivered_ids(chat_id,[r.listing for r in results])
        results = [r for r in results if r.listing.id not in delivered]
        key = f"feed:{chat_id}:{search.category}"
        self.store.set_state(key + ":filter", "" if minimum_discount is None else str(minimum_discount))
        if not results:
            if minimum_discount is not None:
                message = f"Пока нет объявлений с рассчитанной разницей от {minimum_discount:.0%}.\nНажми «Все объявления», чтобы посмотреть остальные варианты."
            else:
                message = "Новых объявлений за последние 7 дней пока нет. Уже отправленные не повторяю. Все объекты доступны в приложении."
            self.telegram.send(chat_id, message, CATEGORY_KEYBOARD)
            return
        snapshot_key = f'feed_snapshot:{chat_id}'
        context = [search.category,active_profile(self.store,chat_id),self.store.get_state(f'view:{chat_id}','all'),self.sort_mode(chat_id),minimum_discount]
        if offset:
            try:
                snapshot = json.loads(self.store.get_state(snapshot_key))
                if snapshot['context']!=context or (token and snapshot['token']!=token): raise ValueError()
            except (ValueError,KeyError,TypeError):
                self.telegram.send(chat_id,'Условия выдачи изменились. Открой категорию заново.',CATEGORY_KEYBOARD)
                return
        else:
            snapshot = {'token':secrets.token_hex(6),'ids':[r.listing.id for r in results],'context':context}
            self.store.set_state(snapshot_key,json.dumps(snapshot))
        by_id = {r.listing.id:r for r in results}
        position, page = offset, []
        while position<len(snapshot['ids']) and len(page)<10:
            ident=snapshot['ids'][position]
            position+=1
            if ident in by_id: page.append(by_id[ident])
        if not page:
            self.store.set_state(key + ":offset", "0")
            self.telegram.send(chat_id, "Ты посмотрел все загруженные объявления. «Все объявления» откроет выдачу сначала.", CATEGORY_KEYBOARD)
            return
        self.store.set_state(key + ":offset", str(position))
        for index, result in enumerate(page):
            keyboard = card_keyboard(result.listing)
            if index == len(page) - 1:
                keyboard["inline_keyboard"].append(self.sorting_keyboard())
            if index == len(page) - 1:
                keyboard["inline_keyboard"].append([{"text": label, "callback_data": "feed:" + value}
                    for value, label in (("all", "Все"), ("10", "10%+"), ("20", "20%+"), ("30", "30%+"))])
            if index == len(page) - 1 and any(ident in by_id for ident in snapshot['ids'][position:]):
                keyboard["inline_keyboard"].append([{"text": "Ещё 10 объявлений", "callback_data": f"more:{search.category}:{snapshot['token']}:{position}"}])
            self.publish(chat_id, result, keyboard)

    def handle(self, update: dict) -> None:
        callback = update.get("callback_query")
        if callback:
            message = callback.get("message", {})
            chat = message.get("chat", {})
            if (chat.get("id") not in self.allowed or chat.get("type") != "private"
                    or callback.get("from", {}).get("id") != chat.get("id")):
                return
            data = callback.get("data", "")
            if data in {'mysearches','myviews'}:
                self.telegram.call('answerCallbackQuery',{'callback_query_id':callback['id']})
                (self.show_searches if data=='mysearches' else self.show_views)(chat['id'])
                return
            if data.startswith('view:') and data[5:] in VIEWS:
                self.telegram.call('answerCallbackQuery',{'callback_query_id':callback['id']})
                self.store.set_state(f"view:{chat['id']}",data[5:])
                self.show_feed(chat['id'])
                return
            if data.startswith('profile:'):
                ident = data[8:]
                profile = next((p for p in self.store.profiles(chat['id']) if p['id']==ident),None)
                if ident!='clear' and not profile: return
                self.telegram.call('answerCallbackQuery',{'callback_query_id':callback['id']})
                self.store.set_state(f"active_search:{chat['id']}",'' if ident=='clear' else ident)
                if profile: self.store.set_category(chat['id'],profile['category'])
                self.show_feed(chat['id'])
                return
            action = re.fullmatch(r'(?:m:(hide|seen|watch)|s:([nicvr])):([A-Za-z0-9_-]{1,55})',data)
            if action:
                item = next((i for i in self.store.all() if i.id==action[3]),None)
                if not item or not message.get('message_id'): return
                meta = self.store.listing_meta(chat['id'],item.id)
                if action[2]: change = {'stage':{'n':'','i':'interested','c':'contacted','v':'inspection','r':'rejected'}[action[2]]}
                else:
                    key = {'hide':'hidden','seen':'seen','watch':'watch'}[action[1]]
                    change = {key:not (watched(meta) if key=='watch' else meta[key])}
                self.store.set_listing_meta(chat['id'],item.id,**change)
                # Keep the user's chosen card section when a background report finishes.
                self.store.set_state(f"card_view:{chat['id']}:{message['message_id']}",'actions')
                self.telegram.call('editMessageReplyMarkup',{'chat_id':chat['id'],'message_id':message['message_id'],'reply_markup':self.action_keyboard(chat['id'],item)})
                self.telegram.call('answerCallbackQuery',{'callback_query_id':callback['id'],'text':'Сохранено'})
                return
            more_digest = re.fullmatch(r"digestmore:([a-f0-9]{12}):(\d{1,5})", data)
            if more_digest:
                self.telegram.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
                self.send_digest(chat["id"], offset=int(more_digest[2]), token=more_digest[1])
                return
            sort = re.fullmatch(r"(sort|digestsort):(newest|price_asc|price_desc)", data)
            if sort:
                self.telegram.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
                self.store.set_state(f"sort:{chat['id']}", sort[2])
                if sort[1] == "digestsort": self.send_digest(chat["id"])
                else: self.show_feed(chat["id"])
                return
            if data.startswith("category:") and data.split(":", 1)[1] in CATEGORIES:
                self.telegram.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
                self.handle({"message": {"chat": chat, "text": "/category " + data.split(":", 1)[1]}})
                return
            if data in {"feed:all", "feed:10", "feed:20", "feed:30"}:
                self.telegram.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
                value = data.split(":", 1)[1]
                self.show_feed(chat["id"], minimum_discount=None if value == "all" else int(value) / 100)
                return
            if data == "digestnow" or data.startswith(("prefcat:", "schedule:")):
                chat_id = chat["id"]
                self.telegram.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
                if data == "digestnow":
                    self.send_digest(chat_id)
                    return
                kind, value = data.split(":", 1)
                if kind == "prefcat" and value in CATEGORIES:
                    categories = self.digest_categories(chat_id)
                    if value in categories and len(categories) > 1:
                        categories.remove(value)
                    elif value not in categories:
                        categories.append(value)
                    self.store.set_state(f"digest_categories:{chat_id}", ",".join(categories))
                elif kind == "schedule" and value in SCHEDULE_LABELS:
                    self.store.set_state(f"schedule:{chat_id}", value)
                    if value != "off": self.store.subscribe(chat_id, True)
                self.show_settings(chat_id)
                return
            more = re.fullmatch(r"more:([a-z]+):(?:([a-f0-9]{12}):)?(\d{1,5})", callback.get("data", ""))
            if more:
                chat_id = chat["id"]
                if more[1] != self.search_for(chat_id).category:
                    self.telegram.send(chat_id, "Категория уже изменена. Нажми «Все объявления».")
                    return
                self.telegram.call("answerCallbackQuery", {"callback_query_id": callback["id"]})
                saved_filter = self.store.get_state(f"feed:{chat_id}:{more[1]}:filter")
                self.show_feed(chat_id, int(more[3]), float(saved_filter) if saved_filter else None,token=more[2] or '')
                return
            paged = re.fullmatch(r'v:([adpch]):(\d{1,3}):([A-Za-z0-9_-]{1,55})', data)
            match = re.fullmatch(r"(ai|check|peers|details|favorite|card|history|actions):([A-Za-z0-9_-]{1,55})", data)
            if paged or match:
                if not isinstance(message.get('message_id'), int):
                    self.telegram.call('answerCallbackQuery', {'callback_query_id': callback['id'], 'text': 'Эта карточка больше недоступна', 'show_alert': True})
                    return
                ident = paged[3] if paged else match[2]
                item = next((item for item in self.store.all() if item.id == ident), None)
                if item is None:
                    self.telegram.call('answerCallbackQuery', {'callback_query_id': callback['id'], 'text': 'Объявление больше не найдено', 'show_alert': True})
                    return
                if match and match[1]=='actions':
                    self.store.set_state(f"card_view:{chat['id']}:{message['message_id']}",'actions')
                    self.telegram.call('editMessageReplyMarkup',{'chat_id':chat['id'],'message_id':message['message_id'],'reply_markup':self.action_keyboard(chat['id'],item)})
                    self.telegram.call('answerCallbackQuery',{'callback_query_id':callback['id']})
                    return
                if match and match[1] == 'favorite':
                    saved = not self.store.listing_meta(chat['id'],item.id)['favorite']
                    self.store.set_listing_meta(chat['id'],item.id,favorite=saved)
                    keyboard = message.get('reply_markup') or card_keyboard(item)
                    for row in keyboard.get('inline_keyboard', []):
                        for button in row:
                            if button.get('callback_data') == data:
                                button['text'] = '★ Сохранено' if saved else '☆ Сохранить'
                    self.telegram.call('editMessageReplyMarkup', {'chat_id': chat['id'], 'message_id': message['message_id'], 'reply_markup': keyboard})
                    self.telegram.call('answerCallbackQuery', {'callback_query_id': callback['id'], 'text': 'Сохранено' if saved else 'Убрано из избранного'})
                    return
                self.telegram.call('answerCallbackQuery', {'callback_query_id': callback['id']})
                view = paged[1] if paged else {'ai':'a','details':'d','peers':'p','check':'c','card':'s','history':'h'}[match[1]]
                self.edit_card_view(message, item, view, int(paged[2]) if paged else 0)
                return
            return
        message = update.get("message", {})
        chat = message.get("chat", {})
        chat_id = chat.get("id")
        if chat.get("type") != "private" or not isinstance(chat_id, int) or chat_id <= 0:
            return
        text = message.get("text", "").strip()
        if not text:
            return
        text = {"Все объявления": "/digest", "Ещё объявления": "/more",
                "🔎 Категории": "/category", "📊 Источники": "/status",
                "📬 Сводка 10 объектов": "/summary", "📬 Сводка 20 объектов": "/summary", "⚙️ Мои сводки": "/settings",
                "От 10%": "/discount 10", "От 20%": "/discount 20", "От 30%": "/discount 30"}.get(text, text)
        command, _, argument = text.partition(" ")
        command = command.split("@")[0].lower()
        if chat_id not in self.allowed:
            if (command == "/start" and len(self.pairing_code) >= 20
                    and hmac.compare_digest(argument.strip().encode(), self.pairing_code.encode())
                    and self.store.claim_owner(chat_id)):
                self.allowed.add(chat_id)
            else:
                return
        selected = next((key for key, label in CATEGORIES.items() if text.casefold() == label.casefold()), None)
        if command == "/category" and argument.strip() in CATEGORIES:
            selected = argument.strip()
        if selected:
            self.store.set_state(f'active_search:{chat_id}','')
            self.store.set_category(chat_id, selected)
            self.telegram.send(chat_id, f"{CATEGORIES[selected]} · Ростовская область\nПоказываю объявления с ценами и фотографиями.", CATEGORY_KEYBOARD)
            self.show_feed(chat_id)
            return
        if not text.startswith('/'):
            self.chat_message(chat_id, text)
            return
        search = self.search_for(chat_id)
        if command == "/category":
            self.telegram.send(chat_id, "🔎 Что ищем?\nРостов-на-Дону и Ростовская область", CATEGORY_CHOICES)
            return
        if command in {"/start", "/stop"}:
            self.store.subscribe(chat_id, command == "/start")
            self.telegram.send(chat_id, HELP if command == "/start" else "Уведомления выключены.",
                               CATEGORY_KEYBOARD)
        elif command == '/app':
            url = miniapp_url()
            if url:
                self.telegram.send(chat_id, 'Радар · объявления, фильтры и разбор ИИ в одном приложении.',
                    {'inline_keyboard':[[{'text':'Открыть приложение','web_app':{'url':url}}]]})
            else:
                self.telegram.send(chat_id, 'Приложение ещё не подключено к HTTPS.')
        elif command == "/settings":
            self.show_settings(chat_id)
        elif command == '/searches':
            self.show_searches(chat_id)
        elif command == '/views':
            self.show_views(chat_id)
        elif command == '/save_search':
            parts = [part.strip() for part in argument.split('|')]
            try:
                if len(parts)!=4: raise ValueError('Формат: /save_search Название | Город или район | Максимальная цена | Исключить слова')
                profile = validate_profile(dict(name=parts[0],locality=parts[1],max_price=parts[2],exclude=parts[3],category=search.category))
                if len(self.store.profiles(chat_id))>=30: raise ValueError('Можно сохранить до 30 поисков')
                ident = secrets.token_hex(6)
                self.store.save_profile(chat_id,ident,profile)
                self.store.set_state(f'active_search:{chat_id}',ident)
                self.telegram.send(chat_id,'Поиск сохранён: '+profile['name'])
                self.show_feed(chat_id)
            except ValueError as error: self.telegram.send(chat_id,str(error))
        elif command == "/summary":
            self.send_digest(chat_id)
        elif command == "/filters":
            self.telegram.send(chat_id, f"{CATEGORIES[search.category]} · {search.region}\nНаселённый пункт: {search.locality or 'все'}\n"
                               f"Порог: {search.discount:.0%} ниже аналогов\nМинимум аналогов: {search.min_comparables}\n"
                               f"Давность наблюдения: до {search.max_age_days} дней\n"
                               "Аналоги: один населённый пункт и район, тип, права, состояние, близкая площадь.")
        elif command == "/status":
            count = sum(item.category == search.category for item in self.store.all())
            source = ", ".join(name for name, _ in self.sources) if self.sources else "Импорт файла"
            states = []
            for name, adapter in self.sources:
                if name == "Акула" and search.category not in AKULA_PATHS or name == "Дром" and search.category != "car" or name == "Яндекс Недвижимость" and search.category == "car":
                    continue
                states.append(self.store.get_state(f"source_status:{name}:{search.category}", name + ": ожидается проверка этой категории"))
            last = self.store.get_state(f'digest_last:{chat_id}', 'ещё не отправлялась')
            try: last = timestamp(last).astimezone(MOSCOW).strftime('%d.%m.%Y %H:%M МСК')
            except (ValueError, TypeError): pass
            account = chatgpt.status()
            ai = "ChatGPT подключён; фото, текст и поиск по району" if account.get('connected') else ("Ollama · резервный текстовый разбор" if self.model else "не подключён")
            self.telegram.send(chat_id, f"{CATEGORIES[search.category]} · объявлений в базе: {count}\nИсточники: {source}\n"
                               + "\n".join(states) + f"\nОхват: первые страницы каталогов; свежая подборка — до 7 дней по дате сайта.\nПоследняя сводка: {last}\nИИ: {ai}")
        elif command == "/digest":
            self.show_feed(chat_id)
        elif command == "/discount":
            if argument.strip() not in {"10", "20", "30"}:
                self.telegram.send(chat_id, "Выбери кнопку «От 10%», «От 20%» или «От 30%».", CATEGORY_KEYBOARD)
                return
            threshold = int(argument.strip()) / 100
            self.store.set_state(f"discount:{chat_id}", str(threshold))
            self.show_feed(chat_id, minimum_discount=threshold)
        elif command == "/more":
            key = f"feed:{chat_id}:{search.category}"
            saved_filter = self.store.get_state(key + ":filter")
            self.show_feed(chat_id, int(self.store.get_state(key + ":offset", "0")), float(saved_filter) if saved_filter else None)
        elif command == "/demo":
            results = shortlist(demo_listings(), replace(search, category="garage"))
            for result in results[:2]:
                self.telegram.send(chat_id, render(result))
        elif command == "/recent":
            self.show_feed(chat_id)
        elif command in {"/check", "/ai", "/history", "/peers", "/details", "/favorite"}:
            pool = self.store.all()
            item = next((item for item in pool if item.id == argument.strip()), None)
            if item is None:
                self.telegram.send(chat_id, "Объявление не найдено. Укажите ID из карточки: /check ID")
                return
            result = assess(item, pool, replace(search, category=item.category))
            if command == "/favorite":
                value = not self.store.listing_meta(chat_id,item.id)['favorite']
                self.store.set_listing_meta(chat_id,item.id,favorite=value)
                self.telegram.send(chat_id, "Сохранено в избранное приложения." if value else "Убрано из избранного.")
            elif command == "/details":
                try: detail = read_detail(item, self.store)
                except SourceUnavailable as error:
                    detail = {"description": item.description, "params": item.attributes.get("visible_params", ""), "error": str(error)}
                self.telegram.send(chat_id, full_card(result, detail), card_keyboard(item))
            elif command == "/peers":
                peers = [other for other in pool if other.id in result.comparable_ids]
                if not peers:
                    self.telegram.send(chat_id, "Пока нет достаточно похожих объявлений. Процент разницы не присваивается.")
                else:
                    self.telegram.send(chat_id, "Похожие предложения, на которых основано сравнение:")
                    for peer in peers[:3]:
                        self.telegram.send_card(chat_id, assess(peer, pool, replace(search, category=peer.category)), card_keyboard(peer))
            elif command == "/history":
                history = self.store.history(item.id)[-15:]
                self.telegram.send(chat_id, "История наблюдений, не история до подключения источника:\n" +
                                   "\n".join(f"{date}: {price:,.0f} ₽" for date, price in history))
            elif command == "/ai":
                self.telegram.send(chat_id, "Готовлю разбор текста и доступных данных. Фото рассматриваются только при подключённом ChatGPT.")
                try:
                    if chatgpt.status()["connected"]:
                        report = analyze(result, self.store)
                        sources = "\n".join(c["url"] for c in report.get("citations", []) if c.get("url"))
                        self.telegram.send(chat_id, f"ИИ-разбор · фото: {report['photos_analyzed']}\n\n" + report["text"] + ("\n\nИсточники:\n" + sources if sources else "\n\nВнешние источники не получены."))
                    else:
                        self.telegram.send(chat_id, "ChatGPT пока не подключён. Локальная модель ниже читает только текст; фото и район не исследованы.\n\n" + summarize(result, self.model))
                except ValueError as error:
                    self.telegram.send(chat_id, str(error))
                except RemoteError:
                    self.telegram.send(chat_id, "Локальная модель не ответила. Расчёт выше остаётся доступен.")
            else:
                self.telegram.send(chat_id, render(result))
        else:
            self.telegram.send(chat_id, HELP)

    def scan(self) -> None:
        if self.source:
            categories = {category for chat_id in self.allowed
                          for category in [self.search_for(chat_id).category] + self.digest_categories(chat_id)} or {self.config.search.category}
            categories.update(p['category'] for chat_id in self.allowed for p in self.store.profiles(chat_id) if p.get('alerts'))
            for category in sorted(categories):
                for name, source in self.sources:
                    try:
                        items = source.fetch(category)
                        if items is not None:
                            self.store.upsert(items)
                            self.store.set_state("source_status:" + name, f"{name} · {CATEGORIES[category]}: {len(items)} объявлений, " + utc_now().isoformat(timespec="minutes"))
                            fresh = sum(recent(item, utc_now(), 7) for item in items)
                            self.store.set_state(f"source_status:{name}:{category}",
                                f"{name}: прочитано {len(items)}, за неделю {fresh} · " + utc_now().astimezone(MOSCOW).strftime('%d.%m %H:%M МСК'))
                            self.store.set_state(f"source_success:{name}:{category}", "последний успех " + utc_now().astimezone(MOSCOW).strftime('%d.%m %H:%M МСК'))
                            LOG.info("Public catalog imported, source %s, category %s, records %s", name, category, len(items))
                    except SourceUnavailable:
                        self.store.set_state("source_status:" + name, name + ": временно недоступен, сохранённые данные доступны")
                        previous = self.store.get_state(f"source_success:{name}:{category}")
                        self.store.set_state(f"source_status:{name}:{category}", name + ": временно недоступен; сохранённые данные доступны" + (" · " + previous if previous else ""))
                        LOG.warning("Public catalog unavailable, source %s, category %s", name, category)
        if self.config.feed_file:
            try:
                changed = self.store.upsert(load_jsonl(self.config.feed_file))
                self.store.set_state("feed_status", f"Последний импорт успешен, изменений: {changed}")
            except (OSError, ValueError):
                self.store.set_state("feed_status", "Ошибка файла источника. Старые записи сохранены; проверьте файл.")
                LOG.warning("Feed import failed; details omitted")
                return
        pool = self.store.all()
        for chat_id in self.store.subscribers():
            if chat_id not in self.allowed:
                continue
            if self.store.get_state(f"schedule:{chat_id}"):
                # Digest settings replace the previous stream of individual alerts.
                continue
            results = shortlist(pool, self.search_for(chat_id))
            sent = 0
            for result in results:
                if result.listing.id in self.store.delivered_ids(chat_id,[result.listing]):
                    continue
                try:
                    self.publish(chat_id, result, card_keyboard(result.listing))
                except RemoteError as error:
                    if error.status == 403:
                        self.store.subscribe(chat_id, False)
                        break
                    raise
                self.store.mark_notified(chat_id, result.listing)
                self.store.mark_delivered(chat_id, result.listing)
                sent += 1
                if sent >= 5:
                    break
        self.price_alerts()
        self.scheduled_digests()

    def run(self) -> None:
        offset = int(self.store.get_state("telegram_offset", "0"))
        next_scan = 0.0
        LOG.info("Bot started; source mode %s", self.config.source)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="source-scan") as scanner:
            pending_scan = None
            while True:
                try:
                    if pending_scan is not None and pending_scan.done():
                        completed, pending_scan = pending_scan, None
                        completed.result()
                    if pending_scan is None and time.monotonic() >= next_scan:
                        next_scan = time.monotonic() + self.config.poll_seconds
                        pending_scan = scanner.submit(self.scan)
                    updates = self.telegram.call("getUpdates", {"offset": offset, "timeout": 25,
                                                                "allowed_updates": ["message", "callback_query"]})
                    if not isinstance(updates, list):
                        raise RemoteError()
                    for update in updates:
                        try:
                            self.handle(update)
                        except RemoteError as error:
                            if error.status not in {400, 403}:
                                raise
                            LOG.warning("Telegram message could not be delivered, code %s", error.status)
                        offset = update["update_id"] + 1
                        self.store.set_state("telegram_offset", str(offset))
                except RemoteError as error:
                    if error.status in {401, 409}:
                        raise ValueError("Проверьте токен, отсутствие webhook и другого экземпляра бота") from None
                    LOG.warning("Network unavailable, code %s", error.status)
                    time.sleep(error.retry_after)


def run_bot(config: Config, store: Store) -> None:
    allowed = {int(value.strip()) for value in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if value.strip()}
    telegram = Telegram(os.getenv("TELEGRAM_BOT_TOKEN", ""))
    identity = telegram.call("getMe", {})
    expected_id = int(os.environ["TELEGRAM_BOT_TOKEN"].split(":", 1)[0])
    if not isinstance(identity, dict) or identity.get("id") != expected_id:
        raise ValueError("Telegram вернул идентификатор другого бота; запуск остановлен")
    url = miniapp_url()
    if url:
        CATEGORY_KEYBOARD['keyboard'] = [row for row in CATEGORY_KEYBOARD['keyboard']
            if not any(isinstance(button,dict) and button.get('web_app') for button in row)]
        menu = {'type':'web_app','text':'Открыть приложение','web_app':{'url':url}}
        menu_owners = set(allowed)
        paired_owner = store.get_state('owner_chat_id')
        if paired_owner and paired_owner.isdigit(): menu_owners.add(int(paired_owner))
        for owner in menu_owners:
            telegram.call('setChatMenuButton',{'chat_id':owner,'menu_button':menu})
    telegram.call("setMyCommands", {"commands": [
        {'command':'app','description':'Открыть приложение Радар'},
        {"command": "start", "description": "Включить уведомления"},
        {"command": "category", "description": "Выбрать категорию"},
        {"command": "digest", "description": "Объявления с фото и ценами"},
        {"command": "summary", "description": "Сводка: 10 объектов с фотографиями"},
        {"command": "settings", "description": "Время и категории автосводок"},
        {'command':'searches','description':'Мои сохранённые поиски'},
        {'command':'views','description':'Непросмотренные, скрытые и статусы'},
        {"command": "filters", "description": "Параметры поиска"},
        {"command": "status", "description": "Состояние бота и источника"},
        {"command": "demo", "description": "Пример карточек, вымышленные данные"},
        {"command": "recent", "description": "Последние загруженные объявления"},
        {"command": "stop", "description": "Выключить уведомления"},
    ]})
    Bot(config, store, telegram, allowed, os.getenv("OLLAMA_MODEL", ""),
        os.getenv("TELEGRAM_PAIRING_CODE", "")).run()
