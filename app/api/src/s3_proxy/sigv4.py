"""AWS Signature Version 4 for S3: checking clients' signatures and signing upstream requests.

Only what S3 uses: ``AWS4-HMAC-SHA256`` in the ``Authorization`` header or in a presigned
query string, S3's URI encoding (paths are encoded once and never normalized), and the payload
hash taken from ``x-amz-content-sha256`` rather than computed. Of the streaming (aws-chunked)
payloads, only unsigned ones with a trailer are accepted (see `chunked`).
"""

import datetime as dt
import hashlib
import hmac
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import quote, unquote

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
# An aws-chunked body with trailing headers and no chunk signatures (see `chunked`).
STREAMING_UNSIGNED_TRAILER = "STREAMING-UNSIGNED-PAYLOAD-TRAILER"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
# How far a request's own timestamp may be from ours, as S3 allows.
MAX_SKEW = 15 * 60.0
# The longest a presigned URL may be valid for (seven days, as S3 allows).
MAX_EXPIRES = 7 * 86400

# The query parameters a presigned URL carries for authentication; never forwarded upstream.
AUTH_PARAMS = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-SignedHeaders",
        "X-Amz-Signature",
        "X-Amz-Security-Token",
    }
)


class SigV4Error(Exception):
    """A request that can't be authenticated, with the S3 error it is answered with."""

    def __init__(self, code: str, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass(frozen=True)
class SignedRequest:
    """A request's SigV4 signature, parsed but not yet checked (that needs the secret)."""

    access_key_id: str
    # As the client declared it: ``UNSIGNED-PAYLOAD`` or the hex SHA-256 of the body.
    payload_hash: str
    signature: str
    scope: str  # date/region/s3/aws4_request
    amz_date: str
    # Canonical requests to try, in order: from the path as sent, and re-encoded with S3's
    # rules (for clients that leave characters like "=" unescaped in the path).
    canonical_requests: tuple[str, ...]

    def verify(self, secret_access_key: str) -> bool:
        """Whether the signature was made with ``secret_access_key``."""
        date, region, service, _ = self.scope.split("/")
        key = signing_key(secret_access_key, date, region, service)
        return any(
            hmac.compare_digest(
                hmac.new(
                    key, string_to_sign(self.amz_date, self.scope, c).encode(), "sha256"
                ).hexdigest(),
                self.signature,
            )
            for c in self.canonical_requests
        )


def uri_encode(text: str, slash: bool = True) -> str:
    """S3's encoding: every byte but ``A-Za-z0-9-._~`` (and ``/`` if ``slash``) as %XX."""
    return quote(text, safe="/~" if slash else "~")


def parse_query(raw_query: str) -> list[tuple[str, str]]:
    """Decoded ``(name, value)`` pairs, in order; ``+`` stays a plus (S3 isn't form-encoded)."""
    pairs = []
    for part in raw_query.split("&"):
        if part:
            name, _, value = part.partition("=")
            pairs.append((unquote(name), unquote(value)))
    return pairs


def canonical_query(params: Iterable[tuple[str, str]]) -> str:
    encoded = sorted((uri_encode(k, slash=False), uri_encode(v, slash=False)) for k, v in params)
    return "&".join(f"{k}={v}" for k, v in encoded)


def canonical_headers(headers: Sequence[tuple[str, str]], signed: Sequence[str]) -> str:
    """``name:value`` lines for the ``signed`` header names (lowercase, sorted)."""
    values: dict[str, list[str]] = {}
    for name, value in headers:
        values.setdefault(name.lower(), []).append(" ".join(value.split()))
    lines = []
    for name in signed:
        if name not in values:
            raise SigV4Error("AccessDenied", f"signed header {name} is missing")
        lines.append(f"{name}:{','.join(values[name])}\n")
    return "".join(lines)


def canonical_request(
    method: str,
    canonical_uri: str,
    params: Iterable[tuple[str, str]],
    headers: Sequence[tuple[str, str]],
    signed: Sequence[str],
    payload_hash: str,
) -> str:
    return "\n".join(
        [
            method,
            canonical_uri or "/",
            canonical_query(params),
            canonical_headers(headers, signed),
            ";".join(signed),
            payload_hash,
        ]
    )


def string_to_sign(amz_date: str, scope: str, request: str) -> str:
    digest = hashlib.sha256(request.encode()).hexdigest()
    return f"{ALGORITHM}\n{amz_date}\n{scope}\n{digest}"


def signing_key(secret_access_key: str, date: str, region: str, service: str) -> bytes:
    key = f"AWS4{secret_access_key}".encode()
    for part in (date, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), "sha256").digest()
    return key


def _parse_time(amz_date: str) -> float:
    try:
        return dt.datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.UTC).timestamp()
    except ValueError:
        raise SigV4Error("AccessDenied", "X-Amz-Date is not a valid timestamp") from None


def _parse_scope(credential: str, amz_date: str) -> tuple[str, str]:
    """(access key id, scope) from ``AKID/date/region/s3/aws4_request``."""
    access_key_id, _, scope = credential.partition("/")
    parts = scope.split("/")
    if not access_key_id or len(parts) != 4 or parts[3] != "aws4_request":
        raise SigV4Error("AuthorizationQueryParametersError", "malformed credential scope", 400)
    if parts[2] != "s3":
        raise SigV4Error("AccessDenied", "the credential scope is not for s3")
    if parts[0] != amz_date[:8]:
        raise SigV4Error("AccessDenied", "the credential date does not match X-Amz-Date")
    return access_key_id, scope


def _uris(raw_path: str) -> tuple[str, ...]:
    normalized = uri_encode(unquote(raw_path))
    return (raw_path,) if normalized == raw_path else (raw_path, normalized)


def parse_request(
    method: str,
    raw_path: str,
    raw_query: str,
    headers: Sequence[tuple[str, str]],
    now: float,
) -> SignedRequest:
    """The signature on a request, from its ``Authorization`` header or presigned query.

    ``raw_path`` is the path as sent (still percent-encoded) and ``headers`` every header as
    received. Raises ``SigV4Error`` for unsigned, malformed, stale or unsupported requests.
    """
    lower = [(name.lower(), value) for name, value in headers]
    first = {}
    for name, value in lower:
        first.setdefault(name, value)
    params = parse_query(raw_query)
    query = dict(params)
    authorization = first.get("authorization")

    if authorization is not None:
        algorithm, _, fields_text = authorization.partition(" ")
        if algorithm != ALGORITHM:
            raise SigV4Error("AccessDenied", "only AWS4-HMAC-SHA256 signatures are supported")
        fields = {}
        for field in fields_text.split(","):
            name, _, value = field.strip().partition("=")
            fields[name] = value
        if not {"Credential", "SignedHeaders", "Signature"} <= fields.keys():
            raise SigV4Error("AuthorizationHeaderMalformed", "malformed Authorization", 400)
        amz_date = first.get("x-amz-date")
        if amz_date is None:
            raise SigV4Error("AccessDenied", "X-Amz-Date is required")
        if abs(_parse_time(amz_date) - now) > MAX_SKEW:
            raise SigV4Error(
                "RequestTimeTooSkewed",
                "the difference between the request time and the server's time is too large",
            )
        payload_hash = first.get("x-amz-content-sha256")
        if payload_hash is None:
            raise SigV4Error("InvalidRequest", "x-amz-content-sha256 is required", 400)
        access_key_id, scope = _parse_scope(fields["Credential"], amz_date)
        signed = fields["SignedHeaders"].split(";")
        signature = fields["Signature"]
    elif "X-Amz-Signature" in query:
        if query.get("X-Amz-Algorithm") != ALGORITHM:
            raise SigV4Error("AccessDenied", "only AWS4-HMAC-SHA256 signatures are supported")
        try:
            amz_date = query["X-Amz-Date"]
            credential = query["X-Amz-Credential"]
            signed = query["X-Amz-SignedHeaders"].split(";")
            expires = int(query["X-Amz-Expires"])
        except KeyError, ValueError:
            raise SigV4Error(
                "AuthorizationQueryParametersError", "malformed presigned query", 400
            ) from None
        if not 0 < expires <= MAX_EXPIRES:
            raise SigV4Error(
                "AuthorizationQueryParametersError", "X-Amz-Expires is out of range", 400
            )
        issued = _parse_time(amz_date)
        if issued - now > MAX_SKEW:
            raise SigV4Error("AccessDenied", "the request is not valid yet")
        if issued + expires < now:
            raise SigV4Error("AccessDenied", "Request has expired")
        access_key_id, scope = _parse_scope(credential, amz_date)
        signature = query["X-Amz-Signature"]
        # Presigned URLs never sign the body.
        payload_hash = UNSIGNED_PAYLOAD
        params = [(k, v) for k, v in params if k != "X-Amz-Signature"]
    else:
        raise SigV4Error("AccessDenied", "the request is not signed")

    # Unsigned aws-chunked bodies (recent SDKs' default) are decoded by the proxy; signed chunks
    # would need every chunk's signature checked.
    if payload_hash.startswith("STREAMING-") and payload_hash != STREAMING_UNSIGNED_TRAILER:
        raise SigV4Error("NotImplemented", "signed aws-chunked uploads are not supported", 501)
    if payload_hash not in (UNSIGNED_PAYLOAD, STREAMING_UNSIGNED_TRAILER) and (
        len(payload_hash) != 64 or any(c not in "0123456789abcdef" for c in payload_hash)
    ):
        raise SigV4Error("InvalidArgument", "x-amz-content-sha256 is not valid", 400)
    if "host" not in signed:
        raise SigV4Error("AccessDenied", "the host header must be signed")
    requests = tuple(
        canonical_request(method, uri, params, lower, signed, payload_hash)
        for uri in _uris(raw_path or "/")
    )
    return SignedRequest(
        access_key_id=access_key_id,
        payload_hash=payload_hash,
        signature=signature,
        scope=scope,
        amz_date=amz_date,
        canonical_requests=requests,
    )


def amz_date(now: float) -> str:
    return dt.datetime.fromtimestamp(now, dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def sign(
    method: str,
    host: str,
    path: str,
    params: Iterable[tuple[str, str]],
    headers: Mapping[str, str],
    payload_hash: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
    now: float,
) -> dict[str, str]:
    """``headers`` plus ``Host``, ``x-amz-date``, ``x-amz-content-sha256`` and
    ``Authorization``, signing every header; ``path`` must already be URI-encoded."""
    date = amz_date(now)
    out = {name.lower(): value for name, value in headers.items()}
    out.update({"host": host, "x-amz-date": date, "x-amz-content-sha256": payload_hash})
    signed = sorted(out)
    scope = f"{date[:8]}/{region}/s3/aws4_request"
    request = canonical_request(method, path, params, list(out.items()), signed, payload_hash)
    signature = hmac.new(
        signing_key(secret_access_key, date[:8], region, "s3"),
        string_to_sign(date, scope, request).encode(),
        "sha256",
    ).hexdigest()
    out["authorization"] = (
        f"{ALGORITHM} Credential={access_key_id}/{scope}, "
        f"SignedHeaders={';'.join(signed)}, Signature={signature}"
    )
    return out
