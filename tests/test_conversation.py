import json
import tempfile
import unittest
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from garage_radar.config import Config, Search
from garage_radar.conversation import turn, validate_plan, matching
from garage_radar.location import listing_map
from garage_radar.models import utc_now
from garage_radar.storage import Store
from test_radar import listing


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'radar.sqlite3')
        self.config = Config(Search(), self.store.path, None, 60)
        self.store.set_state('chatgpt_model', 'test-model')
        self.plan = {'intent': 'search', 'category': 'car', 'query': 'Lexus IS 200', 'locality': '', 'max_price': 1000000}
        self.car = listing(title='Lexus IS200, 2001', category='car', price=900000,
                           attributes={'site_date_at': utc_now().isoformat()})

    def tearDown(self): self.tmp.cleanup()

    def test_exact_model_matches_alias_but_not_year_or_turbo(self):
        plan = validate_plan(self.plan)
        self.assertTrue(matching(self.car, plan))
        self.assertTrue(matching(replace(self.car, title='Лексус ИС 200'), plan))
        self.assertFalse(matching(replace(self.car, title='Lexus IS, 2005'), plan))
        self.assertFalse(matching(replace(self.car, title='Lexus IS200t'), plan))
        self.assertFalse(matching(replace(self.car, price_rub=1100000), plan))

    def test_search_imports_real_results_excludes_old_and_keeps_followup_context(self):
        old = replace(self.car, id='old', url='https://example.org/old', attributes={'site_date_at': (utc_now()-timedelta(days=8)).isoformat()})
        self.store.upsert([old])
        replies = [{'text': json.dumps(self.plan)}]
        with patch('garage_radar.conversation.chatgpt.status', return_value={'connected': True}), patch('garage_radar.conversation.chatgpt.respond', side_effect=replies) as respond, patch('garage_radar.conversation.SOURCE.fetch', return_value=[self.car]) as fetch:
            result = turn(self.store, self.config, 123, 'Lexus is 200 до миллиона')
            self.assertEqual([r.listing.id for r in result['results']], [self.car.id])
            fetch.assert_called_once_with('car', query='Lexus IS 200')
            self.assertIn('Новых объявлений: 1', result['answer'])
            respond.assert_called_once()
        with patch('garage_radar.conversation.chatgpt.status', return_value={'connected': True}), patch('garage_radar.conversation.chatgpt.respond', side_effect=[{'text': json.dumps(self.plan)}]) as respond, patch('garage_radar.conversation.SOURCE.fetch', return_value=None):
            result = turn(self.store, self.config, 123, 'ещё')
            self.assertEqual(result['results'], [])
            self.assertIn('Lexus IS 200', respond.call_args_list[0].args[1][0]['text'])

    def test_existing_deliveries_are_not_sent_again(self):
        self.store.upsert([self.car]); self.store.mark_delivered(123, self.car)
        with patch('garage_radar.conversation.chatgpt.status', return_value={'connected': True}), patch('garage_radar.conversation.chatgpt.respond', side_effect=[{'text': json.dumps(self.plan)}]), patch('garage_radar.conversation.SOURCE.fetch', return_value=None):
            self.assertEqual(turn(self.store, self.config, 123, 'Lexus IS200')['results'], [])

    def test_analysis_uses_referenced_result(self):
        self.store.upsert([self.car]); self.store.set_state('conversation:123', json.dumps({'result_ids': [self.car.id]}))
        plan = {**self.plan, 'intent': 'analysis', 'focus_index': 1}
        with patch('garage_radar.conversation.chatgpt.status', return_value={'connected': True}), patch('garage_radar.conversation.chatgpt.respond', side_effect=[{'text': json.dumps(plan)}, {'text': 'Нужна диагностика.'}]), patch('garage_radar.conversation.analyze', return_value={'text': 'Осмотр обязателен'}) as analyze:
            self.assertEqual(turn(self.store, self.config, 123, 'разбери первый')['answer'], 'Нужна диагностика.')
            self.assertEqual(analyze.call_args.args[0].listing.id, self.car.id)

    def test_untrusted_plan_bounds_and_map_search_not_fake_coordinates(self):
        for change in [{'category': 'anything'}, {'max_price': float('nan')}, {'max_price': True}, {'query': 'x'*161}]:
            with self.assertRaises(ValueError): validate_plan({**self.plan, **change})
        value = listing_map(listing(attributes={'visible_address': 'ул. Тестовая, 1'}))
        self.assertIn('Ростов-на-Дону', value['query'])
        self.assertEqual(value['precision'], 'address_search')
        self.assertIn('Точная точка', value['note'])

    def test_cross_process_lease_prevents_context_overwrite(self):
        self.store.set_state('conversation_busy:123', json.dumps({'expires': time.time()+60, 'token': 'other'}))
        with patch('garage_radar.conversation.chatgpt.respond') as respond:
            with self.assertRaisesRegex(ValueError, 'уже отвечает'):
                turn(self.store, self.config, 123, 'ещё')
            respond.assert_not_called()
        self.assertIn('other', self.store.get_state('conversation_busy:123'))

    def test_all_six_categories_use_the_same_grounded_search(self):
        categories = ['garage', 'apartment', 'house', 'land', 'commercial', 'car']
        items = [listing(identity='item-'+cat, category=cat, attributes={'site_date_at': utc_now().isoformat()}) for cat in categories]
        self.store.upsert(items)
        for index, cat in enumerate(categories):
            with self.subTest(category=cat), patch('garage_radar.conversation.chatgpt.status', return_value={'connected': True}), patch('garage_radar.conversation.chatgpt.respond', return_value={'text': json.dumps({'intent': 'search', 'category': cat, 'query': ''})}), patch('garage_radar.conversation.SOURCE.fetch', return_value=None):
                response = turn(self.store, self.config, 1000+index, 'Найди подходящие объявления')
                self.assertEqual([r.listing.category for r in response['results']], [cat])
