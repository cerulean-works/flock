import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
import time
import urllib.request
from typing import Annotated, Any, ClassVar
from urllib.parse import quote, urlencode

import jwt
import orjson
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.src.api.deps import (
    APIError,
    Email,
    JSONResponse,
    Password,
    SessionDep,
    StoreDep,
    SuperadminDep,
    UserDep,
    app_link,
    current_user,
    visible_org,
)
from app.api.src.core.accounts import User
from app.api.src.core.errors import Conflict, Forbidden, NotFound
from app.api.src.core.store import Store
from app.api.src.settings import Settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["auth"])

DisplayName = Annotated[str, Field(min_length=1, max_length=128)]


class SignupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Email
    name: DisplayName
    password: Password
    create_org: bool = Field(
        True,
        description="with no organization yet, create the default entity's for the account; "
        "the dashboard's setup passes false and creates the first organization next",
    )


class LoginBody(BaseModel):
    email: str
    password: str = Field(max_length=256)


class RefreshBody(BaseModel):
    refresh_token: str


class CodeBody(BaseModel):
    code: str


class MeUpdate(BaseModel):
    """Fields to change; ``default_org: null`` falls back to the first org joined."""

    model_config = ConfigDict(extra="forbid")

    name: DisplayName | None = None
    default_org: str | None = None


class PasswordChange(BaseModel):
    current: str | None = Field(None, description="not needed if the account has no password")
    new: Password


class ApiKeyCreate(BaseModel):
    name: str = Field("default", min_length=1, max_length=128)


class InviteAccept(BaseModel):
    """``name`` and ``password`` create the account; leave them out when accepting as a
    signed-in user."""

    model_config = ConfigDict(extra="forbid")

    name: DisplayName | None = None
    password: Password | None = None


class PasswordReset(BaseModel):
    password: Password


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_superadmin: bool


async def signed_in(store: Store, user: User, status_code: int = 200) -> Response:
    """Start a session: tokens plus the user's profile."""
    tokens = await store.accounts.start_session(user)
    return JSONResponse({**tokens, "user": store.accounts.info(user)}, status_code=status_code)


# ---------------------------------------------------------------------- sign in


@router.get("/auth/config")
async def auth_config(store: StoreDep) -> Response:
    """What a sign-in page should offer."""
    return JSONResponse(
        {"signup_open": store.accounts.signup_open, "google": Google.configured(store.settings)}
    )


@router.post("/auth/signup", status_code=201)
async def signup(body: SignupBody, store: StoreDep) -> Response:
    """Create the first account, which becomes the super admin. Everyone else joins through
    an invitation."""
    user = await store.accounts.signup(
        body.email, body.name, body.password, create_org=body.create_org
    )
    return await signed_in(store, user, 201)


@router.post("/auth/login")
async def login(body: LoginBody, store: StoreDep) -> Response:
    user = await store.accounts.login(body.email, body.password)
    if user is None:
        raise APIError(401, "invalid email or password")
    return await signed_in(store, user)


@router.post("/auth/refresh")
async def refresh(body: RefreshBody, store: StoreDep) -> Response:
    """New tokens for a refresh token. Each refresh token works once: use the new one next."""
    tokens = await store.accounts.refresh(body.refresh_token)
    if tokens is None:
        raise APIError(401, "invalid or expired refresh token")
    return JSONResponse(tokens)


@router.post("/auth/logout", status_code=204)
async def logout(session: SessionDep, store: StoreDep) -> Response:
    """End the session of the request's access token."""
    if session is None:
        raise APIError(400, "sign out with an access token, not an API key")
    await store.accounts.end_session(session)
    return Response(status_code=204)


class Google:
    """Sign in with Google: the OAuth authorization-code flow (with PKCE), run by the server.

    ``start`` keeps the flow's secrets in a signed cookie, so it needs no server-side state and
    survives restarts. The callback redirects to the dashboard with a one-time code, which the
    dashboard exchanges for tokens.
    """

    AUTHORIZE_URL: ClassVar[str] = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL: ClassVar[str] = "https://oauth2.googleapis.com/token"
    ISSUERS: ClassVar[tuple[str, ...]] = ("https://accounts.google.com", "accounts.google.com")
    COOKIE: ClassVar[str] = "flock_google_signin"
    COOKIE_PATH: ClassVar[str] = "/api/v1/auth/google"
    COOKIE_TTL: ClassVar[int] = 600

    @staticmethod
    def configured(settings: Settings) -> bool:
        return bool(settings.google_client_id and settings.google_client_secret)

    @staticmethod
    def redirect_uri(request: Request, settings: Settings) -> str:
        """Where Google sends the browser back: by default through the dashboard, which proxies
        ``/api/v1/auth/google/*`` here, so Flock itself needn't be reachable from browsers.
        """
        base = settings.app_url or settings.public_url or str(request.base_url)
        return settings.google_redirect_url or f"{base.rstrip('/')}/api/v1/auth/google/callback"

    @classmethod
    def exchange(cls, settings: Settings, code: str, verifier: str, redirect_uri: str) -> dict:
        """The claims of the ID token Google issues for ``code`` (blocking: run in a thread)."""
        body = urlencode(
            {
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            }
        ).encode()
        request = urllib.request.Request(
            cls.TOKEN_URL,
            data=body,
            headers={
                "content-type": "application/x-www-form-urlencoded",
                "accept": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=10) as resp:
            tokens = orjson.loads(resp.read())
        # The token came straight from Google's token endpoint over TLS, which authenticates it
        # (OpenID Connect Core 3.1.3.7): its signature needn't be checked. Claims are, below.
        return jwt.decode(tokens["id_token"], options={"verify_signature": False})


@router.get("/auth/google/start")
async def google_start(request: Request, store: StoreDep, invite: str | None = None) -> Response:
    """Redirect to Google's sign-in; ``invite`` accepts an invitation on the way."""
    settings = store.settings
    if not Google.configured(settings):
        raise APIError(404, "Google sign-in is not configured")
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = hashlib.sha256(verifier.encode()).digest()
    redirect_uri = Google.redirect_uri(request, settings)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": base64.urlsafe_b64encode(challenge).rstrip(b"=").decode(),
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    flow = {
        "typ": "google",
        "state": state,
        "nonce": nonce,
        "verifier": verifier,
        "invite": invite,
        "exp": int(time.time()) + Google.COOKIE_TTL,
    }
    response = RedirectResponse(f"{Google.AUTHORIZE_URL}?{urlencode(params)}", status_code=302)
    response.set_cookie(
        Google.COOKIE,
        jwt.encode(flow, store.accounts.secret, algorithm="HS256"),
        max_age=Google.COOKIE_TTL,
        path=Google.COOKIE_PATH,
        secure=redirect_uri.startswith("https:"),
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/auth/google/callback")
async def google_callback(
    request: Request, store: StoreDep, code: str = "", state: str = "", error: str = ""
) -> Response:
    """Where Google sends the browser back. Redirects to the dashboard's ``auth/google`` page
    with a one-time ``code``, or to its ``login`` page with an ``error``."""
    settings = store.settings
    accounts = store.accounts

    def redirect(path: str) -> Response:
        response = RedirectResponse(app_link(request, store, path), status_code=302)
        response.delete_cookie(Google.COOKIE, path=Google.COOKIE_PATH)
        return response

    def fail(reason: str, message: str) -> Response:
        return redirect(f"login?error={reason}&message={quote(message)}")

    if not Google.configured(settings):
        raise APIError(404, "Google sign-in is not configured")
    if error:
        return fail("google", f"Google sign-in failed: {error}")
    try:
        flow = jwt.decode(
            request.cookies.get(Google.COOKIE, ""), accounts.secret, algorithms=["HS256"]
        )
    except jwt.InvalidTokenError:
        return fail("expired", "the sign-in took too long or was started elsewhere")
    if flow.get("typ") != "google" or not hmac.compare_digest(str(flow.get("state")), state):
        return fail("state", "the sign-in was not started here")
    redirect_uri = Google.redirect_uri(request, settings)
    try:
        claims = await asyncio.to_thread(
            Google.exchange, settings, code, flow["verifier"], redirect_uri
        )
    except (OSError, KeyError, ValueError, jwt.PyJWTError) as exc:
        log.warning("Google sign-in: code exchange failed: %s", exc)
        return fail("google", "Google did not confirm the sign-in")
    audience = claims.get("aud")
    if (
        claims.get("iss") not in Google.ISSUERS
        or settings.google_client_id not in (audience if isinstance(audience, list) else [audience])
        or float(claims.get("exp") or 0) < time.time()
        or claims.get("nonce") != flow["nonce"]
        or not claims.get("sub")
        or not claims.get("email")
    ):
        return fail("google", "Google's answer could not be verified")
    if claims.get("email_verified") is not True:
        return fail("unverified", "your Google account's email address is not verified")
    invite = None
    if flow.get("invite"):
        invite = await accounts.find_invite(flow["invite"])
        if invite is None:
            return fail("invite", "this invitation is invalid, expired or already used")
    try:
        user = await accounts.google_user(
            claims["sub"], claims["email"], claims.get("name") or claims["email"], invite
        )
    except (Forbidden, Conflict, NotFound) as exc:
        return fail("forbidden", str(exc))
    return redirect(f"auth/google?code={accounts.issue_code(user)}")


@router.post("/auth/google/exchange")
async def google_exchange(body: CodeBody, store: StoreDep) -> Response:
    """Tokens for the one-time code a Google sign-in ended with (valid for a minute)."""
    user = store.accounts.redeem_code(body.code)
    if user is None:
        raise APIError(401, "invalid or expired sign-in code")
    return await signed_in(store, user)


# ---------------------------------------------------------------------- the signed-in user


@router.get("/me")
async def me(user: UserDep, store: StoreDep) -> Response:
    return JSONResponse(store.accounts.info(user))


@router.patch("/me")
async def update_me(body: MeUpdate, user: UserDep, store: StoreDep) -> Response:
    fields: dict[str, Any] = body.model_dump(include=body.model_fields_set)
    if fields.get("name") is None:
        fields.pop("name", None)
    if fields.get("default_org") is not None:
        fields["default_org"] = visible_org(fields["default_org"], store, user)
    await store.accounts.update_user(user, fields)
    return JSONResponse(store.accounts.info(user))


@router.post("/me/password", status_code=204)
async def change_password(
    body: PasswordChange, user: UserDep, session: SessionDep, store: StoreDep
) -> Response:
    """Change your password; your other sessions end (this one continues)."""
    accounts = store.accounts
    if user.password_hash is not None and not await accounts.check_password(
        user, body.current or ""
    ):
        raise APIError(403, "the current password is wrong")
    await accounts.set_password(user, body.new, keep=session)
    return Response(status_code=204)


@router.get("/me/api-keys")
async def list_api_keys(user: UserDep, store: StoreDep) -> Response:
    keys = store.accounts.api_keys(user)
    return JSONResponse(
        {
            "keys": [
                {"id": k.pk, "name": k.name, "prefix": k.prefix, "created_at": k.created_at}
                for k in keys
            ]
        }
    )


@router.post("/me/api-keys", status_code=201)
async def create_api_key(body: ApiKeyCreate, user: UserDep, store: StoreDep) -> Response:
    """A new API key, for the wandb SDK (``WANDB_API_KEY``) or scripts. It is shown only once."""
    api_key, key = await store.accounts.create_api_key(user, body.name)
    return JSONResponse(
        {
            "id": api_key.pk,
            "name": api_key.name,
            "prefix": api_key.prefix,
            "created_at": api_key.created_at,
            "key": key,
        },
        status_code=201,
    )


@router.delete("/me/api-keys/{key_id}", status_code=204)
async def delete_api_key(key_id: int, user: UserDep, store: StoreDep) -> Response:
    await store.accounts.delete_api_key(user, key_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------- users


def find_user(store: Store, user_id: int) -> User:
    user = store.accounts.users.get(user_id)
    if user is None:
        raise APIError(404, f"user {user_id} not found")
    return user


@router.get("/users")
async def list_users(admin: SuperadminDep, store: StoreDep) -> Response:
    users = sorted(store.accounts.users.values(), key=lambda u: u.pk)
    return JSONResponse({"users": [store.accounts.info(u) for u in users]})


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int, body: UserUpdate, admin: SuperadminDep, store: StoreDep
) -> Response:
    """Make a user a super admin, or stop them being one (there is always one left)."""
    user = find_user(store, user_id)
    await store.accounts.update_user(user, {"is_superadmin": body.is_superadmin})
    return JSONResponse(store.accounts.info(user))


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, admin: SuperadminDep, store: StoreDep) -> Response:
    """Delete an account. Its runs stay, without an owner."""
    await store.accounts.delete_user(find_user(store, user_id))
    return Response(status_code=204)


@router.post("/users/{user_id}/password-reset", status_code=201)
async def create_password_reset(
    user_id: int, request: Request, actor: UserDep, store: StoreDep
) -> Response:
    """A one-time link setting a new password, to hand to the user (no email is sent).

    Super admins can reset anyone; org admins the members of their orgs, except super admins.
    """
    accounts = store.accounts
    user = accounts.users.get(user_id)
    if user is None or not (actor.is_superadmin or set(user.roles) & set(actor.roles)):
        raise APIError(404, f"user {user_id} not found")
    if not accounts.can_reset_password(actor, user):
        raise APIError(403, "you can't reset this user's password")
    token, expires_at = await accounts.create_reset(user, actor)
    return JSONResponse(
        {"url": app_link(request, store, f"reset/{token}"), "expires_at": expires_at},
        status_code=201,
    )


# ---------------------------------------------------------------------- links


@router.get("/invites/{token}")
async def get_invite(token: str, store: StoreDep) -> Response:
    """What an invitation link is for (no sign-in needed: the token is the secret)."""
    accounts = store.accounts
    invite = await accounts.find_invite(token)
    org = store.orgs_by_pk.get(invite.org) if invite is not None else None
    if invite is None or org is None:
        raise APIError(404, "this invitation is invalid, expired or already used")
    return JSONResponse(
        {
            "org": org.name,
            "org_avatar_url": store.avatars.url(org.avatar),
            "email": invite.email,
            "role": invite.role,
            "expires_at": invite.expires_at,
            "account_exists": invite.email in accounts.by_email,
        }
    )


@router.post("/invites/{token}/accept")
async def accept_invite(
    token: str, body: InviteAccept, request: Request, store: StoreDep
) -> Response:
    """Accept an invitation: with ``name`` and ``password`` to create the account (and sign
    in), or signed in as the invited email to join the org with an existing account."""
    accounts = store.accounts
    invite = await accounts.find_invite(token)
    if invite is None:
        raise APIError(404, "this invitation is invalid, expired or already used")
    if body.password is None:
        has_credentials = "authorization" in request.headers or "x-api-key" in request.headers
        if not has_credentials:
            raise APIError(401, "sign in as the invited user, or give a name and password")
        user = current_user(request, store)
        await accounts.accept_invite(invite, user)
        return JSONResponse({"user": accounts.info(user)})
    user = await accounts.register(invite, body.name or invite.email, body.password)
    return await signed_in(store, user, 201)


@router.get("/password-resets/{token}")
async def get_password_reset(token: str, store: StoreDep) -> Response:
    user = await store.accounts.find_reset(token)
    if user is None:
        raise APIError(404, "this reset link is invalid, expired or already used")
    return JSONResponse({"email": user.email})


@router.post("/password-resets/{token}")
async def redeem_password_reset(token: str, body: PasswordReset, store: StoreDep) -> Response:
    """Set a new password from a reset link. Every other session of the user ends."""
    user = await store.accounts.redeem_reset(token, body.password)
    return await signed_in(store, user)
