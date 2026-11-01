import hashlib
import subprocess
import sys
import textwrap
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from app.api.src.core.avatars import Avatars

PNG = b"\x89PNG\r\n\x1a\n" + b"pixels" * 10
JPEG = b"\xff\xd8\xff\xe0" + b"jfif" * 10
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"x" * 10
ME = "/api/v1/me"


def email_hash(email: str) -> str:
    return hashlib.sha256(email.lower().encode()).hexdigest()


class FakeGravatar:
    """Serves ``images`` by email hash, and 404 for every other email (as with ``d=404``)."""

    def __init__(self) -> None:
        self.images: dict[str, bytes] = {}
        self.requests: list[dict[str, Any]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                url = urlparse(self.path)
                digest = url.path.rpartition("/")[2]
                fake.requests.append({"hash": digest, **parse_qs(url.query)})
                body = fake.images.get(digest)
                self.send_response(200 if body is not None else 404)
                self.send_header("content-length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True
        )
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/avatar/"

    def add(self, email: str, image: bytes) -> None:
        self.images[email_hash(email)] = image


@pytest.fixture
def gravatar() -> Iterator[FakeGravatar]:
    fake = FakeGravatar()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()
    fake.thread.join(timeout=5)
    assert not fake.thread.is_alive()


def settled(server) -> None:
    """Wait for the background Gravatar fetches of new accounts."""
    server.call(server.store.avatars.flush())


def test_flush_finishes_when_a_completed_fetch_still_has_a_pending_callback():
    # A timeout outside the event loop also catches a loop that spins without yielding.
    code = """
        import asyncio
        import uvloop
        from app.api.src.core.avatars import Avatars

        async def check():
            avatars = Avatars(None)
            task = asyncio.create_task(asyncio.sleep(0))
            await task
            avatars._tasks.add(task)
            task.add_done_callback(avatars._tasks.discard)
            await avatars.flush()
            assert not avatars._tasks

        asyncio.run(check(), loop_factory=uvloop.new_event_loop)
    """
    subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        check=True,
        capture_output=True,
        timeout=10,
    )


def put_image(client, path: str, data: bytes):
    return client.request("PUT", path, body=data, headers={"Content-Type": "image/png"})


def test_new_accounts_get_their_gravatar(make_flock, gravatar):
    gravatar.add("admin@flock.test", PNG)
    gravatar.add("bob@lab.test", JPEG)
    server = make_flock(gravatar=True, gravatar_url=gravatar.url)  # signs up the admin
    alice = server.add_user("alice@lab.test")  # no Gravatar
    bob = server.add_user("bob@lab.test")
    settled(server)

    admin = server.client().get(ME).json()
    assert admin["avatar_source"] == "gravatar"
    assert admin["avatar_url"] == f"/api/v1/avatars/{hashlib.sha256(PNG).hexdigest()}"
    assert alice.api.get(ME).json()["avatar_url"] is None
    assert bob.api.get(ME).json()["avatar_source"] == "gravatar"
    first = gravatar.requests[0]
    assert first == {"hash": email_hash("admin@flock.test"), "s": ["256"], "d": ["404"]}

    # Served without credentials, cacheable for good.
    image = server.anonymous().get(admin["avatar_url"])
    assert (image.status, image.body) == (200, PNG)
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert image.headers["x-content-type-options"] == "nosniff"
    etag = image.headers["etag"]
    cached = server.anonymous().get(admin["avatar_url"], headers={"If-None-Match": etag})
    assert cached.status == 304

    members = {m["email"]: m for m in alice.api.get("/api/v1/orgs/flock/members").json()["members"]}
    assert members["bob@lab.test"]["avatar_url"] == bob.api.get(ME).json()["avatar_url"]


def test_uploading_and_removing_avatars(flock):
    alice = flock.add_user("alice@lab.test")
    bob = flock.add_user("bob@lab.test")
    path = "/api/v1/me/avatar"
    uploaded = put_image(alice.api, path, PNG).json()
    assert uploaded["avatar_source"] == "upload"
    url = uploaded["avatar_url"]
    assert flock.anonymous().get(url).body == PNG
    webp = put_image(bob.api, path, WEBP).json()["avatar_url"]
    assert webp != url
    assert flock.anonymous().get(webp).headers["content-type"] == "image/webp"

    assert put_image(alice.api, path, b"<svg onload=alert(1)/>").status == 415
    assert put_image(alice.api, path, b"").status == 422
    too_big = PNG + b"x" * Avatars.MAX_BYTES
    assert put_image(alice.api, path, too_big).status == 413
    assert alice.api.get(ME).json()["avatar_url"] == url  # unchanged by refused uploads

    # The same image is stored once; it goes when nobody uses it any more.
    assert put_image(bob.api, path, PNG).json()["avatar_url"] == url
    assert alice.api.delete(path).status == 200
    me = alice.api.get(ME).json()
    assert (me["avatar_url"], me["avatar_source"]) == (None, None)
    assert flock.anonymous().get(url).status == 200  # still bob's
    flock.client().delete(f"/api/v1/users/{bob.id}")
    assert flock.anonymous().get(url).status == 404

    assert flock.anonymous().get("/api/v1/avatars/" + "0" * 64).status == 404
    assert flock.anonymous().get("/api/v1/avatars/nothex").status == 404
    assert flock.anonymous().request("PUT", path, body=PNG).status == 401


def test_organization_avatars(flock):
    admin = flock.client()
    alice = flock.add_user("alice@lab.test")
    path = "/api/v1/orgs/flock/avatar"
    assert put_image(alice.api, path, PNG).status == 403  # members can't
    org = put_image(admin, path, JPEG).json()
    url = org["avatar_url"]
    assert flock.anonymous().get(url).headers["content-type"] == "image/jpeg"
    assert alice.api.get("/api/v1/orgs/flock").json()["avatar_url"] == url
    invite = admin.post("/api/v1/orgs/flock/invites", {"email": "carol@lab.test"}).json()
    public = flock.anonymous().get(f"/api/v1/invites/{invite['token']}").json()
    assert public["org_avatar_url"] == url

    assert admin.post("/api/v1/orgs", {"name": "lab"}).status == 201
    assert put_image(alice.api, "/api/v1/orgs/lab/avatar", PNG).status == 404  # not a member
    assert admin.delete(path).json()["avatar_url"] is None
    assert flock.anonymous().get(url).status == 404

    put_image(admin, "/api/v1/orgs/lab/avatar", PNG)
    lab_url = admin.get("/api/v1/orgs/lab").json()["avatar_url"]
    assert admin.delete("/api/v1/orgs/lab").status == 204
    assert flock.anonymous().get(lab_url).status == 404


def test_fetching_gravatar_on_request(make_flock, gravatar):
    off = make_flock()
    assert off.client().post("/api/v1/me/avatar/gravatar", {}).status == 409

    server = make_flock(gravatar=True, gravatar_url=gravatar.url)
    settled(server)
    api = server.client()
    refetch = "/api/v1/me/avatar/gravatar"
    assert api.post(refetch, {}).status == 404  # no Gravatar for this email
    put_image(api, "/api/v1/me/avatar", JPEG)
    gravatar.add("admin@flock.test", b"not an image")
    assert api.post(refetch, {}).status == 502
    gravatar.add("admin@flock.test", PNG)
    fetched = api.post(refetch, {}).json()
    assert fetched["avatar_source"] == "gravatar"
    assert server.anonymous().get(fetched["avatar_url"]).body == PNG


def test_new_accounts_keep_an_avatar_set_before_their_gravatar_arrives(make_flock, gravatar):
    server = make_flock(gravatar=True, gravatar_url=gravatar.url)
    alice = server.add_user("alice@lab.test")
    settled(server)
    put_image(alice.api, "/api/v1/me/avatar", JPEG)
    user = server.call(server.store.accounts.user_by_email("alice@lab.test"))
    gravatar.add("alice@lab.test", PNG)
    assert server.call(server.store.avatars.gravatar(user, keep_existing=True)) is True
    assert alice.api.get(ME).json()["avatar_source"] == "upload"


def test_avatars_survive_a_restart(make_flock):
    server = make_flock()
    alice = server.add_user("alice@lab.test")
    url = put_image(alice.api, "/api/v1/me/avatar", PNG).json()["avatar_url"]
    org_url = put_image(server.client(), "/api/v1/orgs/flock/avatar", JPEG).json()["avatar_url"]
    server.stop()
    restarted = make_flock(reuse=server)
    me = restarted.client(token=alice.token).get(ME).json()
    assert (me["avatar_url"], me["avatar_source"]) == (url, "upload")
    assert restarted.client().get("/api/v1/orgs/flock").json()["avatar_url"] == org_url
    assert restarted.anonymous().get(url).body == PNG
