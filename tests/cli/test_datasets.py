"""`flock datasets` (D122) and `flock.hub.datasets.convert` through the proxy to
`s3://flock-datasets`, against a flock with S3 on."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import lance
import pytest

from flock.cli import credentials
from flock.hub import datasets, s3
from tests.app.conftest import Flocks, S3Flock, User
from tests.app.utils import cli_login
from tests.conftest import S3Server
from tests.hub.test_datasets_download import FakeHub
from tests.s3.test_datasets_import import rows, write_source

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from click.testing import Result


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


@pytest.fixture
def alice(flock: S3Flock) -> User:
    return flock.add_user("alice@lab.test")


@pytest.fixture
def signed_in(flock: S3Flock, alice: User) -> Iterator[str]:
    """A saved sign-in of ``alice``; the AWS settings `convert` puts in the environment are
    undone afterwards."""
    previous = dict(os.environ)
    token = cli_login(flock, flock.client(token=alice.signup["access_token"]), "laptop")
    credentials.save(flock.url, token, flock.client(token=token).get("/api/v1/me").json())
    try:
        yield token
    finally:
        os.environ.clear()
        os.environ.update(previous)


def read_card(key: str) -> str:
    client = s3.credentials().boto3_client()
    return client.get_object(Bucket="flock-datasets", Key=key)["Body"].read().decode()


def test_convert_and_list_through_the_proxy(
    signed_in: str, flock_cli: Callable[..., Result], tmp_path: Path
) -> None:
    folder = tmp_path / "download"
    write_source(folder)

    done = flock_cli("datasets", "convert", str(folder), "imports/words", "--workers", "2")
    assert done.exit_code == 0, done.output
    assert "✓ imports/words/train: version 1, 500 rows" in done.output
    assert "✓ imports/words/test: version 1, 10 rows" in done.output
    assert "/4: data/train-0.parquet (250 rows, " in done.output  # a line per file, off a terminal

    train = lance.dataset("s3://flock-datasets/imports/words/train")
    assert train.to_table().to_pylist() == rows(0, 500)
    transaction = train.read_transaction(1)
    assert transaction is not None and transaction.transaction_properties is not None
    assert '"repo": "acme/words"' in transaction.transaction_properties["flock.source"]
    assert read_card("imports/words/README.md") == "# A dataset\n"
    assert read_card("imports/words/test/README.md") == "# A dataset\n"

    again = flock_cli("datasets", "convert", str(folder), "imports/words")
    assert again.exit_code == 1
    assert "already has a dataset at imports/words/train" in again.output
    appended = flock_cli("datasets", "convert", str(folder), "imports/words", "--mode", "append")
    assert appended.exit_code == 0, appended.output
    assert "✓ imports/words/train: version 2, 500 rows" in appended.output

    root = flock_cli("datasets", "list")
    assert root.exit_code == 0, root.output
    assert root.output.splitlines() == ["imports/"]
    listed = flock_cli("datasets", "list", "imports/words")
    assert listed.exit_code == 0, listed.output
    assert sorted(listed.output.splitlines()) == [
        "imports/words/test\tversion 2",
        "imports/words/train\tversion 2",
    ]
    one = flock_cli("datasets", "list", "imports/words/train")
    assert one.output == "imports/words/train is a dataset, at version 2.\n"
    assert datasets.list_datasets("imports")["folders"] == [
        {"name": "words", "path": "imports/words"}
    ]


def test_import_downloads_to_a_temporary_folder_unless_kept(
    signed_in: str,
    flock_cli: Callable[..., Result],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    write_source(source, test_split=False)
    downloaded: list[Path] = []

    def download(repo: str, to: Path, **options: Any) -> Path:
        assert (repo, options["config"], options["splits"]) == ("acme/words", "en", ("train",))
        shutil.copytree(source, to, dirs_exist_ok=True)
        downloaded.append(Path(to))
        return Path(to)

    monkeypatch.setattr(datasets, "download_from_hf", download)
    args = ["datasets", "import", "acme/words", "words", "--config", "en", "--split", "train"]
    done = flock_cli(*args, "--workers", "1")
    assert done.exit_code == 0, done.output
    assert "✓ words/train: version 1, 500 rows" in done.output
    assert not downloaded[0].exists()

    kept = tmp_path / "kept"
    done = flock_cli(*args, "--mode", "overwrite", "--keep", str(kept))
    assert done.exit_code == 0, done.output
    assert "✓ words/train: version 2, 500 rows" in done.output
    assert (kept / "flock-import.json").is_file()


def test_download_needs_no_sign_in(
    flock_cli: Callable[..., Result], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import huggingface_hub

    hub = FakeHub(files=["README.md", "train.csv", "test.csv"])
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: hub)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", hub.snapshot_download)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", hub.hf_hub_download)
    to = tmp_path / "words"
    done = flock_cli("datasets", "download", "acme/words", "--to", str(to))
    assert done.exit_code == 0, done.output
    assert done.output.splitlines() == [
        "train\t1 files, 9 B",
        "test\t1 files, 8 B",
        f"✓ Downloaded acme/words (default) to {to}",
        f"  Convert it with: flock datasets convert {to} DEST",
    ]

    missing = flock_cli("datasets", "download", "acme/words", "--to", str(to), "--split", "dev")
    assert missing.exit_code == 1
    assert "has no split dev" in missing.output
    assert flock_cli("datasets", "download", "acme/words").exit_code == 2  # --to is required


def test_convert_refusals(flock_cli: Callable[..., Result], tmp_path: Path) -> None:
    empty = flock_cli("datasets", "convert", str(tmp_path), "words")
    assert empty.exit_code == 1
    assert "has no flock-import.json" in empty.output

    write_source(tmp_path / "download")
    signed_out = flock_cli("datasets", "convert", str(tmp_path / "download"), "words")
    assert signed_out.exit_code == 1
    assert "Not logged in" in signed_out.output
    bad_mode = flock_cli("datasets", "convert", str(tmp_path), "words", "--mode", "replace")
    assert bad_mode.exit_code == 2
