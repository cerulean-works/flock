"""The `flock` command line."""

import click

from flock.cli.account import token, whoami
from flock.cli.login import login, logout


@click.group()
def main() -> None:
    """Flock from the command line."""


main.add_command(login)
main.add_command(logout)
main.add_command(whoami)
main.add_command(token)
