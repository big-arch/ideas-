import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from garage_radar.dates import site_date
from garage_radar.presentation import ranked_feed
from garage_radar.config import Search
from garage_radar.models import utc_now, timestamp
import test_digests as support
from test_digests import listing


class DatesTests(unittest.TestCase):
    def test_relative_and_calendar_dates_use_source_observation_context(self):
        now = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)
        self.assertEqual(timestamp(site_date("20 октября 2026", now)).day, 19)  # MSK midnight is previous UTC day
        self.assertEqual(timestamp(site_date("2 часа назад", now)).hour, 8)
        self.assertEqual(site_date("31 февраля", now), "")
        self.assertEqual(site_date("дата неизвестна", now), "")

    def test_observed_today_is_not_a_publication_date(self):
        today = utc_now()
        fresh = listing()
        old = replace(fresh, id="old", url="https://example.com/old", attributes={"site_date_at": (today-timedelta(days=8)).isoformat()})
        unknown = replace(fresh, id="unknown", url="https://example.com/unknown", attributes={})
        self.assertEqual([r.listing.id for r in ranked_feed([fresh,old,unknown],Search(),recent_days=7,sort_mode="newest")], [fresh.id])

    def test_price_and_newest_order_are_distinct(self):
        now = utc_now()
        a = listing(id="a", url="https://example.com/a", price=1000000, attributes={"site_date_at":(now-timedelta(hours=1)).isoformat()})
        b = listing(id="b", url="https://example.com/b", price=500000, attributes={"site_date_at":(now-timedelta(days=2)).isoformat()})
        for mode,expected in (("newest",["a","b"]),("price_asc",["b","a"]),("price_desc",["a","b"])):
            self.assertEqual([r.listing.id for r in ranked_feed([b,a],Search(),recent_days=7,sort_mode=mode)],expected)


class NextPageTests(unittest.TestCase):
    setUp = support.DigestTests.setUp
    tearDown = support.DigestTests.tearDown

    def test_digest_more_uses_next_ten_and_snapshot_survives_refresh(self):
        items = [replace(listing(), id=f"row-{i}", url=f"https://example.com/{i}", price_rub=100000+i) for i in range(45)]
        self.store.upsert(items)
        self.store.set_state("sort:123","price_asc")
        self.assertEqual(self.bot.send_digest(123),10)
        snapshot = json.loads(self.store.get_state("digest_snapshot:123"))
        self.api.messages.clear()
        self.store.upsert([replace(listing(),id="new-cheapest",url="https://example.com/extra",price_rub=90000)])
        self.assertEqual(self.bot.send_digest(123,offset=10,token=snapshot["token"]),10)
        shown = [m[1] for m in self.api.messages]
        self.assertFalse(any("100 000 ₽" in t for t in shown))
        self.assertFalse(any("90 000 ₽" in t for t in shown))

    def test_missing_dates_do_not_get_padded_to_ten(self):
        self.store.upsert([replace(listing(),attributes={})])
        self.assertEqual(self.bot.send_digest(123),0)
        self.assertIn("без даты",self.api.messages[-1][1])

    def test_next_page_fills_ten_after_snapshot_entry_becomes_inactive(self):
        items = [replace(listing(), id=f'fresh-{i}', url=f'https://example.com/fresh/{i}', price_rub=100000+i) for i in range(25)]
        self.store.upsert(items)
        self.store.set_state('sort:123', 'price_asc')
        self.bot.send_digest(123)
        snapshot = json.loads(self.store.get_state('digest_snapshot:123'))
        self.store.upsert([replace(items[10], active=False)])
        self.api.messages.clear()
        self.assertEqual(self.bot.send_digest(123, offset=10, token=snapshot['token']), 10)
        callback = self.api.messages[-1][2]['inline_keyboard'][0][0]['callback_data']
        self.assertTrue(callback.endswith(':21'))
