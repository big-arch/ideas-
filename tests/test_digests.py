import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from garage_radar.bot import Bot, Telegram, card_keyboard
from garage_radar.config import Config, Search
from garage_radar.models import utc_now
from garage_radar.network import RemoteError
from garage_radar.schedule import due_slot
from garage_radar.sources import SourceUnavailable, parse_public
from garage_radar.storage import Store
from garage_radar.presentation import ranked_feed
from test_radar import FakeTelegram, listing as base_listing


def listing(**kwargs):
    attributes = {"site_date_at": utc_now().isoformat(), **kwargs.pop("attributes", {})}
    return base_listing(attributes=attributes, **kwargs)


class DigestTests(unittest.TestCase):
    def setUp(self):
        account = patch('garage_radar.bot.chatgpt.status', return_value={'connected': False})
        account.start()
        self.addCleanup(account.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "test.sqlite3")
        self.api = FakeTelegram()
        self.api.send_album = lambda chat, results: [self.api.send_card(chat, r, {}) for r in results]
        self.bot = Bot(Config(Search(), self.store.path, None, 60), self.store, self.api, {123, 456})
        self.store.subscribe(123, True)

    def tearDown(self):
        self.temp.cleanup()

    def test_moscow_slots_and_no_backlog(self):
        # UTC06 is Moscow09.
        self.assertIsNotNone(due_slot("twice", datetime(2026, 10, 21, 6, 15, tzinfo=timezone.utc)))
        self.assertIsNone(due_slot("twice", datetime(2026, 10, 21, 6, 31, tzinfo=timezone.utc)))
        self.assertIsNone(due_slot("off", utc_now()))
        self.assertIsNotNone(due_slot("twelve", datetime(2026, 10, 21, 21, 0, tzinfo=timezone.utc)))

    def test_slot_is_not_repeated_after_restart(self):
        self.store.set_state("schedule:123", "twice")
        now = datetime(2026, 10, 21, 6, 10, tzinfo=timezone.utc)
        with patch("garage_radar.bot.utc_now", return_value=now), patch.object(self.bot, "send_digest", return_value=20) as send:
            self.bot.scheduled_digests()
            self.bot.scheduled_digests()
            send.assert_called_once()
        restarted = Bot(self.bot.config, Store(self.store.path), self.api, {123})
        with patch("garage_radar.bot.utc_now", return_value=now), patch.object(restarted, "send_digest") as send:
            restarted.scheduled_digests()
            send.assert_not_called()

    def test_failed_slot_is_visible_and_not_retried_as_duplicate(self):
        self.store.set_state("schedule:123", "twice")
        now = datetime(2026, 10, 21, 6, 10, tzinfo=timezone.utc)
        with patch("garage_radar.bot.utc_now", return_value=now), patch.object(self.bot, "send_digest", side_effect=RemoteError(429)) as send:
            with self.assertRaises(RemoteError): self.bot.scheduled_digests()
            self.bot.scheduled_digests()
            send.assert_called_once()
        self.assertIn("не завершена", self.store.get_state("digest_last:123"))

    def test_digest_caps_at_ten_and_does_not_pad_short_feed(self):
        items = [replace(listing(), id=f"object-{i}", url=f"https://example.com/{i}", price_rub=600000+i) for i in range(25)]
        self.store.upsert(items)
        self.assertEqual(self.bot.send_digest(123), 10)
        self.assertEqual(len(self.api.messages), 12)  # heading + 10 cards + navigation
        self.api.messages.clear()
        self.store.set_category(123, "car")
        self.assertEqual(self.bot.send_digest(123), 0)
        self.assertEqual(len(self.api.messages), 1)

    def test_multi_category_preferences_are_private_and_independent(self):
        self.bot.handle({"callback_query": {"id": "q", "data": "prefcat:car", "from": {"id": 123},
                         "message": {"chat": {"id": 123, "type": "private"}}}})
        self.assertEqual(self.bot.digest_categories(123), ["garage", "car"])
        self.assertEqual(self.bot.digest_categories(456), ["garage"])
        self.assertEqual(self.bot.search_for(123).category, "garage")

    def test_each_digest_object_is_a_separate_post_even_with_photos(self):
        items = [replace(listing(), id=f"object-{i}", url=f"https://example.com/{i}",
                         photo_url="https://avatars.mds.yandex.net/photo.jpg" if i else "") for i in range(20)]
        self.store.upsert(items)
        with patch.object(self.api, "send_album") as albums:
            self.bot.send_digest(123)
        albums.assert_not_called()
        self.assertEqual(len(self.api.messages), 12)  # heading, ten separate posts and navigation

    def test_strong_repost_match_does_not_inflate_feed(self):
        a = listing(description="Подробное описание объекта. " * 10, attributes={"visible_address": "Тестовая улица, 42"})
        b = replace(a, id="other-site", url="https://example.com/other")
        self.assertEqual(len(ranked_feed([a,b], Search())), 1)
        self.assertEqual(len(ranked_feed([a,replace(b,area_m2=25)], Search())), 2)

    def test_album_uses_two_to_ten_photos_and_direct_source_links(self):
        from garage_radar.analysis import assess
        results = [assess(replace(listing(), photo_url="https://avatars.mds.yandex.net/photo.jpg"), [], Search())] * 2
        api = Telegram("123:example")
        with patch.object(api, "call", return_value=[]) as send:
            api.send_album(123, results)
        method, payload = send.call_args.args
        self.assertEqual(method, "sendMediaGroup")
        self.assertEqual(len(payload["media"]), 2)
        self.assertIn('href="', payload["media"][0]["caption"])

    def test_photo_upload_fallback_retains_original_card(self):
        from garage_radar.analysis import assess
        result = assess(replace(listing(), photo_url="https://avatars.mds.yandex.net/photo.jpg"), [], Search())
        api = Telegram("123:example")
        with patch.object(api, "call", side_effect=RemoteError(400)), patch("garage_radar.bot.photo_bytes", return_value=(b"photo", "image/jpeg")), patch("garage_radar.bot.post_photos", return_value={"result": {"photo": [{"file_id": "uploaded"}]}}):
            api.send_card(123, result, {})
        self.assertEqual(api._photo_cache[result.listing.photo_url], "uploaded")


class PublicSourcesTests(unittest.TestCase):
    def test_yandex_visible_price_photo_address_and_city(self):
        html = '''<li data-test="OffersSerpItem"><a href="/offer/123456/" data-test="OffersSerpItemLinkTitle">15 м² · машиноместо</a>
        <div class="AddressWithGeoLinks__addressContainer--hash">Батайск, улица Мира, 1</div>
        <div class="OffersSerpItem__price">850 000 ₽</div><div class="OffersSerpItem__price-detail">56 667 ₽ за м²</div>
        <img src="//avatars.mds.yandex.net/get-realty-offers/photo.jpg"></li>'''
        item = parse_public(html, "garage", "yandex")[0]
        self.assertEqual(item.price_rub, 850000)
        self.assertEqual(item.locality, "Батайск")
        self.assertEqual(item.garage_type, "parking")
        self.assertTrue(item.photo_url.startswith("https://"))
        self.assertIn("Яндекс", card_keyboard(item)["inline_keyboard"][0][0]["text"])

    def test_drom_excludes_other_city_rostov(self):
        html = '''<div data-ftid="bulls-list_bull"><a data-ftid="bull_title" href="https://auto.drom.ru/rostov/lada/vesta/123.html">Лада Веста, 2018</a><span data-ftid="bull_price">900 000</span><span data-ftid="bull_location">Ростов</span></div>'''
        with self.assertRaises(SourceUnavailable): parse_public(html, "car", "drom")

    def test_blocked_source_is_not_an_empty_success(self):
        with self.assertRaises(SourceUnavailable): parse_public("<html>captcha</html>", "garage", "yandex")


if __name__ == "__main__": unittest.main()
