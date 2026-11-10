"""Copies of pinned Hugging Face datasets on the Flock hub, and the staged copies jobs read."""

# `lance` and `flock.hub` are imported where they're used: `flock eval` loads this module for
# every command, and `lance` alone takes about 1.3 s to import.

import contextlib
import fcntl
import functools
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Generator, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import datasets
import pyarrow as pa

REPO = re.compile(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*")
"""A Hugging Face repo id, `<owner>/<name>`, neither part starting with a dot, so it never leaves the folder it is joined to."""

COMMIT = re.compile(r"[0-9a-f]{40}")
"""A full commit hash: the only revision a hub copy is staged and read by, since a branch or tag moves."""

ROOT = "FLOCK_EVAL_HUB"
"""The variable naming the staging root jobs read every dataset from; unset or empty, they read the Hugging Face Hub."""

BUCKET = "s3://flock-datasets"
"""The dataset bucket of the org `flock.hub.s3` is signed in to."""

type Pair = tuple[str | None, str]
"""A configuration (`None` for none) and a split of a Hugging Face repo."""


def root() -> Path | None:
    """The staging root `$FLOCK_EVAL_HUB` names, or `None` if it is unset or empty."""
    value = os.environ.get(ROOT)
    return Path(value) if value else None


def path(repo: str) -> str:
    """The path of `repo`'s hub copy in an org's bucket (stage 034, D2)."""
    return f"eval/{repo}"


def check(
    properties: Mapping[str, str], repo: str, revision: str, where: str
) -> list[dict[str, Any]]:
    """
    The pairs a copy at `where` records, with each one's row count, columns and features, after checking that it is a copy of `repo` at `revision`.

    Raises:
        `ValueError`: if the copy records another repo or revision, or no pairs.
    """
    source = (properties.get("source_repo"), properties.get("source_revision"))
    if source != (repo, revision):
        raise ValueError(f"{where} holds {source[0]}@{source[1]}, not {repo}@{revision}")
    if "pairs" not in properties:
        raise ValueError(f"{where} records no pairs")
    return _pairs(properties["pairs"])


@functools.cache
def _pairs(recorded: str) -> list[dict[str, Any]]:
    """The pairs a copy records, parsed once per process however many tasks read it: a copy holds up to 132 pairs, and each of their tasks reads two. Callers don't change the result."""
    return json.loads(recorded)


def recorded(dataset: Any) -> dict[str, str]:
    """The transaction properties of the Lance version `dataset` is open at, empty if it has none."""
    transaction = dataset.read_transaction(dataset.version)
    return dict(transaction.transaction_properties or {}) if transaction else {}


def write(uri: str, repo: str, revision: str, splits: Mapping[Pair, datasets.Dataset]) -> int:
    """
    Writes the `(subset, split)` tables of `repo` at `revision` as one new Lance version at `uri`, with the provenance [`staged_rows`] checks in its transaction properties.

    Rows keep their split's order and gain `hf_subset` and `hf_split` columns (stage 034, D3: HellaSwag has a `split` of its own). Pairs are written one after another, and each pair's row count, columns and features are recorded, so a reader gets back the rows `datasets` gives for that pair, even where pairs have different columns or one column of different types.

    Returns:
        `int`: the version written.

    Raises:
        `ValueError`: if a table already has an `hf_subset` or `hf_split` column.
    """
    import lance

    # One table for every pair. A column a pair lacks is null in its rows. A column whose type
    # differs between pairs (Paloma's `timestamp` is a time in some sources and text in others)
    # is stored once per type, as `name`, `name__1` and so on, and each pair records which of
    # the stored columns are its own, under which name.
    fields = {name: pa.field(name, pa.string()) for name in ("hf_subset", "hf_split")}
    pairs: list[dict[str, Any]] = []
    for (subset, split), rows in splits.items():
        clash = {"hf_subset", "hf_split"} & set(rows.column_names)
        if clash:
            raise ValueError(f"{repo} {subset or ''} {split}: the rows have a {clash} column")

        columns = {}
        for field in rows.data.schema:
            stored, copy = field.name, 0
            while stored in columns or (stored in fields and fields[stored].type != field.type):
                copy += 1
                stored = f"{field.name}__{copy}"
            fields.setdefault(stored, pa.field(stored, field.type))
            columns[stored] = field.name
        pairs.append(
            {
                "subset": subset,
                "split": split,
                "rows": rows.num_rows,
                "columns": columns,
                "features": rows.features.to_dict(),
            }
        )
    schema = pa.schema(list(fields.values()))

    # One pair's batches at a time, so a repo of a hundred splits isn't held in memory at once. A
    # filtered or shuffled `datasets.Dataset` keeps its rows behind an index, and `iter` gives
    # the rows it gives, in its order.
    def batches() -> Iterator[pa.RecordBatch]:
        for pair, rows in zip(pairs, splits.values(), strict=True):
            for table in rows.with_format("arrow").iter(batch_size=10_000):
                length = table.num_rows
                columns = {
                    "hf_subset": pa.array([pair["subset"]] * length, pa.string()),
                    "hf_split": pa.array([pair["split"]] * length, pa.string()),
                }
                for field in schema:
                    if field.name in pair["columns"]:
                        columns[field.name] = table.column(pair["columns"][field.name])
                    elif field.name not in columns:
                        columns[field.name] = pa.nulls(length, field.type)
                yield from pa.Table.from_pydict(columns, schema=schema).to_batches()

    written = lance.write_dataset(
        pa.RecordBatchReader.from_batches(schema, batches()),
        uri,
        mode="overwrite",
        transaction_properties={
            "source_repo": repo,
            "source_revision": revision,
            "pairs": json.dumps(pairs, ensure_ascii=False),
        },
    )
    return written.version


def pull(org: str, repo: str, revision: str, root: Path) -> Path:
    """
    Stages the version of `org`'s hub copy of `repo` tagged `revision` under `root`, where [`staged_rows`] finds it, and returns its folder.

    The copy is written to a temporary folder and renamed, so an interrupted pull leaves nothing a job could read, and a failed one removes its temporary folder. Pulls of one repo into one root take turns, under a lock on the repo's folder. A staged copy that records the same source and pairs is kept, since those decide its rows, whichever org or version it came from. One staged from an earlier version under the tag, before a split was added, or that can't be read, is replaced; a job reading it then fails.

    The staging root's permissions decide who reads every copy in it, private sets included, whatever the umask: folders between the root and a copy take the root's mode, and a copy's folders and files take it without anyone's write bits but the owner's (and files without execute bits), so nobody the root lets read a copy can change it. A root others can write to must have the sticky bit, as must every folder below it that others can write to, so nobody but a copy's owner can rename or replace it. A missing `root` is made readable by its owner only.

    Signs in to `org` with read-only keys first, so the copy recorded as `org`'s is read from `org`'s bucket.

    Raises:
        `ValueError`: if `repo` isn't `<owner>/<name>`, `revision` isn't a commit hash, the staging root or a folder below it is writable by others without the sticky bit, or the tagged version records another repo or revision, or no pairs.
        [`~hub.s3.S3Error`]: if the sign-in to `org` fails.
    """
    import lance

    from flock.hub import s3

    if not REPO.fullmatch(repo):
        raise ValueError(f"expected a repo OWNER/NAME, got {repo!r}")
    # A staged copy is read by the commit a task pins, never by a branch or tag that moves.
    if not COMMIT.fullmatch(revision):
        raise ValueError(f"{repo}: a staged copy needs a commit hash, not {revision!r}")
    s3.get_process(org, read_only=True)
    uri = f"{BUCKET}/{path(repo)}"
    source = lance.dataset(uri, version=revision)
    properties = recorded(source)
    check(properties, repo, revision, f"{uri} tag {revision} (version {source.version})")

    folder = root / repo / revision
    if _holds(folder, properties):
        return folder

    mode = _make_folders(root, folder.parent)
    with _locked(folder.parent):
        # Another pull may have staged the same rows while this one waited.
        if _holds(folder, properties):
            return folder
        partial = Path(
            tempfile.mkdtemp(dir=folder.parent, prefix=f".{revision}.", suffix=".partial")
        )
        try:
            lance.write_dataset(
                source.to_batches(),
                str(partial),
                schema=source.schema,
                mode="overwrite",
                transaction_properties={
                    **properties,
                    "hub_org": org,
                    "hub_uri": uri,
                    "hub_version": str(source.version),
                },
            )
            _protect(partial, mode)
            _replace(folder, partial)
        finally:
            shutil.rmtree(partial, ignore_errors=True)
    return folder


@contextlib.contextmanager
def _locked(folder: Path) -> Generator[None]:
    """Holds an exclusive lock on `folder`, shared by every process on the machines that mount it. The folder itself is locked, so no lock file sits beside the copies."""
    descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)  # closing releases the lock


def _replace(folder: Path, partial: Path) -> None:
    """Renames `partial` to `folder`, moving a copy already there aside first and deleting it only once the new one is in place. If the rename fails, the old copy is moved back."""
    stale = partial.with_suffix(".stale")
    if folder.exists():
        folder.rename(stale)
    try:
        partial.rename(folder)
    except OSError:
        if stale.exists():
            stale.rename(folder)
        raise
    shutil.rmtree(stale, ignore_errors=True)


def _open(folder: Path) -> Any:
    """
    The Lance dataset in `folder`, or `None` if nothing is there.

    Raises:
        `OSError`: if something is there that can't be read as a copy: a broken one, or another user's that you can't read.
    """
    import lance

    try:
        folder.stat()
    except FileNotFoundError:
        return None
    try:
        return lance.dataset(str(folder))
    except (ValueError, OSError) as error:
        raise OSError(f"{folder} is there but can't be read as a staged copy: {error}") from None


def _holds(folder: Path, properties: Mapping[str, str]) -> bool:
    """Whether the copy in `folder` records every one of `properties`; `False` if it can't be read, so a pull replaces it."""
    try:
        table = _open(folder)
    except OSError:
        return False
    if table is None:
        return False
    staged = recorded(table)
    return all(staged.get(key) == value for key, value in properties.items())


def _make_folders(root: Path, folder: Path) -> int:
    """
    Makes `folder` and the folders between it and `root` with `root`'s mode, setgid and sticky bits included, and returns that mode. A missing `root` is made with mode 0o700, and a new folder is made owner-only before it takes the mode, so it is never open to others' writes without the sticky bit.

    Raises:
        `ValueError`: if others can write to `root` or a folder between it and `folder` without the sticky bit, since they could then replace any copy below it.
    """
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = _guarded_mode(root)
    for made in [*reversed(folder.parents[: len(folder.relative_to(root).parts) - 1]), folder]:
        try:
            made.mkdir(mode=0o700)
            made.chmod(mode)
        except FileExistsError:
            _guarded_mode(made)  # made by another pull or by hand
    return mode


def _guarded_mode(folder: Path) -> int:
    """
    The permission bits of `folder`.

    Raises:
        `ValueError`: if others can write to it and it hasn't the sticky bit.
    """
    mode = stat.S_IMODE(folder.stat().st_mode)
    if mode & 0o022 and not mode & stat.S_ISVTX:
        raise ValueError(
            f"others can write to the staging folder {folder}, so they could replace a copy: "
            f"set its sticky bit (chmod +t) or stage elsewhere"
        )
    return mode


def _protect(folder: Path, mode: int) -> None:
    """Gives `folder` and every folder in it `mode` without group and other write bits or the sticky bit, which nobody else's writes need, and every file in it `mode` without those or any execute bit."""
    folders, files = mode & ~0o022 & ~stat.S_ISVTX, mode & 0o644
    folder.chmod(folders)
    for made in folder.rglob("*"):
        made.chmod(folders if made.is_dir() else files)


def _pull_command(root: Path, repo: str, revision: str) -> str:
    """The command that stages `repo` at `revision` under `root`, with the org left to fill in."""
    return f"flock eval hub pull <org>/{repo}@{revision} --to {root}"


def _copy(root: Path, repo: str, revision: str) -> tuple[Any, list[dict[str, Any]]]:
    """
    The copy of `repo` at `revision` staged under `root`, and the pairs it records.

    Raises:
        `ValueError`: if `revision` isn't a commit hash, or the copy records another repo or revision.
        `FileNotFoundError`: if the pin isn't staged.
        `OSError`: if its folder is there but can't be read, naming whose it is and the pull command that replaces it.
    """
    if not COMMIT.fullmatch(revision):
        raise ValueError(f"{repo}: a staged copy needs a commit hash, not {revision!r}")

    folder = root / repo / revision
    command = _pull_command(root, repo, revision)
    try:
        table = _open(folder)
    except OSError as error:
        raise OSError(
            f"{error}. It belongs to user {folder.lstat().st_uid}; if that is you, run "
            f"`{command}` to replace it, otherwise ask them or stage elsewhere"
        ) from None
    if table is None:
        raise FileNotFoundError(
            f"{repo}@{revision} isn't staged in {root}: run "
            f"`{command}` on a machine that reaches the hub"
        )
    return table, check(recorded(table), repo, revision, str(folder))


def check_staged(root: Path, pins: Mapping[tuple[str, str], Iterable[Pair]]) -> None:
    """
    Checks that each `(repo, revision)` pin is staged under `root` with every pair it maps to, so a run fails before its model loads instead of at its first unstaged task.

    Raises:
        `FileNotFoundError`: naming every pin that isn't staged or lacks a pair, each with its pull command.
        `OSError`: if a pin's folder is there but can't be read.
        `ValueError`: if a revision isn't a commit hash, or a copy records another repo or revision.
    """
    missing = []
    for (repo, revision), pairs in sorted(pins.items()):
        try:
            _, held = _copy(root, repo, revision)
        except FileNotFoundError:
            held = []
        if not set(pairs) <= {(pair["subset"], pair["split"]) for pair in held}:
            missing.append(_pull_command(root, repo, revision))
    if missing:
        raise FileNotFoundError(
            f"not staged in {root}, or staged without a split a task reads; on a machine that "
            f"reaches the hub, run:\n" + "\n".join(missing)
        )


def staged_rows(
    root: Path, repo: str, revision: str, subset: str | None, split: str
) -> datasets.Dataset:
    """
    The rows of one split of `repo` at `revision` from the copy staged under `root`, as `datasets` gives them from the Hugging Face Hub.

    Raises:
        `ValueError`: if `revision` isn't a commit hash, or the staged copy records another repo or revision.
        `FileNotFoundError`: if the pin isn't staged.
        `OSError`: if its folder is there but can't be read.
        `KeyError`: if the staged copy doesn't hold the split.
    """
    table, pairs = _copy(root, repo, revision)
    held = [(pair["subset"], pair["split"]) for pair in pairs]
    if (subset, split) not in held:
        raise KeyError(f"{root / repo / revision} has no split {split!r} of subset {subset!r}")
    index = held.index((subset, split))
    pair = pairs[index]
    # `write` puts the pairs one after another, so a pair's rows start after the earlier pairs'.
    offset = sum(earlier["rows"] for earlier in pairs[:index])

    columns = pair["columns"]
    features = datasets.Features.from_dict(pair["features"])
    rows = table.to_table(columns=list(columns), offset=offset, limit=pair["rows"])
    rows = rows.rename_columns(list(columns.values())).select(list(features))
    rows = rows.cast(features.arrow_schema)
    return datasets.Dataset(rows, info=datasets.DatasetInfo(features=features))
