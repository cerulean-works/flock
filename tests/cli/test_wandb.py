"""`flock login` pointing the wandb SDK at Flock (D41)."""

import socket
import sys

import pytest

from flock.cli import credentials
from flock.cli import login as login_module

QUESTION = "Also log the wandb SDK in to this Flock server, for experiment tracking? [y/N]"


@pytest.fixture
def wandb_logins(monkeypatch) -> list[dict]:
    """Replaces ``wandb.login`` with a fake that records its arguments."""
    wandb = pytest.importorskip("wandb")
    calls: list[dict] = []
    monkeypatch.setattr(wandb, "login", lambda **kwargs: calls.append(kwargs) or True)
    return calls


def keys(user) -> list[str]:
    return [k["name"] for k in user.api.get("/api/v1/me/api-keys").json()["keys"]]


def test_yes_creates_a_key_and_logs_wandb_in(
    server, alice, browser, flock_cli, monkeypatch, wandb_logins
):
    monkeypatch.setattr(login_module, "_terminal", lambda: True)
    approval = browser.answer()
    result = flock_cli("login", "--host", server.url, "--no-browser", input="y\n")
    approval.join()
    assert result.exit_code == 0, result.output
    assert QUESTION in result.stdout
    name = f"wandb on {socket.gethostname()}"
    assert keys(alice) == ["tests", name]
    (call,) = wandb_logins
    assert call == {"key": call["key"], "host": server.url, "relogin": True, "verify": True}
    assert server.client(api_key=call["key"]).get("/api/v1/me").json()["id"] == alice.id
    assert f"✓ Logged the wandb SDK in to {server.url} with a new API key, {name}" in result.stdout
    saved = credentials.load()
    assert saved is not None and saved["wandb_key"] == {"name": name, "prefix": call["key"][:12]}


def test_the_wandb_flag_skips_the_question(server, alice, browser, flock_cli, wandb_logins):
    approval = browser.answer()
    result = flock_cli("login", "--host", server.url, "--wandb")  # no terminal needed
    approval.join()
    assert result.exit_code == 0, result.output
    assert QUESTION not in result.stdout
    assert len(wandb_logins) == 1


@pytest.mark.parametrize("how", ["answer", "flag"])
def test_no(server, alice, browser, flock_cli, monkeypatch, wandb_logins, how):
    monkeypatch.setattr(login_module, "_terminal", lambda: True)
    args = ["login", "--host", server.url, "--no-browser"]
    approval = browser.answer()
    if how == "flag":
        result = flock_cli(*args, "--no-wandb")
    else:
        result = flock_cli(*args, input="\n")  # the default is no
    approval.join()
    assert result.exit_code == 0, result.output
    assert (QUESTION in result.stdout) == (how == "answer")
    assert wandb_logins == []
    assert keys(alice) == ["tests"]
    saved = credentials.load()
    assert saved is not None and "wandb_key" not in saved


def test_already_logged_in(server, alice, browser, flock_cli, monkeypatch, wandb_logins):
    monkeypatch.setattr(login_module, "_terminal", lambda: True)
    monkeypatch.setenv("WANDB_BASE_URL", server.url)
    monkeypatch.setenv("WANDB_API_KEY", alice.api_key)
    approval = browser.answer()
    result = flock_cli("login", "--host", server.url, "--no-browser")
    approval.join()
    assert result.exit_code == 0, result.output
    assert f"The wandb SDK is already logged in to {server.url}." in result.stdout
    assert QUESTION not in result.stdout
    assert wandb_logins == []
    assert keys(alice) == ["tests"]


def test_a_wandb_key_that_no_longer_works_is_replaced(
    server, alice, browser, flock_cli, monkeypatch, wandb_logins
):
    monkeypatch.setenv("WANDB_BASE_URL", server.url)
    monkeypatch.setenv("WANDB_API_KEY", "flock-" + "0" * 48)
    approval = browser.answer()
    result = flock_cli("login", "--host", server.url, "--wandb")
    approval.join()
    assert result.exit_code == 0, result.output
    assert len(wandb_logins) == 1


@pytest.mark.parametrize("flag", [[], ["--wandb"]])
def test_wandb_not_installed(server, alice, browser, flock_cli, monkeypatch, flag):
    monkeypatch.setattr(login_module, "_terminal", lambda: True)
    monkeypatch.setitem(sys.modules, "wandb", None)  # import wandb raises ImportError
    approval = browser.answer()
    result = flock_cli("login", "--host", server.url, "--no-browser", *flag)
    approval.join()
    assert result.exit_code == 0, result.output
    assert "The wandb SDK isn't installed" in result.stdout
    assert f"wandb login --host {server.url} <key>" in result.stdout
    assert QUESTION not in result.stdout
    assert keys(alice) == ["tests"]
