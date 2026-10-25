"""
Writing hub datasets: resumable uploads that commit exactly once, `push`, and edits.

A [`DatasetWriter`] keeps its state in a staging directory, so a crashed upload is rerun with
the same directory: shards already written are skipped, and a commit that already landed is
found by its `flock.upload_id` transaction property rather than made again.
"""

import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import datasets
import lance
import lance.fragment
import pyarrow as pa
import pyarrow.fs as pafs
from lance import LanceOperation
from lance.commit import CommitConflictError
from lance.dataset import LanceDataset, Transaction
from lance.fragment import FragmentMetadata

from flock.hub.client import DatasetNotFound
from flock.hub.config import HubConfig, get_default, parse_repo_id
from flock.hub.dataset import CARD_KEY, Dataset, Pin

if TYPE_CHECKING:
    import pandas

FORMAT_OPTIONS: dict[str, Any] = {
    "data_storage_version": "2.2",
    "max_rows_per_file": 1_000_000,
    "max_bytes_per_file": 2 * 1024**3,
}
"""How every fragment is written (D6). `enable_stable_row_ids` is set when a dataset is created."""

UPLOAD_ID = "flock.upload_id"
MESSAGE = "__lance_commit_message"

_SHARD_ID = re.compile(r"^[A-Za-z0-9._-]{1,200}$")


class CommitConflict(Exception):
    """The dataset was overwritten or restored after the upload's base version, so its rows can't be appended."""


DataLike = Any
"""Data `push` and `write_shard` take: an Arrow table, record batch or reader, an HF `Dataset` or `IterableDataset`, a pandas `DataFrame`, a list of row dicts, or a dict of columns."""


class DatasetWriter:
    """
    A resumable upload to a hub dataset, committed once as one version.

    On creation it records its base version (the latest; `None` for a new dataset), `mode` and `upload_id` in `staging_dir/upload.json`, or reloads them if the file exists. Rerunning a crashed upload with the same `staging_dir` skips the shards already written and never commits twice.

    Args:
        repo_id (`str`):
            The dataset, as `org/name`. On the server it must exist ([`create_dataset`]); in direct mode the first commit creates it.
        mode (`str`, *optional*, defaults to `"append"`):
            `"append"` adds the rows to the base version, rebased onto appends that land in between. `"overwrite"` replaces all rows (and the schema).
        staging_dir (`str` or `Path`):
            Where the upload's state is kept. Reuse it to resume.
        upload_id (`str`, *optional*):
            The upload's id, recorded on its commit. A new uuid if not given.
        hub_config (`HubConfig`, *optional*):
            Where the hub is. Defaults to [`~config.get_default`].

    Raises:
        `ValueError`: if `staging_dir` holds another upload (another dataset, mode or id).
    """

    def __init__(
        self,
        repo_id: str,
        mode: Literal["append", "overwrite"] = "append",
        staging_dir: str | os.PathLike | None = None,
        upload_id: str | None = None,
        *,
        hub_config: HubConfig | None = None,
    ) -> None:
        if mode not in ("append", "overwrite"):
            raise ValueError(f"mode must be 'append' or 'overwrite', got {mode!r}")
        if staging_dir is None:
            raise ValueError("DatasetWriter needs a staging_dir to resume from")
        self.repo_id = repo_id
        self.mode = mode
        self.hub = hub_config or get_default()
        self.staging_dir = Path(staging_dir)
        self.uri, self.dataset_id = _locate(repo_id, self.hub)
        (self.staging_dir / "shards").mkdir(parents=True, exist_ok=True)

        state_path = self.staging_dir / "upload.json"
        if state_path.exists():
            state = json.loads(state_path.read_text())
            expected = {"repo_id": repo_id, "mode": mode}
            if upload_id is not None:
                expected["upload_id"] = upload_id
            for key, value in expected.items():
                if state[key] != value:
                    raise ValueError(
                        f"{self.staging_dir} holds an upload with {key}={state[key]!r}, not {value!r}"
                    )
        else:
            state: dict[str, Any] = {
                "repo_id": repo_id,
                "mode": mode,
                "upload_id": upload_id or str(uuid.uuid4()),
                "read_version": _latest_version(self.uri, self.hub),
            }
            _write_json(state_path, state)
        self.upload_id: str = state["upload_id"]
        self.read_version: int | None = state["read_version"]

    @property
    def committed_version(self) -> int | None:
        """The version this upload committed, or `None` before its commit."""
        path = self.staging_dir / "committed.json"
        return json.loads(path.read_text())["version"] if path.exists() else None

    def shard_ids(self) -> list[str]:
        """The shards written so far, in the order they will be committed (sorted by id)."""
        return sorted(p.stem for p in (self.staging_dir / "shards").glob("*.json"))

    def write_shard(self, shard_id: str, data: DataLike) -> bool:
        """
        Writes one shard's rows as Lance fragments, uncommitted, unless the shard was already written.

        Shards are committed in the order of their ids, so name them to sort, for example `00042`.

        Args:
            shard_id (`str`):
                The shard's name: letters, digits, `.`, `_` and `-`.
            data (`DataLike`):
                The rows. Every shard must have the same schema.

        Returns:
            `bool`: `False` if the shard was already written (by an earlier run), else `True`.

        Raises:
            `ValueError`: for an invalid `shard_id`, after the commit, or if the schema differs from the first shard's.
        """
        if not _SHARD_ID.match(shard_id):
            raise ValueError(f"shard ids are 1-200 of [A-Za-z0-9._-], got {shard_id!r}")
        path = self.staging_dir / "shards" / f"{shard_id}.json"
        if path.exists():
            return False
        if self.committed_version is not None:
            raise ValueError(f"this upload was committed as version {self.committed_version}")

        reader = as_reader(data)
        appending = self.mode == "append" and self.read_version is not None
        if appending:
            reader = self._conform(reader)
        first = self._schema()
        if first is not None and not reader.schema.equals(first):
            raise ValueError(
                f"shard {shard_id!r} has schema {reader.schema}, but the upload's is {first}"
            )
        fragments = lance.fragment.write_fragments(
            reader,
            self.uri,
            # "append" assigns field ids from the existing dataset's schema; otherwise the
            # fragments start a schema of their own.
            mode="append" if appending else "overwrite",
            storage_options=self.hub.lance_storage_options(),
            enable_stable_row_ids=True,
            **FORMAT_OPTIONS,
        )
        assert isinstance(fragments, list)
        if first is None:
            _write_bytes(self.staging_dir / "schema.arrow", reader.schema.serialize().to_pybytes())
        # Written last and atomically: a shard with a file is complete. A crash before this
        # leaves data files no version refers to, which a prune removes.
        _write_json(path, {"fragments": [f.to_json() for f in fragments]})
        return True

    def commit(self, message: str | None = None, properties: dict[str, str] | None = None) -> int:
        """
        Commits every written shard as one version, exactly once.

        If an earlier run already committed (and crashed before recording it), that version is found through its `flock.upload_id` and returned instead.

        Args:
            message (`str`, *optional*):
                The version's message.
            properties (`dict[str, str]`, *optional*):
                More transaction properties, such as `flock.source`.

        Returns:
            `int`: the version.

        Raises:
            `ValueError`: if no shard was written, or `properties` sets `flock.upload_id`.
            [`CommitConflict`]: in append mode, if the dataset was overwritten or restored after the base version.
        """
        if (version := self.committed_version) is not None:
            return version
        if properties and UPLOAD_ID in properties:
            raise ValueError(f"{UPLOAD_ID} is set by the writer")
        shard_ids = self.shard_ids()
        if not shard_ids:
            raise ValueError("no shards were written, so there is nothing to commit")

        version = self._find_commit()
        if version is None:
            transaction_properties = {UPLOAD_ID: self.upload_id, **(properties or {})}
            if message is not None:
                transaction_properties[MESSAGE] = message
            version = self._commit(shard_ids, transaction_properties)
        _mark_committed(self.staging_dir, version)
        return version

    def _commit(self, shard_ids: list[str], transaction_properties: dict[str, str]) -> int:
        fragments = [
            FragmentMetadata.from_json(json.dumps(f))
            for shard_id in shard_ids
            for f in json.loads((self.staging_dir / "shards" / f"{shard_id}.json").read_text())[
                "fragments"
            ]
        ]
        latest = _latest_version(self.uri, self.hub)
        if self.read_version is None and latest is not None:
            raise CommitConflict(
                f"{self.repo_id} was created by another writer (now at version {latest}) after "
                "this upload started; rerun it with a new staging_dir"
            )
        if self.mode == "append" and self.read_version is not None:
            self._check_rebase(latest)
            operation = LanceOperation.Append(fragments)
        else:
            schema = self._schema()
            assert schema is not None
            operation = LanceOperation.Overwrite(schema, fragments)

        try:
            committed = LanceDataset.commit(
                self.uri,
                Transaction(
                    read_version=self.read_version or 0,
                    operation=operation,
                    transaction_properties=transaction_properties,
                ),
                storage_options=self.hub.lance_storage_options(),
                enable_stable_row_ids=True if self.read_version is None else None,
            )
        except CommitConflictError as e:
            raise CommitConflict(f"{self.repo_id}: {e}") from e
        return committed.version

    def _find_commit(self) -> int | None:
        """The version an earlier run of this upload committed, if any."""
        latest = _latest_version(self.uri, self.hub)
        if latest is None:
            return None
        ds = lance.dataset(self.uri, storage_options=self.hub.lance_storage_options())
        for version in range((self.read_version or 0) + 1, latest + 1):
            transaction = ds.read_transaction(version)
            properties = transaction.transaction_properties if transaction is not None else None
            if (properties or {}).get(UPLOAD_ID) == self.upload_id:
                return version
        return None

    def _check_rebase(self, latest: int | None) -> None:
        """Raises [`CommitConflict`] if a version after the base overwrote or restored the data."""
        assert self.read_version is not None and latest is not None
        ds = lance.dataset(self.uri, storage_options=self.hub.lance_storage_options())
        for version in range(self.read_version + 1, latest + 1):
            transaction = ds.read_transaction(version)
            if transaction is None:
                continue
            op = transaction.operation
            if isinstance(op, LanceOperation.Overwrite | LanceOperation.Restore):
                raise CommitConflict(
                    f"{self.repo_id} was {'overwritten' if isinstance(op, LanceOperation.Overwrite) else 'restored'} "
                    f"at version {version}, after this upload's base version {self.read_version}; "
                    "rerun the upload against the new data, or with mode='overwrite' to replace it"
                )

    def _conform(self, reader: pa.RecordBatchReader) -> pa.RecordBatchReader:
        """Casts appended data to the base version's column types, where they differ."""
        # pandas makes `large_string` columns and HF may make `string` ones; Lance only appends
        # data of exactly the dataset's types.
        base = lance.dataset(
            self.uri, version=self.read_version, storage_options=self.hub.lance_storage_options()
        ).schema
        fields = []
        for field in reader.schema:
            index = base.get_field_index(field.name)
            target = base.field(index) if index != -1 else None
            if target is None or isinstance(target.type, pa.ExtensionType):
                fields.append(field)  # unknown columns are Lance's to refuse; blobs stay bytes
            else:
                fields.append(field.with_type(target.type))
        schema = pa.schema(fields, metadata=reader.schema.metadata)
        return reader if schema.equals(reader.schema) else reader.cast(schema)

    def _schema(self) -> pa.Schema | None:
        """The schema of the upload's first shard, if one was written."""
        path = self.staging_dir / "schema.arrow"
        if not path.exists():
            return None
        return pa.ipc.read_schema(pa.py_buffer(path.read_bytes()))


def _mark_committed(staging_dir: Path, version: int) -> None:
    """Records the committed version in `staging_dir/committed.json`."""
    _write_json(staging_dir / "committed.json", {"version": version})


def push(
    data: DataLike,
    repo_id: str,
    mode: Literal["append", "overwrite"] = "append",
    message: str | None = None,
    *,
    properties: dict[str, str] | None = None,
    hub_config: HubConfig | None = None,
) -> Dataset:
    """
    Writes in-memory data to a hub dataset as one new version.

    For uploads that may be interrupted, use a [`DatasetWriter`] with a staging directory you keep.

    Args:
        data (`DataLike`):
            An HF `Dataset` or `IterableDataset`, an Arrow table or reader, a pandas `DataFrame`, a list of row dicts or a dict of columns.
        repo_id (`str`):
            The dataset, as `org/name`.
        mode (`str`, *optional*, defaults to `"append"`):
            `"append"` or `"overwrite"`, as for [`DatasetWriter`].
        message (`str`, *optional*):
            The version's message.
        properties (`dict[str, str]`, *optional*):
            More transaction properties.
        hub_config (`HubConfig`, *optional*):
            Where the hub is.

    Returns:
        [`Dataset`]: the dataset at the new version (streaming).
    """
    hub = hub_config or get_default()
    with tempfile.TemporaryDirectory(prefix="flock-hub-push-") as staging_dir:
        writer = DatasetWriter(repo_id, mode, staging_dir, hub_config=hub)
        writer.write_shard("0", data)
        version = writer.commit(message, properties)
    return _load(repo_id, version, hub)


def create_dataset(
    repo_id: str,
    description: str = "",
    visibility: Literal["private", "internal", "public"] = "private",
    *,
    hub_config: HubConfig | None = None,
) -> dict[str, Any]:
    """
    Creates an empty dataset on the server. Its first version comes with the first write.

    In direct mode there is nothing to create (the first commit creates the Lance dataset), so this only returns the URI.

    Returns:
        `dict`: the server's `DatasetInfo`, or `{"org", "name", "uri"}` in direct mode.
    """
    hub = hub_config or get_default()
    org, name = parse_repo_id(repo_id)
    if hub.direct:
        return {"org": org, "name": name, "uri": hub.dataset_uri(org, name)}
    return hub.client.create_dataset(org, name, description, visibility)


# ---- edits on a Dataset ----


def delete_rows(dataset: Dataset, where: str, message: str | None) -> Dataset:
    """Deletes rows matching `where` at the latest version. See [`Dataset.delete`]."""
    latest = dataset._spec.pin.open_latest()
    latest.delete(where)
    return _after_edit(dataset, latest.version, message)


def update_rows(
    dataset: Dataset, values: dict[str, str], where: str | None, message: str | None
) -> Dataset:
    """Updates rows at the latest version. See [`Dataset.update`]."""
    latest = dataset._spec.pin.open_latest()
    latest.update(values, where)
    return _after_edit(dataset, latest.version, message)


def add_columns(dataset: Dataset, transforms: Any, message: str | None) -> Dataset:
    """Adds columns at the latest version. See [`Dataset.add_columns`]."""
    latest = dataset._spec.pin.open_latest()
    latest.add_columns(transforms)
    return _after_edit(dataset, latest.version, message)


def restore(dataset: Dataset, message: str | None) -> Dataset:
    """Makes the pinned version the latest. See [`Dataset.restore`]."""
    pin = dataset._spec.pin
    old = lance.dataset(
        pin.uri, version=pin.version, storage_options=pin.hub.lance_storage_options()
    )
    old.restore()
    return _after_edit(dataset, old.version, message)


def set_card(dataset: Dataset, text: str, message: str | None) -> Dataset:
    """Stores the card as a sidecar and points a new version to it. See [`Dataset.set_card`]."""
    pin = dataset._spec.pin
    data = text.encode()
    sha = hashlib.sha256(data).hexdigest()
    # Content-addressed, so writing it again (a rerun, the same card twice) is harmless.
    fs, path = _filesystem(_card_uri(pin.uri, sha), pin.hub.lance_storage_options())
    fs.create_dir(path.rpartition("/")[0], recursive=True)
    with fs.open_output_stream(path) as f:
        f.write(data)
    latest = pin.open_latest()
    latest.update_metadata({CARD_KEY: sha})
    return _after_edit(dataset, latest.version, message)


def read_card(pin: Pin) -> str | None:
    """The card the pinned version points to, or `None`."""
    sha = pin.open().metadata.get(CARD_KEY)
    if sha is None:
        return None
    fs, path = _filesystem(_card_uri(pin.uri, sha), pin.hub.lance_storage_options())
    with fs.open_input_stream(path) as f:
        return f.read().decode()


def _card_uri(dataset_uri: str, sha: str) -> str:
    return f"{dataset_uri.rstrip('/')}/_flock/cards/{sha}.md"


def _after_edit(dataset: Dataset, version: int, message: str | None) -> Dataset:
    """Sets an edit's message (on the server) and returns the dataset at the edit's version."""
    pin = dataset._spec.pin
    # These Lance calls can't carry transaction properties, so the server records the message.
    # Direct mode has no server, so the message is dropped.
    if message is not None and not pin.hub.direct:
        org, name = parse_repo_id(dataset.repo_id)
        pin.hub.client.set_version_message(org, name, version, message)
    return dataset.checkout(version)


# ---- helpers ----


def _load(repo_id: str, version: int, hub: HubConfig) -> Dataset:
    # Imported here: `load` imports `dataset`, which imports this module lazily.
    from flock.hub.load import load_dataset

    return load_dataset(repo_id, version, hub_config=hub)


def _locate(repo_id: str, hub: HubConfig) -> tuple[str, str | None]:
    """The dataset's URI and server id (`None` in direct mode)."""
    org, name = parse_repo_id(repo_id)
    if hub.direct:
        return hub.dataset_uri(org, name), None
    try:
        info = hub.client.get_dataset(org, name)
    except DatasetNotFound as e:
        raise DatasetNotFound(f"{repo_id} doesn't exist; create it with create_dataset", 404) from e
    return info["uri"], info["id"]


def _latest_version(uri: str, hub: HubConfig) -> int | None:
    """The dataset's latest version, or `None` if it has none yet."""
    try:
        return lance.dataset(uri, storage_options=hub.lance_storage_options()).version
    except (OSError, ValueError) as e:
        if "not found" in str(e).lower():
            return None
        raise


def as_reader(data: DataLike) -> pa.RecordBatchReader:
    """
    An Arrow reader over any data `push` takes.

    Raises:
        `TypeError`: for data of another type.
    """
    if isinstance(data, pa.RecordBatchReader):
        return data
    if isinstance(data, pa.Table):
        return data.to_reader()
    if isinstance(data, pa.RecordBatch):
        return pa.Table.from_batches([data]).to_reader()
    if isinstance(data, Dataset):
        data = data.to_hf()
    if isinstance(data, datasets.Dataset | datasets.IterableDataset):
        return _hf_reader(data)
    if type(data).__module__.partition(".")[0] == "pandas":
        frame: pandas.DataFrame = data
        return pa.Table.from_pandas(frame, preserve_index=False).to_reader()
    if isinstance(data, list):
        return pa.Table.from_pylist(data).to_reader()
    if isinstance(data, dict):
        return pa.table(data).to_reader()
    raise TypeError(f"can't write data of type {type(data).__name__}")


def _hf_reader(data: datasets.Dataset | datasets.IterableDataset) -> pa.RecordBatchReader:
    """Reads an HF dataset in Arrow batches, without loading it all into memory."""
    tables = data.with_format("arrow").iter(batch_size=10_000)
    first = next(tables, None)
    if data.features is not None:
        schema = data.features.arrow_schema
    elif first is not None:
        schema = first.schema
    else:
        raise ValueError("can't write an empty dataset whose features aren't known")

    def batches() -> Iterator[pa.RecordBatch]:
        if first is not None:
            yield from first.cast(schema).to_batches()
        for table in tables:
            yield from table.cast(schema).to_batches()

    return pa.RecordBatchReader.from_batches(schema, batches())


def _filesystem(uri: str, storage_options: dict[str, str]) -> tuple[pafs.FileSystem, str]:
    """A pyarrow filesystem and path for a file beside a Lance dataset, with Lance's storage options."""
    if uri.startswith("s3://"):
        endpoint = storage_options.get("aws_endpoint", "")
        scheme, _, host = endpoint.rpartition("://")
        fs = pafs.S3FileSystem(
            access_key=storage_options.get("aws_access_key_id"),
            secret_key=storage_options.get("aws_secret_access_key"),
            session_token=storage_options.get("aws_session_token"),
            region=storage_options.get("aws_region", "us-east-1"),
            endpoint_override=host or None,
            scheme=scheme or "https",
        )
        return fs, uri.removeprefix("s3://")
    if uri.startswith("file://"):
        return pafs.LocalFileSystem(), uri.removeprefix("file://")
    return pafs.LocalFileSystem(), os.path.abspath(uri)


def _write_json(path: Path, value: Any) -> None:
    _write_bytes(path, json.dumps(value).encode())


def _write_bytes(path: Path, data: bytes) -> None:
    """Writes `data` to `path` atomically, so a crash never leaves half a file."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
