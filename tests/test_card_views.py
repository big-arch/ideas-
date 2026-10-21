import json
import re
import tempfile
import threading
import unittest
from html import unescape
from pathlib import Path
from unittest.mock import patch

from garage_radar.bot import Bot, card_keyboard
from garage_radar.card_views import pages, section_card
from garage_radar.config import Config, Search
from garage_radar.storage import Store
from garage_radar.advisor import REPORT_VERSION, price_evidence
from garage_radar.analysis import assess
from test_radar import FakeTelegram, listing


class CardViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        self.api = FakeTelegram()
        self.bot = Bot(Config(Search(), self.store.path, None, 60), self.store, self.api, {123})
        self.item = listing()
        self.store.upsert([self.item])
        self.message = {'chat': {'id': 123, 'type': 'private'}, 'message_id': 99,
                        'photo': [{'file_id': 'fake-photo'}], 'reply_markup': card_keyboard(self.item)}

    def tearDown(self):
        self.bot.advisor_worker.shutdown(wait=True)
        self.tmp.cleanup()

    def click(self, data):
        self.bot.handle({'callback_query': {'id': 'test-query', 'data': data,
                                           'from': {'id': 123}, 'message': self.message}})

    def test_cached_analysis_edits_existing_photo_and_pages_without_new_posts(self):
        result = assess(self.item,[self.item],Search())
        self.store.set_state('advisor:target', json.dumps({'version':REPORT_VERSION,'price_evidence':price_evidence(result), 'text': 'Проверка объекта\n' * 150, 'listing_price': self.item.price_rub, 'photos_analyzed': 1}))
        self.click('ai:target')
        method, payload = self.api.calls[-1]
        self.assertEqual(method, 'editMessageCaption')
        self.assertEqual(payload['message_id'], 99)
        self.assertIn('Далее', json.dumps(payload['reply_markup'], ensure_ascii=False))
        self.click('v:a:1:target')
        self.assertEqual(self.api.calls[-1][1]['message_id'], 99)
        self.assertIn('2/', self.api.calls[-1][1]['caption'])
        self.assertEqual(self.api.messages, [])

    def test_favorite_only_changes_keyboard_and_keeps_report(self):
        self.message['caption'] = 'Полный разбор на текущей странице'
        self.click('favorite:target')
        self.assertEqual(self.store.listing_meta(123,'target')['favorite'], 1)
        self.assertIn('editMessageReplyMarkup', [method for method, _ in self.api.calls])
        self.assertNotIn('editMessageCaption', [method for method, _ in self.api.calls])
        self.assertEqual(self.api.messages, [])

    def test_full_details_use_same_post(self):
        with patch('garage_radar.bot.read_detail', return_value={'description': 'Полный текст продавца', 'params': ''}):
            self.click('details:target')
        self.assertIn('Полный текст продавца', self.api.calls[-1][1]['caption'])
        self.assertEqual(self.api.messages, [])

    def test_utf16_limits_and_html_escaping_across_pages(self):
        text = '<опасный тег> ' + '🚗' * 1800 + '&' * 100
        self.assertEqual(''.join(pages(text, 650)), text)
        for page in range(len(pages(text, 650))):
            body, keyboard = section_card(self.item, text, 'a', page, photo=True, favorite=False)
            decoded = unescape(re.sub(r'</?b>', '', body))
            self.assertLessEqual(len(decoded.encode('utf-16-le'))//2, 1024)
            self.assertNotIn('<опасный тег>', body)
            for row in keyboard['inline_keyboard']:
                for button in row:
                    self.assertLessEqual(len(button.get('callback_data', '').encode()), 64)

    def test_background_summary_does_not_overwrite_open_report_page(self):
        from garage_radar.analysis import assess
        started, release = threading.Event(), threading.Event()
        def analyze(*args):
            started.set()
            release.wait(2)
            return {'text': 'Готовый вывод'}
        with patch('garage_radar.bot.chatgpt.status', return_value={'connected': True}), patch.object(self.api, 'send_card', return_value=self.message), patch('garage_radar.bot.analyze', side_effect=analyze):
            self.bot.publish(123, assess(self.item, [self.item], Search()), card_keyboard(self.item))
            self.assertTrue(started.wait(2))
            self.store.set_state('card_view:123:99', 'd:opened')
            release.set()
            self.bot.advisor_worker.shutdown(wait=True)
        self.assertNotIn('editMessageCaption', [method for method, _ in self.api.calls])
