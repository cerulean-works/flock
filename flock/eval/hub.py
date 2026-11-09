"""Copies of pinned Hugging Face datasets on the Flock hub, and the staged copies jobs read."""

import json
import re
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import datasets
import pyarrow as pa

COMMIT = re.compile(r"[0-9a-f]{40}")
"""A full commit hash: the only revision a hub copy is staged and read by, since a branch or tag moves."""


def recorded(dataset: Any) -> dict[str, str]:
    """The transaction properties of the Lance version `dataset` is open at, empty if it has none."""
    transaction = dataset.read_transaction(dataset.version)
    return dict(transaction.transaction_properties or {}) if transaction else {}


def write(
    uri: str, repo: str, revision: str, splits: Mapping[tuple[str | None, str], datasets.Dataset]
) -> int:
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
    # A filtered or shuffled `datasets.Dataset` keeps its rows behind an index; this table is the
    # rows it gives, in its order.
    tables = {pair: rows.with_format("arrow")[:] for pair, rows in splits.items()}
    for (subset, split), rows in splits.items():
        clash = {"hf_subset", "hf_split"} & set(rows.column_names)
        if clash:
            raise ValueError(f"{repo} {subset or ''} {split}: the rows have a {clash} column")

        columns = {}
        for field in tables[(subset, split)].schema:
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

    def batches() -> Iterator[pa.RecordBatch]:
        for pair, table in zip(pairs, tables.values(), strict=True):
            for batch in table.to_batches(max_chunksize=10_000):
                length = batch.num_rows
                columns = {
                    "hf_subset": pa.array([pair["subset"]] * length, pa.string()),
                    "hf_split": pa.array([pair["split"]] * length, pa.string()),
                }
                for field in schema:
                    if field.name in pair["columns"]:
                        columns[field.name] = batch.column(pair["columns"][field.name])
                    elif field.name not in columns:
                        columns[field.name] = pa.nulls(length, field.type)
                yield pa.RecordBatch.from_pydict(columns, schema=schema)

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


def pull(org: str, uri: str, revision: str, root: Path) -> Path:
    """
    Stages the version of `org`'s hub copy at `uri` (`.../eval/<owner>/<name>`) tagged `revision` under `root`, where [`staged_rows`] finds it, and returns its folder.

    The copy is written to a temporary folder and renamed, so an interrupted pull leaves nothing a job could read, and a failed one removes its temporary folder. A pin already staged from the tagged version of `org`'s copy, or staged meanwhile by another pull, is left as it is; one staged from another version, or that can't be read, is replaced.

    Every folder and file the pull makes takes the mode of `root` (files without execute bits), so the staging root's permissions decide who reads every copy in it, private sets included. A missing `root` is made readable by its owner only.

    Raises:
        `ValueError`: if the tagged version records another revision, a repo other than the one `uri` names, or no pairs.
    """
    import lance

    source = lance.dataset(uri, version=revision)
    properties = recorded(source)
    if properties.get("source_revision") != revision:
        raise ValueError(
            f"{uri} tag {revision} is on version {source.version}, which records revision "
            f"{properties.get('source_revision')!r}"
        )

    repo = properties.get("source_repo") or ""
    if not repo or not uri.endswith(f"/eval/{repo}"):
        raise ValueError(f"{uri} tag {revision} holds a copy of {repo!r}")
    if "pairs" not in properties:
        raise ValueError(
            f"{uri} tag {revision} is on version {source.version}, which records no pairs"
        )

    # URIs are alike across orgs, so the org is recorded beside the hub version.
    staged = {**properties, "hub_org": org, "hub_uri": uri, "hub_version": str(source.version)}
    folder = root / repo / revision
    # A copy staged from an earlier version under the tag is replaced: the mirror moves the tag
    # when a task starts reading another split of the pin.
    if _staged(folder) == staged:
        return folder

    mode = _make_folders(root, folder.parent)
    partial = Path(tempfile.mkdtemp(dir=folder.parent, prefix=f".{revision}.", suffix=".partial"))
    try:
        lance.write_dataset(
            source.to_batches(),
            str(partial),
            schema=source.schema,
            mode="overwrite",
            transaction_properties=staged,
        )
        _set_mode(partial, mode)
        # Another pull of the same version may have finished meanwhile, and a job may be
        # reading its copy already.
        if _staged(folder) != staged:
            if folder.exists():
                stale = partial.with_suffix(".stale")
                folder.rename(stale)
                shutil.rmtree(stale)
            partial.rename(folder)
    except Exception:
        if _staged(folder) != staged:
            raise
        # Another pull of the same version renamed its copy in first.
    finally:
        shutil.rmtree(partial, ignore_errors=True)
    return folder


def _staged(folder: Path) -> dict[str, str] | None:
    """What the copy staged in `folder` records, or `None` if there is none or it can't be read."""
    import lance

    try:
        return recorded(lance.dataset(str(folder)))
    except ValueError, OSError:
        return None


def _make_folders(root: Path, folder: Path) -> int:
    """Makes `folder` and the folders between it and `root` with `root`'s mode, and returns that mode. A missing `root` is made with mode 0o700."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = root.stat().st_mode & 0o777
    for path in [*reversed(folder.parents[: len(folder.relative_to(root).parts) - 1]), folder]:
        if not path.exists():
            path.mkdir(exist_ok=True)
            path.chmod(mode)
    return mode


def _set_mode(folder: Path, mode: int) -> None:
    """Gives `folder` and every folder in it `mode`, and every file in it `mode` without execute bits."""
    folder.chmod(mode)
    for path in folder.rglob("*"):
        path.chmod(mode if path.is_dir() else mode & 0o666)


def staged_rows(
    root: Path, repo: str, revision: str, subset: str | None, split: str
) -> datasets.Dataset:
    """
    The rows of one split of `repo` at `revision` from the copy staged under `root`, as `datasets` gives them from the Hugging Face Hub.

    Raises:
        `ValueError`: if `revision` isn't a commit hash, or the staged copy records another repo or revision.
        `FileNotFoundError`: if the pin isn't staged.
        `KeyError`: if the staged copy doesn't hold the split.
    """
    import lance

    if not COMMIT.fullmatch(revision):
        raise ValueError(f"{repo}: a staged copy needs a commit hash, not {revision!r}")

    folder = root / repo / revision
    if not folder.exists():
        raise FileNotFoundError(
            f"{repo}@{revision} isn't staged in {root}: run "
            f"`flock eval hub pull <org>/{repo}@{revision} --to {root}` on a machine that "
            f"reaches the hub"
        )

    table = lance.dataset(str(folder))
    properties = recorded(table)
    source = (properties.get("source_repo"), properties.get("source_revision"))
    if source != (repo, revision):
        raise ValueError(f"{folder} holds {source[0]}@{source[1]}, not {repo}@{revision}")

    if "pairs" not in properties:
        raise ValueError(f"{folder} records no pairs: pull {repo}@{revision} again")
    pairs = json.loads(properties["pairs"])
    held = [(pair["subset"], pair["split"]) for pair in pairs]
    if (subset, split) not in held:
        raise KeyError(f"{folder} has no split {split!r} of subset {subset!r}")
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
