from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.app.conftest import Flocks, S3Flock
from tests.app.test_datasets import Writer, table
from tests.app.test_evals import log_run
from tests.app.test_models import Pusher
from tests.conftest import S3Server
from tests.eval.toy import PROVENANCE, write_run

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any

pytestmark = pytest.mark.skipif(
    not os.environ.get("FLOCK_WEB_BUILD"), reason="needs the built web app"
)


def session(user: Any) -> dict[str, str]:
    """The dashboard's cookies for a user signed in through it."""
    return {
        "flock_access": user.signup["access_token"],
        "flock_user": base64.urlsafe_b64encode(json.dumps(user.signup["user"]).encode())
        .decode()
        .rstrip("="),
    }


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
    sessions = [session(user) for user in (member, outsider)]
    with (
        httpx.Client(base_url=web) as anon,
        httpx.Client(base_url=web, cookies=sessions[0]) as inside,
        httpx.Client(base_url=web, cookies=sessions[1]) as outside,
    ):
        for path in (
            "/api/projects/flock/p/runs",
            "/api/datasets/flock/rows?path=private",
        ):
            assert inside.get(path).status_code == 200, path
            for client in (anon, outside):
                response = client.get(path)
                assert response.status_code == 404, (path, response.text)
                assert response.headers["cache-control"] == "private, no-store"
                assert "row 1946" not in response.text
                assert "private-run-146" not in response.text
        for path in ("/flock/p", "/datasets/flock/private"):
            assert inside.get(path).status_code == 200, path
            redirect = anon.get(path)
            assert redirect.status_code in (303, 307), (path, redirect.text)
            assert redirect.headers["location"].startswith("/login"), redirect.headers
            assert outside.get(path).status_code == 404, path
            data = inside.get(path + "/__data.json")
            assert data.status_code == 200
            assert data.json()["type"] == "data", data.text
            for client in (anon, outside):
                response = client.get(path + "/__data.json")
                assert response.status_code == 200, response.text
                payload = response.json()
                if path == "/flock/p":
                    # Project content loads in the universal loader through the run relay.
                    # __data.json carries only the server layouts, not that private run data.
                    assert payload["type"] == "data", payload
                elif client is anon:
                    assert payload["type"] == "redirect", payload
                    assert payload["location"].startswith("/login"), payload
                else:
                    assert payload["type"] == "data", payload
                    assert any(
                        node and node.get("error", {}).get("status") == 404
                        for node in payload["nodes"]
                    ), payload
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
        assert (
            api.request("PUT", "/api/v1/orgs/flock/datasets/public", {"path": "private"}).status
            == 200
        )
        for client in (anon, outside):
            for path in ("/datasets/flock/private", "/api/datasets/flock/rows?path=private"):
                response = client.get(path)
                assert response.status_code == 200, (path, response.text)
                assert response.headers["cache-control"] == "private, no-store"
            response = client.get("/datasets/flock/private/__data.json")
            assert response.status_code == 200
            assert response.json()["type"] == "data"
            assert response.headers["cache-control"] == "private, no-store"


def test_eval_results_comparisons_and_model_evals_follow_the_projects_access(
    flock: S3Flock, web: str, tmp_path: Path
) -> None:
    """023: a private project's results file, item comparison and eval scores reach members only."""
    api = flock.client()
    version = Pusher(flock).push("m")
    scored = {"model": "flock:flock/m@1", "model_digest": version["digest"]}
    a = write_run(tmp_path / "a", {"secret_task": ("h", [True, False])}, PROVENANCE)
    b = write_run(tmp_path / "b", {"secret_task": ("h", [False, True])}, PROVENANCE)
    log_run(api, "p", "a", a)
    log_run(api, "p", "b", b)
    for run in ("a", "b"):
        api.create_run("p", run, config=scored)  # links each to the version it scored
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    member = flock.add_user("member@web.test")
    outsider = flock.add_user("outsider@web.test", org="lab")
    with (
        httpx.Client(base_url=web) as anon,
        httpx.Client(base_url=web, cookies=session(member)) as inside,
        httpx.Client(base_url=web, cookies=session(outsider)) as outside,
    ):
        for path in ("/flock/p/runs/a", "/flock/p/runs/a/compare?with=flock%2Fp%2Fb"):
            page = inside.get(path)
            assert page.status_code == 200, (path, page.text)
            assert "secret_task" in page.text
            redirect = anon.get(path)
            assert redirect.status_code in (303, 307), (path, redirect.text)
            assert redirect.headers["location"].startswith("/login")
            refused = outside.get(path)
            assert refused.status_code == 404, path
            assert "secret_task" not in refused.text
        evals = "/api/models/flock/m/versions/1/evals"
        found = inside.get(evals)
        assert found.status_code == 200, found.text
        assert [run["id"] for run in found.json()["runs"]] == ["a", "b"]
        assert found.json()["runs"][0]["scores"]["values"]["secret_task/acc_raw@h"] == 0.5
        assert anon.get(evals).status_code == 401
        assert outside.get(evals).status_code == 404
