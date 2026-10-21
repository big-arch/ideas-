from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import Listing, timestamp


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS listings (
                    id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS prices (
                    listing_id TEXT, observed_at TEXT, price REAL,
                    UNIQUE(listing_id, observed_at, price));
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS subscriptions (chat_id INTEGER PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS notifications (
                    chat_id INTEGER, listing_id TEXT, price REAL,
                    PRIMARY KEY(chat_id, listing_id, price));
                CREATE TABLE IF NOT EXISTS listing_state (
                    user_id TEXT, listing_id TEXT, seen INTEGER NOT NULL DEFAULT 0,
                    hidden INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT '', watch INTEGER NOT NULL DEFAULT -1,
                    PRIMARY KEY(user_id, listing_id));
                CREATE TABLE IF NOT EXISTS search_profiles (
                    user_id TEXT, id TEXT, payload TEXT NOT NULL, PRIMARY KEY(user_id,id));
                CREATE TABLE IF NOT EXISTS price_drop_events (
                    id INTEGER PRIMARY KEY, listing_id TEXT, previous REAL, current REAL, observed_at TEXT,
                    UNIQUE(listing_id,previous,current,observed_at));
                CREATE TABLE IF NOT EXISTS price_drop_deliveries (
                    user_id TEXT, event_id INTEGER, PRIMARY KEY(user_id,event_id));
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def upsert(self, listings: list[Listing]) -> int:
        changed = 0
        with self.connection() as db:
            for item in listings:
                matches = db.execute("SELECT payload FROM listings WHERE id=? OR url=?", (item.id, item.url)).fetchall()
                if len(matches) > 1:
                    raise ValueError("Конфликт идентификатора и ссылки объявления")
                row = matches[0] if matches else None
                if row:
                    old = Listing.from_dict(json.loads(row[0]))
                    if old.id != item.id:
                        raise ValueError("Конфликт идентификатора и ссылки объявления")
                    if timestamp(item.observed_at) < timestamp(old.observed_at):
                        continue
                    if old.to_dict() == item.to_dict():
                        continue
                db.execute("INSERT INTO listings VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET url=excluded.url, payload=excluded.payload",
                           (item.id, item.url, json.dumps(item.to_dict(), ensure_ascii=False)))
                db.execute("INSERT OR IGNORE INTO prices VALUES (?, ?, ?)", (item.id, item.observed_at, item.price_rub))
                if row and item.price_rub < old.price_rub:
                    db.execute('INSERT OR IGNORE INTO price_drop_events(listing_id,previous,current,observed_at) VALUES (?,?,?,?)',
                               (item.id, old.price_rub, item.price_rub, item.observed_at))
                changed += 1
        return changed

    def all(self) -> list[Listing]:
        with self.connection() as db:
            rows = db.execute("SELECT payload FROM listings").fetchall()
        return [Listing.from_dict(json.loads(row[0])) for row in rows]

    def history(self, listing_id: str) -> list[tuple]:
        with self.connection() as db:
            rows = db.execute("SELECT observed_at, price FROM prices WHERE listing_id=? ORDER BY rowid", (listing_id,)).fetchall()
        return sorted(rows,key=lambda row:timestamp(row[0]))

    def listing_states(self, user_id) -> dict:
        with self.connection() as db:
            rows = db.execute('SELECT listing_id,seen,hidden,favorite,stage,watch FROM listing_state WHERE user_id=?', (str(user_id),)).fetchall()
        return {r[0]: dict(zip(('seen','hidden','favorite','stage','watch'),r[1:])) for r in rows}

    def listing_meta(self, user_id, ident: str) -> dict:
        with self.connection() as db:
            row = db.execute('SELECT seen,hidden,favorite,stage,watch FROM listing_state WHERE user_id=? AND listing_id=?', (str(user_id),ident)).fetchone()
        if row:
            return dict(zip(('seen','hidden','favorite','stage','watch'),row))
        legacy = str(user_id) == self.get_state('owner_chat_id') and self.get_state('favorite:'+ident) == '1'
        return dict(seen=0,hidden=0,favorite=int(legacy),stage='',watch=-1)

    def set_listing_meta(self, user_id, ident: str, **changes) -> dict:
        for key,value in changes.items():
            if key not in {'seen','hidden','favorite','stage','watch'}:
                raise ValueError('Неизвестное действие с объектом')
            if key == 'stage' and value not in {'','interested','contacted','inspection','rejected'}:
                raise ValueError('Неизвестный статус объекта')
            if key != 'stage' and (type(value) not in (int,bool) or value not in ({-1,0,1} if key=='watch' else {0,1})):
                raise ValueError('Некорректное действие с объектом')
        initial = self.listing_meta(user_id,ident)
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO listing_state VALUES (?,?,?,?,?,?,?)',
                       (str(user_id),ident,initial['seen'],initial['hidden'],initial['favorite'],initial['stage'],initial['watch']))
            for key,value in changes.items():
                db.execute(f'UPDATE listing_state SET {key}=? WHERE user_id=? AND listing_id=?', (value,str(user_id),ident))
        return self.listing_meta(user_id,ident)

    def price_change(self, ident: str) -> dict | None:
        history = self.history(ident)
        previous, change = None, None
        for observed,price in history:
            if previous is not None and price != previous:
                change = {'previous':previous,'current':price,'observed_at':observed,
                          'amount':previous-price,'percent':(previous-price)/previous*100}
            previous = price
        return change if change and change['current'] < change['previous'] else None

    def profiles(self, user_id) -> list[dict]:
        with self.connection() as db:
            rows = db.execute('SELECT id,payload FROM search_profiles WHERE user_id=? ORDER BY rowid', (str(user_id),)).fetchall()
        return [{'id':ident,**json.loads(payload)} for ident,payload in rows]

    def save_profile(self, user_id, ident: str, profile: dict) -> None:
        with self.connection() as db:
            db.execute('INSERT INTO search_profiles VALUES (?,?,?) ON CONFLICT(user_id,id) DO UPDATE SET payload=excluded.payload',
                       (str(user_id),ident,json.dumps(profile,ensure_ascii=False)))

    def delete_profile(self, user_id, ident: str) -> None:
        with self.connection() as db:
            db.execute('DELETE FROM search_profiles WHERE user_id=? AND id=?',(str(user_id),ident))
        if self.get_state(f'active_search:{user_id}') == ident:
            self.set_state(f'active_search:{user_id}','')

    def pending_price_drops(self, user_id) -> list[dict]:
        with self.connection() as db:
            rows = db.execute('SELECT e.id,e.listing_id,e.previous,e.current,e.observed_at FROM price_drop_events e WHERE NOT EXISTS (SELECT 1 FROM price_drop_deliveries d WHERE d.user_id=? AND d.event_id=e.id) ORDER BY e.id LIMIT 100', (str(user_id),)).fetchall()
        return [dict(zip(('id','listing_id','previous','current','observed_at'),row)) for row in rows]

    def finish_price_drop(self, user_id, event_id: int) -> None:
        with self.connection() as db:
            db.execute('INSERT OR IGNORE INTO price_drop_deliveries VALUES (?,?)', (str(user_id),event_id))

    def get_state(self, key: str, default: str = "") -> str:
        with self.connection() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self.connection() as db:
            db.execute("INSERT INTO state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def claim_state(self, key: str, value: str) -> bool:
        with self.connection() as db:
            return db.execute("INSERT OR IGNORE INTO state VALUES (?, ?)", (key, value)).rowcount == 1

    def subscribe(self, chat_id: int, enabled: bool) -> None:
        with self.connection() as db:
            if enabled:
                db.execute("INSERT OR IGNORE INTO subscriptions VALUES (?)", (chat_id,))
            else:
                db.execute("DELETE FROM subscriptions WHERE chat_id=?", (chat_id,))

    def subscribers(self) -> list[int]:
        with self.connection() as db:
            return [r[0] for r in db.execute("SELECT chat_id FROM subscriptions")]

    def set_category(self, chat_id: int, category: str) -> None:
        self.set_state(f"category:{chat_id}", category)

    def claim_owner(self, chat_id: int) -> bool:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO state VALUES ('owner_chat_id', ?)", (str(chat_id),))
            row = db.execute("SELECT value FROM state WHERE key='owner_chat_id'").fetchone()
        return row[0] == str(chat_id)

    def category(self, chat_id: int, default: str) -> str:
        return self.get_state(f"category:{chat_id}", default)

    def notified(self, chat_id: int, item: Listing) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM notifications WHERE chat_id=? AND listing_id=? AND price=?",
                              (chat_id, item.id, item.price_rub)).fetchone() is not None

    def mark_notified(self, chat_id: int, item: Listing) -> None:
        with self.connection() as db:
            db.execute("INSERT OR IGNORE INTO notifications VALUES (?, ?, ?)", (chat_id, item.id, item.price_rub))


def load_jsonl(path: Path) -> list[Listing]:
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError("Файл больше 20 МБ; разбейте импорт на части")
    result = []
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = Listing.from_dict(json.loads(line))
            except (ValueError, TypeError) as error:
                raise ValueError(f"Некорректное объявление в строке {number}: {error}") from error
            if item.demo:
                raise ValueError("Демо-объявления нельзя импортировать в рабочую базу")
            result.append(item)
    return result
