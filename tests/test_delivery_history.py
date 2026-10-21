import json
import unittest
from dataclasses import replace
import test_digests as digests
from garage_radar.bot import Bot, card_keyboard


class DeliveryHistoryTests(unittest.TestCase):
    setUp = digests.DigestTests.setUp
    tearDown = digests.DigestTests.tearDown
    def test_auto_digest_never_repeats_after_restart_or_price_change(self):
        item = digests.listing()
        self.store.upsert([item])
        self.assertEqual(self.bot.send_digest(123,automatic=True),1)
        self.api.messages.clear()
        self.store.upsert([replace(item,price_rub=item.price_rub-10000)])
        restarted = Bot(self.bot.config,self.store,self.api,{123})
        self.assertEqual(restarted.send_digest(123,automatic=True),0)
        self.assertEqual(self.api.messages,[])

    def test_old_persisted_post_buttons_are_migrated(self):
        item = digests.listing()
        self.store.upsert([item])
        self.store.set_state('card_keyboard:456:99',json.dumps(card_keyboard(item)))
        self.store.set_state('delivery_history_v1:456','')
        restarted = Bot(self.bot.config,self.store,self.api,{456})
        self.assertEqual(restarted.send_digest(456,automatic=True),0)

    def test_strong_repost_identity_is_private_and_price_independent(self):
        item = digests.listing(description='Описание именно этого объекта. '*12,attributes={'visible_address':'Проверочная, 2'})
        repost = replace(item,id='another-id',url='https://example.org/new-id',price_rub=123000)
        self.store.upsert([item,repost])
        self.store.mark_delivered(123,item)
        self.assertIn(repost.id,self.store.delivered_ids(123,[repost]))
        self.assertEqual(self.store.delivered_ids(456,[repost]),set())
