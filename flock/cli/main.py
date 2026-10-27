"""The `flock` command line."""

import click

from flock.cli.account import token, whoami
from flock.cli.login import login, logout
from flock.cli.s3_commands import s3_group


@click.group()
def main() -> None:
    """Flock from the command line."""


main.add_command(login)
main.add_command(logout)
main.add_command(whoami)
main.add_command(token)
main.add_command(s3_group)
