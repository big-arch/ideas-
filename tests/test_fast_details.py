import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from garage_radar.storage import Store
from garage_radar.webapp import quick_detail
from test_radar import listing


class FastDetailTests(unittest.TestCase):
    def test_slow_source_does_not_block_card_and_only_one_fetch_starts(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/'test.sqlite3')
            item=listing(description='Известный текст каталога')
            def slow(*args):
                started.set()
                release.wait(3)
                finished.set()
                return {'description':'Полный текст'}
            with patch('garage_radar.webapp.read_detail',side_effect=slow) as read:
                before=time.monotonic()
                value=quick_detail(item,store)
                self.assertLess(time.monotonic()-before,.5)
                self.assertTrue(started.wait(1))
                self.assertEqual(value['description'],item.description)
                self.assertTrue(value['pending'])
                quick_detail(item,store)
                self.assertEqual(read.call_count,1)
                release.set()
                self.assertTrue(finished.wait(1))
