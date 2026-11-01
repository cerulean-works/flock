import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from flock.cli import credentials
from flock.cli import login as login_module
from flock.cli.main import main
from tests.app.conftest import Flocks, FlockServer, Postgres, User
from tests.app.utils import cli_login


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A config dir of the test's own, no Flock or wandb settings from the environment, and no
    terminal (tests that want one patch ``_terminal``). Yields the config dir."""
    for name in list(os.environ):
        if name.startswith(("FLOCK_", "WANDB_")) or name in ("XDG_CONFIG_HOME", "SSH_CONNECTION"):
            monkeypatch.delenv(name)
    config = tmp_path / "config"
    monkeypatch.setenv("FLOCK_CONFIG_DIR", str(config))
    monkeypatch.setenv("WANDB_CONFIG_DIR", str(tmp_path / "wandb"))
    monkeypatch.setenv("NETRC", str(tmp_path / "netrc"))
    monkeypatch.setattr(login_module, "_terminal", lambda: False)
    reset_wandb()
    yield config
    reset_wandb()


def reset_wandb() -> None:
    """Forget the wandb SDK's settings, which it loads once per process."""
    wandb = sys.modules.get("wandb")
    if wandb is not None:
        wandb.teardown()


@pytest.fixture
def make_flock(postgres: Postgres, tmp_path: Path) -> Iterator[Flocks]:
    """Starts flocks on fresh databases, as in the app tests."""
    flocks = Flocks(postgres, tmp_path)
    yield flocks
    flocks.stop()


@pytest.fixture
def server(make_flock: Flocks, monkeypatch: pytest.MonkeyPatch) -> FlockServer:
    """A flock whose device sign-ins ask the CLI to poll every 50 ms."""
    server = make_flock()
    monkeypatch.setattr(server.store.accounts, "DEVICE_INTERVAL", 0.05)
    return server


@pytest.fixture
def alice(server: FlockServer) -> User:
    return server.add_user("alice@lab.test")


class Browser:
    """Stands in for the user's browser: signed in to Flock (a browser session's access token),
    it approves or denies the code ``flock login`` shows, through the API."""

    def __init__(self, server: FlockServer, user: User) -> None:
        self.server = server
        self.token = user.signup["access_token"]
        self.seen: list[dict] = []  # what the approval page showed

    def pending_code(self, timeout: float = 10.0) -> str:
        """The code of the sign-in waiting for approval (what the user reads in the terminal)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = self.server.call(
                self.server.store.db.fetchval(
                    "SELECT user_code FROM device_grants WHERE user_pk IS NULL AND NOT denied"
                )
            )
            if pending is not None:
                return f"{pending[:4]}-{pending[4:]}"
            time.sleep(0.01)
        raise AssertionError("flock login started no sign-in")

    def decide(self, code: str, approve: bool = True) -> None:
        client = self.server.client(token=self.token)
        page = client.get(f"/api/v1/auth/device/{code}")
        assert page.status == 200, page.body
        self.seen.append(page.json())
        decided = client.post(f"/api/v1/auth/device/{code}", {"approve": approve})
        assert decided.status == 204, decided.body

    def answer(self, approve: bool = True) -> threading.Thread:
        """Approves (or denies) the next sign-in in the background, once ``flock login`` starts
        it."""
        thread = threading.Thread(
            target=lambda: self.decide(self.pending_code(), approve), daemon=True
        )
        thread.start()
        return thread


@pytest.fixture
def browser(server: FlockServer, alice: User) -> Browser:
    return Browser(server, alice)


@pytest.fixture
def flock_cli() -> Callable[..., Result]:
    """Runs ``flock`` with arguments, and ``input`` on stdin."""

    def run(*args: str, input: str | None = None) -> Result:
        return CliRunner().invoke(main, list(args), input=input)

    return run


@pytest.fixture
def signed_in(server: FlockServer, alice: User) -> str:
    """Saves a sign-in of ``alice`` to ``server``, as ``flock login`` would; returns its token."""
    token = cli_login(server, server.client(token=alice.signup["access_token"]))
    me = server.client(token=token).get("/api/v1/me").json()
    credentials.save(server.url, token, me)
    return token
