import base64
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.test import SimpleTestCase
from rest_framework.test import APIRequestFactory

from api.renders import EncryptedJSONRenderer, encrypt_payload


class EncryptedRendererTests(SimpleTestCase):
    def _decrypt(self, payload, token):
        key = hashlib.sha256(token.encode("utf-8")).digest()
        nonce = base64.urlsafe_b64decode(payload["_n"] + "==")
        ciphertext = base64.urlsafe_b64decode(payload["_t"] + "==")
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        return json.loads(plaintext.decode("utf-8"))

    def test_encrypt_payload_serializes_decimal_and_dates_with_drf_encoder(self):
        token = "secret-token"

        payload = encrypt_payload(
            {
                "data": {
                    "amount": Decimal("1.25"),
                    "score": Decimal("2.50"),
                    "day": date(2026, 10, 29),
                    "created_at": datetime(2026, 10, 29, 12, 34, 56),
                }
            },
            token=token,
        )

        decrypted = self._decrypt(payload, token)

        self.assertEqual(
            decrypted,
            {
                "data": {
                    "amount": 1.25,
                    "score": 2.5,
                    "day": "2026-10-29",
                    "created_at": "2026-10-29T12:34:56",
                }
            },
        )

    def test_renderer_encrypts_decimal_response_payload(self):
        token = "secret-token"
        request = APIRequestFactory().get(
            "/api/batteries/",
            HTTP_AUTHORIZATION=f"Token {token}",
        )

        class DummyView:
            action = "list"
            encrypt_response = True

        rendered = EncryptedJSONRenderer().render(
            {"amount": Decimal("1.25")},
            renderer_context={"request": request, "view": DummyView()},
        )

        outer = json.loads(rendered.decode("utf-8"))
        self.assertIn("_n", outer)
        self.assertIn("_t", outer)
        self.assertEqual(self._decrypt(outer, token), {"data": {"amount": 1.25}})
