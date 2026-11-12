"""`flock.hub.s3` and `flock s3` (D58, D59), against a flock with S3 on."""

from __future__ import annotations

import configparser
import datetime as dt
import json
import os
import pickle
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from flock.cli import credentials
from flock.hub import s3
from flock.hub.cli import parse_duration, set_profile
from tests.app.conftest import Flocks, S3Flock, User
from tests.app.utils import cli_login
from tests.conftest import S3Server

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from click.testing import Result

    from tests.conftest import Clock


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


@pytest.fixture
def alice(flock: S3Flock) -> User:
    return flock.add_user("alice@lab.test")


@pytest.fixture
def signed_in(flock: S3Flock, alice: User) -> str:
    """A saved sign-in of ``alice``, as ``flock login`` leaves it."""
    token = cli_login(flock, flock.client(token=alice.signup["access_token"]), "laptop")
    credentials.save(flock.url, token, flock.client(token=token).get("/api/v1/me").json())
    return token


def keys_of(flock: S3Flock, alice: User) -> list[dict]:
    return alice.api.get("/api/v1/me/s3-keys").json()["keys"]


@pytest.fixture(params=["cli", "python"])
def native_lance_profile(
    flock: S3Flock,
    signed_in: str,
    flock_cli: Callable[..., Result],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[None]:
    previous = dict(os.environ)
    try:
        if request.param == "python":
            os.environ.update(
                {
                    "AWS_ACCESS_KEY_ID": "stale-key",
                    "AWS_SECRET_ACCESS_KEY": "stale-secret",
                    "AWS_SESSION_TOKEN": "stale-session",
                    "AWS_PROFILE": "missing-profile",
                    "AWS_DEFAULT_PROFILE": "another-missing-profile",
                    "AWS_CONFIG_FILE": str(tmp_path / "missing-config"),
                    "AWS_SHARED_CREDENTIALS_FILE": str(tmp_path / "missing-credentials"),
                    "AWS_PROVIDER_SCHEME": "token",
                    "AWS_ENDPOINT": "https://wrong.invalid",
                    "AWS_ENDPOINT_URL_S3": "https://wrong.invalid",
                    "AWS_REGION": "wrong-region",
                    "AWS_VIRTUAL_HOSTED_STYLE_REQUEST": "true",
                    "ALLOW_HTTP": "false",
                }
            )
            s3.get_process(org="flock", host=flock.url, token=signed_in, ttl=3, read_only=True)
        else:
            _configure_native_lance_profile(flock, flock_cli, tmp_path, monkeypatch)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def _configure_native_lance_profile(
    flock: S3Flock,
    flock_cli: Callable[..., Result],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real Flock credential_process, isolated from the user's AWS configuration."""
    for name in list(os.environ):
        if name.startswith("AWS_"):
            monkeypatch.delenv(name)
    config = tmp_path / "aws-config"
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("ALLOW_HTTP", "true")  # The test proxy uses localhost HTTP.
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}")
    result = flock_cli(
        "s3",
        "configure",
        "--org",
        "flock",
        "--profile",
        "lance",
        "--read-only",
        "--host",
        flock.url,
    )
    assert result.exit_code == 0, result.output
    # Shorten only this test profile's keys so the real expiry path is quick to exercise.
    config.write_text(
        "\n".join(
            f"{line} --ttl 3s" if line.startswith("credential_process = ") else line
            for line in config.read_text().splitlines()
        )
        + "\n"
    )
    monkeypatch.setenv("AWS_PROFILE", "lance")


def test_get_process_pins_sign_in_and_sets_the_entire_environment(
    flock: S3Flock,
    alice: User,
    signed_in: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = dict(os.environ)
    user_config = tmp_path / "user-config"
    user_config.write_text("[default]\nregion = untouched\n")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(user_config))
    try:
        environment = s3.get_process(
            org="flock",
            host=flock.url,
            token=signed_in,
            ttl=3600,
            read_only=True,
        )
        assert all(os.environ[name] == value for name, value in environment.items())
        assert environment["AWS_ENDPOINT"] == environment["AWS_ENDPOINT_URL"] == flock.proxy.url
        assert environment["AWS_ENDPOINT_URL_S3"] == flock.proxy.url
        assert environment["AWS_REGION"] == environment["AWS_DEFAULT_REGION"] == "us-east-1"
        assert environment["ALLOW_HTTP"] == "true"
        assert user_config.read_text() == "[default]\nregion = untouched\n"
        config = Path(environment["AWS_CONFIG_FILE"])
        parser = configparser.ConfigParser()
        parser.read(config)
        command = shlex.split(parser["profile flock"]["credential_process"])
        assert command[0] == sys.executable
        assert signed_in not in parser["profile flock"]["credential_process"]
        assert signed_in not in config.read_text()
        settings = Path(command[-1])
        assert stat.S_IMODE(settings.stat().st_mode) == 0o600
        assert stat.S_IMODE(settings.parent.stat().st_mode) == 0o700
        assert keys_of(flock, alice) == []  # setup discovers the endpoint without issuing a key

        # The provider retains its resolved identity when ambient sign-ins change.
        os.environ["FLOCK_TOKEN"] = "wrong-token"
        os.environ["FLOCK_HOST"] = "https://wrong.invalid"
        done = subprocess.run(
            command,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        issued = json.loads(done.stdout)
        assert issued["Version"] == 1
        assert issued["Expiration"].endswith("Z")
        assert issued["AccessKeyId"] in {key["access_key_id"] for key in keys_of(flock, alice)}
        assert "AWS_ACCESS_KEY_ID" not in os.environ
        assert signed_in not in done.stdout + done.stderr
    finally:
        os.environ.clear()
        os.environ.update(previous)


@pytest.mark.parametrize("ttl", [0, -1, float("nan"), float("inf")])
def test_get_process_rejects_invalid_lifetimes_without_changing_environment(ttl: float) -> None:
    previous = dict(os.environ)
    with pytest.raises(ValueError, match="positive and finite"):
        s3.get_process(ttl=ttl)
    assert dict(os.environ) == previous


def test_credentials_formats(
    flock: S3Flock, alice: User, signed_in: str, flock_cli: Callable[..., Result]
) -> None:
    env = flock_cli("s3", "credentials")
    assert env.exit_code == 0, env.output
    exports = dict(
        line.removeprefix("export ").split("=", 1)
        for line in env.output.splitlines()
        if line.startswith("export ")
    )
    assert env.output.startswith("# Flock S3 key for flock, valid until ")
    assert exports["AWS_ENDPOINT_URL"] == flock.proxy.url
    assert exports["AWS_REGION"] == exports["AWS_DEFAULT_REGION"] == "us-east-1"
    (key,) = keys_of(flock, alice)
    assert shlex.split(exports["AWS_ACCESS_KEY_ID"]) == [key["access_key_id"]]
    assert key["source"] == "laptop"

    as_json = flock_cli("s3", "credentials", "--format", "json", "--ttl", "15m", "--read-only")
    assert as_json.exit_code == 0, as_json.output
    body = json.loads(as_json.output)
    assert set(body) == {
        "endpoint", "region", "org", "access_key_id", "secret_access_key", "read_only",
        "expires_at",
    }  # fmt: skip
    assert body["read_only"] is True
    assert abs(body["expires_at"] - time.time() - 900) < 5

    process = flock_cli("s3", "credentials", "--format", "process")
    assert process.exit_code == 0, process.output
    body = json.loads(process.output)
    assert body["Version"] == 1
    assert set(body) == {"Version", "AccessKeyId", "SecretAccessKey", "Expiration"}
    assert body["Expiration"].endswith("Z")
    assert len(keys_of(flock, alice)) == 3


def test_errors_exit_1(
    make_flock: Flocks, flock_cli: Callable[..., Result], flock: S3Flock
) -> None:
    signed_out = flock_cli("s3", "credentials")
    assert signed_out.exit_code == 1
    assert "Not logged in" in signed_out.output

    plain = make_flock()
    user = plain.add_user("bob@lab.test")
    token = cli_login(plain, plain.client(token=user.signup["access_token"]))
    credentials.save(plain.url, token, {})
    for args in (["credentials"], ["buckets", "list"], ["configure"]):
        off = flock_cli("s3", *args)
        assert off.exit_code == 1, off.output
        assert "S3 isn't enabled" in off.output

    credentials.save(plain.url, "flock_cli_revoked", {})
    refused = flock_cli("s3", "credentials")
    assert refused.exit_code == 1
    assert "run flock login" in refused.output

    assert flock_cli("s3", "credentials", "--ttl", "soon").exit_code == 2


def test_durations() -> None:
    assert parse_duration("90") == 90
    assert parse_duration("90s") == 90
    assert parse_duration("15m") == 900
    assert parse_duration("8h") == 8 * 3600
    assert parse_duration("1.5d") == 1.5 * 86400


def test_configure_writes_a_profile_and_asks_before_replacing_one(
    flock: S3Flock,
    alice: User,
    signed_in: str,
    flock_cli: Callable[..., Result],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "aws" / "config"
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    config.parent.mkdir()
    config.write_text("[default]\nregion = eu-west-1\n\n[profile other]\noutput = json\n")
    done = flock_cli("s3", "configure")
    assert done.exit_code == 0, done.output
    text = config.read_text()
    assert text.startswith("[default]\nregion = eu-west-1\n\n[profile other]\noutput = json\n\n")
    assert "[profile flock]\ncredential_process = " in text
    assert f"endpoint_url = {flock.proxy.url}\n" in text
    assert "s3 =\n    addressing_style = path\n" in text
    process = next(line for line in text.splitlines() if line.startswith("credential_process"))
    command = shlex.split(process.partition(" = ")[2])
    assert command[1:] == ["s3", "credentials", "--org", "flock", "--format", "process"]
    assert keys_of(flock, alice) == []  # configuring fetches no key

    # Replacing asks first; no keeps the file as it is.
    kept = flock_cli("s3", "configure", "--read-only", input="n\n")
    assert kept.exit_code == 1
    assert config.read_text() == text
    replaced = flock_cli("s3", "configure", "--read-only", input="y\n")
    assert replaced.exit_code == 0, replaced.output
    updated = config.read_text()
    assert updated.count("[profile flock]") == 1
    assert "--format process --read-only" in updated
    assert updated.startswith("[default]\nregion = eu-west-1\n\n[profile other]\noutput = json\n")

    # Another profile is added beside it.
    assert flock_cli("s3", "configure", "--profile", "lab").exit_code == 0
    assert "[profile lab]" in config.read_text() and "[profile flock]" in config.read_text()


def test_set_profile_keeps_what_follows() -> None:
    text = "[profile a]\nx = 1\n\n[profile b]\ny = 2\n"
    replaced, found = set_profile(text, "[profile a]", ["z = 3"])
    assert found
    assert replaced == "[profile a]\nz = 3\n\n[profile b]\ny = 2\n"
    added, found = set_profile("", "[profile c]", ["w = 4"])
    assert (added, found) == ("[profile c]\nw = 4\n", False)


def test_buckets_commands(
    flock: S3Flock, alice: User, signed_in: str, flock_cli: Callable[..., Result]
) -> None:
    # Every org has flock-datasets and flock-models, which Flock manages (013, 014).
    managed = flock_cli("s3", "buckets", "list")
    assert managed.exit_code == 0
    assert managed.output.startswith("flock-datasets\t") and "managed by Flock" in managed.output
    created = flock_cli("s3", "buckets", "create", "checkpoints", "shards")
    assert created.exit_code == 0, created.output
    assert "s3://checkpoints" in created.output
    listed = flock_cli("s3", "buckets", "list")
    assert [line.split("\t")[0] for line in listed.output.splitlines()] == [
        "checkpoints",
        "flock-datasets",
        "flock-models",
        "shards",
    ]
    assert "by alice@lab.test" in listed.output
    as_json = json.loads(flock_cli("s3", "buckets", "list", "--json").output)
    assert as_json[0]["upstream_name"] == f"{flock.bucket_prefix}00001-checkpoints"
    bad = flock_cli("s3", "buckets", "create", "Bad_Name")
    assert bad.exit_code == 1 and "Couldn't create Bad_Name" in bad.output

    # Members can't delete buckets.
    refused = flock_cli("s3", "buckets", "delete", "--yes", "shards")
    assert refused.exit_code == 1 and "only admins" in refused.output
    assert flock_cli("s3", "buckets", "delete", "shards", input="n\n").exit_code == 1

    token = cli_login(flock, flock.client(token=flock_admin_token(flock)), "admin-laptop")
    credentials.save(flock.url, token, {})
    deleted = flock_cli("s3", "buckets", "delete", "shards", input="y\n")
    assert deleted.exit_code == 0, deleted.output
    assert [b["name"] for b in s3.list_buckets()] == [
        "checkpoints",
        "flock-datasets",
        "flock-models",
    ]


def flock_admin_token(flock: S3Flock) -> str:
    """A browser session of the flock's super admin."""
    admin = flock.call(flock.store.accounts.user_by_email(flock.ADMIN_EMAIL))
    return flock.call(flock.store.accounts.start_session(admin))["access_token"]


def test_credentials_renew_before_they_expire(
    flock: S3Flock, alice: User, signed_in: str, clock: Callable[[str], Clock]
) -> None:
    wall = clock("flock.hub.s3.client")
    creds = s3.credentials(ttl=120)
    first = creds.access_key_id
    assert creds.org == "flock"
    assert creds.access_key_id == first  # not yet due: half its lifetime is left
    wall.advance(61)
    second = creds.access_key_id
    assert second != first
    assert creds.uri("checkpoints", "run-1/", "model.pt") == "s3://checkpoints/run-1/model.pt"
    options = creds.storage_options()
    assert options["aws_endpoint"] == flock.proxy.url
    assert options["allow_http"] == "true"
    assert creds.env()["AWS_ACCESS_KEY_ID"] == creds.access_key_id
    assert creds.fsspec_options()["endpoint_url"] == flock.proxy.url


def test_credentials_pickle_without_their_key(flock: S3Flock, alice: User, signed_in: str) -> None:
    creds = s3.credentials()
    secret = creds.secret_access_key
    data = pickle.dumps(creds)
    assert secret.encode() not in data
    assert creds.access_key_id.encode() not in data
    copy = pickle.loads(data)
    assert copy.org == "flock"
    assert copy.access_key_id != creds.access_key_id  # it fetched its own
    assert len(keys_of(flock, alice)) == 2


def test_boto3_clients_renew_their_key(
    flock: S3Flock, alice: User, signed_in: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice.api.post("/api/v1/orgs/flock/buckets", {"name": "data"})
    creds = s3.credentials(ttl=240)
    client = creds.boto3_client()
    client.put_object(Bucket="data", Key="x", Body=b"x")
    used = {k["access_key_id"] for k in keys_of(flock, alice)}
    # Exercise botocore's real refresh callback while the original server key stays valid.
    monkeypatch.setattr(
        client._request_signer._credentials,
        "_time_fetcher",
        lambda: dt.datetime.now(dt.UTC) + dt.timedelta(seconds=121),
    )
    assert client.get_object(Bucket="data", Key="x")["Body"].read() == b"x"
    assert len({k["access_key_id"] for k in keys_of(flock, alice)} - used) == 1
    assert s3.create_bucket("more")["name"] == "more"
    names = [b["Name"] for b in client.list_buckets()["Buckets"]]
    assert names == ["data", "flock-datasets", "flock-models", "more"]


def test_lance_native_profile_renews_after_expiry(
    flock: S3Flock, alice: User, native_lance_profile: None
) -> None:
    import lance
    import pyarrow as pa

    writer = s3.credentials()
    uri = writer.uri("flock-datasets", "renewal")
    lance.write_dataset(
        pa.table({"text": ["first"]}), uri, storage_options=writer.storage_options()
    )
    lance.write_dataset(
        pa.table({"text": ["second"]}),
        uri,
        mode="append",
        storage_options=writer.storage_options(),
    )
    dataset = lance.dataset(uri, version=2)
    assert dataset.take([0]).to_pylist() == [{"text": "first"}]
    before_keys = keys_of(flock, alice)
    before = {key["access_key_id"] for key in before_keys}
    short = [key for key in before_keys if key["read_only"]]
    assert short
    time.sleep(max(key["expires_at"] for key in short) - time.time() + 0.05)

    # Read another fragment through the same open dataset, after the proxy key expires.
    assert dataset.take([1]).to_pylist() == [{"text": "second"}]
    after = {key["access_key_id"] for key in keys_of(flock, alice)}
    assert not {key["access_key_id"] for key in short} & after
    assert after - before


def test_spawned_lance_worker_renews_native_profile_between_epochs(
    flock: S3Flock,
    alice: User,
    native_lance_profile: None,
) -> None:
    import lance
    import pyarrow as pa
    from lance.torch.data import SafeLanceDataset
    from torch.utils.data import DataLoader
    from torch.utils.data.dataloader import _MultiProcessingDataLoaderIter

    writer = s3.credentials()
    uri = writer.uri("flock-datasets", "worker-renewal")
    rows = [{"text": "first"}, {"text": "second"}]
    lance.write_dataset(pa.Table.from_pylist(rows), uri, storage_options=writer.storage_options())
    dataset = SafeLanceDataset(uri, dataset_options={"version": 1})
    loader = DataLoader(
        dataset,
        batch_size=1,
        num_workers=1,
        multiprocessing_context="spawn",
        persistent_workers=True,
        prefetch_factor=1,
        timeout=120,
    )
    iterator = iter(loader)
    assert isinstance(iterator, _MultiProcessingDataLoaderIter)
    try:
        assert [text for batch in iterator for text in batch["text"]] == ["first", "second"]
        before = {key["access_key_id"] for key in keys_of(flock, alice)}
        # Only the writer's long-lived key survives this delay.
        time.sleep(3.05)
        assert [text for batch in loader for text in batch["text"]] == ["first", "second"]
        after = {key["access_key_id"] for key in keys_of(flock, alice)}
        assert after - before
    finally:
        iterator._shutdown_workers()


def test_the_module_imports_no_s3_library() -> None:
    source = Path(s3.__file__).read_text()
    top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert all(
        "boto" not in line and "lance" not in line and "s3fs" not in line for line in top_level
    )
