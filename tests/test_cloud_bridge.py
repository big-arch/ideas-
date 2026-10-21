import unittest
from garage_radar.cloud_bridge import valid_request


class BridgeBoundaryTests(unittest.TestCase):
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
