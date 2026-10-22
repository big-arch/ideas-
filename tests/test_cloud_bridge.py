import unittest
import io
import json
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from garage_radar.cloud_bridge import LocalApp
from garage_radar.cloud_bridge import valid_request


class BridgeBoundaryTests(unittest.TestCase):
    def test_expired_session_recovers_only_before_mutation(self):
        app = LocalApp(); app.csrf = 'old'
        class Response(io.BytesIO): status = 200
        rejected = HTTPError('http://127.0.0.1:8766/api/chat',403,'Forbidden',{},io.BytesIO(json.dumps({'error':'Сеанс не подтверждён'}).encode()))
        app.opener = Mock()
        app.opener.open.side_effect = [rejected, Response(b'{"ok":true}')]
        def reconnect(): app.csrf = 'new'
        with patch.object(app, 'connect', side_effect=reconnect) as connect:
            result = app.request({'path':'/api/chat','data':{'text':'Привет'}})
            connect.assert_called_once()
        self.assertEqual(result['code'],200)
        self.assertEqual(app.opener.open.call_count,2)

    def test_business_error_is_never_retried(self):
        app=LocalApp();app.csrf='valid';app.opener=Mock()
        app.opener.open.side_effect=HTTPError('http://127.0.0.1:8766/api/chat',500,'error',{},io.BytesIO(b'{"error":"failure"}'))
        with patch.object(app,'connect') as connect:
            self.assertEqual(app.request({'path':'/api/chat','data':{'text':'x'}})['code'],500)
            connect.assert_not_called()
        self.assertEqual(app.opener.open.call_count,1)

    def test_allowed_business_operations(self):
        for value in ({'path':'/api/state'}, {'path':'/api/list?category=garage&sort=price_asc'}, {'path':'/api/meta','data':{'id':'a','changes':{'hidden':True}}}):
            self.assertTrue(valid_request(value))

    def test_oauth_files_external_and_mutations_via_get_rejected(self):
        for path in ('/api/connect','/api/disconnect','/auth/callback','/.env','//evil.example/api/state','https://evil.example/api/state','/api/../api/connect','/api/state#x','/api/state\r\nX: bad'):
            self.assertFalse(valid_request({'path':path}),path)
            self.assertFalse(valid_request({'path':path,'data':{}}),path)
        self.assertFalse(valid_request({'path':'/api/meta'}))
        self.assertFalse(valid_request({'path':'/api/state','data':{}}))

    def test_payload_boundaries(self):
        self.assertFalse(valid_request({'path':'/api/meta','data':[]}))
        self.assertFalse(valid_request({'path':'/api/meta','data':{'text':'a'*8000}}))
        self.assertFalse(valid_request(None))


if __name__ == '__main__': unittest.main()
