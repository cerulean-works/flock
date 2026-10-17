import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self


@dataclass
class Settings:
    """Server configuration; set each field as the environment variable ``TRACKER_<FIELD>``."""

    database_url: str = field(
        default="postgresql://tracker:tracker@127.0.0.1:5432/tracker",
        doc="PostgreSQL connection URL",
    )
    files_dir: Path = field(default=Path("tracker-files"), doc="where uploaded run files go")
    migrate_on_startup: bool = field(
        default=True,
        doc="apply pending migrations on startup; when off, refuse to start on an old schema",
    )
    default_entity: str = field(
        default="flock", doc="entity runs land in when API keys are not configured"
    )
    default_project: str = field(
        default="uncategorized", doc="project runs land in when none is given"
    )
    api_keys: dict[str, str] = field(
        default_factory=dict,
        doc="accepted keys as user:key,user:key (stored key -> user); empty accepts any key",
    )
    public_url: str | None = field(default=None, doc="external base URL, used in file upload URLs")
    history_cache_points: int = field(
        default=20_000_000,
        doc="history points cached in memory for finished runs (active runs are always kept)",
    )
    crash_timeout: float = field(
        default=600.0, doc="seconds without heartbeats before a running run is marked crashed"
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
        """Settings from ``TRACKER_<FIELD>`` variables, then raw string ``overrides`` on top."""
        values: dict[str, Any] = {}
        defaults = cls()
        for spec in dataclasses.fields(cls):
            raw = (overrides or {}).get(spec.name)
            if raw is None:
                raw = environ.get(f"TRACKER_{spec.name.upper()}")
            if not raw:  # unset, or set to "" (as compose does for optional variables)
                continue
            default = getattr(defaults, spec.name)  # values are coerced to its type
            if isinstance(default, bool):  # bool("false") is True, so parse the words
                if raw.lower() not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
                    raise ValueError(f"{spec.name} must be true or false, got {raw!r}")
                values[spec.name] = raw.lower() in ("1", "true", "yes", "on")
            elif isinstance(default, dict):  # user:key pairs, stored key -> user
                pairs = [item.strip().partition(":") for item in raw.split(",") if item.strip()]
                if not all(user and key for user, _, key in pairs):
                    raise ValueError(f"{spec.name} must look like user:key,user:key")
                values[spec.name] = {key: user for user, _, key in pairs}
            elif isinstance(default, list):
                values[spec.name] = [item.strip() for item in raw.split(",") if item.strip()]
            elif default is None:
                values[spec.name] = raw
            else:
                values[spec.name] = type(default)(raw)
        return cls(**values)
