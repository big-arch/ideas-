import threading
import unittest
from unittest.mock import patch
import test_digests as digests


class BackgroundScanTests(unittest.TestCase):
    setUp = digests.DigestTests.setUp
    tearDown = digests.DigestTests.tearDown

    def test_commands_are_processed_during_slow_source_scan(self):
        started, release, handled = threading.Event(), threading.Event(), threading.Event()
        def scan():
            started.set()
            release.wait(3)
        calls = 0
        def updates(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.assertTrue(started.wait(1))
                return [{'update_id': 1}]
            release.set()
            raise KeyboardInterrupt
        def handle(update):
            self.assertFalse(release.is_set())
            handled.set()
        with patch.object(self.bot, 'scan', side_effect=scan), patch.object(self.bot, 'handle', side_effect=handle), patch.object(self.api, 'call', side_effect=updates):
            try:
                with self.assertRaises(KeyboardInterrupt): self.bot.run()
            finally: release.set()
        self.assertTrue(handled.is_set())
