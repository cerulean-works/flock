"""AWS profiles that run `flock s3 credentials --format process` (D59)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import boto3
import pytest

from tests.s3.conftest import S3Env


@pytest.fixture
def flock_env(env: S3Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """``flock`` signed in as alice through ``FLOCK_TOKEN``, and an AWS config of the test's
    own; returns the environment for subprocesses."""
    for name in list(os.environ):
        if name.startswith(("AWS_", "FLOCK_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("FLOCK_TOKEN", env.users["alice"].token)
    monkeypatch.setenv("FLOCK_HOST", env.flock.url)
    monkeypatch.setenv("FLOCK_CONFIG_DIR", str(tmp_path / "flock"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    return dict(os.environ)


def flock_executable() -> str:
    found = shutil.which("flock") or str(Path(sys.executable).parent / "flock")
    if not Path(found).exists():
        pytest.skip("the flock command isn't installed")
    return found


def keys(env: S3Env) -> set[str]:
    listed = env.api("alice").get("/api/v1/me/s3-keys").json()["keys"]
    return {k["access_key_id"] for k in listed}


def test_flock_s3_configure_profile(env: S3Env, flock_env: dict[str, str]):
    bucket = env.bucket()
    done = subprocess.run(
        [flock_executable(), "s3", "configure", "--profile", "lab"],
        env=flock_env, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    session = boto3.Session(profile_name="lab")
    client = session.client("s3")
    client.put_object(Bucket=bucket, Key="x", Body=b"through the profile")
    assert client.get_object(Bucket=bucket, Key="x")["Body"].read() == b"through the profile"

    aws = shutil.which("aws")
    if aws is not None:
        listed = subprocess.run(
            [aws, "s3", "ls", f"s3://{bucket}/", "--profile", "lab"],
            env=flock_env, capture_output=True, text=True, timeout=60, check=False,
        )  # fmt: skip
        assert listed.returncode == 0, listed.stderr
        assert " x\n" in listed.stdout


def test_credential_process_rotates_short_keys(env: S3Env, flock_env: dict[str, str]):
    """botocore asks the process again once a key has fewer than 15 minutes left: with keys of
    11 minutes, each request gets a new one."""
    bucket = env.bucket()
    command = f"{flock_executable()} s3 credentials --format process --ttl 11m"
    Path(flock_env["AWS_CONFIG_FILE"]).write_text(
        "[profile rotating]\n"
        f"credential_process = {command}\n"
        f"endpoint_url = {env.proxy_url}\n"
        "region = us-east-1\n"
        "s3 =\n    addressing_style = path\n"
    )
    before = keys(env)
    client = boto3.Session(profile_name="rotating").client("s3")
    client.put_object(Bucket=bucket, Key="x", Body=b"x")
    after_put = len(keys(env) - before)
    assert after_put >= 1
    client.get_object(Bucket=bucket, Key="x")
    assert len(keys(env) - before) > after_put
