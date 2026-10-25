"""
Hugging Face imports (D9): a dataset on the Hugging Face Hub, pinned to a commit, as one hub version.

Small mode loads every split with `datasets.load_dataset` and writes them in one commit. It is
what mirrors use, run by the server as `python -m flock.hub.importer`. Staged mode
(`flock-hub import --workdir ...`) is for corpora too large for that: it downloads the raw
files, converts each one to the Dolma schema in a process pool, and uploads the results, keeping
its progress in the work directory so that every stage resumes after a crash.

Whatever the mode, the version carries the transaction property `flock.source=hf:<repo>@<sha>`,
its card is the repo's README under a provenance header, and it is tagged `hf-<sha[:8]>`.
"""

import concurrent.futures
import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import datasets
import datasets.config
import datasets.exceptions
import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import (
    EntryNotFoundError,
    RepositoryNotFoundError,
    RevisionNotFoundError,
)

from flock.hub.client import DatasetNotFound
from flock.hub.config import HubConfig, get_default, parse_repo_id
from flock.hub.dataset import Dataset
from flock.hub.write import DatasetWriter, _write_bytes, _write_json, as_reader, create_dataset

log = logging.getLogger(__name__)

MIRROR_ORG = "hf"
"""The mirror org in direct mode. On a server, the server names it (its `FLOCK_HUB_MIRROR_ORG`)."""

SOURCE_KEY = "flock.source"
"""The transaction property naming what an import came from: `hf:<repo>@<sha>`."""

FAKE_ROOT_ENV = "FLOCK_HUB_IMPORT_FAKE_ROOT"
"""Test only: a folder of fake Hugging Face repos (`<root>/<owner>/<name>/`) used instead of the Hub."""

Stage = Literal["all", "download", "process", "upload"]
Progress = Callable[[str, int, int], None]

_SHA = re.compile(r"^[0-9a-f]{40}$")
_FORMATS = (".parquet", ".arrow", ".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".txt")
_COMPRESSIONS = ("", ".gz", ".zst", ".bz2", ".xz")
_JSON_FORMATS = (".json", ".jsonl", ".ndjson")

DOLMA_FEATURES = datasets.Features(
    {
        "id": datasets.Json(),
        "text": datasets.Value("large_string"),
        "source": datasets.Json(),
        "added": datasets.Json(),
        "created": datasets.Json(),
        "metadata": datasets.Json(),
        "attributes": datasets.Json(),
    }
)
"""
What HF's `json` builder reads a Dolma file as.

`Json` takes `metadata` and `attributes` of any shape. The short fields are `Json` too: Arrow's JSON reader would otherwise turn ISO dates such as `added` into timestamps, and they would come back reformatted. They are decoded to strings by [`_to_dolma`].
"""

_SCALARS = ("id", "source", "added", "created")

DOLMA_SCHEMA = pa.schema(
    [
        pa.field("id", pa.string()),
        pa.field("text", pa.large_string()),
        pa.field("source", pa.string()),
        pa.field("added", pa.string()),
        pa.field("created", pa.string()),
        pa.field("metadata", pa.string()),
        pa.field("attributes", pa.string()),
    ]
)
"""What staged imports write: `metadata` and `attributes` as JSON strings, since their shape varies across shards."""


class RepoNotFound(LookupError):
    """The Hugging Face repo, or the revision, doesn't exist (or the token can't see it)."""


# ---- names ----


def mirror_name(repo: str, config: str | None = None) -> str:
    """A mirror's dataset name: `<owner>--<name>`, plus `--<config>` when a config is given."""
    name = repo.replace("/", "--")
    return f"{name}--{config}" if config else name


def mirror_repo_id(repo: str, config: str | None = None, org: str = MIRROR_ORG) -> str:
    """The mirror of `repo` (and `config`) in `org`, such as `hf/cais--mmlu--all`."""
    return f"{org}/{mirror_name(repo, config)}"


def hf_tag(sha: str) -> str:
    """The tag of an import of commit `sha`: `hf-<sha[:8]>`."""
    return f"hf-{sha[:8]}"


def is_sha(revision: str | None) -> bool:
    """Whether `revision` is a full commit sha (40 lowercase hex characters)."""
    return revision is not None and bool(_SHA.match(revision))


# ---- where repos come from ----


@dataclass(frozen=True)
class RepoFile:
    """A file of a repo at one commit, and the split it belongs to (from the README's `configs`)."""

    path: str
    size: int
    split: str = "train"


class HfSource:
    """
    The Hugging Face Hub, through `huggingface_hub`.

    Args:
        token (`str`, *optional*):
            A Hugging Face token. Defaults to `huggingface_hub`'s own (`HF_TOKEN`, or the saved login).
    """

    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self._infos: dict[tuple[str, str | None], Any] = {}

    def _info(self, repo: str, revision: str | None) -> Any:
        key = (repo, revision)
        if key not in self._infos:
            try:
                self._infos[key] = HfApi(token=self.token).dataset_info(
                    repo, revision=revision, files_metadata=True
                )
            except (RepositoryNotFoundError, RevisionNotFoundError) as e:
                raise RepoNotFound(f"{repo}@{revision or 'main'} not found on Hugging Face") from e
        return self._infos[key]

    def sha(self, repo: str, revision: str | None) -> str:
        """The commit `revision` (default `main`) points to."""
        sha = self._info(repo, revision).sha
        assert sha is not None
        return sha

    def files(self, repo: str, sha: str) -> list[RepoFile]:
        """Every file of the repo at `sha`, with its size."""
        siblings = self._info(repo, sha).siblings or []
        return [RepoFile(s.rfilename, s.size or 0) for s in siblings]

    def download(self, repo: str, sha: str, path: str, dest: Path) -> Path:
        """Downloads one file of the repo into `dest`, keeping its path, and returns where it is."""
        local = hf_hub_download(
            repo, path, repo_type="dataset", revision=sha, local_dir=dest, token=self.token
        )
        return Path(local)

    def readme(self, repo: str, sha: str) -> str | None:
        """The repo's README at `sha`, or `None`."""
        with tempfile.TemporaryDirectory(prefix="flock-hub-readme-") as tmp:
            try:
                return self.download(repo, sha, "README.md", Path(tmp)).read_text()
            except EntryNotFoundError:
                return None

    def dataset_path(self, repo: str, sha: str) -> tuple[str, dict[str, Any]]:
        """What `datasets.load_dataset` is given for the repo at `sha`: its path and keyword arguments."""
        return repo, {"revision": sha, "token": self.token}


class LocalSource:
    """
    Test only: fake Hugging Face repos in folders, `<root>/<owner>/<name>/`.

    A repo's sha is a hash of its files, so editing a fixture makes a new commit. Only `main` and that sha resolve.
    """

    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root)

    def _dir(self, repo: str) -> Path:
        path = self.root / repo
        if not path.is_dir():
            raise RepoNotFound(f"{repo} not found under {self.root}")
        return path

    def _paths(self, repo: str) -> list[str]:
        base = self._dir(repo)
        found = []
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(base).as_posix()
            if path.is_file() and not any(part.startswith(".") for part in rel.split("/")):
                found.append(rel)
        return found

    def sha(self, repo: str, revision: str | None) -> str:
        digest = hashlib.sha1()
        base = self._dir(repo)
        for rel in self._paths(repo):
            digest.update(rel.encode() + b"\0" + (base / rel).read_bytes() + b"\0")
        sha = digest.hexdigest()
        if revision not in (None, "main", sha):
            raise RepoNotFound(f"{repo}@{revision} not found (the fake repo is at {sha})")
        return sha

    def _check(self, repo: str, sha: str) -> Path:
        if self.sha(repo, None) != sha:
            raise RepoNotFound(f"{repo}@{sha} not found (the fake repo changed)")
        return self._dir(repo)

    def files(self, repo: str, sha: str) -> list[RepoFile]:
        base = self._check(repo, sha)
        return [RepoFile(rel, (base / rel).stat().st_size) for rel in self._paths(repo)]

    def download(self, repo: str, sha: str, path: str, dest: Path) -> Path:
        target = dest / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._check(repo, sha) / path, target)
        return target

    def readme(self, repo: str, sha: str) -> str | None:
        path = self._check(repo, sha) / "README.md"
        return path.read_text() if path.exists() else None

    def dataset_path(self, repo: str, sha: str) -> tuple[str, dict[str, Any]]:
        return str(self._check(repo, sha)), {}


Source = HfSource | LocalSource


def source(token: str | None = None) -> Source:
    """The Hugging Face Hub, or the fake repos under `$FLOCK_HUB_IMPORT_FAKE_ROOT` when it is set (tests)."""
    fake = os.environ.get(FAKE_ROOT_ENV)
    if not fake:
        return HfSource(token)
    _offline()
    return LocalSource(fake)


def _offline() -> None:
    """With fake repos (tests), keeps `datasets` off the network."""
    if os.environ.get(FAKE_ROOT_ENV):
        # `datasets` otherwise pings Hugging Face to count downloads of its packaged builders.
        datasets.config.HF_UPDATE_DOWNLOAD_COUNTS = False


# ---- which files a config has ----


@dataclass
class Plan:
    """A repo pinned to a commit, and the data files of the config being imported."""

    repo: str
    config: str | None
    sha: str
    files: list[RepoFile]

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def splits(self) -> list[str]:
        return list(dict.fromkeys(f.split for f in self.files))


def plan(
    repo: str,
    config: str | None = None,
    revision: str | None = None,
    data_files: list[str] | None = None,
    *,
    src: Source | None = None,
) -> Plan:
    """
    Pins `revision` to a sha and lists the data files of `config`.

    The files are those the README's `configs` YAML gives the config, `data_files` globs if given, or else every data-like file (Parquet, Arrow, JSON Lines, CSV and text, compressed or not). It is a size estimate for mirrors and the download list for staged imports.

    Raises:
        [`RepoNotFound`]: if the repo or revision doesn't exist.
        `ValueError`: for a config the README doesn't define, or a missing config when it defines several.
    """
    src = src or source()
    sha = src.sha(repo, revision)
    files = src.files(repo, sha)
    if data_files:
        return Plan(repo, config, sha, _select(files, {"train": list(data_files)}))
    configs = _readme_configs(src.readme(repo, sha)) if _has(files, "README.md") else []
    if configs:
        chosen = _choose_config(repo, configs, config)
        return Plan(repo, config, sha, _select(files, _splits_of(chosen.get("data_files"))))
    data = [f for f in files if _data_like(f.path)]
    if config:
        data = [f for f in data if f.path.startswith(f"{config}/")]
        if not data:
            raise ValueError(f"{repo} has no config {config!r}: its README defines no configs")
    return Plan(repo, config, sha, data)


def _has(files: list[RepoFile], path: str) -> bool:
    return any(f.path == path for f in files)


def _data_like(path: str) -> bool:
    name = path.rpartition("/")[2].lower()
    if name.startswith(".") or name == "readme.md":
        return False
    return any(name.endswith(fmt + comp) for fmt in _FORMATS for comp in _COMPRESSIONS)


def _readme_configs(readme: str | None) -> list[dict[str, Any]]:
    """The `configs` list of a README's YAML front matter."""
    if not readme or not readme.startswith("---"):
        return []
    front, sep, _ = readme[3:].partition("\n---")
    if not sep:
        return []
    meta = yaml.safe_load(front) or {}
    configs = meta.get("configs") if isinstance(meta, dict) else None
    return [c for c in configs or [] if isinstance(c, dict)]


def _choose_config(repo: str, configs: list[dict[str, Any]], config: str | None) -> dict[str, Any]:
    names = [c.get("config_name", "default") for c in configs]
    if config is not None:
        for c, name in zip(configs, names, strict=True):
            if name == config:
                return c
        raise ValueError(f"{repo} has no config {config!r}; its configs are {names}")
    defaults = [c for c in configs if c.get("default")]
    if defaults:
        return defaults[0]
    if len(configs) == 1 or "default" in names:
        return configs[names.index("default")] if "default" in names else configs[0]
    raise ValueError(f"{repo} has several configs, so pass one of {names}")


def _splits_of(data_files: Any) -> dict[str, list[str]]:
    """A config's `data_files` (a glob, a list of globs, or a list of `{split, path}`) by split."""
    if data_files is None:
        return {"train": ["**"]}
    if isinstance(data_files, str):
        return {"train": [data_files]}
    splits: dict[str, list[str]] = {}
    for item in data_files:
        if isinstance(item, str):
            splits.setdefault("train", []).append(item)
        else:
            paths = item["path"]
            splits.setdefault(item.get("split", "train"), []).extend(
                [paths] if isinstance(paths, str) else paths
            )
    return splits


def _select(files: list[RepoFile], splits: dict[str, list[str]]) -> list[RepoFile]:
    """The files matching each split's globs, tagged with the split. A glob naming a folder takes its files."""
    chosen: dict[str, RepoFile] = {}
    for split, patterns in splits.items():
        for pattern in patterns:
            pattern = pattern.removeprefix("./")
            regex = _glob(pattern)
            matched = [f for f in files if regex.match(f.path)]
            if not matched:
                matched = [f for f in files if f.path.startswith(pattern.rstrip("/") + "/")]
            for f in matched:
                if f.path not in chosen and (pattern != "**" or _data_like(f.path)):
                    chosen[f.path] = RepoFile(f.path, f.size, split)
    return sorted(chosen.values(), key=lambda f: (f.split, f.path))


def _glob(pattern: str) -> re.Pattern[str]:
    """fsspec-style globs as HF's `data_files` use them: `**` crosses folders, `*` and `?` don't."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        elif pattern[i] == "[" and (end := pattern.find("]", i + 1)) != -1:
            out, i = out + pattern[i : end + 1], end + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


# ---- the card ----


def provenance_card(readme: str | None, repo: str, config: str | None, sha: str) -> str:
    """
    The card of an import: the HF README with a provenance header.

    The header goes after the README's YAML front matter, so tools that read it (license, configs) still find it first.
    """
    today = dt.datetime.now(dt.UTC).date().isoformat()
    config_part = f", config `{config}`" if config else ""
    header = (
        f"> Imported from Hugging Face [`{repo}`](https://huggingface.co/datasets/{repo})"
        f"{config_part}, commit `{sha}`, on {today}.\n"
    )
    readme = readme or ""
    front, body = "", readme
    if readme.startswith("---"):
        end = readme.find("\n---", 3)
        if end != -1:
            cut = readme.find("\n", end + 4)
            cut = len(readme) if cut == -1 else cut + 1
            front, body = readme[:cut], readme[cut:]
    if front and not front.endswith("\n"):
        front += "\n"
    return f"{front}{header}\n{body.lstrip(chr(10))}" if body.strip() else f"{front}{header}"


# ---- small mode ----


def import_hf_dataset(
    repo: str,
    config: str | None = None,
    revision: str | None = None,
    target: str | None = None,
    *,
    workdir: str | os.PathLike | None = None,
    num_proc: int = 1,
    stage: Stage = "all",
    mode: Literal["overwrite", "append"] = "overwrite",
    data_files: list[str] | None = None,
    urls_file: str | None = None,
    hub_config: HubConfig | None = None,
) -> Dataset | None:
    """
    Imports a Hugging Face dataset at one commit as a new version of a hub dataset.

    Without `workdir`, the import runs in small mode: `datasets.load_dataset` loads every split of the config, and they are written in one commit, with a `split` column when there are several. With `workdir`, it runs in staged mode, for corpora of JSON Lines files (such as Dolma) or Parquet files: the raw files are downloaded, each is converted to the Dolma schema (JSON files) or kept as is (Parquet), and the results are uploaded in one commit. Every stage keeps its progress in `workdir`, so rerunning the same call resumes it.

    Either way the version carries `flock.source=hf:<repo>@<sha>`, its card is the repo's README under a provenance header, and it is tagged `hf-<sha[:8]>`.

    Args:
        repo (`str`):
            The Hugging Face dataset, such as `cais/mmlu`.
        config (`str`, *optional*):
            The config to import. Required when the repo defines several and none is the default.
        revision (`str`, *optional*):
            A branch, tag or commit. Defaults to `main`. It is pinned to a commit sha first; a resumed staged import keeps the sha of its first run.
        target (`str`, *optional*):
            The hub dataset, as `org/name`. Defaults to the mirror name, such as `hf/cais--mmlu--all`. On a server it is created if it doesn't exist.
        workdir (`str` or `Path`, *optional*):
            Staged mode's work directory. Reuse it to resume.
        num_proc (`int`, *optional*, defaults to 1):
            Processes converting files in staged mode.
        stage (`str`, *optional*, defaults to `"all"`):
            Staged mode only: `"download"`, `"process"` or `"upload"` runs that stage alone (the ones before it must be done); `"all"` runs what is left.
        mode (`str`, *optional*, defaults to `"overwrite"`):
            `"overwrite"` replaces the target's rows; `"append"` adds to them.
        data_files (`list[str]`, *optional*):
            Staged mode: globs of the files to import, instead of the config's.
        urls_file (`str`, *optional*):
            Staged mode: a file listing one URL per line to download instead of repo files (for repos such as `allenai/dolma`). A local path, or else a path in the repo.
        hub_config (`HubConfig`, *optional*):
            Where the hub is. Defaults to [`~config.get_default`].

    Returns:
        [`Dataset`]: the target at the imported version (streaming), or `None` when `stage` stops before the upload.

    Raises:
        [`RepoNotFound`]: if the repo or revision doesn't exist.
        `ValueError`: for an unknown config, staged options without `workdir`, or a `workdir` holding another import.
    """
    if mode not in ("overwrite", "append"):
        raise ValueError(f"mode must be 'overwrite' or 'append', got {mode!r}")
    if stage not in ("all", "download", "process", "upload"):
        raise ValueError(f"stage must be all, download, process or upload, got {stage!r}")
    if workdir is None and (stage != "all" or data_files or urls_file):
        raise ValueError(
            "stage, data_files and urls_file are for staged imports, which need a workdir"
        )
    hub = hub_config or get_default()
    target = target or mirror_repo_id(repo, config)
    if workdir is not None:
        return _staged(
            repo, config, revision, target, Path(workdir), num_proc, stage, mode,
            data_files, urls_file, hub,
        )  # fmt: skip
    src = source()
    sha = src.sha(repo, revision)
    _ensure_target(target, hub)
    with tempfile.TemporaryDirectory(prefix="flock-hub-import-") as tmp:
        versions = import_small(repo, config, sha, target, hub, Path(tmp), src=src, mode=mode)
    return _load(target, versions[-1], hub)


def import_small(
    repo: str,
    config: str | None,
    sha: str,
    target: str,
    hub: HubConfig,
    workdir: Path,
    *,
    src: Source | None = None,
    mode: Literal["overwrite", "append"] = "overwrite",
    progress: Progress | None = None,
) -> list[int]:
    """
    Small mode at a known sha: loads every split with `datasets.load_dataset`, commits them, sets the card and tags the result.

    Returns:
        `list[int]`: the versions made (the data, then the card), the last one tagged `hf-<sha8>`.
    """
    src = src or source()
    report = progress or (lambda stage, done, total: None)
    path, kwargs = src.dataset_path(repo, sha)
    report("load", 0, 1)
    loaded = datasets.load_dataset(path, config, cache_dir=str(workdir / "hf_cache"), **kwargs)
    assert isinstance(loaded, datasets.DatasetDict)
    report("load", 1, 1)
    splits = list(loaded.items())
    writer = DatasetWriter(target, mode, workdir / "upload", hub_config=hub)
    for i, (name, split) in enumerate(splits):
        reader = as_reader(split)
        writer.write_shard(
            f"{i:05d}", _with_split(reader, str(name)) if len(splits) > 1 else reader
        )
        report("write", i + 1, len(splits))
    version = writer.commit(
        f"Import {repo}{f' ({config})' if config else ''} at {sha[:8]} from Hugging Face",
        {SOURCE_KEY: f"hf:{repo}@{sha}"},
    )
    report("card", 0, 1)
    versions = _finish(
        target, version, hub, provenance_card(src.readme(repo, sha), repo, config, sha), sha
    )
    report("card", 1, 1)
    return versions


def _with_split(reader: pa.RecordBatchReader, name: str) -> pa.RecordBatchReader:
    """Adds a `split` column holding `name` to every batch."""
    schema = reader.schema.append(pa.field("split", pa.string()))

    def batches() -> Iterator[pa.RecordBatch]:
        for batch in reader:
            column = pa.array([name] * batch.num_rows, pa.string())
            yield pa.RecordBatch.from_arrays([*batch.columns, column], schema=schema)

    return pa.RecordBatchReader.from_batches(schema, batches())


def _ensure_target(target: str, hub: HubConfig) -> None:
    """Creates the target on the server if it doesn't exist (in direct mode the first commit does)."""
    if hub.direct:
        return
    org, name = parse_repo_id(target)
    try:
        hub.client.get_dataset(org, name)
    except DatasetNotFound:
        create_dataset(target, hub_config=hub)


def _finish(target: str, version: int, hub: HubConfig, card: str, sha: str) -> list[int]:
    """Sets the card after the data commit and tags the result; a rerun finds both done and changes nothing."""
    dataset = _load(target, version, hub)
    versions = [version]
    latest = dataset.checkout()
    tag = hf_tag(sha)
    tags = latest.tags
    if tag in tags and tags[tag] >= version:
        return [version, tags[tag]] if tags[tag] != version else versions
    if latest.card != card:
        latest = latest.set_card(card, f"Card of the {sha[:8]} import")
        versions.append(latest.version)
    if tag in tags:
        latest.untag(tag)
    latest.tag(tag)
    return versions


def _load(repo_id: str, version: int, hub: HubConfig) -> Dataset:
    from flock.hub.load import load_dataset

    return load_dataset(repo_id, version, hub_config=hub, auto_import=False)


# ---- staged mode ----


def _staged(
    repo: str,
    config: str | None,
    revision: str | None,
    target: str,
    workdir: Path,
    num_proc: int,
    stage: Stage,
    mode: Literal["overwrite", "append"],
    data_files: list[str] | None,
    urls_file: str | None,
    hub: HubConfig,
) -> Dataset | None:
    workdir.mkdir(parents=True, exist_ok=True)
    state = _state(repo, config, revision, target, mode, workdir, data_files, urls_file)
    if stage in ("all", "download"):
        _download(state, workdir)
    if stage in ("all", "process"):
        _require(state, workdir, "raw", "download")
        _process(state, workdir, num_proc)
    if stage in ("all", "upload"):
        _require(state, workdir, "processed", "process")
        version = _upload(state, workdir, hub)
        return _load(target, version, hub)
    return None


def _state(
    repo: str,
    config: str | None,
    revision: str | None,
    target: str,
    mode: str,
    workdir: Path,
    data_files: list[str] | None,
    urls_file: str | None,
) -> dict[str, Any]:
    """`workdir/state.json`: what is imported, pinned at the first run. Later runs must ask for the same."""
    path = workdir / "state.json"
    if path.exists():
        state = json.loads(path.read_text())
        asked = {"repo": repo, "config": config, "target": target, "mode": mode}
        for key, value in asked.items():
            if state[key] != value:
                raise ValueError(
                    f"{workdir} holds an import with {key}={state[key]!r}, not {value!r}"
                )
        if is_sha(revision) and revision != state["sha"]:
            raise ValueError(f"{workdir} holds an import of {state['sha']}, not {revision}")
        return state

    src = source()
    if urls_file is not None:
        sha = src.sha(repo, revision)
        urls = _read_urls(urls_file, src, repo, sha, workdir)
        units = [
            {"id": f"{i:05d}", "url": url, "name": _url_name(url), "split": "train"}
            for i, url in enumerate(urls)
        ]
    else:
        found = plan(repo, config, revision, data_files, src=src)
        sha = found.sha
        units = [
            {
                "id": f"{i:05d}",
                "path": f.path,
                "name": f.path.rpartition("/")[2],
                "split": f.split,
                "size": f.size,
            }
            for i, f in enumerate(found.files)
        ]
    if not units:
        raise ValueError(f"{repo}@{sha} has no data files to import")
    state = {
        "repo": repo, "config": config, "sha": sha, "target": target, "mode": mode, "units": units,
        "splits": list(dict.fromkeys(u["split"] for u in units)),
        "created": dt.datetime.now(dt.UTC).isoformat(),
    }  # fmt: skip
    _write_json(path, state)
    return state


def _read_urls(urls_file: str, src: Source, repo: str, sha: str, workdir: Path) -> list[str]:
    local = Path(urls_file)
    if not local.exists():
        local = src.download(repo, sha, urls_file, workdir / "repo")
    return [
        line.strip()
        for line in local.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def _url_name(url: str) -> str:
    return urlsplit(url).path.rpartition("/")[2] or "file"


def _raw(workdir: Path, unit: dict[str, Any]) -> Path:
    return workdir / "raw" / f"{unit['id']}-{unit['name']}"


def _marker(workdir: Path, stage: str, unit: dict[str, Any]) -> Path:
    return workdir / stage / f"{unit['id']}.done"


def _require(state: dict[str, Any], workdir: Path, stage: str, name: str) -> None:
    missing = [u["id"] for u in state["units"] if not _marker(workdir, stage, u).exists()]
    if missing:
        raise ValueError(
            f"the {name} stage isn't done for {len(missing)} of {len(state['units'])} files "
            f"(first: {missing[0]}); run it first, or use --stage all"
        )


def _download(state: dict[str, Any], workdir: Path) -> None:
    """Downloads every raw file without a marker, a few at a time."""
    (workdir / "raw").mkdir(exist_ok=True)
    todo = [u for u in state["units"] if not _marker(workdir, "raw", u).exists()]
    total, done = len(state["units"]), len(state["units"]) - len(todo)
    log.info("download: %d of %d files already done", done, total)
    src = source()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(_download_one, state, workdir, unit, src) for unit in todo]
        for future in concurrent.futures.as_completed(futures):
            future.result()
            done += 1
            log.info("download: %d/%d", done, total)


def _download_one(state: dict[str, Any], workdir: Path, unit: dict[str, Any], src: Source) -> None:
    final = _raw(workdir, unit)
    if "url" in unit:
        download_url(unit["url"], final)
    else:
        with tempfile.TemporaryDirectory(prefix=".dl-", dir=workdir / "raw") as tmp:
            local = src.download(state["repo"], state["sha"], unit["path"], Path(tmp))
            os.replace(local, final)
    _write_bytes(_marker(workdir, "raw", unit), b"")


def download_url(url: str, dest: Path, *, transport: httpx.BaseTransport | None = None) -> None:
    """
    Downloads `url` to `dest`, resuming a partial download (`dest` + `.part`) with a Range request.

    A server that ignores the Range header sends the whole file again, which replaces the partial one.
    """
    part = dest.with_name(dest.name + ".part")
    start = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={start}-"} if start else {}
    with (
        httpx.Client(transport=transport, follow_redirects=True, timeout=60.0) as client,
        client.stream("GET", url, headers=headers) as response,
    ):
        # 416: the partial file is already complete.
        if not (response.status_code == 416 and start):
            response.raise_for_status()
            append = start > 0 and response.status_code == 206
            with part.open("ab" if append else "wb") as f:
                for chunk in response.iter_bytes(1 << 20):
                    f.write(chunk)
    os.replace(part, dest)


def _process(state: dict[str, Any], workdir: Path, num_proc: int) -> None:
    """Converts every downloaded file without a marker, one file per task."""
    (workdir / "processed").mkdir(exist_ok=True)
    add_split = len(state["splits"]) > 1
    todo = [u for u in state["units"] if not _marker(workdir, "processed", u).exists()]
    jobs = [
        (str(_raw(workdir, u)), str(workdir / "processed" / f"{u['id']}.parquet"),
         u["split"] if add_split else None, str(workdir / "hf_cache" / u["id"]),
         str(_marker(workdir, "processed", u)))
        for u in todo
    ]  # fmt: skip
    total, done = len(state["units"]), len(state["units"]) - len(todo)
    log.info("process: %d of %d files already done", done, total)
    if num_proc <= 1:
        for job in jobs:
            process_file(*job)
            done += 1
            log.info("process: %d/%d", done, total)
        return
    with concurrent.futures.ProcessPoolExecutor(max_workers=num_proc) as pool:
        for _ in pool.map(_process_job, jobs):
            done += 1
            log.info("process: %d/%d", done, total)


def _process_job(job: tuple[str, str, str | None, str, str]) -> int:
    return process_file(*job)


def process_file(raw: str, out: str, split: str | None, cache_dir: str, marker: str) -> int:
    """
    Converts one raw file to a Parquet file and marks it done.

    JSON files are read with HF's `json` builder as [`DOLMA_FEATURES`] and mapped in batches to [`DOLMA_SCHEMA`]; Parquet files are kept as they are. `split`, when given, is added as a `split` column.

    Returns:
        `int`: the rows written.
    """
    datasets.disable_progress_bars()
    _offline()  # in a fresh worker process
    name = Path(raw).name.split("-", 1)[1].lower()
    fmt = next((f for f in _FORMATS if any(name.endswith(f + c) for c in _COMPRESSIONS)), None)
    if fmt in _JSON_FORMATS:
        try:
            loaded = datasets.load_dataset(
                "json",
                data_files=[raw],
                features=DOLMA_FEATURES,
                split="train",
                cache_dir=cache_dir,
            )
        except datasets.exceptions.DatasetGenerationError as e:
            # HF's message is generic; the cause says which field didn't fit.
            raise ValueError(
                f"{name} isn't Dolma documents ({', '.join(DOLMA_SCHEMA.names)}): {e.__cause__ or e}"
            ) from e
        features = datasets.Features.from_arrow_schema(DOLMA_SCHEMA)
        converted = loaded.with_format("arrow").map(
            _to_dolma, batched=True, batch_size=10_000, features=features
        )
    elif fmt == ".parquet":
        converted = datasets.load_dataset(
            "parquet", data_files=[raw], split="train", cache_dir=cache_dir
        ).with_format("arrow")
    else:
        raise ValueError(f"staged imports read JSON Lines and Parquet files, not {name}")
    assert isinstance(converted, datasets.Dataset)

    tmp = Path(out).with_name(f".{Path(out).name}.tmp")
    rows = 0
    writer: pq.ParquetWriter | None = None
    for table in converted.iter(batch_size=10_000):
        assert isinstance(table, pa.Table)
        if split is not None:
            table = table.append_column("split", pa.array([split] * table.num_rows, pa.string()))
        if writer is None:
            writer = pq.ParquetWriter(tmp, table.schema)
        writer.write_table(table)
        rows += table.num_rows
    if writer is None:  # no rows: still a file with the schema
        schema = converted.features.arrow_schema
        if split is not None:
            schema = schema.append(pa.field("split", pa.string()))
        writer = pq.ParquetWriter(tmp, schema)
    writer.close()
    os.replace(tmp, out)
    shutil.rmtree(cache_dir, ignore_errors=True)  # HF's copy of the file isn't needed any more
    _write_bytes(Path(marker), b"")
    return rows


def _to_dolma(table: pa.Table) -> pa.Table:
    """A batch of Dolma documents as [`DOLMA_SCHEMA`]: `metadata` and `attributes` stay JSON text, the short fields become their string values."""
    columns = []
    for field in DOLMA_SCHEMA:
        column = table.column(field.name)
        if isinstance(column.type, pa.ExtensionType):
            column = pa.chunked_array(
                [chunk.storage for chunk in column.chunks], column.type.storage_type
            )
        if field.name in _SCALARS:
            column = pa.array([_scalar(v) for v in column.to_pylist()], pa.string())
        columns.append(column.cast(field.type))
    return pa.Table.from_arrays(columns, schema=DOLMA_SCHEMA)


def _scalar(text: str | None) -> str | None:
    """A JSON value as a string: strings as they are, anything else (a numeric `id`) as JSON."""
    if text is None:
        return None
    value = json.loads(text)
    return value if isinstance(value, str) or value is None else json.dumps(value)


def _upload(state: dict[str, Any], workdir: Path, hub: HubConfig) -> int:
    """Writes each processed file as a shard, commits them once, then sets the card and the tag."""
    done_path = workdir / "upload" / "imported.json"
    if done_path.exists():
        return json.loads(done_path.read_text())["version"]
    target, sha, repo = state["target"], state["sha"], state["repo"]
    _ensure_target(target, hub)
    writer = DatasetWriter(target, state["mode"], workdir / "upload", hub_config=hub)
    units = state["units"]

    def write(unit: dict[str, Any]) -> None:
        parquet = pq.ParquetFile(workdir / "processed" / f"{unit['id']}.parquet")
        reader = pa.RecordBatchReader.from_batches(parquet.schema_arrow, parquet.iter_batches())
        writer.write_shard(unit["id"], reader)

    # The first shard alone: it records the upload's schema, which the others are checked against.
    if units:
        write(units[0])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for i, _ in enumerate(pool.map(write, units[1:]), start=2):
            log.info("upload: %d/%d", i, len(units))
    version = writer.commit(
        f"Import {repo}{f' ({state["config"]})' if state['config'] else ''} at {sha[:8]} from Hugging Face",
        {SOURCE_KEY: f"hf:{repo}@{sha}"},
    )
    src = source()
    card = provenance_card(src.readme(repo, sha), repo, state["config"], sha)
    final = _finish(target, version, hub, card, sha)[-1]
    _write_json(done_path, {"version": final})
    return final


# ---- the server's entry point ----


def _direct_target(uri: str, storage_options: dict[str, str]) -> tuple[HubConfig, str]:
    """A direct-mode config and repo id whose dataset URI is exactly `uri`."""
    root, org, name = uri.rstrip("/").rsplit("/", 2)
    return HubConfig(root=root, storage_options=storage_options), f"{org}/{name}"


def main(argv: list[str] | None = None) -> int:
    """
    `python -m flock.hub.importer [JOB]`: a small-mode import into a Lance URI the caller chose (the server, for mirrors).

    The job is JSON, from the first argument or stdin: `{"repo", "config", "sha", "uri", "storage_options"}`. Progress goes to stdout as JSON lines (`{"stage", "done", "total"}`), then `{"version": N, "versions": [...]}`. On failure, the error goes to stderr and the exit status is 1.
    """
    argv = sys.argv[1:] if argv is None else argv
    datasets.disable_progress_bars()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    def emit(value: dict[str, Any]) -> None:
        print(json.dumps(value), flush=True)

    try:
        job = json.loads(argv[0] if argv else sys.stdin.read())
        hub, repo_id = _direct_target(job["uri"], job.get("storage_options") or {})
        with tempfile.TemporaryDirectory(prefix="flock-hub-mirror-") as tmp:
            versions = import_small(
                job["repo"], job.get("config") or None, job["sha"], repo_id, hub, Path(tmp),
                progress=lambda stage, done, total: emit({"stage": stage, "done": done, "total": total}),
            )  # fmt: skip
    except Exception as e:  # noqa: BLE001 - any failure is reported to the server the same way
        print(f"{type(e).__name__}: {e}", file=sys.stderr, flush=True)
        return 1
    emit({"version": versions[-1], "versions": versions})
    return 0


if __name__ == "__main__":
    sys.exit(main())
