import asyncio
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.api.src.core.errors import Conflict, Forbidden, NotFound

if TYPE_CHECKING:
    import asyncpg

    from app.api.src.core.store import Org, Store


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
    """A sign-in: its refresh token (stored hashed) mints access tokens until it expires."""

    pk: int
    user: int
    expires_at: float


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
    """Users, their memberships, API keys and sessions, invites and password resets.

    Users, memberships, key digests and sessions are small and live in memory (this server is
    the database's only writer, D2), so authenticating a request is a hash or an HMAC and a dict
    lookup. Invites and resets are only read when redeemed, from the database.
    """

    ISSUER: ClassVar[str] = "flock"
    hasher: ClassVar[PasswordHasher] = PasswordHasher()  # argon2id
    INVITE_COLUMNS: ClassVar[str] = (
        "pk, org_pk, email, role, created_by, extract(epoch FROM created_at)::float8, "
        "extract(epoch FROM expires_at)::float8"
    )
    # How long a one-time Google sign-in code (exchanged for tokens by the UI) stays valid.
    CODE_TTL: ClassVar[float] = 60.0

    def __init__(self, store: Store) -> None:
        self.store = store
        self.users: dict[int, User] = {}
        self.by_email: dict[str, User] = {}
        self.by_google: dict[str, User] = {}
        self.keys: dict[bytes, ApiKey] = {}  # by sha256 of the key
        self.sessions: dict[int, Session] = {}
        self.secret = b""
        self._signup = asyncio.Lock()
        self._codes: dict[bytes, tuple[int, float]] = {}  # code digest -> (user pk, expiry)
        # Verified against when a login names an unknown email, so it takes as long as any.
        self._dummy_hash = ""

    @staticmethod
    def digest(secret: str) -> bytes:
        return hashlib.sha256(secret.encode()).digest()

    async def load(self) -> None:
        db = self.store.db
        secret = self.store.settings.jwt_secret
        if not secret:
            secret = await db.fetchval("SELECT value FROM meta WHERE key = 'jwt_secret'")
        self.secret = secret.encode()
        for record in await db.fetch(
            "SELECT pk, email, name, password_hash, google_sub, is_superadmin, default_org_pk, "
            "extract(epoch FROM created_at)::float8, avatar, avatar_source FROM users"
        ):
            *fields, avatar, avatar_source = record
            user = User(*fields, avatar=bytes(avatar) if avatar else None)
            user.avatar_source = avatar_source
            self._add_user(user)
        for user, org, role in await db.fetch("SELECT user_pk, org_pk, role FROM memberships"):
            self.users[user].roles[org] = role
        for pk, user, name, prefix, digest, created_at in await db.fetch(
            "SELECT pk, user_pk, name, prefix, hash, extract(epoch FROM created_at)::float8 "
            "FROM api_keys"
        ):
            self.keys[bytes(digest)] = ApiKey(pk, user, name, prefix, created_at)
        await db.execute("DELETE FROM sessions WHERE expires_at < now()")
        for record in await db.fetch(
            "SELECT pk, user_pk, extract(epoch FROM expires_at)::float8 FROM sessions"
        ):
            self.sessions[record[0]] = Session(*record)
        self._dummy_hash = await asyncio.to_thread(self.hasher.hash, secrets.token_hex(16))

    def _add_user(self, user: User) -> User:
        self.users[user.pk] = user
        self.by_email[user.email] = user
        if user.google_sub:
            self.by_google[user.google_sub] = user
        return user

    # ------------------------------------------------------------------ lookups

    def role(self, user: User, org: Org) -> str | None:
        """``"admin"``, ``"member"``, or None for users outside the org."""
        return "admin" if user.is_superadmin else user.roles.get(org.pk)

    def default_org(self, user: User) -> Org | None:
        orgs = self.store.orgs_by_pk
        org = orgs.get(user.default_org) if user.default_org is not None else None
        if org is None or self.role(user, org) is None:
            org = orgs.get(min(user.roles)) if user.roles else None  # the first org joined
        return org

    def members(self, org: Org) -> list[tuple[User, str]]:
        return [(u, u.roles[org.pk]) for u in self.users.values() if org.pk in u.roles]

    def info(self, user: User) -> dict[str, Any]:
        orgs = self.store.orgs_by_pk
        default = self.default_org(user)
        return {
            "id": user.pk,
            "email": user.email,
            "name": user.name,
            "is_superadmin": user.is_superadmin,
            "has_password": user.password_hash is not None,
            "google": user.google_sub is not None,
            "default_org": default.name if default else None,
            "orgs": sorted(
                ({"name": orgs[pk].name, "role": role} for pk, role in user.roles.items()),
                key=lambda org: org["name"],
            ),
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

    def can_reset_password(self, actor: User, user: User) -> bool:
        """Super admins reset anyone's password; org admins reset members of their orgs, except
        super admins (that would let an org admin take over the whole server)."""
        if actor.is_superadmin:
            return True
        return not user.is_superadmin and any(actor.roles.get(org) == "admin" for org in user.roles)

    # ------------------------------------------------------------------ credentials

    def user_for_key(self, key: str) -> User | None:
        found = self.keys.get(self.digest(key))
        return self.users.get(found.user) if found is not None else None

    def session_for_token(self, token: str) -> Session | None:
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
        session = self.sessions.get(claims["sid"])
        if (
            claims.get("typ") != "access"
            or session is None
            or str(session.user) != claims["sub"]
            or session.user not in self.users
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
        user = self.by_email.get(email.lower())
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
        session = self.sessions[pk] = Session(pk, user.pk, expires_at)
        return self._tokens(session, refresh)

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
        """New tokens for a refresh token, which is replaced (each one works once)."""
        new = secrets.token_urlsafe(32)
        expires_at = time.time() + self.store.settings.refresh_token_ttl
        pk = await self.store.db.fetchval(
            "UPDATE sessions SET refresh_hash = $2, expires_at = to_timestamp($3) "
            "WHERE refresh_hash = $1 AND expires_at > now() RETURNING pk",
            self.digest(refresh),
            self.digest(new),
            expires_at,
        )
        session = self.sessions.get(pk) if pk is not None else None
        if session is None or session.user not in self.users:
            return None
        session.expires_at = expires_at
        return self._tokens(session, new)

    async def end_session(self, session: Session) -> None:
        await self.store.db.execute("DELETE FROM sessions WHERE pk = $1", session.pk)
        self.sessions.pop(session.pk, None)

    async def end_sessions(self, user: User, keep: Session | None = None) -> None:
        """Sign ``user`` out everywhere (except ``keep``); their access tokens stop working."""
        kept = keep.pk if keep is not None else None
        await self.store.db.execute(
            "DELETE FROM sessions WHERE user_pk = $1 AND pk IS DISTINCT FROM $2", user.pk, kept
        )
        for session in [s for s in self.sessions.values() if s.user == user.pk]:
            if session.pk != kept:
                del self.sessions[session.pk]

    def issue_code(self, user: User) -> str:
        """A one-time code standing for a completed Google sign-in (see ``redeem_code``)."""
        now = time.time()
        for digest, (_, expires_at) in list(self._codes.items()):
            if expires_at < now:
                del self._codes[digest]
        code = secrets.token_urlsafe(32)
        self._codes[self.digest(code)] = (user.pk, now + self.CODE_TTL)
        return code

    def redeem_code(self, code: str) -> User | None:
        user, expires_at = self._codes.pop(self.digest(code), (None, 0.0))
        return self.users.get(user) if user is not None and expires_at > time.time() else None

    # ------------------------------------------------------------------ API keys

    def api_keys(self, user: User) -> list[ApiKey]:
        return sorted((k for k in self.keys.values() if k.user == user.pk), key=lambda k: k.pk)

    async def create_api_key(self, user: User, name: str) -> tuple[ApiKey, str]:
        """A new key for ``user``; the key itself is returned only here."""
        # "<prefix>-<40+ word characters>" is the shape the wandb SDK accepts.
        key = f"flock-{secrets.token_hex(24)}"
        digest = self.digest(key)
        record = await self.store.db.fetchrow(
            "INSERT INTO api_keys (user_pk, name, prefix, hash, created_at) "
            "VALUES ($1, $2, $3, $4, now()) RETURNING pk, extract(epoch FROM created_at)::float8",
            user.pk,
            name,
            key[:12],
            digest,
        )
        assert record is not None
        api_key = self.keys[digest] = ApiKey(record[0], user.pk, name, key[:12], record[1])
        return api_key, key

    async def delete_api_key(self, user: User, pk: int) -> None:
        digest = next((d for d, k in self.keys.items() if k.pk == pk and k.user == user.pk), None)
        if digest is None:
            raise NotFound(f"API key {pk} not found")
        await self.store.db.execute("DELETE FROM api_keys WHERE pk = $1", pk)
        del self.keys[digest]

    # ------------------------------------------------------------------ users

    @property
    def signup_open(self) -> bool:
        """Only the first account signs up freely; everyone else needs an invite."""
        return not self.users

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
        create_org: bool = True,
    ) -> User:
        """Create the first account: the super admin, an admin of every org. With no orgs yet,
        the default entity's org is created for it, unless ``create_org`` is false (the
        dashboard's setup then has the super admin name the first org)."""
        password_hash = await self.hash_password(password) if password is not None else None
        async with self._signup:
            if not self.signup_open:
                raise Forbidden("signup is closed: ask an admin for an invitation")
            store = self.store
            orgs = list(store.orgs_by_pk.values())
            if not orgs and create_org:
                orgs = [await store.create_org(store.settings.default_entity)]
            async with store.db.pool.acquire() as conn, conn.transaction():
                user = await self._insert_user(conn, email, name, password_hash, google_sub, True)
                await conn.executemany(
                    "INSERT INTO memberships (user_pk, org_pk, role, created_at) "
                    "VALUES ($1, $2, 'admin', now())",
                    [(user.pk, org.pk) for org in orgs],
                )
            user.roles = {org.pk: "admin" for org in orgs}
            self._add_user(user)
        self.store.avatars.gravatar_later(user)
        return user

    def _last_superadmin(self, user: User) -> bool:
        return [u for u in self.users.values() if u.is_superadmin] == [user]

    async def update_user(self, user: User, fields: dict[str, Any]) -> None:
        """Change ``name``, ``default_org`` (an Org, or None) and/or ``is_superadmin``."""
        name = fields.get("name", user.name)
        default = user.default_org
        if "default_org" in fields:
            default = fields["default_org"].pk if fields["default_org"] is not None else None
        superadmin = fields.get("is_superadmin", user.is_superadmin)
        if user.is_superadmin and not superadmin and self._last_superadmin(user):
            raise Conflict("the server needs a super admin")
        await self.store.db.execute(
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
        if user.is_superadmin and self._last_superadmin(user):
            raise Conflict("the server needs a super admin")
        await self.store.db.execute("DELETE FROM users WHERE pk = $1", user.pk)
        del self.users[user.pk]
        del self.by_email[user.email]
        if user.google_sub:
            del self.by_google[user.google_sub]
        for digest in [d for d, k in self.keys.items() if k.user == user.pk]:
            del self.keys[digest]
        for pk in [pk for pk, s in self.sessions.items() if s.user == user.pk]:
            del self.sessions[pk]
        self.store.forget_owner(user.pk)
        await self.store.avatars.release(user.avatar)

    async def google_user(
        self, google_sub: str, email: str, name: str, invite: Invite | None = None
    ) -> User:
        """The account for a Google sign-in (Google verified ``email``): found by Google id,
        else linked by email, else created from ``invite`` or as the first account."""
        email = email.lower()
        user = self.by_google.get(google_sub)
        if user is None and (user := self.by_email.get(email)) is not None:
            if user.google_sub is not None:
                raise Forbidden(f"{email} is linked to another Google account")
            await self.store.db.execute(
                "UPDATE users SET google_sub = $2 WHERE pk = $1", user.pk, google_sub
            )
            user.google_sub = google_sub
            self.by_google[google_sub] = user
        if user is not None:
            if invite is not None:
                await self.accept_invite(invite, user)
            return user
        if invite is not None:
            if invite.email != email:
                raise Forbidden(f"this invitation is for {invite.email}")
            return await self.register(invite, name, google_sub=google_sub)
        if self.signup_open:
            # Only the dashboard signs in with Google: its setup has the first org named next.
            return await self.signup(email, name, google_sub=google_sub, create_org=False)
        raise Forbidden(f"there is no account for {email}: ask an admin for an invitation")

    # ------------------------------------------------------------------ memberships

    async def add_member(self, user: User, org: Org, role: str) -> None:
        if org.pk in user.roles:
            raise Conflict(f"{user.email} is already a member of {org.name}")
        await self.store.db.execute(
            "INSERT INTO memberships (user_pk, org_pk, role, created_at) "
            "VALUES ($1, $2, $3, now())",
            user.pk,
            org.pk,
            role,
        )
        user.roles[org.pk] = role

    def _check_admin_remains(self, user: User, org: Org) -> None:
        admins = [u for u, role in self.members(org) if role == "admin"]
        if admins == [user]:
            raise Conflict(f"{org.name} needs an admin: make someone else admin first")

    async def set_role(self, user: User, org: Org, role: str) -> None:
        if org.pk not in user.roles:
            raise NotFound(f"{user.email} is not a member of {org.name}")
        if role != "admin":
            self._check_admin_remains(user, org)
        await self.store.db.execute(
            "UPDATE memberships SET role = $3 WHERE user_pk = $1 AND org_pk = $2",
            user.pk,
            org.pk,
            role,
        )
        user.roles[org.pk] = role

    async def remove_member(self, user: User, org: Org) -> None:
        if org.pk not in user.roles:
            raise NotFound(f"{user.email} is not a member of {org.name}")
        self._check_admin_remains(user, org)
        await self.store.db.execute(
            "DELETE FROM memberships WHERE user_pk = $1 AND org_pk = $2", user.pk, org.pk
        )
        del user.roles[org.pk]

    def forget_org(self, org: Org) -> None:
        """Drop a deleted org's memberships (the database cascades them)."""
        for user in self.users.values():
            user.roles.pop(org.pk, None)
            if user.default_org == org.pk:
                user.default_org = None

    # ------------------------------------------------------------------ invites

    async def create_invite(self, org: Org, email: str, role: str, by: User) -> tuple[Invite, str]:
        """An invite to ``org``; the token for its link is returned only here."""
        email = email.lower()
        user = self.by_email.get(email)
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

    async def _claim(self, conn: asyncpg.Connection, invite: Invite) -> Org:
        """Use up an invite (so it can't be accepted twice); returns its org."""
        claimed = await conn.fetchval("DELETE FROM invites WHERE pk = $1 RETURNING pk", invite.pk)
        org = self.store.orgs_by_pk.get(invite.org)
        if claimed is None or org is None:
            raise NotFound("this invitation was already used or withdrawn")
        return org

    async def accept_invite(self, invite: Invite, user: User) -> None:
        """Add an existing account to the invite's org (the account must have its email)."""
        if user.email != invite.email:
            raise Forbidden(f"this invitation is for {invite.email}")
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            org = await self._claim(conn, invite)
            if org.pk not in user.roles:
                await conn.execute(
                    "INSERT INTO memberships (user_pk, org_pk, role, created_at) "
                    "VALUES ($1, $2, $3, now())",
                    user.pk,
                    org.pk,
                    invite.role,
                )
        user.roles.setdefault(org.pk, invite.role)

    async def register(
        self,
        invite: Invite,
        name: str,
        password: str | None = None,
        google_sub: str | None = None,
    ) -> User:
        """Create the account an invite is for, as a member of its org."""
        if invite.email in self.by_email:
            raise Conflict("an account with this email exists: sign in to accept the invitation")
        password_hash = await self.hash_password(password) if password is not None else None
        async with self.store.db.pool.acquire() as conn, conn.transaction():
            org = await self._claim(conn, invite)
            user = await self._insert_user(conn, invite.email, name, password_hash, google_sub)
            await conn.execute(
                "INSERT INTO memberships (user_pk, org_pk, role, created_at) "
                "VALUES ($1, $2, $3, now())",
                user.pk,
                org.pk,
                invite.role,
            )
        user.roles[org.pk] = invite.role
        self._add_user(user)
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
        return self.users.get(pk) if pk is not None else None

    async def redeem_reset(self, token: str, password: str) -> User:
        """Set the password of the reset's user, ending all their sessions."""
        pk = await self.store.db.fetchval(
            "DELETE FROM password_resets WHERE token_hash = $1 AND expires_at > now() "
            "RETURNING user_pk",
            self.digest(token),
        )
        user = self.users.get(pk) if pk is not None else None
        if user is None:
            raise NotFound("this reset link is invalid, expired or already used")
        await self.set_password(user, password)
        return user
