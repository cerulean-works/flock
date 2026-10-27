"""`flock whoami` and `flock token`."""

import json

import click

from flock.cli import credentials
from flock.cli.api import Api, error_of
from flock.cli.credentials import Credentials

SOURCES = {"file": "flock login", "env": "FLOCK_TOKEN", "argument": "an argument"}


def _resolve(host: str | None) -> Credentials:
    found = credentials.resolve(host)
    if found is None:
        where = f" to {credentials.normalize_host(host)}" if host else ""
        raise click.ClickException(f"Not logged in{where}. Run flock login.")
    return found


@click.command()
@click.option("--host", help="The Flock API to ask (default: FLOCK_HOST, else your sign-in's).")
@click.option("--json", "as_json", is_flag=True, help="Print GET /api/v1/me's answer as JSON.")
def whoami(host: str | None, as_json: bool) -> None:
    """Show who you're logged in to Flock as, on which server."""
    found = _resolve(host)
    me = Api(found.host, found.token).get("me")
    if me.status_code == 401:
        reason = error_of(me)
        if not reason.startswith("wandb keys"):  # else the server's reason says what to use
            reason = "it was revoked or has expired. Run flock login."
        raise click.ClickException(
            f"{found.host} refused the credential from {SOURCES[found.source]}: {reason}"
        )
    if me.status_code != 200:
        raise click.ClickException(f"{found.host} answered: {error_of(me)}")
    profile = me.json()
    if as_json:
        click.echo(json.dumps({**profile, "host": found.host, "source": found.source}, indent=2))
        return
    click.echo(f"{profile['name']} <{profile['email']}>")
    click.echo(f"  Server: {found.host}")
    orgs = [
        f"{org['display_name']} ({org['name']}), {org['role']}"
        + (", default" if org["name"] == profile.get("default_org") else "")
        for org in profile.get("orgs", [])
    ]
    click.echo(f"  Orgs:   {orgs[0] if orgs else 'none'}")
    for line in orgs[1:]:
        click.echo(f"          {line}")
    source = SOURCES[found.source]
    if found.source == "file":
        source += f" ({credentials.credentials_path()})"
    click.echo(f"  From:   {source}")


@click.command()
@click.option("--host", help="The Flock API the token is for (default: FLOCK_HOST, else any).")
def token(host: str | None) -> None:
    """Print your Flock token, for handing to another tool. It doesn't contact the server."""
    click.echo(_resolve(host).token)
