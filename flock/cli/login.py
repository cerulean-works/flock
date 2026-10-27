"""`flock login` and `flock logout`."""

import os
import platform
import socket
import sys
import time
import webbrowser
from typing import Any

import click

from flock.cli import credentials
from flock.cli.api import Api, error_of
from flock.cli.credentials import DEFAULT_HOST, normalize_host

HOST_HELP = (
    "The Flock API's URL. Default: FLOCK_HOST, else the server you're logged in to, "
    f"else {DEFAULT_HOST}."
)


def _terminal() -> bool:
    """Whether someone is at a terminal to answer questions."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def login_host(host: str | None) -> str:
    """The server `flock login` signs in to: `--host`, `FLOCK_HOST`, the saved sign-in's
    server, else the default."""
    saved = credentials.load()
    chosen = host or os.environ.get("FLOCK_HOST") or (saved["host"] if saved else DEFAULT_HOST)
    return normalize_host(chosen)


@click.command()
@click.option("--host", help=HOST_HELP)
@click.option(
    "--no-browser", is_flag=True, help="Only print the URL and code, without opening a browser."
)
@click.option(
    "--wandb/--no-wandb",
    "wandb_login",
    default=None,
    help="Also log the wandb SDK in to this Flock server, or don't (default: ask, yes).",
)
def login(host: str | None, no_browser: bool, wandb_login: bool | None) -> None:
    """Log in to Flock on this machine, approving the sign-in in a browser."""
    host = login_host(host)
    try:
        if not _should_sign_in(host):
            return
        token, user = _device_sign_in(host, no_browser)
    except KeyboardInterrupt:
        raise click.ClickException("Login interrupted: nothing was saved.") from None
    credentials.save(host, token, user)
    click.echo(f"✓ Logged in to {host} as {user['email']}")
    if os.environ.get("FLOCK_TOKEN"):
        click.echo("Note: FLOCK_TOKEN is set, and Flock clients use it before this sign-in.")
    key = _log_in_wandb(host, token, wandb_login)
    if key is not None:
        credentials.save(host, token, user, wandb_key=key)


def _should_sign_in(host: str) -> bool:
    """False when the saved sign-in still works for ``host`` and isn't to be replaced."""
    saved = credentials.load()
    if saved is None or saved["host"] != host:
        return True
    me = Api(host, saved["token"]).get("me")
    if me.status_code != 200:
        return True
    email = me.json()["email"]
    if not _terminal():
        click.echo(f"Already logged in to {host} as {email}.")
        return False
    return click.confirm(f"Already logged in as {email}. Log in again?", default=False)


def _device_sign_in(host: str, no_browser: bool) -> tuple[str, dict[str, Any]]:
    """Runs a device sign-in (RFC 8628); returns the CLI token and the user."""
    api = Api(host)
    client = {
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.machine()}",
    }
    started = api.post("auth/device", {"client": client})
    if started.status_code != 200:
        raise click.ClickException(f"{host} couldn't start a sign-in: {error_of(started)}")
    grant = started.json()
    click.echo(f"! Copy your one-time code: {grant['user_code']}")
    uri = grant["verification_uri"]
    if not no_browser and _terminal() and not os.environ.get("SSH_CONNECTION"):
        input(f"Press Enter to open {uri} in your browser...")
        if not webbrowser.open(grant["verification_uri_complete"]):
            click.echo(f"Couldn't open a browser: open {uri} and enter the code.")
    else:
        click.echo(f"Open {uri} in a browser where you're signed in to Flock, and enter the code.")

    interval = float(grant["interval"])
    while True:
        time.sleep(interval)
        polled = api.post("auth/device/token", {"device_code": grant["device_code"]})
        if polled.status_code == 200:
            body = polled.json()
            return body["access_token"], body["user"]
        match error_of(polled):
            case "authorization_pending":
                pass
            case "slow_down":
                interval += 5
            case "access_denied":
                raise click.ClickException("The sign-in was denied in the browser.")
            case "expired_token":
                raise click.ClickException(
                    "The code expired before it was approved: run flock login again."
                )
            case reason:
                raise click.ClickException(f"Sign-in failed: {reason}")


def _wandb_key(host: str) -> str | None:
    """The API key the wandb SDK would send, if it's pointed at ``host``. An SDK whose
    internals moved reads as not logged in."""
    try:
        from wandb.errors import Error as WandbError
        from wandb.sdk import wandb_setup
        from wandb.sdk.lib import wbauth
    except ImportError:
        return None
    try:
        settings = wandb_setup.singleton().settings
        if normalize_host(settings.base_url) != host:
            return None
        return settings.api_key or wbauth.read_netrc_auth(host=host)
    except WandbError, AttributeError, OSError, ValueError:  # e.g. a malformed .netrc
        return None


def _key_works(host: str, key: str) -> bool:
    try:
        return Api(host, key).get("me").status_code == 200
    except click.ClickException:
        return False


def _log_in_wandb(host: str, token: str, choice: bool | None) -> dict[str, str] | None:
    """Points the wandb SDK at ``host`` with a new API key, unless declined (``choice``, else
    ask, defaulting to yes; without a terminal, yes). Returns the key's name and prefix when it made one."""
    if choice is False:
        return None
    try:
        import wandb
    except ImportError:
        click.echo(
            "The wandb SDK isn't installed. To log it in to this Flock server later, create an "
            f"API key on your account page and run: wandb login --host {host} <key>"
        )
        return None
    key = _wandb_key(host)
    if key is not None and _key_works(host, key):
        click.echo(f"The wandb SDK is already logged in to {host}.")
        return None
    question = "Also log the wandb SDK in to this Flock server, for experiment tracking?"
    if choice is None and _terminal() and not click.confirm(question, default=True):
        return None
    name = f"wandb on {socket.gethostname()}"
    created = Api(host, token).post("me/api-keys", {"name": name})
    if created.status_code != 201:
        raise click.ClickException(f"Couldn't create an API key for wandb: {error_of(created)}")
    api_key = created.json()
    try:
        wandb.login(key=api_key["key"], host=host, relogin=True, verify=True)
    except (wandb.Error, OSError, ValueError) as error:
        raise click.ClickException(
            f"wandb couldn't log in ({error}). Revoke the API key {name} on your account page."
        ) from None
    click.echo(f"✓ Logged the wandb SDK in to {host} with a new API key, {name}")
    return {"name": name, "prefix": api_key["prefix"]}


@click.command()
@click.option("--host", help="Log out of this server only.")
def logout(host: str | None) -> None:
    """Log out of Flock on this machine, ending its sign-in on the server."""
    found = credentials.resolve(host)
    if found is None:
        click.echo("Not logged in.")
        return
    if found.source == "env":
        raise click.ClickException(
            "The credential comes from FLOCK_TOKEN, which flock logout can't change: unset it."
        )
    saved = credentials.load() or {}
    try:
        ended = Api(found.host, found.token).post("auth/logout")
    except click.ClickException:
        ended = None
    # 401: the session already ended (revoked on the account page, or expired).
    if ended is None or ended.status_code not in (204, 401):
        reason = "couldn't be reached" if ended is None else f"refused ({error_of(ended)})"
        click.echo(
            f"Warning: {found.host} {reason}. The sign-in stays listed under Command line "
            "sign-ins on your account page until you revoke it there or it expires.",
            err=True,
        )
    credentials.delete()
    click.echo(f"Logged out of {found.host}.")
    if key := saved.get("wandb_key"):
        click.echo(
            f"The wandb SDK stays logged in with the API key {key['name']} ({key['prefix']}…). "
            "Revoke it on your account page to end that too."
        )
