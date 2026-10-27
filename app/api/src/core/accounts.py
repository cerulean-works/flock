import asyncio
import hashlib
import logging
import secrets
import string
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

import jwt
import orjson
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.api.src.core.errors import Conflict, Forbidden, NotFound, TooManyAttempts

if TYPE_CHECKING:
    import asyncpg

    from app.api.src.core.store import Org, Store

log = logging.getLogger(__name__)


@dataclass(eq=False)
class User:
    pk: int
    email: str
    name: str
    password_hash: str | None
    google_sub: str | None
    is_superadmin: bool
    # The org wandb.init logs to when no entity is given (an org pk), if the user chose one.
    default_org: int | None
    created_at: float
    roles: dict[int, str] = field(default_factory=dict)  # org pk -> "admin" or "member"
    # The digest of the avatar image (see Avatars), and "gravatar" or "upload".
    avatar: bytes | None = None
    avatar_source: str | None = None


@dataclass(eq=False)
class ApiKey:
    pk: int
    user: int
    name: str
    prefix: str  # the key's first characters, to tell keys apart in listings
    created_at: float


@dataclass(eq=False)
class Session:
    """A sign-in. A web session's refresh token (stored hashed) mints access tokens until it
    expires; a CLI session's token (``flock login``) is sent as is, and each use extends it."""

    pk: int
    user: int
    expires_at: float
    kind: str = "web"  # or "cli"
    name: str | None = None  # a CLI session's machine (its hostname)
    created_at: float = 0.0
    last_used_at: float | None = None


@dataclass(eq=False)
class DeviceGrant:
    """A pending ``flock login`` (RFC 8628's device authorization grant): the CLI polls with
    its device code while the user approves ``user_code`` in a signed-in browser."""

    digest: bytes  # of the device code
    user_code: str  # 8 letters, without the dash it is shown with
    hostname: str
    platform: str
    ip: str | None
    created_at: float
    expires_at: float
    interval: float  # seconds the CLI waits between polls
    polled_at: float = 0.0
    user: int | None = None  # who approved it
    denied: bool = False


@dataclass(eq=False)
class Invite:
    pk: int
    org: int
    email: str
    role: str
    created_by: int | None
    created_at: float
    expires_at: float


class Accounts:
    """Users, their memberships, wandb keys and sessions, invites and password resets, and the
    short-lived state of signing in (Google's one-time codes, ``flock login``'s device grants,
    wrong-code counts).

    Everything is read from PostgreSQL (D84). What every request authenticates with (a key or
    token digest's user, a session, a user's roles) is cached for ``TTL`` seconds per replica,
    and dropped whenever ``flock_auth`` announces a change to users, memberships, keys,
    sessions or orgs (triggers in migration 0013). While the bus isn't listening nothing is
    cached, so a revoked session or a changed role applies at once (D19, D47). Nothing is
    cached as missing, so new keys and sessions work at once.
    """

    ISSUER: ClassVar[str] = "flock"
    CHANNEL: ClassVar[str] = "flock_auth"
    TTL: ClassVar[float] = 30.0
    hasher: ClassVar[PasswordHasher] = PasswordHasher()  # argon2id
    USER_COLUMNS: ClassVar[str] = (
        "u.pk, u.email, u.name, u.password_hash, u.google_sub, u.is_superadmin, "
        "u.default_org_pk, extract(epoch FROM u.created_at)::float8, u.avatar, u.avatar_source, "
        "(SELECT coalesce(jsonb_object_agg(m.org_pk, m.role), '{}') FROM memberships m "
        "JOIN orgs o ON o.pk = m.org_pk AND o.deleted_at IS NULL WHERE m.user_pk = u.pk)::text"
    )
    SESSION_COLUMNS: ClassVar[str] = (
        "pk, user_pk, extract(epoch FROM expires_at)::float8, kind, name, "
        "extract(epoch FROM created_at)::float8, extract(epoch FROM last_used_at)::float8"
    )
    INVITE_COLUMNS: ClassVar[str] = (
        "pk, org_pk, email, role, created_by, extract(epoch FROM created_at)::float8, "
        "extract(epoch FROM expires_at)::float8"
    )
    GRANT_COLUMNS: ClassVar[str] = (
        "digest, user_code, hostname, platform, ip, extract(epoch FROM created_at)::float8, "
        "extract(epoch FROM expires_at)::float8, poll_interval, "
        "coalesce(extract(epoch FROM polled_at)::float8, 0), user_pk, denied"
    )
    # Serializes signing up (only the first account becomes super admin) and changes to who
    # is a super admin (one is always left).
    SUPERADMIN_LOCK: ClassVar[int] = 0x7472_6163_6B65_75  # Stable lock key; keep across releases.
    # How long a one-time Google sign-in code (exchanged for tokens by the UI) stays valid.
    CODE_TTL: ClassVar[float] = 60.0
    CLI_TOKEN_PREFIX: ClassVar[str] = "flock_cli_"
    WANDB_KEY_PREFIX: ClassVar[str] = "flock-wandb-"
    # A CLI session's use is written to the database at most this often (seconds).
    CLI_WRITE_INTERVAL: ClassVar[float] = 3600.0
    # Device sign-ins (RFC 8628): user codes use GitHub's alphabet, without vowels or
    # lookalikes. Pending sign-ins are capped so the public start route can't grow the table.
    USER_CODE_ALPHABET: ClassVar[str] = "BCDFGHJKLMNPQRSTVWXZ"
    DEVICE_INTERVAL: ClassVar[float] = 5.0
    MAX_DEVICE_GRANTS: ClassVar[int] = 1000
    # Wrong user codes a user may type per window before being refused (429).
    WRONG_CODES: ClassVar[int] = 10
    WRONG_CODE_WINDOW: ClassVar[float] = 600.0

    def __init__(self, store: Store) -> None:
        self.store = store
        self.secret = b""
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._generation = 0
        # CLI sessions used on this replica: pk -> (last use, expiry), and when their use was
        # last written to the database.
        self._used: dict[int, tuple[float, float]] = {}
        self._written: dict[int, float] = {}
        self._tasks: set[asyncio.Task] = set()
        # Verified against when a login names an unknown email, so it takes as long as any.
        self._dummy_hash = ""
        store.bus.listen(self.CHANNEL, self.invalidate)
        store.bus.on_reset(self.invalidate)

    @staticmethod
    def digest(secret: str) -> bytes:
        return hashlib.sha256(secret.encode()).digest()

    async def load(self) -> None:
        secret = self.store.settings.jwt_secret
        if not secret:
            secret = await self.store.db.fetchval("SELECT value FROM meta WHERE key = 'jwt_secret'")
        self.secret = secret.encode()
        self._dummy_hash = await asyncio.to_thread(self.hasher.hash, secrets.token_hex(16))

    async def flush(self) -> None:
        """Wait for writes of CLI sessions' use (tests, and shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def sweep(self) -> None:
        """Delete expired sessions, sign-in codes, device grants and wrong-code counts (D81:
        idempotent, so every replica runs it)."""
        db = self.store.db
        await db.execute("DELETE FROM sessions WHERE expires_at < now()")
        await db.execute("DELETE FROM auth_codes WHERE expires_at < now()")
        await db.execute("DELETE FROM device_grants WHERE expires_at < now()")
        await db.execute(
            "DELETE FROM auth_failures WHERE at < now() - make_interval(secs => $1)",
            self.WRONG_CODE_WINDOW,
        )

    # ------------------------------------------------------------------ the cache (D84)

    def invalidate(self, *_: object) -> None:
        self._generation += 1
        self._cache.clear()

    async def _cached(self, key: tuple, load: Callable[[], Awaitable[Any]]) -> Any:
        hit = self._cache.get(key)
        now = time.monotonic()
        if hit is not None and hit[0] > now:
            return hit[1]
        generation = self._generation
        value = await load()
        # Not if a notification arrived while loading: the value may predate the change.
        if value is not None and self.store.bus.listening and generation == self._generation:
            self._cache[key] = (now + self.TTL, value)
        return value

    # ------------------------------------------------------------------ lookups

    @staticmethod
    def _user(record: asyncpg.Record) -> User:
        *fields, avatar, avatar_source, roles = record
        user = User(*fields, avatar=bytes(avatar) if avatar else None)
        user.avatar_source = avatar_source
        user.roles = {int(org): role for org, role in orjson.loads(roles).items()}
        return user

    async def _users(self, where: str, *args: Any) -> list[User]:
        records = await self.store.db.fetch(
            f"SELECT {self.USER_COLUMNS} FROM users u WHERE {where} ORDER BY u.pk", *args
        )
        return [self._user(record) for record in records]

    async def user(self, pk: int | None) -> User | None:
        """The user ``pk`` (with their roles), or None."""
        if pk is None:
            return None

        async def load() -> User | None:
            found = await self._users("u.pk = $1", pk)
            return found[0] if found else None

        return await self._cached(("user", pk), load)

    async def user_by_email(self, email: str) -> User | None:
        found = await self._users("u.email = $1", email.lower())
        return found[0] if found else None

    async def users(self) -> list[User]:
        """Every account, oldest first."""
        return await self._users("true")

    def role(self, user: User, org: Org) -> str | None:
        """``"admin"``, ``"member"``, or None for users outside the org."""
        return "admin" if user.is_superadmin else user.roles.get(org.pk)

    def default_org(self, user: User) -> Org | None:
        orgs = self.store.orgs_by_pk
        org = orgs.get(user.default_org) if user.default_org is not None else None
        if org is None or self.role(user, org) is None:
            org = orgs.get(min(user.roles)) if user.roles else None  # the first org joined
        return org

    async def members(self, org: Org) -> list[tuple[User, str]]:
        users = await self._users(
            "EXISTS (SELECT FROM memberships m WHERE m.user_pk = u.pk AND m.org_pk = $1)", org.pk
        )
        return [(user, user.roles[org.pk]) for user in users if org.pk in user.roles]

    async def info(self, user: User) -> dict[str, Any]:
        orgs = await self.store.db.fetch(
            "SELECT pk, name, display_name FROM orgs WHERE pk = ANY($1) AND deleted_at IS NULL",
            list(user.roles),
        )
        default = self.default_org(user)
        return {
            "id": user.pk,
            "email": user.email,
            "name": user.name,
            "is_superadmin": user.is_superadmin,
            "has_password": user.password_hash is not None,
            "google": user.google_sub is not None,
            "default_org": default.name if default else None,
            "orgs": [
                {"name": name, "display_name": display_name, "role": user.roles[pk]}
                for pk, name, display_name in sorted(
                    orgs, key=lambda org: (org[2].casefold(), org[1])
                )
            ],
            "created_at": user.created_at,
            "avatar_url": self.store.avatars.url(user.avatar),
            "avatar_source": user.avatar_source,
        }

    def brief(self, user: User) -> dict[str, Any]:
        """A user as shown next to things they did (feed items, run owners, members)."""
        return {
            "id": user.pk,
            "name": user.name,
            "email": user.email,
            "avatar_url": self.store.avatars.url(user.avatar),
        }

    async def briefs(self, pks: Iterable[int | None]) -> dict[int, dict[str, Any]]:
        """``brief`` of each user of ``pks`` that exists, by pk."""
        wanted = list({pk for pk in pks if pk is not None})
        if not wanted:
            return {}
        records = await self.store.db.fetch(
            "SELECT pk, name, email, avatar FROM users WHERE pk = ANY($1)", wanted
        )
        return {
            pk: {
                "id": pk,
                "name": name,
                "email": email,
                "avatar_url": self.store.avatars.url(bytes(avatar) if avatar else None),
            }
            for pk, name, email, avatar in records
        }

    def can_reset_password(self, actor: User, user: User) -> bool:
        """Super admins reset anyone's password; org admins reset members of their orgs, except
        super admins (that would let an org admin take over the whole server)."""
        if actor.is_superadmin:
            return True
        return not user.is_superadmin and any(actor.roles.get(org) == "admin" for org in user.roles)

    # ------------------------------------------------------------------ credentials

    async def api_key(self, key: str) -> ApiKey | None:
        digest = self.digest(key)

        async def load() -> ApiKey | None:
            record = await self.store.db.fetchrow(
                "SELECT pk, user_pk, name, prefix, extract(epoch FROM created_at)::float8 "
                "FROM api_keys WHERE hash = $1",
                digest,
            )
            return ApiKey(*record) if record is not None else None

        return await self._cached(("key", digest), load)

    async def _session(self, where: str, value: Any) -> Session | None:
        record = await self.store.db.fetchrow(
            f"SELECT {self.SESSION_COLUMNS} FROM sessions WHERE {where}", value
        )
        return Session(*record) if record is not None else None

    async def session_for_token(self, token: str) -> Session | None:
        """The live session an access token belongs to, or None for invalid or ended ones."""
        try:
            claims = jwt.decode(
                token,
                self.secret,
                algorithms=["HS256"],
                issuer=self.ISSUER,
                options={"require": ["exp", "iat", "sub", "sid"]},
            )
        except jwt.InvalidTokenError:
            return None
        if claims.get("typ") != "access" or not isinstance(claims["sid"], int):
            return None
        sid = claims["sid"]
        session = await self._cached(("session", sid), lambda: self._session("pk = $1", sid))
        if (
            session is None
            or str(session.user) != claims["sub"]
            or await self.user(session.user) is None
        ):
            return None
        return session

    async def hash_password(self, password: str) -> str:
        # argon2 takes tens of ms of CPU by design: keep it off the event loop.
        return await asyncio.to_thread(self.hasher.hash, password)

    async def check_password(self, user: User | None, password: str) -> bool:
        stored = user.password_hash if user is not None else None
        try:
            await asyncio.to_thread(self.hasher.verify, stored or self._dummy_hash, password)
        except VerificationError, InvalidHashError:
            return False
        return stored is not None

    async def login(self, email: str, password: str) -> User | None:
        user = await self.user_by_email(email)
        return user if await self.check_password(user, password) else None

    async def start_session(self, user: User) -> dict[str, Any]:
        refresh = secrets.token_urlsafe(32)
        expires_at = time.time() + self.store.settings.refresh_token_ttl
        pk = await self.store.db.fetchval(
            "INSERT INTO sessions (user_pk, refresh_hash, created_at, expires_at) "
            "VALUES ($1, $2, now(), to_timestamp($3)) RETURNING pk",
            user.pk,
            self.digest(refresh),
            expires_at,
        )
        return self._tokens(Session(pk, user.pk, expires_at), refresh)

    def _tokens(self, session: Session, refresh: str) -> dict[str, Any]:
        now = int(time.time())
        ttl = int(self.store.settings.access_token_ttl)
        claims = {
            "iss": self.ISSUER,
            "sub": str(session.user),
            "sid": session.pk,
            "typ": "access",
            "iat": now,
            "exp": now + ttl,
        }
        return {
            "access_token": jwt.encode(claims, self.secret, algorithm="HS256"),
            "token_type": "bearer",
            "expires_in": ttl,
            "refresh_token": refresh,
            "refresh_expires_at": session.expires_at,
        }

    async def refresh(self, refresh: str) -> dict[str, Any] | None:
        """New tokens for a refresh token, which is replaced (each one works once). CLI tokens
        are refused: they never rotate."""
        new = secrets.token_urlsafe(32)
        expires_at = time.time() + self.store.settings.refresh_token_ttl
        record = await self.store.db.fetchrow(
            "UPDATE sessions SET refresh_hash = $2, expires_at = to_timestamp($3) "
            "WHERE refresh_hash = $1 AND kind = 'web' AND expires_at > now() "
            "RETURNING pk, user_pk",
            self.digest(refresh),
            self.digest(new),
            expires_at,
        )
        if record is None or await self.user(record[1]) is None:
            return None
        return self._tokens(Session(record[0], record[1], expires_at), new)

    async def end_session(self, session: Session) -> None:
        await self.store.db.execute("DELETE FROM sessions WHERE pk = $1", session.pk)

    async def end_sessions(self, user: User, keep: Session | None = None) -> None:
        """Sign ``user`` out everywhere (except ``keep``), CLI sessions included; their access
        tokens and CLI tokens stop working."""
        kept = keep.pk if keep is not None else None
        await self.store.db.execute(
            "DELETE FROM sessions WHERE user_pk = $1 AND pk IS DISTINCT FROM $2", user.pk, kept
        )

    async def issue_code(self, user: User) -> str:
        """A one-time code standing for a completed Google sign-in (see ``redeem_code``)."""
        code = secrets.token_urlsafe(32)
        await self.store.db.execute(
            "INSERT INTO auth_codes (digest, user_pk, expires_at) "
            "VALUES ($1, $2, now() + make_interval(secs => $3))",
            self.digest(code),
            user.pk,
            self.CODE_TTL,
        )
        return code

    async def redeem_code(self, code: str) -> User | None:
        pk = await self.store.db.fetchval(
            "DELETE FROM auth_codes WHERE digest = $1 AND expires_at > now() RETURNING user_pk",
            self.digest(code),
        )
        return await self.user(pk)

    # ------------------------------------------------------------------ CLI sessions

    async def start_cli_session(self, user: User, name: str) -> tuple[Session, str]:
        """A new CLI session on the machine ``name``; its token is returned only here."""
        token = f"{self.CLI_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
        expires_at = time.time() + self.store.settings.cli_session_ttl
        record = await self.store.db.fetchrow(
            "INSERT INTO sessions (user_pk, refresh_hash, created_at, expires_at, kind, name) "
            "VALUES ($1, $2, now(), to_timestamp($3), 'cli', $4) "
            "RETURNING pk, extract(epoch FROM created_at)::float8",
            user.pk,
            self.digest(token),
            expires_at,
            name,
        )
        assert record is not None
        pk, created_at = record
        return Session(pk, user.pk, expires_at, "cli", name, created_at), token

    async def session_for_cli_token(self, token: str) -> Session | None:
        """The live CLI session ``token`` belongs to, or None. Each use extends the session by
        ``cli_session_ttl``; the database hears of it at most every ``CLI_WRITE_INTERVAL``, so
        until then only this replica knows of the use (and lists it)."""
        digest = self.digest(token)
        session = await self._cached(
            ("cli", digest), lambda: self._session("refresh_hash = $1 AND kind = 'cli'", digest)
        )
        if session is None or await self.user(session.user) is None:
            return None
        now = time.time()
        _, extended = self._used.get(session.pk, (None, 0.0))
        if max(session.expires_at, extended) <= now:  # idle too long; the sweep deletes it
            return None
        expires_at = now + self.store.settings.cli_session_ttl
        self._used[session.pk] = (now, expires_at)
        written = self._written.setdefault(session.pk, session.last_used_at or session.created_at)
        if now - written >= self.CLI_WRITE_INTERVAL:
            self._written[session.pk] = now
            task = asyncio.get_running_loop().create_task(self._write_use(session.pk))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return session

    async def _write_use(self, pk: int) -> None:
        last_used_at, expires_at = self._used[pk]
        try:
            await self.store.db.execute(
                "UPDATE sessions SET last_used_at = to_timestamp($2), "
                "expires_at = greatest(expires_at, to_timestamp($3)) WHERE pk = $1",
                pk,
                last_used_at,
                expires_at,
            )
        except Exception:
            log.exception("could not record the use of CLI session %s", pk)

    async def cli_sessions(self, user: User) -> list[Session]:
        """``user``'s live CLI sessions, with this replica's record of their use."""
        records = await self.store.db.fetch(
            f"SELECT {self.SESSION_COLUMNS} FROM sessions "
            "WHERE user_pk = $1 AND kind = 'cli' ORDER BY pk",
            user.pk,
        )
        now = time.time()
        sessions = []
        for record in records:
            session = Session(*record)
            used = self._used.get(session.pk)
            if used is not None:
                session.last_used_at = max(session.last_used_at or 0.0, used[0])
                session.expires_at = max(session.expires_at, used[1])
            if session.expires_at > now:
                sessions.append(session)
        return sessions

    async def end_cli_session(self, user: User, pk: int) -> None:
        deleted = await self.store.db.fetchval(
            "DELETE FROM sessions WHERE pk = $1 AND kind = 'cli' AND user_pk = $2 RETURNING pk",
            pk,
            user.pk,
        )
        if deleted is None:
            raise NotFound(f"command line sign-in {pk} not found")

    # ------------------------------------------------------------------ device sign-in

    @staticmethod
    def normalize_user_code(code: str) -> str:
        """A user code as typed (any case, with or without the dash) in its stored form."""
        return "".join(ch for ch in code.upper() if ch.isalnum())

    async def start_device(
        self, hostname: str, platform: str, ip: str | None
    ) -> tuple[DeviceGrant, str] | None:
        """A new pending sign-in and its device code, or None when too many are pending."""
        db = self.store.db
        await db.execute("DELETE FROM device_grants WHERE expires_at <= now()")
        if await db.fetchval("SELECT count(*) FROM device_grants") >= self.MAX_DEVICE_GRANTS:
            return None
        device_code = secrets.token_urlsafe(32)
        record = None
        while record is None:  # until the user code is one no pending sign-in has
            user_code = "".join(secrets.choice(self.USER_CODE_ALPHABET) for _ in range(8))
            record = await db.fetchrow(
                "INSERT INTO device_grants (digest, user_code, hostname, platform, ip, "
                "created_at, expires_at, poll_interval) VALUES ($1, $2, $3, $4, $5, now(), "
                "now() + make_interval(secs => $6), $7) ON CONFLICT (user_code) DO NOTHING "
                f"RETURNING {self.GRANT_COLUMNS}",
                self.digest(device_code),
                user_code,
                hostname,
                platform,
                ip,
                self.store.settings.device_code_ttl,
                self.DEVICE_INTERVAL,
            )
        return self._grant(record), device_code

    @staticmethod
    def _grant(record: asyncpg.Record) -> DeviceGrant:
        digest, *fields = record
        return DeviceGrant(bytes(digest), *fields)

    async def poll_device(self, device_code: str) -> str | tuple[User, str]:
        """What the CLI's poll gets: an RFC 8628 error code (``authorization_pending``,
        ``slow_down``, ``access_denied`` or ``expired_token``), or once approved, the user and
        a new CLI token. Any answer but the first two uses the sign-in up."""
        digest = self.digest(device_code)
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            record = await conn.fetchrow(
                f"SELECT {self.GRANT_COLUMNS} FROM device_grants WHERE digest = $1 FOR UPDATE",
                digest,
            )
            grant = self._grant(record) if record is not None else None
            now = time.time()
            if grant is None or grant.expires_at <= now or grant.denied:
                await conn.execute("DELETE FROM device_grants WHERE digest = $1", digest)
                return "access_denied" if grant is not None and grant.denied else "expired_token"
            user = await self.user(grant.user)
            if user is None:
                # Polling sooner than asked slows the CLI down; some slack absorbs network
                # jitter.
                too_soon = now - grant.polled_at < grant.interval * 0.8
                await conn.execute(
                    "UPDATE device_grants SET polled_at = to_timestamp($2), "
                    "poll_interval = poll_interval + $3 WHERE digest = $1",
                    digest,
                    now,
                    5.0 if too_soon else 0.0,
                )
                return "slow_down" if too_soon else "authorization_pending"
            # Deleted before the token is made: of concurrent polls, one gets the token.
            await conn.execute("DELETE FROM device_grants WHERE digest = $1", digest)
        _, token = await self.start_cli_session(user, grant.hostname)
        return user, token

    async def device_grant(self, user: User, user_code: str) -> DeviceGrant | None:
        """The undecided sign-in ``user_code`` names, as ``user`` typed it. Each miss counts
        against the user, who gets ``TooManyAttempts`` after ``WRONG_CODES`` misses within
        ``WRONG_CODE_WINDOW`` seconds (on any replica)."""
        db = self.store.db
        misses = await db.fetchval(
            "SELECT count(*) FROM auth_failures "
            "WHERE user_pk = $1 AND at > now() - make_interval(secs => $2)",
            user.pk,
            self.WRONG_CODE_WINDOW,
        )
        if misses >= self.WRONG_CODES:
            raise TooManyAttempts("too many wrong codes: wait a few minutes and try again")
        record = await db.fetchrow(
            f"SELECT {self.GRANT_COLUMNS} FROM device_grants WHERE user_code = $1 "
            "AND expires_at > now() AND user_pk IS NULL AND NOT denied",
            self.normalize_user_code(user_code),
        )
        if record is None:
            await db.execute("INSERT INTO auth_failures (user_pk, at) VALUES ($1, now())", user.pk)
            return None
        return self._grant(record)

    async def decide_device(self, user: User, grant: DeviceGrant, approve: bool) -> None:
        """Approve the sign-in as ``user`` (the CLI's next poll gets a token), or deny it."""
        await self.store.db.execute(
            "UPDATE device_grants SET user_pk = $2, denied = $3 WHERE digest = $1",
            grant.digest,
            user.pk if approve else None,
            not approve,
        )

    # ------------------------------------------------------------------ wandb keys

    async def api_keys(self, user: User) -> list[ApiKey]:
        records = await self.store.db.fetch(
            "SELECT pk, user_pk, name, prefix, extract(epoch FROM created_at)::float8 "
            "FROM api_keys WHERE user_pk = $1 ORDER BY pk",
            user.pk,
        )
        return [ApiKey(*record) for record in records]

    async def create_api_key(self, user: User, name: str) -> tuple[ApiKey, str]:
        """A new wandb key for ``user`` (D71); the key itself is returned only here."""
        # The wandb SDK wants 40+ word characters after a key's first "-" (older SDKs: exactly
        # 40 after its last), so 40 random letters and digits follow the prefix.
        alphabet = string.ascii_letters + string.digits
        key = self.WANDB_KEY_PREFIX + "".join(secrets.choice(alphabet) for _ in range(40))
        record = await self.store.db.fetchrow(
            "INSERT INTO api_keys (user_pk, name, prefix, hash, created_at) "
            "VALUES ($1, $2, $3, $4, now()) RETURNING pk, extract(epoch FROM created_at)::float8",
            user.pk,
            name,
            key[:12],
            self.digest(key),
        )
        assert record is not None
        return ApiKey(record[0], user.pk, name, key[:12], record[1]), key

    async def delete_api_key(self, user: User, pk: int) -> None:
        deleted = await self.store.db.fetchval(
            "DELETE FROM api_keys WHERE pk = $1 AND user_pk = $2 RETURNING pk", pk, user.pk
        )
        if deleted is None:
            raise NotFound(f"wandb key {pk} not found")

    # ------------------------------------------------------------------ users

    async def signup_open(self) -> bool:
        """Only the first account signs up freely; everyone else needs an invite."""
        return not await self.store.db.fetchval("SELECT EXISTS (SELECT FROM users)")

    async def _insert_user(
        self,
        conn: asyncpg.Connection,
        email: str,
        name: str,
        password_hash: str | None = None,
        google_sub: str | None = None,
        is_superadmin: bool = False,
    ) -> User:
        pk, created_at = await conn.fetchrow(
            "INSERT INTO users (email, name, password_hash, google_sub, is_superadmin, "
            "created_at) VALUES ($1, $2, $3, $4, $5, now()) "
            "RETURNING pk, extract(epoch FROM created_at)::float8",
            email.lower(),
            name,
            password_hash,
            google_sub,
            is_superadmin,
        )
        return User(
            pk, email.lower(), name, password_hash, google_sub, is_superadmin, None, created_at
        )

    async def signup(
        self,
        email: str,
        name: str,
        password: str | None = None,
        google_sub: str | None = None,
    ) -> User:
        """Create the first account: the super admin, an admin of every existing org.
        On a fresh server, setup asks them to name and create the first organization next."""
        password_hash = await self.hash_password(password) if password is not None else None
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", self.SUPERADMIN_LOCK)
            if await conn.fetchval("SELECT EXISTS (SELECT FROM users)"):
                raise Forbidden("signup is closed: ask an admin for an invitation")
            user = await self._insert_user(conn, email, name, password_hash, google_sub, True)
            orgs = await conn.fetch(
                "INSERT INTO memberships (user_pk, org_pk, role, created_at) "
                "SELECT $1, pk, 'admin', now() FROM orgs WHERE deleted_at IS NULL "
                "RETURNING org_pk",
                user.pk,
            )
        user.roles = {org: "admin" for (org,) in orgs}
        self.store.avatars.gravatar_later(user)
        return user

    async def update_user(self, user: User, fields: dict[str, Any]) -> None:
        """Change ``name``, ``default_org`` (an Org, or None) and/or ``is_superadmin``."""
        name = fields.get("name", user.name)
        default = user.default_org
        if "default_org" in fields:
            default = fields["default_org"].pk if fields["default_org"] is not None else None
        superadmin = fields.get("is_superadmin", user.is_superadmin)
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            if user.is_superadmin and not superadmin:
                await conn.execute("SELECT pg_advisory_xact_lock($1)", self.SUPERADMIN_LOCK)
                others = await conn.fetchval(
                    "SELECT count(*) FROM users WHERE is_superadmin AND pk <> $1", user.pk
                )
                if not others:
                    raise Conflict("the server needs a super admin")
            await conn.execute(
                "UPDATE users SET name = $2, default_org_pk = $3, is_superadmin = $4 WHERE pk = $1",
                user.pk,
                name,
                default,
                superadmin,
            )
        user.name, user.default_org, user.is_superadmin = name, default, superadmin

    async def set_password(self, user: User, password: str, keep: Session | None = None) -> None:
        """Set a password and end the user's other sessions."""
        password_hash = await self.hash_password(password)
        await self.store.db.execute(
            "UPDATE users SET password_hash = $2 WHERE pk = $1", user.pk, password_hash
        )
        user.password_hash = password_hash
        await self.end_sessions(user, keep)

    async def delete_user(self, user: User) -> None:
        """Delete an account; its runs stay, without an owner."""
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            if user.is_superadmin:
                await conn.execute("SELECT pg_advisory_xact_lock($1)", self.SUPERADMIN_LOCK)
                others = await conn.fetchval(
                    "SELECT count(*) FROM users WHERE is_superadmin AND pk <> $1", user.pk
                )
                if not others:
                    raise Conflict("the server needs a super admin")
            # Its runs lose their owner (the foreign key sets it to NULL): so does the run table.
            await conn.execute(
                """
                WITH gone AS (
                    DELETE FROM run_values v USING runs r
                    WHERE r.owner_pk = $1 AND v.run_pk = r.pk AND v.key = 'owner'
                    RETURNING v.project_pk
                )
                INSERT INTO project_key_changes
                    (project_pk, key, int_only, runs, nums, strs, bools, lists, objects)
                SELECT project_pk, 'owner', true, -count(*), -count(*), 0, 0, 0, 0
                FROM gone GROUP BY project_pk
                """,
                user.pk,
            )
            await conn.execute("DELETE FROM users WHERE pk = $1", user.pk)
        self.store.forget_owner(user.pk)
        await self.store.avatars.release(user.avatar)

    async def google_user(
        self, google_sub: str, email: str, name: str, invite: Invite | None = None
    ) -> User:
        """The account for a Google sign-in (Google verified ``email``): found by Google id,
        else linked by email, else created from ``invite`` or as the first account."""
        email = email.lower()
        found = await self._users("u.google_sub = $1", google_sub)
        user = found[0] if found else None
        if user is None and (user := await self.user_by_email(email)) is not None:
            if user.google_sub is not None:
                raise Forbidden(f"{email} is linked to another Google account")
            await self.store.db.execute(
                "UPDATE users SET google_sub = $2 WHERE pk = $1", user.pk, google_sub
            )
            user.google_sub = google_sub
        if user is not None:
            if invite is not None:
                await self.accept_invite(invite, user)
            return user
        if invite is not None:
            if invite.email != email:
                raise Forbidden(f"this invitation is for {invite.email}")
            return await self.register(invite, name, google_sub=google_sub)
        if await self.signup_open():
            return await self.signup(email, name, google_sub=google_sub)
        raise Forbidden(f"there is no account for {email}: ask an admin for an invitation")

    # ------------------------------------------------------------------ memberships

    @staticmethod
    async def _lock_members(conn: asyncpg.Connection, org: Org) -> None:
        """Serialize changes to ``org``'s members, so it always keeps an admin."""
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('members:' || $1::bigint, 0))", org.pk
        )

    async def add_member(self, user: User, org: Org, role: str) -> None:
        added = await self.store.db.fetchval(
            "INSERT INTO memberships (user_pk, org_pk, role, created_at) "
            "VALUES ($1, $2, $3, now()) ON CONFLICT DO NOTHING RETURNING org_pk",
            user.pk,
            org.pk,
            role,
        )
        if added is None:
            raise Conflict(f"{user.email} is already a member of {org.name}")
        user.roles[org.pk] = role

    @staticmethod
    async def _check_admin_remains(conn: asyncpg.Connection, user: User, org: Org) -> None:
        others = await conn.fetchval(
            "SELECT count(*) FROM memberships WHERE org_pk = $1 AND role = 'admin' "
            "AND user_pk <> $2",
            org.pk,
            user.pk,
        )
        if not others and user.roles.get(org.pk) == "admin":
            raise Conflict(f"{org.name} needs an admin: make someone else admin first")

    async def set_role(self, user: User, org: Org, role: str) -> None:
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await self._lock_members(conn, org)
            if org.pk not in user.roles:
                raise NotFound(f"{user.email} is not a member of {org.name}")
            if role != "admin":
                await self._check_admin_remains(conn, user, org)
            await conn.execute(
                "UPDATE memberships SET role = $3 WHERE user_pk = $1 AND org_pk = $2",
                user.pk,
                org.pk,
                role,
            )
        user.roles[org.pk] = role

    async def remove_member(self, user: User, org: Org) -> None:
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await self._lock_members(conn, org)
            if org.pk not in user.roles:
                raise NotFound(f"{user.email} is not a member of {org.name}")
            await self._check_admin_remains(conn, user, org)
            await conn.execute(
                "DELETE FROM memberships WHERE user_pk = $1 AND org_pk = $2", user.pk, org.pk
            )
        del user.roles[org.pk]

    # ------------------------------------------------------------------ invites

    async def create_invite(self, org: Org, email: str, role: str, by: User) -> tuple[Invite, str]:
        """An invite to ``org``; the token for its link is returned only here."""
        email = email.lower()
        user = await self.user_by_email(email)
        if user is not None and org.pk in user.roles:
            raise Conflict(f"{email} is already a member of {org.name}")
        token = secrets.token_urlsafe(32)
        record = await self.store.db.fetchrow(
            "INSERT INTO invites (org_pk, email, role, token_hash, created_by, created_at, "
            f"expires_at) VALUES ($1, $2, $3, $4, $5, now(), now() + make_interval(secs => $6)) "
            f"RETURNING {self.INVITE_COLUMNS}",
            org.pk,
            email,
            role,
            self.digest(token),
            by.pk,
            self.store.settings.invite_ttl,
        )
        assert record is not None
        return Invite(*record), token

    async def invites(self, org: Org) -> list[Invite]:
        """``org``'s invites that are still waiting to be accepted."""
        return [
            Invite(*record)
            for record in await self.store.db.fetch(
                f"SELECT {self.INVITE_COLUMNS} FROM invites "
                "WHERE org_pk = $1 AND expires_at > now() ORDER BY pk",
                org.pk,
            )
        ]

    async def find_invite(self, token: str) -> Invite | None:
        record = await self.store.db.fetchrow(
            f"SELECT {self.INVITE_COLUMNS} FROM invites "
            "WHERE token_hash = $1 AND expires_at > now()",
            self.digest(token),
        )
        return Invite(*record) if record is not None else None

    async def delete_invite(self, org: Org, pk: int) -> None:
        deleted = await self.store.db.fetchval(
            "DELETE FROM invites WHERE pk = $1 AND org_pk = $2 RETURNING pk", pk, org.pk
        )
        if deleted is None:
            raise NotFound(f"invite {pk} not found")

    @staticmethod
    async def _claim(conn: asyncpg.Connection, invite: Invite) -> None:
        """Use up an invite (so it can't be accepted twice), if its org still exists."""
        claimed = await conn.fetchval(
            "DELETE FROM invites WHERE pk = $1 "
            "AND EXISTS (SELECT FROM orgs WHERE pk = $2 AND deleted_at IS NULL) RETURNING pk",
            invite.pk,
            invite.org,
        )
        if claimed is None:
            raise NotFound("this invitation was already used or withdrawn")

    async def accept_invite(self, invite: Invite, user: User) -> None:
        """Add an existing account to the invite's org (the account must have its email)."""
        if user.email != invite.email:
            raise Forbidden(f"this invitation is for {invite.email}")
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await self._claim(conn, invite)
            await conn.execute(
                "INSERT INTO memberships (user_pk, org_pk, role, created_at) "
                "VALUES ($1, $2, $3, now()) ON CONFLICT DO NOTHING",
                user.pk,
                invite.org,
                invite.role,
            )
        user.roles.setdefault(invite.org, invite.role)

    async def register(
        self,
        invite: Invite,
        name: str,
        password: str | None = None,
        google_sub: str | None = None,
    ) -> User:
        """Create the account an invite is for, as a member of its org."""
        if await self.user_by_email(invite.email) is not None:
            raise Conflict("an account with this email exists: sign in to accept the invitation")
        password_hash = await self.hash_password(password) if password is not None else None
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            await self._claim(conn, invite)
            user = await self._insert_user(conn, invite.email, name, password_hash, google_sub)
            await conn.execute(
                "INSERT INTO memberships (user_pk, org_pk, role, created_at) "
                "VALUES ($1, $2, $3, now())",
                user.pk,
                invite.org,
                invite.role,
            )
        user.roles[invite.org] = invite.role
        self.store.avatars.gravatar_later(user)
        return user

    # ------------------------------------------------------------------ password resets

    async def create_reset(self, user: User, by: User) -> tuple[str, float]:
        """A one-time password reset for ``user``: its token and expiry time."""
        token = secrets.token_urlsafe(32)
        expires_at = await self.store.db.fetchval(
            "INSERT INTO password_resets (user_pk, token_hash, created_by, created_at, "
            "expires_at) VALUES ($1, $2, $3, now(), now() + make_interval(secs => $4)) "
            "RETURNING extract(epoch FROM expires_at)::float8",
            user.pk,
            self.digest(token),
            by.pk,
            self.store.settings.reset_ttl,
        )
        return token, expires_at

    async def find_reset(self, token: str) -> User | None:
        pk = await self.store.db.fetchval(
            "SELECT user_pk FROM password_resets WHERE token_hash = $1 AND expires_at > now()",
            self.digest(token),
        )
        return await self.user(pk)

    async def redeem_reset(self, token: str, password: str) -> User:
        """Set the password of the reset's user, ending all their sessions."""
        pk = await self.store.db.fetchval(
            "DELETE FROM password_resets WHERE token_hash = $1 AND expires_at > now() "
            "RETURNING user_pk",
            self.digest(token),
        )
        user = await self.user(pk)
        if user is None:
            raise NotFound("this reset link is invalid, expired or already used")
        await self.set_password(user, password)
        return user
