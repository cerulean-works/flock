"""S3 credentials for the hub (D46), shared by the API, which issues them, and the S3 proxy,
which checks them.

Secrets are derived from ``meta.hub_key_secret`` and the access key id, so neither kind of
credential stores a secret: a long-lived key is a row in ``hub_keys``, and temporary
credentials are a signed session token that the proxy checks without the database.
"""

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

import orjson

KEY_PREFIX = "FLKH"
TEMP_PREFIX = "FLKT"


@dataclass(frozen=True)
class Session:
    """What a temporary credential's session token says about its holder."""

    user: int
    expires_at: float
    read_only: bool
    access_key_id: str
    # Writes to the mirror org; only on credentials the API mints for its own HF imports.
    mirror_writer: bool = False


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(server_secret: str, message: str) -> bytes:
    return hmac.new(server_secret.encode(), message.encode(), hashlib.sha256).digest()


def new_access_key_id(prefix: str) -> str:
    """``prefix`` (``KEY_PREFIX`` or ``TEMP_PREFIX``) plus 16 random base32 characters."""
    return prefix + base64.b32encode(secrets.token_bytes(10)).decode()


def secret_for(server_secret: str, access_key_id: str) -> str:
    """The secret access key that belongs to ``access_key_id``."""
    kind = "temp" if access_key_id.startswith(TEMP_PREFIX) else "key"
    return _b64(_mac(server_secret, f"{kind}:{access_key_id}"))


def make_session_token(server_secret: str, session: Session) -> str:
    """A token carrying ``session``, signed so it can't be altered."""
    body = _b64(
        orjson.dumps(
            {
                "u": session.user,
                "e": session.expires_at,
                "r": session.read_only,
                "a": session.access_key_id,
                "m": session.mirror_writer,
            }
        )
    )
    return f"{body}.{_b64(_mac(server_secret, body))}"


def read_session_token(server_secret: str, token: str, now: float) -> Session | None:
    """The session in ``token``, or None if it is malformed, forged or expired."""
    body, _, mac = token.partition(".")
    try:
        valid = hmac.compare_digest(_unb64(mac), _mac(server_secret, body))
        claims = orjson.loads(_unb64(body)) if valid else None
    except ValueError, orjson.JSONDecodeError:
        return None
    if not isinstance(claims, dict) or claims.get("e", 0) <= now:
        return None
    try:
        return Session(
            user=int(claims["u"]),
            expires_at=float(claims["e"]),
            read_only=bool(claims["r"]),
            access_key_id=str(claims["a"]),
            mirror_writer=bool(claims.get("m", False)),
        )
    except KeyError, TypeError, ValueError:
        return None
