"""QWeather JWT (Ed25519) authentication.

QWeather's newer auth signs a short-lived JWT with an Ed25519 key instead of
sending a long-lived API key. Per the official docs the API key still works,
but its daily request volume gets limited from 2027-01-01, so JWT is the
preferred path.

Token shape (from the official spec):
    header  {"alg": "EdDSA", "kid": "<credential id>"}
    payload {"sub": "<project id>", "iat": <unix>, "exp": <unix>}
    sent as Authorization: Bearer <header>.<payload>.<signature>

Signing needs ``cryptography``, which already ships with
python-telegram-bot[all]; it is declared explicitly in requirements because
this module depends on it directly.
"""

import base64
import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from core.config import settings

# Official maximum; the default TTL is far shorter so a leaked token expires fast.
MAX_TOKEN_TTL_SECONDS = 86400
# Re-sign slightly before expiry so an in-flight request cannot use a dead token.
REFRESH_MARGIN_SECONDS = 60


def _b64url(raw: bytes) -> str:
    """base64url without padding, as required by JWT."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class QWeatherJWTSigner:
    """Signs and caches QWeather JWTs."""

    def __init__(
        self,
        private_key_pem: str,
        key_id: str,
        subject: str,
        ttl_seconds: int = 900,
    ) -> None:
        self.key_id = key_id
        self.subject = subject
        self.ttl_seconds = max(60, min(int(ttl_seconds), MAX_TOKEN_TTL_SECONDS))
        self._private_key = self._load_key(private_key_pem)
        self._token: Optional[str] = None
        self._expires_at: float = 0.0

    @staticmethod
    def _load_key(private_key_pem: str):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        # .env cannot hold real newlines, so accept the escaped form too.
        pem = private_key_pem.strip().replace("\\n", "\n")
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError(
                f"QWeather JWT requires an Ed25519 private key, got {type(key).__name__}"
            )
        return key

    def _sign(self, issued_at: int) -> str:
        header = {"alg": "EdDSA", "kid": self.key_id}
        payload = {
            "sub": self.subject,
            "iat": issued_at,
            "exp": issued_at + self.ttl_seconds,
        }
        signing_input = ".".join(
            (
                _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
                _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
            )
        )
        signature = self._private_key.sign(signing_input.encode("ascii"))
        return f"{signing_input}.{_b64url(signature)}"

    def token(self) -> str:
        """A valid token, re-signed only when the cached one is about to expire."""
        now = time.time()
        if self._token is not None and now < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._token
        issued_at = int(now)
        self._token = self._sign(issued_at)
        self._expires_at = issued_at + self.ttl_seconds
        logger.debug(f"Signed new QWeather JWT, valid {self.ttl_seconds}s")
        return self._token


def _read_private_key() -> Optional[str]:
    if settings.qweather_jwt_private_key:
        return settings.qweather_jwt_private_key
    if settings.qweather_jwt_private_key_file:
        try:
            return Path(settings.qweather_jwt_private_key_file).read_text(encoding="utf-8")
        except Exception as error:
            logger.error(f"Failed to read QWEATHER_JWT_PRIVATE_KEY_FILE: {error}")
    return None


def build_signer() -> Optional[QWeatherJWTSigner]:
    """Signer for the configured mode, or None to fall back to the API key.

    ``auto`` (default) uses JWT when it is fully configured and silently keeps
    the API key otherwise, so adding the key material is the only step needed
    to migrate. ``jwt`` makes a misconfiguration loud instead.
    """
    mode = settings.qweather_auth_mode
    if mode == "api_key":
        return None

    private_key = _read_private_key()
    if not (private_key and settings.qweather_jwt_kid and settings.qweather_jwt_sub):
        if mode == "jwt":
            raise ValueError(
                "QWEATHER_AUTH_MODE=jwt requires QWEATHER_JWT_PRIVATE_KEY (or _FILE), "
                "QWEATHER_JWT_KID and QWEATHER_JWT_SUB"
            )
        return None

    try:
        signer = QWeatherJWTSigner(
            private_key_pem=private_key,
            key_id=settings.qweather_jwt_kid,
            subject=settings.qweather_jwt_sub,
            ttl_seconds=settings.qweather_jwt_ttl_seconds,
        )
    except Exception as error:
        if mode == "jwt":
            raise
        logger.error(f"QWeather JWT unavailable, falling back to API key: {error}")
        return None

    logger.info(
        "QWeather auth: JWT (Ed25519) kid={} ttl={}s",
        settings.qweather_jwt_kid,
        signer.ttl_seconds,
    )
    return signer
