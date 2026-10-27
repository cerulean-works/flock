"""`flock login` and `flock logout`, against a real Flock."""

import re
import socket

import pytest

from flock.cli import credentials
from flock.cli import login as login_module
from flock.cli.credentials import DEFAULT_HOST
from flock.cli.login import login_host

ME = "/api/v1/me"


def code_in(output: str) -> str:
    match = re.search(r"^! Copy your one-time code: ([A-Z]{4}-[A-Z]{4})$", output, re.MULTILINE)
    assert match, output
    return match.group(1)


def test_logging_in_without_a_terminal(server, alice, browser, flock_cli):
    approval = browser.answer()
    result = flock_cli("login", "--host", server.url)
    approval.join()
    assert result.exit_code == 0, result.output
    assert code_in(result.stdout) == browser.seen[0]["user_code"]
    assert f"Open {server.url}/device in a browser where you're signed in" in result.stdout
    assert "Press Enter" not in result.stdout
    assert f"✓ Logged in to {server.url} as alice@lab.test" in result.stdout
    assert "wandb" not in result.stdout  # nobody to ask

    # The approval page showed this machine.
    assert browser.seen[0]["hostname"] == socket.gethostname()
    assert browser.seen[0]["ip"] == "127.0.0.1"

    saved = credentials.load()
    assert saved is not None
    assert (saved["host"], saved["user"]["email"]) == (server.url, "alice@lab.test")
    assert server.client(token=saved["token"]).get(ME).json()["id"] == alice.id
    (listed,) = alice.api.get("/api/v1/me/cli-sessions").json()["sessions"]
    assert listed["name"] == socket.gethostname()


@pytest.mark.parametrize("why", ["--no-browser", "ssh"])
def test_no_browser_on_request_or_over_ssh(server, browser, flock_cli, monkeypatch, why):
    monkeypatch.setattr(login_module, "_terminal", lambda: True)
    monkeypatch.setattr(login_module.webbrowser, "open", lambda url: pytest.fail("opened"))
    args = ["login", "--host", server.url, "--no-wandb"]
    if why == "ssh":
        monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 50000 10.0.0.2 22")
    else:
        args.append("--no-browser")
    approval = browser.answer()
    result = flock_cli(*args)
    approval.join()
    assert result.exit_code == 0, result.output
    assert "Press Enter" not in result.stdout
    assert f"Open {server.url}/device in a browser" in result.stdout


def test_a_terminal_opens_the_browser_on_enter(server, browser, flock_cli, monkeypatch):
    monkeypatch.setattr(login_module, "_terminal", lambda: True)
    opened = []

    def open_browser(url: str) -> bool:
        opened.append(url)
        browser.answer()  # the user approves in the page that opened
        return True

    monkeypatch.setattr(login_module.webbrowser, "open", open_browser)
    result = flock_cli("login", "--host", server.url, "--no-wandb", input="\n")
    assert result.exit_code == 0, result.output
    code = code_in(result.stdout)
    assert f"Press Enter to open {server.url}/device in your browser..." in result.stdout
    assert opened == [f"{server.url}/device?code={code}"]
    assert credentials.load() is not None


def test_logging_in_again(server, alice, browser, signed_in, flock_cli, monkeypatch):
    # Without a terminal, a working sign-in is kept.
    result = flock_cli("login", "--host", server.url)
    assert result.exit_code == 0, result.output
    assert f"Already logged in to {server.url} as alice@lab.test." in result.stdout
    saved = credentials.load()
    assert saved is not None and saved["token"] == signed_in

    monkeypatch.setattr(login_module, "_terminal", lambda: True)
    result = flock_cli("login", "--host", server.url, input="n\n")
    assert result.exit_code == 0, result.output
    assert "Already logged in as alice@lab.test. Log in again? [y/N]" in result.stdout
    assert "one-time code" not in result.stdout

    approval = browser.answer()
    result = flock_cli("login", "--host", server.url, "--no-browser", "--no-wandb", input="y\n")
    approval.join()
    assert result.exit_code == 0, result.output
    saved = credentials.load()
    assert saved is not None and saved["token"] != signed_in
    assert len(alice.api.get("/api/v1/me/cli-sessions").json()["sessions"]) == 2

    # A sign-in that stopped working is replaced without asking.
    sessions = alice.api.get("/api/v1/me/cli-sessions").json()["sessions"]
    for session in sessions:
        assert alice.api.delete(f"/api/v1/me/cli-sessions/{session['id']}").status == 204
    approval = browser.answer()
    result = flock_cli("login", "--no-browser", "--no-wandb")  # the saved sign-in's server
    approval.join()
    assert result.exit_code == 0, result.output
    assert "Already logged in" not in result.stdout


def test_the_default_host_and_its_precedence(server, browser, flock_cli, monkeypatch):
    assert login_host(None) == DEFAULT_HOST == "https://flock-api.cerulean.works"
    credentials.save("http://saved.test/", "flock_cli_x", {"email": "a@b.c"})
    assert login_host(None) == "http://saved.test"
    monkeypatch.setenv("FLOCK_HOST", "env.test")
    assert login_host(None) == "https://env.test"
    assert login_host("http://option.test") == "http://option.test"

    monkeypatch.setenv("FLOCK_HOST", server.url)
    approval = browser.answer()
    result = flock_cli("login")
    approval.join()
    assert result.exit_code == 0, result.output
    assert f"✓ Logged in to {server.url} as alice@lab.test" in result.stdout


def test_a_denied_sign_in_saves_nothing(server, browser, flock_cli):
    approval = browser.answer(approve=False)
    result = flock_cli("login", "--host", server.url)
    approval.join()
    assert result.exit_code == 1
    assert result.stderr == "Error: The sign-in was denied in the browser.\n"
    assert credentials.load() is None


def test_an_expired_code_saves_nothing(make_flock, flock_cli, monkeypatch):
    server = make_flock(device_code_ttl=0.3)
    monkeypatch.setattr(server.store.accounts, "DEVICE_INTERVAL", 0.05)
    result = flock_cli("login", "--host", server.url)
    assert result.exit_code == 1
    assert "The code expired before it was approved: run flock login again." in result.stderr
    assert credentials.load() is None


def test_an_interrupted_login_saves_nothing(server, flock_cli, monkeypatch):
    def interrupt(seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(login_module.time, "sleep", interrupt)
    result = flock_cli("login", "--host", server.url)
    assert result.exit_code == 1
    assert result.stderr == "Error: Login interrupted: nothing was saved.\n"
    assert credentials.load() is None


def test_an_unreachable_server(flock_cli):
    result = flock_cli("login", "--host", "http://127.0.0.1:9")
    assert result.exit_code == 1
    assert result.stderr.startswith("Error: Couldn't reach http://127.0.0.1:9")


def test_logout_ends_the_session_on_the_server(server, alice, signed_in, flock_cli, isolated):
    result = flock_cli("logout")
    assert result.exit_code == 0, result.output
    assert result.stdout == f"Logged out of {server.url}.\n"
    assert server.client(token=signed_in).get(ME).status == 401
    assert alice.api.get("/api/v1/me/cli-sessions").json() == {"sessions": []}
    assert not (isolated / "credentials.json").exists()
    assert flock_cli("logout").stdout == "Not logged in.\n"


def test_logout_deletes_the_file_when_the_server_is_down(flock_cli):
    credentials.save("http://127.0.0.1:9", "flock_cli_x", {"email": "a@b.c"})
    result = flock_cli("logout")
    assert result.exit_code == 0, result.output
    assert "http://127.0.0.1:9 couldn't be reached" in result.stderr
    assert "until you revoke it there or it expires" in result.stderr
    assert credentials.load() is None


def test_logout_of_a_revoked_sign_in(server, alice, signed_in, flock_cli):
    (session,) = alice.api.get("/api/v1/me/cli-sessions").json()["sessions"]
    alice.api.delete(f"/api/v1/me/cli-sessions/{session['id']}")
    result = flock_cli("logout")
    assert result.exit_code == 0, result.output
    assert result.stderr == ""
    assert credentials.load() is None


def test_logout_refuses_a_token_from_the_environment(server, signed_in, flock_cli, monkeypatch):
    monkeypatch.setenv("FLOCK_TOKEN", signed_in)
    result = flock_cli("logout")
    assert result.exit_code == 1
    assert "FLOCK_TOKEN" in result.stderr
    assert credentials.load() is not None
    assert server.client(token=signed_in).get(ME).status == 200


def test_logout_names_the_wandb_key_it_leaves(server, alice, signed_in, flock_cli):
    saved = credentials.load()
    assert saved is not None
    key = {"name": "wandb on node-7", "prefix": "flock-abcdef"}
    credentials.save(saved["host"], saved["token"], saved["user"], wandb_key=key)
    result = flock_cli("logout")
    assert result.exit_code == 0, result.output
    assert "The wandb SDK stays logged in with the API key wandb on node-7 (flock-abcdef…)" in (
        result.stdout
    )
