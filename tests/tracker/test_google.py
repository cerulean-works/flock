import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
import orjson
import pytest

from src.api.auth import Google

CLIENT_ID = "client-id.apps.googleusercontent.com"
CALLBACK = "/api/v1/auth/google/callback"
APP_URL = "https://ui.test"


class FakeGoogle:
    """Google's token endpoint: answers code exchanges with an ID token holding ``claims``."""

    def __init__(self) -> None:
        self.claims: dict = {}
        self.requests: list[dict[str, str]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                form = parse_qs(self.rfile.read(int(self.headers["content-length"])).decode())
                fake.requests.append({key: values[0] for key, values in form.items()})
                # Unsigned as far as the tracker cares: it came from the token endpoint itself.
                token = jwt.encode(fake.claims, "a key Google would never share with us", "HS256")
                body = orjson.dumps({"access_token": "x", "id_token": token})
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/token"

    def sign_in(self, tracker, email: str, sub: str = "", invite: str | None = None, **claims):
        """Run the browser's side of a sign-in; returns where the callback redirects to.

        Google is told to come back through the dashboard (``APP_URL``), which proxies the
        callback to the tracker; here the callback goes to the tracker directly.
        ``claims`` override the ID token's; ``state`` and ``no_cookie`` tamper with the flow.
        """
        state = claims.pop("state", None)
        no_cookie = claims.pop("no_cookie", False)
        browser = tracker.anonymous()
        start = browser.get("/api/v1/auth/google/start", **({"invite": invite} if invite else {}))
        assert start.status == 302, start.body
        location = urlparse(start.headers["location"])
        query = {key: values[0] for key, values in parse_qs(location.query).items()}
        assert f"{location.scheme}://{location.netloc}{location.path}" == Google.AUTHORIZE_URL
        assert (query["client_id"], query["redirect_uri"]) == (CLIENT_ID, APP_URL + CALLBACK)
        assert query["code_challenge_method"] == "S256"
        cookie = start.headers["set-cookie"].partition(";")[0]
        self.claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "iat": time.time(),
            "exp": time.time() + 300,
            "nonce": query["nonce"],
            "sub": sub or f"google-{email}",
            "email": email,
            "email_verified": True,
            "name": email.partition("@")[0].title(),
        } | claims
        headers = None if no_cookie else {"Cookie": cookie}
        callback = browser.get(
            CALLBACK, headers=headers, code="the-code", state=state or query["state"]
        )
        assert callback.status == 302, callback.body
        return callback.headers["location"]

    def tokens(self, tracker, location: str) -> dict:
        """Exchange the one-time code a successful sign-in redirected with."""
        assert location.startswith("https://ui.test/auth/google?code="), location
        code = location.partition("code=")[2]
        exchanged = tracker.anonymous().post("/api/v1/auth/google/exchange", {"code": code})
        assert exchanged.status == 200, exchanged.body
        replay = tracker.anonymous().post("/api/v1/auth/google/exchange", {"code": code})
        assert replay.status == 401  # one use only
        return exchanged.json()


@pytest.fixture
def google(monkeypatch) -> Iterator[FakeGoogle]:
    fake = FakeGoogle()
    monkeypatch.setattr(Google, "TOKEN_URL", fake.url)
    yield fake
    fake.server.shutdown()


GOOGLE_SETTINGS = {
    "google_client_id": CLIENT_ID,
    "google_client_secret": "client-secret",
    "app_url": APP_URL,
}


def test_google_sign_in_as_the_first_user(make_tracker, google):
    server = make_tracker(bootstrap=False, **GOOGLE_SETTINGS)
    assert server.anonymous().get("/api/v1/auth/config").json()["google"] is True
    tokens = google.tokens(server, google.sign_in(server, "Root@Gmail.com", sub="g-1"))
    user = tokens["user"]
    assert (user["email"], user["name"], user["is_superadmin"]) == ("root@gmail.com", "Root", True)
    assert (user["google"], user["has_password"]) == (True, False)
    (exchange,) = google.requests
    assert exchange["client_secret"] == "client-secret"
    assert exchange["grant_type"] == "authorization_code"
    assert exchange["redirect_uri"] == APP_URL + CALLBACK
    assert exchange["code_verifier"]  # PKCE
    again = google.tokens(server, google.sign_in(server, "renamed@gmail.com", sub="g-1"))
    assert again["user"]["id"] == user["id"]  # found by Google id, whatever the email now


def test_google_links_existing_accounts_and_accepts_invites(make_tracker, google):
    server = make_tracker(**GOOGLE_SETTINGS)
    alice = server.add_user("alice@lab.test")
    linked = google.tokens(server, google.sign_in(server, "Alice@lab.test"))
    assert (linked["user"]["id"], linked["user"]["google"]) == (alice.id, True)

    stranger = google.sign_in(server, "eve@gmail.com")
    assert stranger.startswith("https://ui.test/login?error=forbidden")

    admin = server.client()
    invite = admin.post("/api/v1/orgs/flock/invites", {"email": "carol@gmail.com"}).json()
    wrong = google.sign_in(server, "eve@gmail.com", invite=invite["token"])
    assert wrong.startswith("https://ui.test/login?error=forbidden")
    joined = google.tokens(
        server, google.sign_in(server, "carol@gmail.com", invite=invite["token"])
    )
    assert joined["user"]["orgs"] == [{"name": "flock", "role": "member"}]
    used = google.sign_in(server, "carol@gmail.com", sub="other", invite=invite["token"])
    assert used.startswith("https://ui.test/login?error=invite")

    # A Google account can't take over an email already linked to another Google account.
    hijack = google.sign_in(server, "alice@lab.test", sub="someone-else")
    assert hijack.startswith("https://ui.test/login?error=forbidden")


@pytest.mark.parametrize(
    ("claims", "error"),
    [
        ({"state": "forged"}, "state"),
        ({"no_cookie": True}, "expired"),
        ({"nonce": "replayed"}, "google"),
        ({"aud": "another-client"}, "google"),
        ({"iss": "https://evil.test"}, "google"),
        ({"exp": time.time() - 10}, "google"),
        ({"email_verified": False}, "unverified"),
    ],
)
def test_google_sign_ins_that_fail_verification(make_tracker, google, claims, error):
    server = make_tracker(bootstrap=False, **GOOGLE_SETTINGS)
    location = google.sign_in(server, "root@gmail.com", **claims)
    assert location.startswith(f"https://ui.test/login?error={error}&message="), location
    assert server.anonymous().get("/api/v1/auth/config").json()["signup_open"] is True


def test_google_errors_and_configuration(make_tracker, google):
    server = make_tracker(**GOOGLE_SETTINGS)
    denied = server.anonymous().get(CALLBACK, error="access_denied")
    assert denied.headers["location"].startswith("https://ui.test/login?error=google")
    plain = make_tracker()
    assert plain.anonymous().get("/api/v1/auth/google/start").status == 404
    assert plain.anonymous().get(CALLBACK, code="x", state="y").status == 404


def test_google_callback_defaults_to_the_tracker_without_a_dashboard_url(make_tracker):
    settings = {k: v for k, v in GOOGLE_SETTINGS.items() if k != "app_url"}
    server = make_tracker(bootstrap=False, **settings)
    start = server.anonymous().get("/api/v1/auth/google/start")
    query = parse_qs(urlparse(start.headers["location"]).query)
    assert query["redirect_uri"] == [server.url + CALLBACK]
