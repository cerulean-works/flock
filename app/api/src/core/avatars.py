"""Avatar images of users and organizations: stored in the database by the SHA-256 of their
bytes, served without credentials at a URL made of that digest, and fetched from Gravatar
when an account is created."""

import asyncio
import hashlib
import http.client
import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import asyncpg

    from app.api.src.core.accounts import User
    from app.api.src.core.store import Org, Store

log = logging.getLogger(__name__)


class ImageError(ValueError):
    """Bytes that aren't an accepted avatar image; ``status`` is the HTTP status to answer."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def image_type(data: bytes) -> str | None:
    """The content type of a PNG, JPEG, GIF or WebP image, from its leading bytes."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class Avatars:
    """Avatars of ``store.accounts`` users and ``store`` orgs (``store.avatars``).

    Users and orgs keep their avatar's digest in memory; image bytes are read from the
    database when served (browsers cache them for good: a digest's bytes never change).
    """

    MAX_BYTES: ClassVar[int] = 1 << 20
    GRAVATAR_SIZE: ClassVar[int] = 256
    GRAVATAR_TIMEOUT: ClassVar[float] = 10.0

    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = asyncio.Lock()  # serializes changes, so cleanup never races a new use
        self._tasks: set[asyncio.Task] = set()

    @staticmethod
    def url(digest: bytes | None) -> str | None:
        return f"/api/v1/avatars/{digest.hex()}" if digest is not None else None

    @classmethod
    def check(cls, data: bytes) -> str:
        """The content type of an avatar upload, or ImageError."""
        if not data:
            raise ImageError("the image is empty", 422)
        if len(data) > cls.MAX_BYTES:
            raise ImageError(f"images are at most {cls.MAX_BYTES} bytes", 413)
        content_type = image_type(data)
        if content_type is None:
            raise ImageError("images must be PNG, JPEG, GIF or WebP", 415)
        return content_type

    async def image(self, digest: bytes) -> tuple[str, bytes] | None:
        record = await self.store.db.fetchrow(
            "SELECT content_type, data FROM avatars WHERE digest = $1", digest
        )
        return (record[0], bytes(record[1])) if record is not None else None

    # ------------------------------------------------------------------ changes

    async def _store(self, conn: asyncpg.Connection, data: bytes | None) -> bytes | None:
        if data is None:
            return None
        content_type = self.check(data)
        digest = hashlib.sha256(data).digest()
        await conn.execute(
            "INSERT INTO avatars (digest, content_type, data, created_at) "
            "VALUES ($1, $2, $3, now()) ON CONFLICT (digest) DO NOTHING",
            digest,
            content_type,
            data,
        )
        return digest

    @staticmethod
    async def _release(conn: asyncpg.Connection, digest: bytes | None) -> None:
        """Delete an image nothing uses any more."""
        if digest is not None:
            await conn.execute(
                "DELETE FROM avatars WHERE digest = $1 "
                "AND NOT EXISTS (SELECT 1 FROM users WHERE avatar = $1) "
                "AND NOT EXISTS (SELECT 1 FROM orgs WHERE avatar = $1)",
                digest,
            )

    async def set_user(self, user: User, data: bytes | None, source: str = "upload") -> None:
        """Set (``data``: image bytes) or remove (None) a user's avatar."""
        async with self._lock, self.store.db.pool.acquire() as conn, conn.transaction():
            if not await conn.fetchval("SELECT EXISTS (SELECT FROM users WHERE pk = $1)", user.pk):
                return  # deleted meanwhile
            digest = await self._store(conn, data)
            source_value = source if digest is not None else None
            await conn.execute(
                "UPDATE users SET avatar = $2, avatar_source = $3 WHERE pk = $1",
                user.pk,
                digest,
                source_value,
            )
            old, user.avatar, user.avatar_source = user.avatar, digest, source_value
            if old != digest:
                await self._release(conn, old)

    async def set_org(self, org: Org, data: bytes | None) -> None:
        async with self._lock, self.store.db.pool.acquire() as conn, conn.transaction():
            if org.deleted:
                return
            digest = await self._store(conn, data)
            await conn.execute("UPDATE orgs SET avatar = $2 WHERE pk = $1", org.pk, digest)
            old, org.avatar = org.avatar, digest
            if old != digest:
                await self._release(conn, old)

    async def release(self, digest: bytes | None) -> None:
        """Clean up after the user or org using ``digest`` was deleted."""
        if digest is not None:
            async with self._lock, self.store.db.pool.acquire() as conn:
                await self._release(conn, digest)

    # ------------------------------------------------------------------ Gravatar

    def _download(self, email: str) -> bytes | None:
        """The Gravatar image of ``email``, or None if it has none (blocking: use a thread)."""
        settings = self.store.settings
        digest = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        url = f"{settings.gravatar_url}{digest}?s={self.GRAVATAR_SIZE}&d=404"
        request = urllib.request.Request(url, headers={"user-agent": "flock"})
        try:
            with urllib.request.urlopen(request, timeout=self.GRAVATAR_TIMEOUT) as resp:
                return resp.read(self.MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    async def gravatar(self, user: User, keep_existing: bool = False) -> bool:
        """Set the user's avatar to their Gravatar; False if they have none. With
        ``keep_existing``, an avatar the user already has (or set meanwhile) wins."""
        data = await asyncio.to_thread(self._download, user.email)
        if data is None:
            return False
        self.check(data)
        if keep_existing and user.avatar is not None:
            return True
        await self.set_user(user, data, "gravatar")
        return True

    def gravatar_later(self, user: User) -> None:
        """Fetch a new account's Gravatar in the background, if enabled."""
        if not self.store.settings.gravatar:
            return

        async def fetch() -> None:
            try:
                found = await self.gravatar(user, keep_existing=True)
            except (OSError, ValueError, http.client.HTTPException) as e:
                log.warning("could not fetch the Gravatar of %s: %s", user.email, e)
                return
            if found:
                log.info("set the avatar of %s from Gravatar", user.email)

        task = asyncio.get_running_loop().create_task(fetch())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def flush(self) -> None:
        """Wait for background Gravatar fetches (tests, and shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
