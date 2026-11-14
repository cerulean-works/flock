"""`flock.hub.datasets.download_from_hf` (D119): which files a download picks, with the Hub
mocked (no network)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import huggingface_hub
import pytest
from huggingface_hub import DatasetCardData
from huggingface_hub.errors import GatedRepoError, RevisionNotFoundError

from flock.hub.datasets import DatasetError, Manifest, download_from_hf
from flock.hub.datasets._download import match

SHA = "a" * 40
PARQUET_SHA = "b" * 40
RESPONSE: Any = SimpleNamespace(status_code=404, headers={}, request=None)
"""Stands in for the HTTP response the Hub's errors carry."""


@dataclass
class FakeHub:
    """The few Hub calls a download makes: each revision's files and card, and downloads
    that write placeholder files and record what they were asked for."""

    files: list[str]
    card: dict[str, Any] | None = None
    converted: list[str] | None = None
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    error: Exception | None = None

    def dataset_info(self, repo: str, *, revision: str | None = None, **_: Any) -> Any:
        if self.error is not None:
            raise self.error
        if revision == "refs/convert/parquet":
            if self.converted is None:
                raise RevisionNotFoundError("no conversion", response=RESPONSE)
            files, sha, card = self.converted, PARQUET_SHA, None
        else:
            files, sha = self.files, SHA
            card = DatasetCardData(**self.card) if self.card is not None else None
        return SimpleNamespace(
            sha=sha, siblings=[SimpleNamespace(rfilename=f) for f in files], card_data=card
        )

    def snapshot_download(self, repo: str, **kwargs: Any) -> str:
        self.snapshots.append(kwargs)
        folder = Path(kwargs["local_dir"])
        available = self.converted if kwargs["revision"] == PARQUET_SHA else self.files
        import fnmatch

        for path in available or []:
            if any(fnmatch.fnmatchcase(path, p) for p in kwargs["allow_patterns"]):
                (folder / path).parent.mkdir(parents=True, exist_ok=True)
                (folder / path).write_text(path)
        return str(folder)

    def hf_hub_download(self, repo: str, filename: str, **kwargs: Any) -> str:
        self.downloads.append({"filename": filename, **kwargs})
        path = Path(kwargs["local_dir"]) / filename
        path.write_text("# card\n")
        return str(path)


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> FakeHub:
    fake = FakeHub(files=[])
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda **_: fake)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake.snapshot_download)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake.hf_hub_download)
    return fake


def splits_of(manifest: Manifest, config: str | None = None) -> dict[str, list[str]]:
    """The files of each split of `config` (the only config downloaded by default)."""
    if config is None:
        (config,) = manifest.splits
    return {split: [f.path for f in files] for split, files in manifest.splits[config].items()}


def test_card_configs_choose_the_files(hub: FakeHub, tmp_path: Path) -> None:
    hub.files = [
        ".gitattributes",
        "README.md",
        "en/train-00001.parquet",
        "en/train-00000.parquet",
        "en/test.parquet",
        "fr/train.jsonl",
        "fr/notes.txt",
    ]
    hub.card = {
        "configs": [
            {
                "config_name": "en",
                "default": True,
                "data_files": [
                    {"split": "train", "path": "en/train-*"},
                    {"split": "test", "path": "en/test.parquet"},
                ],
            },
            {"config_name": "fr", "data_files": "fr/*"},
        ]
    }

    folder = download_from_hf("acme/words", tmp_path / "words")

    manifest = Manifest.read(folder)
    assert (manifest.repo, manifest.revision, list(manifest.splits)) == ("acme/words", SHA, ["en"])
    assert manifest.configs == ["en", "fr"]
    assert manifest.card == "README.md"
    assert splits_of(manifest) == {
        "train": ["en/train-00000.parquet", "en/train-00001.parquet"],
        "test": ["en/test.parquet"],
    }
    (snapshot,) = hub.snapshots
    assert snapshot["revision"] == SHA
    assert snapshot["repo_type"] == "dataset"
    assert sorted(snapshot["allow_patterns"]) == sorted(
        ["en/train-00000.parquet", "en/train-00001.parquet", "en/test.parquet"]
    )
    assert (folder / "en/train-00001.parquet").is_file()
    assert not (folder / "fr").exists()
    assert (folder / "README.md").read_text() == "# card\n"
    assert hub.downloads[0]["revision"] == SHA

    # Another config, with the files Flock can't read left out, and one split only.
    fr = Manifest.read(download_from_hf("acme/words", tmp_path / "fr", config="fr"))
    assert splits_of(fr, "fr") == {"train": ["fr/train.jsonl"]}
    en = Manifest.read(download_from_hf("acme/words", tmp_path / "test", splits=["test"]))
    assert splits_of(en) == {"test": ["en/test.parquet"]}


def test_default_file_names_without_a_card(hub: FakeHub, tmp_path: Path) -> None:
    hub.files = [
        "README.md",
        ".gitattributes",
        "data/train-00001-of-00002.parquet",
        "data/train-00000-of-00002.parquet",
        "data/validation-00000-of-00001.parquet",
        "data/test-00000-of-00001.parquet",
    ]
    manifest = Manifest.read(download_from_hf("acme/words", tmp_path / "a"))
    assert manifest.configs == ["default"]
    assert splits_of(manifest) == {
        "train": ["data/train-00000-of-00002.parquet", "data/train-00001-of-00002.parquet"],
        "validation": ["data/validation-00000-of-00001.parquet"],
        "test": ["data/test-00000-of-00001.parquet"],
    }

    # Split names in file names, compressed JSON Lines, and CSV.
    hub.files = ["train.csv", "dev.jsonl.gz", "test.csv", "LICENSE"]
    manifest = Manifest.read(download_from_hf("acme/words", tmp_path / "b"))
    assert splits_of(manifest) == {
        "train": ["train.csv"],
        "validation": ["dev.jsonl.gz"],
        "test": ["test.csv"],
    }
    assert [f.format for f in manifest.splits["default"]["validation"]] == ["json"]
    assert manifest.card is None
    assert len(hub.downloads) == 1  # only the first repo had a card

    # Everything is train when no names say otherwise.
    hub.files = ["part-b.arrow", "part-a.arrow", "LICENSE"]
    manifest = Manifest.read(download_from_hf("acme/words", tmp_path / "c"))
    assert splits_of(manifest) == {"train": ["part-a.arrow", "part-b.arrow"]}


def test_falls_back_to_the_hubs_parquet_conversion(hub: FakeHub, tmp_path: Path) -> None:
    hub.files = ["README.md", "words.py", "data/words.tar.gz"]
    hub.converted = [
        "default/train/0001.parquet",
        "default/train/0000.parquet",
        "default/test/0000.parquet",
        ".gitattributes",
    ]
    folder = download_from_hf("acme/words", tmp_path / "words")
    manifest = Manifest.read(folder)
    assert manifest.ref == "refs/convert/parquet"
    assert manifest.revision == PARQUET_SHA
    assert splits_of(manifest) == {
        "test": ["default/test/0000.parquet"],
        "train": ["default/train/0000.parquet", "default/train/0001.parquet"],
    }
    assert hub.snapshots[0]["revision"] == PARQUET_SHA
    # The card comes from the repo, since the conversion's branch has none.
    assert hub.downloads[0]["revision"] is None
    assert json.loads((folder / "flock-import.json").read_text())["ref"] == "refs/convert/parquet"

    hub.converted = ["default/partial-train/0000.parquet"]
    with pytest.raises(DatasetError, match="only converted part of"):
        download_from_hf("acme/words", tmp_path / "partial")

    hub.converted = None
    with pytest.raises(DatasetError, match="can't be imported"):
        download_from_hf("acme/words", tmp_path / "none")


def test_several_configs(hub: FakeHub, tmp_path: Path) -> None:
    hub.files = [
        "README.md",
        "en/train.parquet",
        "en/test.parquet",
        "fr/train.parquet",
        "fr/validation.parquet",
        "de/train.parquet",
    ]
    hub.card = {
        "configs": [
            {
                "config_name": name,
                "data_files": [
                    {"split": split, "path": f"{name}/{split}.parquet"}
                    for split in ("train", "test", "validation")
                    if f"{name}/{split}.parquet" in hub.files
                ],
            }
            for name in ("en", "fr", "de")
        ]
    }

    # Each config's files, in one folder and one manifest, in the repo's order.
    folder = download_from_hf("acme/words", tmp_path / "all", config=["fr", "en"])
    manifest = Manifest.read(folder)
    assert manifest.configs == ["en", "fr", "de"]
    assert list(manifest.splits) == ["en", "fr"]
    assert splits_of(manifest, "en") == {
        "train": ["en/train.parquet"],
        "test": ["en/test.parquet"],
    }
    assert splits_of(manifest, "fr") == {
        "train": ["fr/train.parquet"],
        "validation": ["fr/validation.parquet"],
    }
    assert sorted(hub.snapshots[0]["allow_patterns"]) == [
        "en/test.parquet",
        "en/train.parquet",
        "fr/train.parquet",
        "fr/validation.parquet",
    ]
    assert (folder / "fr/validation.parquet").is_file()
    assert not (folder / "de").exists()

    # A split is kept in every config that has it.
    manifest = Manifest.read(
        download_from_hf(
            "acme/words", tmp_path / "tv", config=["en", "fr"], splits=["test", "validation"]
        )
    )
    assert {c: list(s) for c, s in manifest.splits.items()} == {
        "en": ["test"],
        "fr": ["validation"],
    }

    # Or each config gets its own splits.
    manifest = Manifest.read(
        download_from_hf(
            "acme/words", tmp_path / "own", config={"en": ["test"], "fr": None}, splits=["train"]
        )
    )
    assert {c: list(s) for c, s in manifest.splits.items()} == {
        "en": ["test"],
        "fr": ["train"],
    }

    hub.snapshots.clear()
    with pytest.raises(DatasetError, match="No config chosen has the split dev"):
        download_from_hf("acme/words", tmp_path, config=["en", "fr"], splits=["train", "dev"])
    with pytest.raises(DatasetError, match="The config de has none of the splits test"):
        download_from_hf("acme/words", tmp_path, config=["en", "de"], splits=["test"])
    with pytest.raises(DatasetError, match="The config fr has no split test"):
        download_from_hf("acme/words", tmp_path, config={"en": None, "fr": ["test"]})
    with pytest.raises(DatasetError, match="has no config it, es. Its configs are en, fr, de"):
        download_from_hf("acme/words", tmp_path, config=["en", "it", "es"])
    with pytest.raises(DatasetError, match="No split of the config en is chosen"):
        download_from_hf("acme/words", tmp_path, config={"en": []})
    with pytest.raises(DatasetError, match="Choose at least one config"):
        download_from_hf("acme/words", tmp_path, config=[])
    assert hub.snapshots == []


def test_several_configs_of_the_parquet_conversion(hub: FakeHub, tmp_path: Path) -> None:
    hub.files = ["README.md", "words.py"]
    hub.converted = [
        "en/train/0000.parquet",
        "en/test/0000.parquet",
        "fr/train/0000.parquet",
        "fr/partial-test/0000.parquet",
    ]
    manifest = Manifest.read(
        download_from_hf("acme/words", tmp_path / "a", config=["en", "fr"], splits=["train"])
    )
    assert manifest.ref == "refs/convert/parquet"
    assert splits_of(manifest, "en") == {"train": ["en/train/0000.parquet"]}
    assert splits_of(manifest, "fr") == {"train": ["fr/train/0000.parquet"]}
    with pytest.raises(DatasetError, match="only converted part of the fr/test split"):
        download_from_hf("acme/words", tmp_path / "b", config=["en", "fr"])


def test_refusals(hub: FakeHub, tmp_path: Path) -> None:
    hub.files = ["a/train.parquet", "b/train.parquet"]
    hub.card = {
        "configs": [
            {"config_name": "a", "data_files": "a/*"},
            {"config_name": "b", "data_files": "b/*"},
        ]
    }
    with pytest.raises(DatasetError, match="several configs, so choose one with --config: a, b"):
        download_from_hf("acme/words", tmp_path)
    with pytest.raises(DatasetError, match="has no config c. Its configs are a, b"):
        download_from_hf("acme/words", tmp_path, config="c")
    with pytest.raises(DatasetError, match="has no split test. Its splits are train"):
        download_from_hf("acme/words", tmp_path, config="a", splits=["test"])
    assert hub.snapshots == []

    hub.error = GatedRepoError("gated", response=RESPONSE)
    with pytest.raises(DatasetError, match="is gated"):
        download_from_hf("acme/words", tmp_path)


def test_patterns_match_as_datasets_does() -> None:
    files = ["data/train.parquet", "data/sub/train.parquet", ".hidden/train.parquet", "__x/t.csv"]
    assert match(files, "**/train.parquet") == ["data/sub/train.parquet", "data/train.parquet"]
    assert match(files, "data/*") == ["data/train.parquet"]
    assert match(files, "**") == ["data/sub/train.parquet", "data/train.parquet"]
    assert match(files, ".hidden/*") == [".hidden/train.parquet"]
    assert match(["README.md", "a.csv"], "*") == ["a.csv"]
