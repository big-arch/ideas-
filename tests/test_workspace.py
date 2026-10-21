import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from garage_radar.bot import Bot, card_keyboard
from garage_radar.config import Config, Search
from garage_radar.models import utc_now
from garage_radar.network import RemoteError
from garage_radar.storage import Store
from garage_radar.workspace import filter_results, matches_profile, validate_profile, watched
from test_radar import FakeTelegram, listing


class PersonalWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)/'test.sqlite3')
        self.api = FakeTelegram()
        self.bot = Bot(Config(Search(),self.store.path,None,60),self.store,self.api,{123,456})
        self.addCleanup(self.bot.advisor_worker.shutdown,wait=True)
        account = patch('garage_radar.bot.chatgpt.status',return_value={'connected':False})
        account.start();self.addCleanup(account.stop)
        self.now = utc_now()
        self.item = listing(attributes={'site_date_at':self.now.isoformat()},observed_at=(self.now-timedelta(minutes=1)).isoformat())
        self.store.upsert([self.item])

    def tearDown(self): self.tmp.cleanup()

    def test_seen_hidden_and_rejected_are_private_and_persistent(self):
        result = SimpleNamespace(listing=self.item)
        self.store.set_listing_meta(123,self.item.id,seen=True)
        self.assertEqual(filter_results(self.store,123,[result],view='unseen'),[])
        self.assertEqual(len(filter_results(self.store,456,[result],view='unseen')),1)
        self.store.set_listing_meta(123,self.item.id,hidden=True)
        self.assertEqual(filter_results(self.store,123,[result]),[])
        self.assertEqual(len(filter_results(self.store,123,[result],view='hidden')),1)
        self.store.set_listing_meta(123,self.item.id,hidden=False,stage='rejected')
        self.assertEqual(filter_results(self.store,123,[result]),[])
        self.assertEqual(len(filter_results(self.store,123,[result],view='rejected')),1)
        self.assertEqual(Store(self.store.path).listing_meta(123,self.item.id)['stage'],'rejected')

    def test_favorites_migrate_for_owner_only_and_enable_price_watch(self):
        self.store.set_state('owner_chat_id','123')
        self.store.set_state('favorite:'+self.item.id,'1')
        self.assertTrue(watched(self.store.listing_meta(123,self.item.id)))
        self.assertFalse(watched(self.store.listing_meta(456,self.item.id)))
        self.store.set_listing_meta(123,self.item.id,seen=True,watch=False)
        self.assertEqual(self.store.listing_meta(123,self.item.id)['favorite'],1)
        self.assertFalse(watched(self.store.listing_meta(123,self.item.id)))

    def test_profile_matches_budget_address_and_literal_words(self):
        profile = validate_profile(dict(name='Гараж',category='garage',locality='Советский',max_price=700000,exclude='аренда, доля'))
        self.assertTrue(matches_profile(self.item,profile))
        self.assertFalse(matches_profile(replace(self.item,description='Только аренда'),profile))
        self.assertFalse(matches_profile(replace(self.item,price_rub=800000),profile))
        self.assertFalse(matches_profile(replace(self.item,category='car'),profile))
        self.store.save_profile(123,'test-search',profile)
        self.assertEqual(self.store.profiles(456),[])
        self.store.set_state('active_search:123','test-search')
        self.store.delete_profile(123,'test-search')
        self.assertEqual(self.store.get_state('active_search:123'),'')

    def test_invalid_budgets_and_stage_updates_are_rejected(self):
        for value in ('nan','inf',-1):
            with self.assertRaises(ValueError):validate_profile(dict(name='test',max_price=value))
        with self.assertRaises(ValueError):validate_profile(dict(name='test',min_price=20,max_price=10))
        with self.assertRaises(ValueError):self.store.set_listing_meta(123,self.item.id,stage='fake')
        with self.assertRaises(ValueError):self.store.set_listing_meta(123,self.item.id,hidden='false')

    def drop(self):
        changed = replace(self.item,price_rub=self.item.price_rub-100000,observed_at=self.now.isoformat())
        self.store.upsert([changed])
        return changed

    def test_actual_drop_has_before_after_and_alert_is_not_repeated(self):
        self.store.subscribe(123,True)
        self.store.set_listing_meta(123,self.item.id,stage='interested')
        changed = self.drop()
        self.store.upsert([replace(changed,observed_at=(self.now+timedelta(seconds=1)).isoformat())])
        change = self.store.price_change(self.item.id)
        self.assertEqual(change['previous'],self.item.price_rub)
        self.assertEqual(change['amount'],100000)
        self.bot.price_alerts();self.bot.price_alerts()
        self.assertEqual(len(self.api.messages),1)
        self.assertIn('600,000 ₽ → 500,000 ₽',self.api.messages[0][1])

    def test_saved_profile_alerts_but_hidden_and_explicit_watch_off_do_not(self):
        profile = validate_profile(dict(name='Гаражи',category='garage',max_price=700000))
        self.store.save_profile(123,'search',profile)
        self.store.save_profile(456,'search',profile)
        self.store.subscribe(123,True);self.store.subscribe(456,True)
        self.store.set_listing_meta(456,self.item.id,watch=False)
        self.drop();self.bot.price_alerts()
        self.assertEqual([m[0] for m in self.api.messages],[123])

    def test_failed_alert_delivery_remains_pending(self):
        self.store.subscribe(123,True)
        self.store.set_listing_meta(123,self.item.id,watch=True)
        self.drop()
        with patch.object(self.api,'send',side_effect=RemoteError(500)):
            with self.assertRaises(RemoteError):self.bot.price_alerts()
        self.assertEqual(len(self.store.pending_price_drops(123)),1)
        self.bot.price_alerts()
        self.assertEqual(self.store.pending_price_drops(123),[])

    def test_unseen_manual_pagination_keeps_snapshot_when_first_page_is_read(self):
        self.store.upsert([replace(self.item,id=f'row-{i}',url=f'https://example.org/row-{i}',price_rub=100000+i) for i in range(25)])
        self.store.set_state('view:123','unseen')
        self.assertEqual(self.bot.sort_mode(123),'price_asc')
        self.bot.show_feed(123)
        snapshot = json.loads(self.store.get_state('feed_snapshot:123'))
        for ident in snapshot['ids'][:10]: self.store.set_listing_meta(123,ident,seen=True)
        self.api.messages.clear()
        self.bot.show_feed(123,offset=10,token=snapshot['token'])
        self.assertEqual(len(self.api.messages),10)
        self.assertIn('100 010 ₽',self.api.messages[0][1])
        self.assertNotIn('100 020 ₽',self.api.messages[0][1])

    def test_card_actions_edit_keyboard_and_preserve_message(self):
        message={'chat':{'id':123,'type':'private'},'message_id':99,'photo':[{'file_id':'test'}]}
        self.bot.handle({'callback_query':{'id':'test','data':'s:v:target','from':{'id':123},'message':message}})
        self.assertEqual(self.store.listing_meta(123,'target')['stage'],'inspection')
        self.assertEqual(self.api.messages,[])
        self.assertIn('editMessageReplyMarkup',[method for method,_ in self.api.calls])
