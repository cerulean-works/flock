import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Self

from app.api.src.settings import Settings


@dataclass(frozen=True)
class ProxyConfig:
    """The proxy's settings: the API's database URL plus the hub's ``FLOCK_HUB_*`` variables.

    These are read here rather than from ``Settings`` until the hub settings land there.
    """

    database_url: str
    s3_endpoint: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_region: str = "us-east-1"
    bucket: str = "flock-hub"
    mirror_org: str = "hf"
    pool_size: int = 16

    @classmethod
    def load(cls, environ: Mapping[str, str] = os.environ) -> Self:
        settings = Settings.load(environ=environ)

        def get(name: str, default: str | None = None) -> str:
            value = environ.get(f"FLOCK_HUB_{name}") or default
            if value is None:
                raise RuntimeError(f"FLOCK_HUB_{name} must be set to run the hub's S3 proxy")
            return value

        return cls(
            database_url=settings.database_url,
            s3_endpoint=get("S3_ENDPOINT"),
            s3_access_key_id=get("S3_ACCESS_KEY_ID"),
            s3_secret_access_key=get("S3_SECRET_ACCESS_KEY"),
            s3_region=get("S3_REGION", cls.s3_region),
            bucket=get("BUCKET", cls.bucket),
            mirror_org=get("MIRROR_ORG", cls.mirror_org),
            pool_size=settings.pool_size,
        )
