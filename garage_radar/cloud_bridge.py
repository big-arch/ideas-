"""Outbound-only Telegram Mini App bridge. OAuth stays on the loopback server."""
from __future__ import annotations

import http.cookiejar
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, build_opener, HTTPCookieProcessor, urlopen

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = 'http://127.0.0.1:8766'
READ = {'/api/state', '/api/models', '/api/list', '/api/item'}
WRITE = {'/api/model', '/api/favorite', '/api/meta', '/api/preferences', '/api/search/save', '/api/search/activate', '/api/search/delete', '/api/analyze', '/api/chat'}
LOG = logging.getLogger(__name__)


def valid_request(value):
    if not isinstance(value, dict): return False
    path = value.get('path')
    if not isinstance(path, str) or len(path) > 2400 or any(c in path for c in '\r\n\\#'): return False
    url = urlsplit(path)
    if url.scheme or url.netloc or not path.startswith('/api/'): return False
    if 'data' not in value: return url.path in READ
    return path in WRITE and isinstance(value['data'], dict) and len(json.dumps(value['data'])) < 7500


class LocalApp:
    def __init__(self):
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.lock = threading.Lock()
        self.csrf = ''

    def connect(self):
        with self.opener.open(ORIGIN + '/', timeout=10) as r: r.read()
        with self.opener.open(ORIGIN + '/api/state', timeout=30) as r:
            self.csrf = json.load(r)['csrf']

    def request(self, value, *, retry=True):
        if not valid_request(value): return {'code': 400, 'value': {'error': 'Действие недоступно'}}
        with self.lock:
            if not self.csrf: self.connect()
            csrf = self.csrf
        raw = json.dumps(value['data']).encode() if 'data' in value else None
        request = Request(ORIGIN + value['path'], data=raw, headers={'Content-Type': 'application/json', 'Origin': ORIGIN, 'X-CSRF-Token': csrf})
        try:
            with self.opener.open(request, timeout=240) as r:
                result = json.load(r)
                code = r.status
        except HTTPError as error:
            code = error.code
            try: result = json.loads(error.read())
            except ValueError: result = {'error': 'Приложение не ответило'}
            if code == 401:
                self.csrf = ''
                if retry and 'data' not in value: return self.request(value,retry=False)
        if value['path'] == '/api/state' and code == 200:
            # Never send the desktop session/CSRF or account identity to a browser.
            result['csrf'] = 'telegram'
            result['chatgpt'] = {'connected': bool(result.get('chatgpt', {}).get('connected'))}
            result['auth_progress'] = {}
            # Bootstrap the first list in the same cloud round trip as settings.
            category = next((p['category'] for p in result.get('profiles',[])
                if p['id']==result.get('active_profile')),result.get('category','garage'))
            path = '/api/list?'+urlencode({'category':category,'sort':result.get('sort','price_asc'),'view':result.get('view','all')})
            feed = self.request({'path':path})
            if feed['code']==200: result['initial_list'] = {'path':path,'value':feed['value']}
        return {'code': code, 'value': result}


class Bridge:
    def __init__(self, config):
        self.endpoint = config['endpoint']
        if self.endpoint != 'https://renbakjudvdnkjvjujti.supabase.co/functions/v1/radar-miniapp':
            raise ValueError('Неизвестный адрес облачного моста')
        self.key = config['key']
        self.local = LocalApp()

    def cloud(self, path, value):
        request = Request(self.endpoint + path, data=json.dumps(value).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key})
        with urlopen(request, timeout=35) as response: return json.load(response)

    def execute(self, job):
        try: response = self.local.request(job['request'])
        except (OSError, ValueError, URLError): response = {'code': 503, 'value': {'error': 'Локальное приложение недоступно. Запусти start.cmd.'}}
        # Retry the result only; never repeat a mutation after a lost acknowledgement.
        for attempt in range(3):
            try:
                self.cloud('/bridge/result', {'id': job['id'], 'response': response})
                return
            except (OSError, ValueError): time.sleep(2 + attempt)
        LOG.warning('Cloud result delivery failed; request was not repeated')

    def run(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            while True:
                try:
                    for job in self.cloud('/bridge/poll', {})['jobs']: pool.submit(self.execute, job)
                except (OSError, ValueError, KeyError): LOG.warning('Cloud bridge temporarily unavailable')
                time.sleep(2)


def main():
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    config = json.loads((ROOT / 'runtime/cloud-bridge.json').read_text())
    Bridge(config).run()


if __name__ == '__main__': main()
