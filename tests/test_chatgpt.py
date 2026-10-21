import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from garage_radar import chatgpt
from garage_radar.details import parse_detail
from garage_radar.network import RemoteError
from test_radar import listing


class ConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = patch.object(chatgpt, "ROOT", Path(self.tmp.name)); self.root.start()
        self.pending = {"state": "state-example", "nonce": "nonce-example", "verifier": "verifier", "redirect": "http://127.0.0.1:8766/auth/callback",
                        "time": time.time(), "client_id": "dynamic_agent_client", "subject": ""}
        self.query = {"state": "state-example", "code": "code-example", "client_id": "oaiapp_test"}
        self.claims = {"iss": "https://auth.openai.com", "aud": "oaiapp_test", "sub": "subject", "nonce": "nonce-example",
                       "exp": int(time.time()+3600), "iat": int(time.time())}

    def tearDown(self): self.root.stop(); self.tmp.cleanup()

    def tokens(self, **claims):
        return {"id_token": jwt.encode({**self.claims, **claims}, self.private, algorithm="RS256"),
                "access_token": "fake-access", "refresh_token": "fake-refresh", "scope": chatgpt.SCOPES, "expires_in": 3600}

    def test_state_mismatch_stops_before_exchange(self):
        with patch.object(chatgpt, "request_json") as request:
            with self.assertRaises(ValueError): chatgpt.finish({**self.query,"state":"wrong"}, self.pending)
            request.assert_not_called()

    def test_nonce_issuer_audience_and_granted_permission_are_verified(self):
        for changed in ({"nonce":"wrong"},{"iss":"https://other.example"},{"aud":"other-client"}):
            with self.subTest(changed=changed), patch.object(chatgpt, "request_json",return_value=self.tokens(**changed)), patch.object(jwt,"PyJWKClient") as client:
                client.return_value.get_signing_key_from_jwt.return_value.key=self.private.public_key()
                with self.assertRaises(ValueError): chatgpt.finish(self.query,self.pending)
        no_permission=self.tokens();no_permission["scope"]="openid profile"
        with patch.object(chatgpt,"request_json",return_value=no_permission),patch.object(jwt,"PyJWKClient") as client:
            client.return_value.get_signing_key_from_jwt.return_value.key=self.private.public_key()
            with self.assertRaises(ValueError):chatgpt.finish(self.query,self.pending)

    def test_valid_identity_is_encrypted_and_recoverable(self):
        with patch.object(chatgpt,"request_json",return_value=self.tokens()),patch.object(jwt,"PyJWKClient") as client:
            client.return_value.get_signing_key_from_jwt.return_value.key=self.private.public_key()
            chatgpt.finish(self.query,self.pending)
        encrypted=(Path(self.tmp.name)/"credentials.bin").read_bytes()
        self.assertNotIn(b"fake-access",encrypted)
        self.assertEqual(chatgpt.load()["access_token"],"fake-access")
        self.assertTrue(chatgpt.status()["connected"])

    def test_small_clock_skew_is_allowed_but_large_skew_is_rejected(self):
        with patch.object(chatgpt, "request_json", return_value=self.tokens(iat=int(time.time()+13))), patch.object(jwt, "PyJWKClient") as client:
            client.return_value.get_signing_key_from_jwt.return_value.key = self.private.public_key()
            chatgpt.finish(self.query, self.pending)
        with patch.object(chatgpt, "request_json", return_value=self.tokens(iat=int(time.time()+120))), patch.object(jwt, "PyJWKClient") as client:
            client.return_value.get_signing_key_from_jwt.return_value.key = self.private.public_key()
            with self.assertRaisesRegex(ValueError, "Время Windows"):
                chatgpt.finish(self.query, self.pending)

    def test_missing_plan_permission_has_its_own_message(self):
        tokens = self.tokens()
        tokens['scope'] = 'openid profile email'
        with patch.object(chatgpt, "request_json", return_value=tokens), patch.object(jwt, "PyJWKClient") as client:
            client.return_value.get_signing_key_from_jwt.return_value.key = self.private.public_key()
            with self.assertRaisesRegex(ValueError, "не предоставил разрешение использовать план"):
                chatgpt.finish(self.query, self.pending)

    def test_stream_must_end_with_completed_event(self):
        def response(events):
            stream=io.BytesIO(b"".join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events))
            return stream
        events=[{"type":"response.output_text.delta","delta":"Partial text"}]
        with patch.object(chatgpt,"access_token",return_value="example"),patch.object(chatgpt,"urlopen",return_value=response(events)):
            with self.assertRaises(RemoteError):chatgpt.respond("rules",[],model="account-model")
        events.append({"type":"response.completed","response":{"output":[]}})
        with patch.object(chatgpt,"access_token",return_value="example"),patch.object(chatgpt,"urlopen",return_value=response(events)):
            self.assertEqual(chatgpt.respond("rules",[],model="account-model")["text"],"Partial text")


class DetailTests(unittest.TestCase):
    def test_full_description_and_deduplicated_source_photos(self):
        document='''<div data-marker="item-view/item-description">Полный текст &lt;продавца&gt;</div>
        <script type="application/ld+json">{"@type":"Product","image":["https://avatars.mds.yandex.net/get-realty-offers/123/unique/large","https://avatars.mds.yandex.net/get-realty-offers/123/unique/small","https://evil.example/image.jpg"]}</script>'''
        detail=parse_detail(document,listing())
        self.assertEqual(detail["description"],"Полный текст <продавца>")
        self.assertEqual(len(detail["photos"]),1)
        self.assertEqual(detail["scope"],"detail")

    def test_challenge_page_does_not_become_property_facts(self):
        from garage_radar.avito import SourceUnavailable
        with self.assertRaises(SourceUnavailable):parse_detail("<html>captcha</html>",listing())
