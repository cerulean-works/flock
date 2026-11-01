"""The `flock` command line."""

import click

from flock.cli.account import token, whoami
from flock.cli.login import login, logout
from flock.eval.cli import main as eval_cli
from flock.hub.cli import models_group, s3_group


@click.group()
def main() -> None:
    """Flock from the command line."""


main.add_command(eval_cli, name="eval")

main.add_command(login)
main.add_command(logout)
main.add_command(whoami)
main.add_command(token)
main.add_command(s3_group)
main.add_command(models_group)
