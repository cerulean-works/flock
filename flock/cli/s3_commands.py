"""`flock s3`: S3 keys for the shell and the AWS tools, and an org's buckets."""

import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import click

from flock.cli import s3
from flock.cli.s3 import S3Error

UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> float:
    """Seconds in `text`: a number, optionally followed by s, m, h or d (`90s`, `8h`, `7d`)."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*", text.lower())
    if match is None or float(match[1]) <= 0:
        raise click.BadParameter(f"{text!r} isn't a duration such as 90s, 15m, 8h or 7d")
    return float(match[1]) * UNITS[match[2] or "s"]


class Duration(click.ParamType):
    name = "duration"

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None):
        return value if isinstance(value, float) else parse_duration(str(value))


def _fail(error: S3Error) -> click.ClickException:
    return click.ClickException(str(error))


ORG_HELP = "The org whose buckets the key covers (default: your default org)."
HOST_HELP = "The Flock API (default: FLOCK_HOST, else your sign-in's)."


@click.group("s3")
def s3_group() -> None:
    """S3 access to your org's buckets, through Flock."""


@s3_group.command("credentials")
@click.option("--org", help=ORG_HELP)
@click.option("--ttl", type=Duration(), help="How long the key lives (default: the org's limit).")
@click.option("--read-only", is_flag=True, help="A key that can only read.")
@click.option(
    "--format",
    "output",
    type=click.Choice(["env", "json", "process"]),
    default="env",
    show_default=True,
    help="Shell exports, the API's JSON, or the AWS CLI's credential_process JSON.",
)
@click.option("--host", help=HOST_HELP)
def credentials_command(
    org: str | None, ttl: float | None, read_only: bool, output: str, host: str | None
) -> None:
    """Print a new S3 key for an org's buckets.

    \b
    eval "$(flock s3 credentials)"     # in a shell or job script
    aws s3 ls
    """
    try:
        creds = s3.credentials(org, host=host, ttl=ttl, read_only=read_only)
        current = creds.current()
    except S3Error as error:
        raise _fail(error) from None
    if output == "json":
        click.echo(json.dumps(current, indent=2))
    elif output == "process":
        click.echo(json.dumps(creds.credential_process()))
    else:
        expires = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(current["expires_at"]))
        click.echo(f"# Flock S3 key for {current['org']}, valid until {expires}")
        for name, value in creds.env().items():
            click.echo(f"export {name}={shlex.quote(value)}")


def aws_config_path() -> Path:
    """The AWS CLI's config file: `AWS_CONFIG_FILE`, else `~/.aws/config`."""
    configured = os.environ.get("AWS_CONFIG_FILE")
    return Path(configured).expanduser() if configured else Path.home() / ".aws" / "config"


def _flock_executable() -> str:
    found = shutil.which("flock")
    if found:
        return found
    argv0 = Path(sys.argv[0])
    return str(argv0.resolve()) if argv0.name == "flock" and argv0.exists() else "flock"


def set_profile(text: str, header: str, body: list[str]) -> tuple[str, bool]:
    """`text` (an AWS config file) with the section `header` set to `body`'s lines, replacing
    it if present. Returns the new text and whether a section was replaced."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    section = [header, *body]
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        return "\n".join([*lines, *section]) + "\n", False
    end = start + 1
    while end < len(lines) and not lines[end].lstrip().startswith("["):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():  # keep the blank line before the next
        end -= 1
    return "\n".join([*lines[:start], *section, *lines[end:]]) + "\n", True


@s3_group.command("configure")
@click.option("--org", help=ORG_HELP)
@click.option("--profile", default="flock", show_default=True, help="The AWS profile to write.")
@click.option("--read-only", is_flag=True, help="Only ever fetch keys that can only read.")
@click.option("--host", help=HOST_HELP)
def configure(org: str | None, profile: str, read_only: bool, host: str | None) -> None:
    """Add an AWS profile that fetches Flock S3 keys as it needs them.

    The profile runs `flock s3 credentials --format process` (credential_process), so the AWS
    CLI, boto3 and DuckDB get a new key whenever the last one expires, and none is stored.
    """
    try:
        current = s3.endpoint(org, host=host)
    except S3Error as error:
        raise _fail(error) from None
    command = [_flock_executable(), "s3", "credentials", "--org", current["org"]]
    command += ["--format", "process"]
    if read_only:
        command.append("--read-only")
    if host:
        command += ["--host", host]
    header = "[default]" if profile == "default" else f"[profile {profile}]"
    body = [
        f"credential_process = {shlex.join(command)}",
        f"endpoint_url = {current['endpoint']}",
        f"region = {current['region']}",
        "s3 =",
        "    addressing_style = path",
    ]
    path = aws_config_path()
    text = path.read_text() if path.exists() else ""
    exists = header in (line.strip() for line in text.splitlines())
    if exists and not click.confirm(f"{path} already has the profile {profile}. Replace it?"):
        raise click.ClickException("Nothing was changed.")
    updated, _ = set_profile(text, header, body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated)
    click.echo(f"✓ Wrote the profile {profile} to {path}, for {current['org']}'s buckets.")
    click.echo(f"  Try: aws s3 ls --profile {profile}")


@s3_group.group("buckets")
def buckets() -> None:
    """List, create and delete your org's buckets."""


@buckets.command("list")
@click.option("--org", help="The org (default: your default org).")
@click.option("--host", help=HOST_HELP)
@click.option("--json", "as_json", is_flag=True, help="Print the API's answer as JSON.")
def list_command(org: str | None, host: str | None, as_json: bool) -> None:
    """List an org's buckets."""
    try:
        found = s3.list_buckets(org, host=host)
    except S3Error as error:
        raise _fail(error) from None
    if as_json:
        click.echo(json.dumps(found, indent=2))
        return
    if not found:
        click.echo("No buckets yet. Create one with: flock s3 buckets create NAME")
    for bucket in found:
        created = time.strftime("%Y-%m-%d", time.localtime(bucket["created_at"]))
        by = bucket["created_by"]["email"] if bucket["created_by"] else "a deleted user"
        click.echo(f"{bucket['name']}\tcreated {created} by {by}")


@buckets.command("create")
@click.argument("names", nargs=-1, required=True)
@click.option("--org", help="The org (default: your default org).")
@click.option("--host", help=HOST_HELP)
def create_command(names: tuple[str, ...], org: str | None, host: str | None) -> None:
    """Create buckets. Names are lowercase letters, digits, hyphens and dots."""
    for name in names:
        try:
            s3.create_bucket(name, org, host=host)
        except S3Error as error:
            raise click.ClickException(f"Couldn't create {name}: {error}") from None
        click.echo(f"✓ Created {name}: s3://{name}")


@buckets.command("delete")
@click.argument("names", nargs=-1, required=True)
@click.option("--org", help="The org (default: your default org).")
@click.option("--host", help=HOST_HELP)
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
def delete_command(names: tuple[str, ...], org: str | None, host: str | None, yes: bool) -> None:
    """Delete buckets and everything in them (org admins only). There is no undo."""
    listed = ", ".join(names)
    if not yes and not click.confirm(f"Delete {listed} and everything in them?"):
        raise click.ClickException("Nothing was deleted.")
    for name in names:
        try:
            s3.delete_bucket(name, org, host=host)
        except S3Error as error:
            raise click.ClickException(f"Couldn't delete {name}: {error}") from None
        click.echo(f"✓ Deleted {name}; it's being emptied and removed in the background.")
