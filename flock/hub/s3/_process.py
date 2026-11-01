"""Private AWS profile setup and the program it runs to obtain fresh S3 keys."""

import json
import math
import os
import shlex
import sys
import tempfile
from pathlib import Path

from flock.cli import credentials as flock_credentials

from .client import S3Error, credentials, endpoint

# Keep the profile and its sign-in alive while native clients or spawned workers use it.
_directories: list[tempfile.TemporaryDirectory[str]] = []


def configure_process(
    org: str | None,
    *,
    host: str | None,
    token: str | None,
    ttl: float | None,
    read_only: bool,
) -> dict[str, str]:
    if ttl is not None and (not math.isfinite(ttl) or ttl <= 0):
        raise ValueError("ttl must be positive and finite")
    found = flock_credentials.resolve(host, token)
    if found is None:
        raise S3Error("Not logged in: run flock login, or set FLOCK_TOKEN.")
    current = endpoint(org, host=found.host, token=found.token)

    directory = tempfile.TemporaryDirectory(prefix="flock-s3-")
    root = Path(directory.name)
    command = shlex.join(
        [
            sys.executable,
            "-m",
            "flock.hub.s3._process",
            str(root / "flock.json"),
        ]
    )
    settings = {
        "org": current["org"],
        "host": found.host,
        "token": found.token,
        "ttl": ttl,
        "read_only": read_only,
    }
    try:
        for name, contents in {
            "flock.json": json.dumps(settings),
            "credentials": "",
            "config": (
                "[profile flock]\n"
                f"credential_process = {command}\n"
                f"endpoint_url = {current['endpoint']}\n"
                f"region = {current['region']}\n"
                "s3 =\n    addressing_style = path\n"
            ),
        }.items():
            path = root / name
            path.touch(mode=0o600)
            path.write_text(contents)
    except BaseException:
        directory.cleanup()
        raise
    _directories.append(directory)

    environment = {
        "AWS_PROFILE": "flock",
        "AWS_DEFAULT_PROFILE": "flock",
        "AWS_CONFIG_FILE": str(root / "config"),
        "AWS_SHARED_CREDENTIALS_FILE": str(root / "credentials"),
        "AWS_SDK_LOAD_CONFIG": "1",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS": "false",
        "AWS_ENDPOINT": current["endpoint"],
        "AWS_ENDPOINT_URL": current["endpoint"],
        "AWS_ENDPOINT_URL_S3": current["endpoint"],
        "AWS_REGION": current["region"],
        "AWS_DEFAULT_REGION": current["region"],
        "AWS_VIRTUAL_HOSTED_STYLE_REQUEST": "false",
        "AWS_SKIP_SIGNATURE": "false",
        "ALLOW_HTTP": str(current["endpoint"].startswith("http://")).lower(),
        "AWS_ALLOW_HTTP": str(current["endpoint"].startswith("http://")).lower(),
    }
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        "AWS_PROVIDER_SCHEME",
    ):
        os.environ.pop(name, None)
    os.environ.update(environment)
    return environment


if __name__ == "__main__":
    try:
        settings = json.loads(Path(sys.argv[1]).read_text())
        print(json.dumps(credentials(**settings).credential_process()))
    except (S3Error, OSError, ValueError) as error:
        print(f"Flock credential process failed: {error}", file=sys.stderr)
        sys.exit(1)
