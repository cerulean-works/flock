"""`flock whoami` and `flock token`."""

import json

import httpx

from flock.cli import api, credentials
from tests.app.utils import machines


def test_whoami(server, alice, signed_in, flock_cli):
    result = flock_cli("whoami")
    assert result.exit_code == 0, result.output
    path = credentials.credentials_path()
    assert result.stdout == (
        "alice <alice@lab.test>\n"
        f"  Server: {server.url}\n"
        "  Orgs:   flock (flock), member, default\n"
        f"  From:   flock login ({path})\n"
    )


def test_whoami_json(server, alice, signed_in, flock_cli):
    result = flock_cli("whoami", "--json")
    assert result.exit_code == 0, result.output
    shown = json.loads(result.stdout)
    assert shown == alice.api.get("/api/v1/me").json() | {"host": server.url, "source": "file"}


def test_whoami_with_flock_token(server, alice, flock_cli, monkeypatch):
    monkeypatch.setenv("FLOCK_HOST", server.url)
    monkeypatch.setenv("FLOCK_TOKEN", alice.token)
    result = flock_cli("whoami", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["source"] == "env"
    assert flock_cli("whoami").stdout.endswith("  From:   FLOCK_TOKEN\n")

    monkeypatch.setenv("FLOCK_TOKEN", alice.api_key)  # a wandb key doesn't work (D72)
    refused = flock_cli("whoami")
    assert refused.exit_code == 1
    assert "refused the credential from FLOCK_TOKEN. wandb keys only work" in refused.stderr


def test_whoami_signed_out_or_revoked(server, alice, signed_in, flock_cli):
    other = flock_cli("whoami", "--host", "http://other.test")
    assert other.exit_code == 1
    assert other.stderr == "Error: Not logged in to http://other.test. Run flock login.\n"

    (session,) = machines(alice)
    alice.api.delete(f"/api/v1/me/cli-sessions/{session['id']}")
    revoked = flock_cli("whoami")
    assert revoked.exit_code == 1
    assert "refused the credential from flock login" in revoked.stderr
    assert "Run flock login." in revoked.stderr

    credentials.delete()
    signed_out = flock_cli("whoami")
    assert signed_out.exit_code == 1
    assert signed_out.stderr == "Error: Not logged in. Run flock login.\n"


def refuse(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"flock token made a request: {request.url}")


def test_token_prints_only_the_token_without_a_request(signed_in, flock_cli, monkeypatch):
    monkeypatch.setattr(api, "TRANSPORT", httpx.MockTransport(refuse))
    result = flock_cli("token")
    assert result.exit_code == 0, result.output
    assert result.stdout == f"{signed_in}\n"
    assert result.stderr == ""


def test_token_from_the_environment(flock_cli, monkeypatch):
    monkeypatch.setattr(api, "TRANSPORT", httpx.MockTransport(refuse))
    monkeypatch.setenv("FLOCK_TOKEN", "flock-abc")
    assert flock_cli("token").stdout == "flock-abc\n"


def test_token_signed_out(flock_cli):
    result = flock_cli("token")
    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == "Error: Not logged in. Run flock login.\n"
