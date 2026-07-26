"""QWeather JWT (Ed25519) authentication tests.

The token structure is asserted against the official spec — header
{"alg":"EdDSA","kid":...}, payload {"sub","iat","exp"}, sent as
Authorization: Bearer — and the signature is verified with the matching
public key, so these tests prove the crypto, not just the shape.

End-to-end acceptance still needs real console credentials; nothing here
contacts QWeather.
"""

import base64
import json
import os
import time
import unittest

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("QWEATHER_API_KEY", "test-qweather-key")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature
from pydantic import ValidationError

from core.config import Settings, settings
from services import qweather_auth
from services.qweather_auth import MAX_TOKEN_TTL_SECONDS, QWeatherJWTSigner, build_signer


def make_key_pair():
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    return pem, key.public_key()


def decode_segment(segment: str) -> dict:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


class TokenStructureTests(unittest.TestCase):
    def setUp(self):
        self.pem, self.public_key = make_key_pair()
        self.signer = QWeatherJWTSigner(self.pem, key_id="CRED123", subject="PROJ456", ttl_seconds=900)

    def test_header_matches_the_official_spec(self):
        header = decode_segment(self.signer.token().split(".")[0])
        self.assertEqual(header, {"alg": "EdDSA", "kid": "CRED123"})

    def test_payload_carries_sub_iat_and_exp(self):
        before = int(time.time())
        payload = decode_segment(self.signer.token().split(".")[1])
        self.assertEqual(payload["sub"], "PROJ456")
        self.assertGreaterEqual(payload["iat"], before)
        self.assertEqual(payload["exp"] - payload["iat"], 900)

    def test_signature_verifies_with_the_public_key(self):
        header_b64, payload_b64, signature_b64 = self.signer.token().split(".")
        signature = base64.urlsafe_b64decode(signature_b64 + "=" * (-len(signature_b64) % 4))
        self.public_key.verify(signature, f"{header_b64}.{payload_b64}".encode("ascii"))

    def test_tampered_payload_fails_verification(self):
        header_b64, _payload_b64, signature_b64 = self.signer.token().split(".")
        signature = base64.urlsafe_b64decode(signature_b64 + "=" * (-len(signature_b64) % 4))
        forged = base64.urlsafe_b64encode(
            json.dumps({"sub": "EVIL", "iat": 0, "exp": 9999999999}).encode()
        ).rstrip(b"=").decode()
        with self.assertRaises(InvalidSignature):
            self.public_key.verify(signature, f"{header_b64}.{forged}".encode("ascii"))

    def test_segments_are_base64url_without_padding(self):
        for segment in self.signer.token().split("."):
            self.assertNotIn("=", segment)
            self.assertNotIn("+", segment)
            self.assertNotIn("/", segment)

    def test_escaped_newlines_in_pem_are_accepted(self):
        escaped = self.pem.replace("\n", "\\n")
        signer = QWeatherJWTSigner(escaped, key_id="K", subject="S")
        self.assertEqual(len(signer.token().split(".")), 3)

    def test_non_ed25519_key_is_rejected(self):
        from cryptography.hazmat.primitives.asymmetric import rsa

        rsa_pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        with self.assertRaises(ValueError):
            QWeatherJWTSigner(rsa_pem, key_id="K", subject="S")

    def test_garbage_key_raises(self):
        with self.assertRaises(Exception):
            QWeatherJWTSigner("not a pem", key_id="K", subject="S")


class TokenLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.pem, _public = make_key_pair()

    def test_token_is_reused_until_close_to_expiry(self):
        signer = QWeatherJWTSigner(self.pem, key_id="K", subject="S", ttl_seconds=900)
        first = signer.token()
        self.assertEqual(first, signer.token())

    def test_token_is_resigned_when_the_refresh_margin_is_reached(self):
        signer = QWeatherJWTSigner(self.pem, key_id="K", subject="S", ttl_seconds=900)
        signer.token()
        # Pretend the cached token is nearly expired.
        signer._expires_at = time.time() + 5
        stale_expiry = signer._expires_at
        signer.token()
        # Ed25519 signatures are deterministic (RFC 8032), so a token re-signed
        # within the same second is byte-identical — the expiry is the signal.
        self.assertGreater(signer._expires_at, stale_expiry)

    def test_a_later_issue_time_produces_a_different_token(self):
        signer = QWeatherJWTSigner(self.pem, key_id="K", subject="S", ttl_seconds=900)
        first = signer._sign(1_700_000_000)
        second = signer._sign(1_700_000_060)
        self.assertNotEqual(first, second)

    def test_ttl_is_clamped_to_the_official_maximum(self):
        signer = QWeatherJWTSigner(self.pem, key_id="K", subject="S", ttl_seconds=999999)
        self.assertEqual(signer.ttl_seconds, MAX_TOKEN_TTL_SECONDS)
        payload = decode_segment(signer.token().split(".")[1])
        self.assertEqual(payload["exp"] - payload["iat"], MAX_TOKEN_TTL_SECONDS)

    def test_ttl_has_a_sane_floor(self):
        signer = QWeatherJWTSigner(self.pem, key_id="K", subject="S", ttl_seconds=1)
        self.assertGreaterEqual(signer.ttl_seconds, 60)


class ModeSelectionTests(unittest.TestCase):
    def setUp(self):
        self.pem, _public = make_key_pair()
        self._saved = {
            name: getattr(settings, name)
            for name in (
                "qweather_auth_mode",
                "qweather_jwt_private_key",
                "qweather_jwt_private_key_file",
                "qweather_jwt_kid",
                "qweather_jwt_sub",
            )
        }

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(settings, name, value)

    def _configure_jwt(self):
        settings.qweather_jwt_private_key = self.pem
        settings.qweather_jwt_kid = "CRED"
        settings.qweather_jwt_sub = "PROJ"

    def test_auto_without_jwt_config_uses_api_key(self):
        settings.qweather_auth_mode = "auto"
        settings.qweather_jwt_private_key = None
        settings.qweather_jwt_kid = None
        settings.qweather_jwt_sub = None
        self.assertIsNone(build_signer())

    def test_auto_with_jwt_config_uses_jwt(self):
        settings.qweather_auth_mode = "auto"
        self._configure_jwt()
        self.assertIsNotNone(build_signer())

    def test_api_key_mode_never_signs(self):
        settings.qweather_auth_mode = "api_key"
        self._configure_jwt()
        self.assertIsNone(build_signer())

    def test_jwt_mode_without_credentials_raises_loudly(self):
        settings.qweather_auth_mode = "jwt"
        settings.qweather_jwt_private_key = None
        settings.qweather_jwt_kid = None
        settings.qweather_jwt_sub = None
        with self.assertRaises(ValueError):
            build_signer()

    def test_auto_mode_survives_a_broken_key(self):
        settings.qweather_auth_mode = "auto"
        settings.qweather_jwt_private_key = "-----BEGIN PRIVATE KEY-----\nbroken\n-----END PRIVATE KEY-----"
        settings.qweather_jwt_kid = "CRED"
        settings.qweather_jwt_sub = "PROJ"
        # Weather data must keep flowing on the API key.
        self.assertIsNone(build_signer())

    def test_private_key_file_is_read(self):
        from tempfile import NamedTemporaryFile

        settings.qweather_auth_mode = "auto"
        settings.qweather_jwt_private_key = None
        settings.qweather_jwt_kid = "CRED"
        settings.qweather_jwt_sub = "PROJ"
        with NamedTemporaryFile("w", suffix=".pem", delete=False) as handle:
            handle.write(self.pem)
            path = handle.name
        try:
            settings.qweather_jwt_private_key_file = path
            self.assertTrue(qweather_auth.jwt_config_complete())
            self.assertIsNotNone(build_signer())
        finally:
            os.unlink(path)


class AdapterHeaderTests(unittest.TestCase):
    def test_api_key_header_when_no_signer(self):
        from adapters.qweather import QWeatherAdapter

        adapter = QWeatherAdapter.__new__(QWeatherAdapter)
        adapter.api_key = "KEY123"
        adapter._jwt_signer = None
        self.assertEqual(adapter._auth_headers(), {"X-QW-Api-Key": "KEY123"})

    def test_bearer_header_when_signer_present(self):
        from adapters.qweather import QWeatherAdapter

        pem, _public = make_key_pair()
        adapter = QWeatherAdapter.__new__(QWeatherAdapter)
        adapter.api_key = "KEY123"
        adapter._jwt_signer = QWeatherJWTSigner(pem, key_id="K", subject="S")
        headers = adapter._auth_headers()
        self.assertNotIn("X-QW-Api-Key", headers)
        self.assertTrue(headers["Authorization"].startswith("Bearer "))
        self.assertEqual(len(headers["Authorization"].split(" ")[1].split(".")), 3)

    def test_signing_failure_falls_back_to_api_key(self):
        from adapters.qweather import QWeatherAdapter

        class BrokenSigner:
            def token(self):
                raise RuntimeError("hsm offline")

        adapter = QWeatherAdapter.__new__(QWeatherAdapter)
        adapter.api_key = "KEY123"
        adapter._jwt_signer = BrokenSigner()
        self.assertEqual(adapter._auth_headers(), {"X-QW-Api-Key": "KEY123"})

    def test_signing_failure_without_api_key_raises(self):
        from adapters.qweather import QWeatherAdapter

        class BrokenSigner:
            def token(self):
                raise RuntimeError("hsm offline")

        adapter = QWeatherAdapter.__new__(QWeatherAdapter)
        adapter.api_key = None
        adapter._jwt_signer = BrokenSigner()
        with self.assertRaises(RuntimeError):
            adapter._auth_headers()


class ConfigValidationTests(unittest.TestCase):
    def setUp(self):
        # Settings(_env_file=None) still reads real environment variables, and
        # this module sets QWEATHER_API_KEY at import, so clear it per test.
        self._removed = {}
        for name in ("QWEATHER_API_KEY", "QWEATHER_AUTH_MODE"):
            if name in os.environ:
                self._removed[name] = os.environ.pop(name)

    def tearDown(self):
        os.environ.update(self._removed)

    def _settings(self, **overrides):
        base = dict(bot_token="test-token", _env_file=None)
        base.update(overrides)
        return Settings(**base)

    def test_api_key_alone_is_valid(self):
        configured = self._settings(qweather_api_key="key")
        self.assertEqual(configured.qweather_auth_mode, "auto")

    def test_jwt_trio_alone_is_valid_without_api_key(self):
        pem, _public = make_key_pair()
        configured = self._settings(
            qweather_jwt_private_key=pem,
            qweather_jwt_kid="CRED",
            qweather_jwt_sub="PROJ",
        )
        self.assertIsNone(configured.qweather_api_key)

    def test_no_credentials_at_all_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._settings()

    def test_jwt_mode_requires_the_full_trio(self):
        with self.assertRaises(ValidationError):
            self._settings(qweather_api_key="key", qweather_auth_mode="jwt")

    def test_api_key_mode_requires_a_key(self):
        pem, _public = make_key_pair()
        with self.assertRaises(ValidationError):
            self._settings(
                qweather_auth_mode="api_key",
                qweather_jwt_private_key=pem,
                qweather_jwt_kid="CRED",
                qweather_jwt_sub="PROJ",
            )

    def test_ttl_bounds_are_enforced(self):
        for bad in (10, 90000):
            with self.assertRaises(ValidationError):
                self._settings(qweather_api_key="key", qweather_jwt_ttl_seconds=bad)

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValidationError):
            self._settings(qweather_api_key="key", qweather_auth_mode="oauth")

    def test_blank_credential_strings_are_treated_as_unset(self):
        configured = self._settings(qweather_api_key="key", qweather_jwt_kid="   ")
        self.assertIsNone(configured.qweather_jwt_kid)


if __name__ == "__main__":
    unittest.main()
