"""S3 keys (D55), shared by the API, which issues them, and the S3 proxy, which checks them.

A key is a row in ``s3_keys``. Its secret is never stored: it is derived from the access key
id and ``meta.s3_key_secret``, so a database dump without ``meta`` leaks no keys.
"""

import base64
import hashlib
import hmac
import secrets

PREFIX = "FLK"
# Base32's alphabet, as AWS access key ids use (uppercase letters and digits 2-7).
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def new_access_key_id() -> str:
    """``FLK`` and 17 random base32 characters: 20 in all, like an AWS access key id."""
    return PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(17))


def secret_for(server_secret: str, access_key_id: str) -> str:
    """The secret access key of ``access_key_id``: its HMAC-SHA256 under ``server_secret``,
    as unpadded URL-safe base64 (43 characters)."""
    mac = hmac.new(server_secret.encode(), access_key_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
