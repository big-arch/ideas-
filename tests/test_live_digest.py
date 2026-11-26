import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_digests as support
from garage_radar.analysis import assess
from garage_radar.avito import SourceUnavailable
from garage_radar.details import confirmed_for_digest, parse_detail, read_detail, ListingWithdrawn
from garage_radar.models import utc_now
from garage_radar.presentation import ranked_feed
from garage_radar.schedule import due_slot
from garage_radar.sources import parse_public
from urllib.error import HTTPError


class LiveDigestTests(unittest.TestCase):
    setUp = support.DigestTests.setUp
    tearDown = support.DigestTests.tearDown

    def test_bumping_site_date_cannot_turn_a_known_old_listing_into_a_new_one(self):
        now = utc_now()
        attributes = {"source":"avito_public_html","source_scope":"partial_catalog","site_date_at":now.isoformat()}
        item = support.listing(attributes=attributes)
        self.store.upsert([replace(item,observed_at=(now-timedelta(days=10)).isoformat()),item])
        self.assertFalse(ranked_feed(self.store.all(),self.bot.config.search,recent_days=7))

    def test_drom_sold_marker_updates_only_its_own_card(self):
        card = '<div data-ftid="bulls-list_bull"><a data-ftid="bull_title" href="https://auto.drom.ru/rostov-na-donu/lada/granta/ID.html">Лада Гранта, 2022</a><i data-ftid="bull_price">895 000</i><span data-ftid="bull_location">Ростов-на-Дону</span><b data-ftid="bull_date">сегодня</b>STATUS</div>'
        html = card.replace("ID","1").replace("STATUS", '<i data-ftid="bull_sold">снят с продажи</i>') + card.replace("ID","2").replace("STATUS","")
        closed, active = parse_public(html, "car", "drom")
        self.assertFalse(closed.active)
        self.assertTrue(active.active)
        old = replace(active,id="old",observed_at=(utc_now()-timedelta(hours=2)).isoformat())
        self.assertEqual([r.listing.id for r in ranked_feed([closed,active,old],replace(self.bot.config.search,category="car"),recent_days=7)], [active.id])

    def test_live_page_must_match_identity_status_and_price(self):
        item = support.listing(url="https://auto.drom.ru/rostov-na-donu/lada/granta/123.html",attributes={"source":"drom_public_html"})
        def document(url, availability):
            return f'<script type="application/ld+json">{{"@type":"Car","offers":{{"url":"{url}","price":600000,"priceCurrency":"RUB","availability":"https://schema.org/{availability}"}}}}</script>'
        self.assertEqual(parse_detail(document(item.url, "InStock"),item)["availability"], "active")
        self.assertEqual(parse_detail(document(item.url+"other", "InStock"),item)["availability"], "unknown")
        with self.assertRaises(ListingWithdrawn): parse_detail(document(item.url,"SoldOut"),item)
        with patch("garage_radar.details.read_detail", return_value={"availability":"active","source_price":599000}):
            self.assertFalse(confirmed_for_digest(item,self.store))

    def test_withdrawal_persisted_network_failure_never_deletes_listing(self):
        item = support.listing(url="https://www.avito.ru/test/item-123",attributes={"source":"avito_public_html"})
        self.store.upsert([item])
        for code,expected in ((429,True),(410,False)):
            with patch("garage_radar.details.urlopen",side_effect=HTTPError(item.url,code,"",{},None)):
                with self.assertRaises(SourceUnavailable): read_detail(item,self.store,refresh=True)
            self.assertEqual(self.store.all()[0].active,expected)
        recent_item = replace(item,observed_at=utc_now().isoformat())
        self.store.upsert([recent_item]);self.store.mark_withdrawn(item)
        self.assertTrue(self.store.all()[0].active)

    def test_current_two_hour_slot_recovers_after_wake(self):
        now = datetime(2026,11,26,10,45,tzinfo=timezone.utc)
        self.assertEqual(due_slot("twelve",now),"2026-11-26T12:00+03:00")
        self.assertEqual(due_slot("twelve",now+timedelta(minutes=20)),"2026-11-26T14:00+03:00")

    def test_empty_slot_can_deliver_once_after_next_refresh(self):
        now = utc_now().replace(minute=0,second=0,microsecond=0)
        self.store.set_state("schedule:123","twelve")
        with patch.object(self.bot,"send_digest",side_effect=[0,2]) as send:
            for minutes in (0,1,16,17):
                with patch("garage_radar.bot.utc_now",return_value=now+timedelta(minutes=minutes)):
                    self.bot.scheduled_digests()
        self.assertEqual(send.call_count,2)
        self.assertTrue(self.store.get_state("digest_sent:123"))

    def test_auto_skips_unconfirmed_continues_and_ignores_manual_view(self):
        a = support.listing(identity="a",price=400000)
        b = support.listing(identity="b",price=600000)
        self.store.upsert([a,b])
        self.store.set_state("view:123","hidden")
        with patch("garage_radar.bot.confirmed_for_digest",side_effect=[False,True]):
            self.assertEqual(self.bot.send_digest(123,automatic=True),1)
        self.assertEqual(self.store.delivered_ids(123,[a,b]),{b.id})
