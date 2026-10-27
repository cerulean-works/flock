import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self


@dataclass
class Settings:
    """Server configuration; set each field as the environment variable ``FLOCK_<FIELD>``."""

    database_url: str = field(
        default="postgresql://flock:flock@127.0.0.1:5432/flock",
        doc="PostgreSQL connection URL",
    )
    files_dir: Path = field(default=Path("flock-files"), doc="where uploaded run files go")
    migrate_on_startup: bool = field(
        default=True,
        doc="apply pending migrations on startup; when off, refuse to start on an old schema",
    )
    api_public_url: str | None = field(
        default=None, doc="public API base URL for SDK file uploads; request URL when unset"
    )
    web_url: str | None = field(
        default=None,
        doc="public dashboard origin for setup, invitation, reset and Google sign-in links",
    )
    jwt_secret: str | None = field(
        default=None, doc="key signing access tokens (default: one generated in the database)"
    )
    access_token_ttl: float = field(default=900.0, doc="seconds an access token is valid")
    refresh_token_ttl: float = field(
        default=30 * 86400.0, doc="seconds a sign-in lasts without being refreshed"
    )
    invite_ttl: float = field(default=7 * 86400.0, doc="seconds an invite link is valid")
    reset_ttl: float = field(default=86400.0, doc="seconds a password reset link is valid")
    device_code_ttl: float = field(
        default=900.0, doc="seconds a `flock login` code waits to be approved"
    )
    cli_session_ttl: float = field(
        default=90 * 86400.0, doc="seconds a command-line sign-in lasts without being used"
    )
    google_client_id: str | None = field(
        default=None, doc="OAuth client id enabling Sign in with Google"
    )
    google_client_secret: str | None = field(default=None, doc="that OAuth client's secret")
    google_redirect_url: str | None = field(
        default=None,
        doc="Google callback URL (default: <web_url, else api_public_url>/api/v1/auth/google/callback)",
    )
    gravatar: bool = field(
        default=True,
        doc="fetch new accounts' avatars from Gravatar (sends the SHA-256 of their email)",
    )
    gravatar_url: str = field(
        default="https://gravatar.com/avatar/",
        doc="Gravatar (or a Libravatar-compatible mirror) avatar URL, before the email hash",
    )
    wandb_base_url: str = field(
        default="https://api.wandb.ai",
        doc="W&B API that organization imports read from, unless the import names another",
    )
    history_cache_points: int = field(
        default=20_000_000,
        doc="history points cached in memory for finished runs (active runs are always kept)",
    )
    crash_timeout: float = field(
        default=600.0, doc="seconds without heartbeats before a running run is marked crashed"
    )
    s3_upstream_endpoint: str | None = field(
        default=None,
        doc="the S3 server (e.g. Ceph RGW) the S3 proxy fronts; S3 features are off when unset",
    )
    s3_upstream_region: str = field(default="us-east-1", doc="that S3 server's region")
    s3_upstream_access_key_id: str | None = field(
        default=None, doc="the S3 server's access key id, which the proxy signs requests with"
    )
    s3_upstream_secret_access_key: str | None = field(default=None, doc="that key's secret")
    s3_bucket_prefix: str = field(
        default="",
        doc="put in front of every bucket name on the S3 server (shortening the names allowed)",
    )
    s3_public_url: str | None = field(
        default=None, doc="the S3 proxy's URL as clients reach it, handed out with S3 keys"
    )
    s3_trash_bucket: str | None = field(
        default=None,
        doc="the bucket on the S3 server deleted datasets wait in (default: <prefix>flock-trash)",
    )
    pool_size: int = field(default=16, doc="maximum number of database connections")
    cors_origins: list[str] = field(
        default_factory=list, doc="origins allowed to call the API from browsers, comma separated"
    )
    log_level: str = field(default="info", doc="log level")

    @classmethod
    def load(
        cls,
        overrides: Mapping[str, str | None] | None = None,
        environ: Mapping[str, str] = os.environ,
    ) -> Self:
        """Settings from ``FLOCK_<FIELD>`` variables, then raw string ``overrides`` on top."""
        values: dict[str, Any] = {}
        defaults = cls()
        for spec in dataclasses.fields(cls):
            raw = (overrides or {}).get(spec.name)
            if raw is None:
                raw = environ.get(f"FLOCK_{spec.name.upper()}")
            if not raw:  # unset, or set to "" (as compose does for optional variables)
                continue
            default = getattr(defaults, spec.name)  # values are coerced to its type
            if isinstance(default, bool):  # bool("false") is True, so parse the words
                if raw.lower() not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
                    raise ValueError(f"{spec.name} must be true or false, got {raw!r}")
                values[spec.name] = raw.lower() in ("1", "true", "yes", "on")
            elif isinstance(default, list):
                values[spec.name] = [item.strip() for item in raw.split(",") if item.strip()]
            elif default is None:
                values[spec.name] = raw
            else:
                values[spec.name] = type(default)(raw)
        return cls(**values)
