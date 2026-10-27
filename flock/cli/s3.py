"""S3 access through Flock: keys for an org's buckets, renewed before they expire.

```python
from flock.cli import s3

creds = s3.credentials(org="acme")
client = creds.boto3_client()
client.upload_file("model.pt", "checkpoints", "run-1/model.pt")
```

The Flock credential comes from [`credentials.resolve`], so `flock login` or `FLOCK_TOKEN` is
all a job needs. This module only needs httpx: boto3 and the rest are imported when asked for.
"""

import datetime as dt
import threading
import time
from typing import Any, Self

import httpx

from flock.cli import credentials as flock_credentials

RENEW_BEFORE = 300.0
"""Keys are renewed once fewer than this many seconds are left (or half their lifetime, for
keys shorter than 10 minutes)."""


class S3Error(RuntimeError):
    """Flock couldn't hand out S3 access: no sign-in, no S3 on the server, or a refusal."""


def _missing(package: str, extra: str = "") -> ImportError:
    return ImportError(f"{package} isn't installed: pip install {extra or package}")


class _Api:
    """The few Flock API calls this module makes, as the resolved user."""

    def __init__(self, host: str | None, token: str | None) -> None:
        found = flock_credentials.resolve(host, token)
        if found is None:
            where = f" to {flock_credentials.normalize_host(host)}" if host else ""
            raise S3Error(f"Not logged in{where}: run flock login, or set FLOCK_TOKEN.")
        self.host = found.host
        self.client = httpx.Client(base_url=found.host, headers=found.auth_headers(), timeout=30)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.client.close()

    def call(self, method: str, path: str, body: Any = None, ok: int = 200) -> Any:
        try:
            response = self.client.request(method, f"/api/v1/{path}", json=body)
        except httpx.HTTPError as error:
            raise S3Error(f"Couldn't reach {self.host}: {error}") from None
        if response.status_code != ok:
            try:
                reason = response.json().get("error")
            except ValueError, AttributeError:
                reason = None
            if response.status_code == 401:
                reason = f"{self.host} refused your credential ({reason}): run flock login"
            raise S3Error(reason or f"{self.host} answered {response.status_code}")
        return response.json() if response.content else None

    def default_org(self, org: str | None) -> str:
        if org is not None:
            return org
        found = self.call("GET", "me").get("default_org")
        if found is None:
            raise S3Error("You don't belong to any organization.")
        return found


class S3Credentials:
    """
    S3 access to one org's buckets, through Flock's proxy, renewed before it expires.

    Each accessor returns current values: a key is fetched when first needed, and again once
    fewer than 5 minutes are left. A pickled copy holds no key, so each DataLoader worker or
    subprocess fetches its own.

    Args:
        org (`str`, *optional*):
            The org whose buckets the keys cover. Defaults to your default org.
        host (`str`, *optional*):
            The Flock API. Defaults as [`credentials.resolve`] does.
        token (`str`, *optional*):
            A Flock CLI token or API key to exchange. Defaults to your sign-in.
        ttl (`float`, *optional*):
            Seconds each key lives, at most the org's ephemeral key lifetime (the default).
        read_only (`bool`, *optional*, defaults to `False`):
            Ask for keys that can only read.
    """

    def __init__(
        self,
        org: str | None = None,
        host: str | None = None,
        token: str | None = None,
        ttl: float | None = None,
        read_only: bool = False,
    ) -> None:
        self.org = org
        self.host = host
        self.token = token
        self.ttl = ttl
        self.read_only = read_only
        self._lock = threading.Lock()
        self._current: dict[str, Any] | None = None
        self._issued_at = 0.0

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        del state["_lock"]
        state["_current"] = None
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"S3Credentials(org={self.org!r}, read_only={self.read_only})"

    def _due(self, now: float) -> bool:
        current = self._current
        if current is None:
            return True
        expires_at = current["expires_at"]
        margin = min(RENEW_BEFORE, (expires_at - self._issued_at) / 2)
        return expires_at - now < margin

    def renew(self) -> dict[str, Any]:
        """Fetch a new key now, whatever is left of the current one. Returns it as
        `POST /api/v1/s3/credentials` answers."""
        body: dict[str, Any] = {"read_only": self.read_only}
        if self.org is not None:
            body["org"] = self.org
        if self.ttl is not None:
            body["ttl"] = self.ttl
        with _Api(self.host, self.token) as api:
            issued_at = time.time()
            current = api.call("POST", "s3/credentials", body)
        self.org = current["org"]
        self._current, self._issued_at = current, issued_at
        return current

    def current(self) -> dict[str, Any]:
        """The key in use, renewed first if it's due: `endpoint`, `region`, `org`,
        `access_key_id`, `secret_access_key` and `expires_at` (seconds since the epoch)."""
        with self._lock:
            if self._due(time.time()):
                return self.renew()
            assert self._current is not None
            return self._current

    @property
    def endpoint(self) -> str:
        return self.current()["endpoint"]

    @property
    def region(self) -> str:
        return self.current()["region"]

    @property
    def access_key_id(self) -> str:
        return self.current()["access_key_id"]

    @property
    def secret_access_key(self) -> str:
        return self.current()["secret_access_key"]

    @property
    def expires_at(self) -> float:
        return self.current()["expires_at"]

    def uri(self, bucket: str, *path: str) -> str:
        """An `s3://` URI in the org's `bucket`, as clients configured here resolve it."""
        parts = [bucket, *(p.strip("/") for p in path if p.strip("/"))]
        return "s3://" + "/".join(parts)

    def env(self) -> dict[str, str]:
        """Environment variables for the AWS CLI, boto3 and most S3 tools (path-style needs
        setting separately in tools that don't default to it)."""
        current = self.current()
        return {
            "AWS_ACCESS_KEY_ID": current["access_key_id"],
            "AWS_SECRET_ACCESS_KEY": current["secret_access_key"],
            "AWS_ENDPOINT_URL": current["endpoint"],
            "AWS_ENDPOINT_URL_S3": current["endpoint"],
            "AWS_REGION": current["region"],
            "AWS_DEFAULT_REGION": current["region"],
        }

    def storage_options(self) -> dict[str, str]:
        """Lance (object_store) `storage_options` with the current key. Lance doesn't renew
        them: call this again for each dataset you open."""
        current = self.current()
        options = {
            "aws_endpoint": current["endpoint"],
            "aws_access_key_id": current["access_key_id"],
            "aws_secret_access_key": current["secret_access_key"],
            "aws_region": current["region"],
            "aws_virtual_hosted_style_request": "false",
        }
        if current["endpoint"].startswith("http://"):
            options["allow_http"] = "true"
        return options

    def fsspec_options(self) -> dict[str, Any]:
        """Keyword arguments for `s3fs.S3FileSystem(**…)`, with the current key."""
        current = self.current()
        return {
            "key": current["access_key_id"],
            "secret": current["secret_access_key"],
            "endpoint_url": current["endpoint"],
            "client_kwargs": {"region_name": current["region"]},
            "config_kwargs": {"s3": {"addressing_style": "path"}},
        }

    def credential_process(self) -> dict[str, Any]:
        """The current key as the AWS CLI's `credential_process` prints it."""
        current = self.current()
        return {
            "Version": 1,
            "AccessKeyId": current["access_key_id"],
            "SecretAccessKey": current["secret_access_key"],
            "Expiration": iso(current["expires_at"]),
        }

    def boto3_client(self, **kwargs: Any) -> Any:
        """A boto3 S3 client on the proxy, path-style, that renews its key by itself.
        `kwargs` go to `Session.client`."""
        try:
            import boto3
            from botocore.config import Config
            from botocore.credentials import RefreshableCredentials
            from botocore.session import get_session
        except ImportError:
            raise _missing("boto3") from None

        def metadata() -> dict[str, Any]:
            return {
                "access_key": current["access_key_id"],
                "secret_key": current["secret_access_key"],
                "token": None,
                "expiry_time": iso(current["expires_at"]),
            }

        def refresh() -> dict[str, Any]:
            nonlocal current
            with self._lock:  # botocore asks only when it's due: always a new key
                current = self.renew()
            return metadata()

        current = self.current()
        refreshable = RefreshableCredentials.create_from_metadata(
            metadata=metadata(), refresh_using=refresh, method="flock"
        )
        # botocore renews 15 minutes before expiry by default, and fails keys with less than
        # 10 left: fit both to keys that may be shorter.
        lifetime = current["expires_at"] - time.time()
        refreshable._advisory_refresh_timeout = min(RENEW_BEFORE, lifetime / 2)
        refreshable._mandatory_refresh_timeout = min(RENEW_BEFORE / 5, lifetime / 4)
        session = get_session()
        session._credentials = refreshable
        config = Config(s3={"addressing_style": "path"})
        if "config" in kwargs:
            config = config.merge(kwargs.pop("config"))
        return boto3.Session(botocore_session=session).client(
            "s3",
            endpoint_url=current["endpoint"],
            region_name=current["region"],
            config=config,
            **kwargs,
        )


def iso(timestamp: float) -> str:
    """A time in ISO 8601, as the AWS tools write expirations."""
    return dt.datetime.fromtimestamp(timestamp, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def credentials(
    org: str | None = None,
    *,
    host: str | None = None,
    token: str | None = None,
    ttl: float | None = None,
    read_only: bool = False,
) -> S3Credentials:
    """
    S3 access to `org`'s buckets, renewed before it expires. A key is fetched now, so a
    missing sign-in or a server without S3 raises at once.

    Args:
        org (`str`, *optional*):
            The org whose buckets the keys cover. Defaults to your default org.
        host (`str`, *optional*):
            The Flock API. Defaults as [`credentials.resolve`] does.
        token (`str`, *optional*):
            A Flock CLI token or API key to exchange. Defaults to your sign-in.
        ttl (`float`, *optional*):
            Seconds each key lives, at most the org's ephemeral key lifetime (the default).
        read_only (`bool`, *optional*, defaults to `False`):
            Ask for keys that can only read.

    Raises:
        `S3Error`: without a sign-in, when the server has no S3, or when it refuses.
    """
    creds = S3Credentials(org, host, token, ttl, read_only)
    creds.current()
    return creds


def endpoint(
    org: str | None = None, *, host: str | None = None, token: str | None = None
) -> dict[str, str]:
    """Where the server's S3 proxy is, without fetching a key: `{endpoint, region, org}`, with
    `org` defaulting to yours.

    Raises:
        `S3Error`: without a sign-in, or when the server has no S3.
    """
    with _Api(host, token) as api:
        config = api.call("GET", "auth/config").get("s3")
        if config is None:
            raise S3Error(f"S3 isn't enabled on {api.host}")
        return {**config, "org": api.default_org(org)}


def list_buckets(org: str | None = None, *, host: str | None = None, token: str | None = None):
    """The buckets of `org` (default: yours), as `GET /api/v1/orgs/{org}/buckets` lists them."""
    with _Api(host, token) as api:
        return api.call("GET", f"orgs/{api.default_org(org)}/buckets")["buckets"]


def create_bucket(
    name: str, org: str | None = None, *, host: str | None = None, token: str | None = None
) -> dict[str, Any]:
    """Create the bucket `name` in `org` (default: yours). Any member can."""
    with _Api(host, token) as api:
        return api.call("POST", f"orgs/{api.default_org(org)}/buckets", {"name": name}, ok=201)


def delete_bucket(
    name: str, org: str | None = None, *, host: str | None = None, token: str | None = None
) -> None:
    """Delete the bucket `name` of `org` (default: yours) and everything in it. Only admins
    can. It's emptied and removed in the background; the name is free again after that."""
    with _Api(host, token) as api:
        api.call("DELETE", f"orgs/{api.default_org(org)}/buckets/{name}", ok=204)
