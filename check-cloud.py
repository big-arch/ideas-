"""Operational check; reads an ignored local bridge key, never prints it."""
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from garage_radar.cloud_bridge import Bridge

bridge = Bridge(json.loads(Path('runtime/cloud-bridge.json').read_text()))
print('bridge_authenticated:', 'jobs' in bridge.cloud('/bridge/poll', {}))
print('local_chatgpt_connected:', bridge.local.request({'path':'/api/state'})['value']['chatgpt']['connected'])
request = Request(bridge.endpoint + '/request', data=json.dumps({'path':'/api/state'}).encode(), headers={'Content-Type':'application/json'})
try:
    with urlopen(request, timeout=30): raise RuntimeError('Anonymous request unexpectedly succeeded')
except HTTPError as error:
    print('anonymous_rejected:', error.code == 401)
