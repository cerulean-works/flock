from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.app.conftest import Flocks, S3Flock
from tests.app.test_datasets import Writer, table
from tests.app.test_models import Pusher
from tests.conftest import S3Server

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any

pytestmark = pytest.mark.skipif(
    not os.environ.get("FLOCK_WEB_BUILD"), reason="needs the built web app"
)


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


@pytest.fixture
def web(flock: S3Flock, tmp_path: Path, wait_for: Callable[..., Any]) -> Iterator[str]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "ORIGIN": url,
        "FLOCK_API_URL": f"http://127.0.0.1:{flock.port}",
    }
    with (tmp_path / "web.log").open("w") as output:
        process = subprocess.Popen(
            ["node", os.environ["FLOCK_WEB_BUILD"]], env=env, stdout=output, stderr=output
        )
        try:

            def listening() -> bool:
                if process.poll() is not None:
                    pytest.fail((tmp_path / "web.log").read_text())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        return True
                except OSError:
                    return False

            wait_for(listening, timeout=30)
            yield url
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def test_built_web_enforces_caller_identity_across_pages_data_and_relays(
    flock: S3Flock, web: str
) -> None:
    api = flock.client()
    api.create_run("p", "r1", config={"secret_note": "private-run-146"})
    api.stream("p", "r1", history=[{"_step": 0, "loss": 1}], complete=0)
    Writer(flock).write("private", table(1946, 1))
    Pusher(flock).push("private-model")
    assert (
        api.request("DELETE", "/api/v1/orgs/flock/datasets/public", {"path": "private"}).status
        == 204
    )
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    member = flock.add_user("member@web.test")
    outsider = flock.add_user("outsider@web.test", org="lab")
    with (
        httpx.Client(base_url=web) as anon,
        httpx.Client(
            base_url=web, cookies={"flock_access": member.signup["access_token"]}
        ) as inside,
        httpx.Client(
            base_url=web, cookies={"flock_access": outsider.signup["access_token"]}
        ) as outside,
    ):
        for path in (
            "/api/projects/flock/p/runs",
            "/api/datasets/flock/rows?path=private",
        ):
            assert inside.get(path).status_code == 200, path
            for client in (anon, outside):
                response = client.get(path)
                assert response.status_code == 404, (path, response.text)
                assert "row 1946" not in response.text
                assert "private-run-146" not in response.text
        for path in ("/flock/p", "/datasets/flock/private"):
            assert inside.get(path).status_code == 200, path
            assert anon.get(path).status_code == 303, path
            assert outside.get(path).status_code == 404, path
            data = inside.get(path + "/__data.json")
            assert data.status_code == 200
            assert data.json()["type"] == "data", data.text
            for client in (anon, outside):
                response = client.get(path + "/__data.json")
                assert response.status_code == 200, response.text
                payload = response.json()
                if client is anon:
                    assert payload["type"] == "redirect", payload
                    assert payload["location"].startswith("/login"), payload
                else:
                    assert payload["type"] == "data", payload
                    assert any(node and node.get("status") == 404 for node in payload["nodes"]), (
                        payload
                    )
                assert "private-run-146" not in response.text
                assert "row 1946" not in response.text
        for path in (
            "/api/models/flock/private-model/versions",
            "/api/imports/flock/hf",
            "/api/storage/flock/flock-datasets",
        ):
            assert inside.get(path).status_code == 200, path
            assert anon.get(path).status_code == 401, path
            assert outside.get(path).status_code == 404, path
        # An incoming bearer header cannot replace the session identity used by the relay.
        for client in (anon, outside):
            response = client.get(
                "/api/datasets/flock/rows?path=private",
                headers={"authorization": "Bearer " + member.signup["access_token"]},
            )
            assert response.status_code == 404
        assert api.patch("/api/v1/projects/flock/p", {"visibility": "public"}).status == 200
        for client in (anon, outside, inside):
            response = client.get("/flock/p")
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
        assert anon.get("/api/datasets/flock/rows?path=private").status_code == 404
        refused = anon.post("/api/datasets/flock/rename", json={"path": "private", "to": "leaked"})
        assert refused.status_code == 401
        assert inside.get("/api/datasets/flock/rows?path=private").status_code == 200
