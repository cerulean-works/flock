"""S3 keys for the hub (D46): long-lived keys for external tools, and temporary credentials
for ``flock.hub``. Secrets are derived (``hub_credentials``), never stored; the S3 proxy checks
both kinds of credential on its own."""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from app.api.src import hub_credentials
from app.api.src.core.errors import NotFound

if TYPE_CHECKING:
    from app.api.src.core.accounts import User
    from app.api.src.core.store import Org, Store


@dataclass(eq=False)
class HubKey:
    pk: int
    user: int
    name: str
    access_key_id: str
    read_only: bool
    org: int | None  # the only org it reaches; None for every org of its user
    created_at: float
    last_used_at: float | None


class HubKeys:
    """S3 keys and temporary credentials (``store.hub_keys``).

    Keys are read from the database when listed, not kept in memory: the proxy, not this
    server, updates ``last_used_at``.
    """

    COLUMNS: ClassVar[str] = (
        "pk, user_pk, name, access_key_id, read_only, org_pk, "
        "extract(epoch FROM created_at)::float8, extract(epoch FROM last_used_at)::float8"
    )

    def __init__(self, store: Store) -> None:
        self.store = store
        self.secret = ""

    async def load(self) -> None:
        self.secret = await self.store.db.fetchval(
            "SELECT value FROM meta WHERE key = 'hub_key_secret'"
        )

    @property
    def endpoint(self) -> str | None:
        """The S3 endpoint clients are told to use: the proxy."""
        settings = self.store.settings
        return settings.hub_proxy_public_url or settings.hub_proxy_url

    def info(self, key: HubKey) -> dict[str, Any]:
        org = self.store.orgs_by_pk.get(key.org) if key.org is not None else None
        return {
            "pk": key.pk,
            "name": key.name,
            "access_key_id": key.access_key_id,
            "read_only": key.read_only,
            "org": org.name if org is not None else None,
            "created_at": key.created_at,
            "last_used_at": key.last_used_at,
        }

    async def keys(self, user: User) -> list[HubKey]:
        return [
            HubKey(*record)
            for record in await self.store.db.fetch(
                f"SELECT {self.COLUMNS} FROM hub_keys WHERE user_pk = $1 ORDER BY pk", user.pk
            )
        ]

    async def create(
        self, user: User, name: str, read_only: bool = False, org: Org | None = None
    ) -> tuple[HubKey, str]:
        """A new key; its secret is returned only here."""
        access_key_id = hub_credentials.new_access_key_id(hub_credentials.KEY_PREFIX)
        record = await self.store.db.fetchrow(
            "INSERT INTO hub_keys (user_pk, name, access_key_id, read_only, org_pk, created_at) "
            f"VALUES ($1, $2, $3, $4, $5, now()) RETURNING {self.COLUMNS}",
            user.pk,
            name,
            access_key_id,
            read_only,
            org.pk if org is not None else None,
        )
        assert record is not None
        return HubKey(*record), hub_credentials.secret_for(self.secret, access_key_id)

    async def delete(self, user: User, pk: int) -> None:
        deleted = await self.store.db.fetchval(
            "DELETE FROM hub_keys WHERE pk = $1 AND user_pk = $2 RETURNING pk", pk, user.pk
        )
        if deleted is None:
            raise NotFound(f"S3 key {pk} not found")
        # The proxy caches keys for a few seconds; this drops the revoked one at once.
        await self.store.datasets.invalidate()

    def temporary(
        self, user: User, read_only: bool = False, mirror_writer: bool = False
    ) -> dict[str, Any]:
        """Short-lived credentials for ``user``, checked by the proxy without the database.
        ``mirror_writer`` is only for the server's own Hugging Face imports (D50)."""
        settings = self.store.settings
        access_key_id = hub_credentials.new_access_key_id(hub_credentials.TEMP_PREFIX)
        expires_at = time.time() + settings.hub_credentials_ttl
        session = hub_credentials.Session(
            user.pk, expires_at, read_only, access_key_id, mirror_writer
        )
        return {
            "endpoint": self.endpoint,
            "region": settings.hub_s3_region,
            "access_key_id": access_key_id,
            "secret_access_key": hub_credentials.secret_for(self.secret, access_key_id),
            "session_token": hub_credentials.make_session_token(self.secret, session),
            "expires_at": expires_at,
        }
