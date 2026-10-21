import http.client
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from garage_radar import webapp
from garage_radar.storage import Store
from garage_radar.network import RemoteError


class LoopbackSignInTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = webapp.ThreadingHTTPServer(('127.0.0.1', 0), webapp.Handler)
        self.server.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        webapp.PENDING_SIGN_INS.clear()
        webapp.SESSIONS.clear()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join()
        webapp.PENDING_SIGN_INS.clear()
        webapp.SESSIONS.clear()
        self.tmp.cleanup()

    def callback(self, state):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port)
        connection.request('GET', '/auth/callback?state=' + state,
                           headers={'Host': '127.0.0.1:8766'})
        response = connection.getresponse()
        status, headers = response.status, dict(response.getheaders())
        response.read()
        connection.close()
        return status, headers

    def test_callback_from_another_browser_accepts_valid_state_once(self):
        pending = {'state': 'test-state', 'time': time.time()}
        webapp.register_sign_in(pending)
        with patch.object(webapp.chatgpt, 'finish') as finish:
            status, headers = self.callback('test-state')
            self.assertEqual(status, 303)
            self.assertIn('HttpOnly', headers['Set-Cookie'])
            finish.assert_called_once_with({'state': 'test-state'}, pending)
            self.callback('test-state')
            finish.assert_called_once()

    def test_unknown_state_never_exchanges_code(self):
        with patch.object(webapp.chatgpt, 'finish') as finish:
            self.callback('not-started')
            finish.assert_not_called()
        self.assertIn('Сеанс входа истёк', self.server.store.get_state('chatgpt_auth_progress'))

    def test_token_error_is_visible_without_secrets(self):
        webapp.register_sign_in({'state': 'test-state', 'time': time.time()})
        with patch.object(webapp.chatgpt, 'finish', side_effect=RemoteError(403)):
            self.callback('test-state')
        progress = self.server.store.get_state('chatgpt_auth_progress')
        self.assertIn('HTTP 403', progress)
        self.assertNotIn('test-state', progress)

