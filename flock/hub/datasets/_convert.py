"""Converting a downloaded folder to Lance datasets in `flock-datasets` (D120, D121), with
many processes writing fragments and the parent committing each split at once."""

import json
import multiprocessing
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TextIO
from urllib.parse import urlsplit

from flock.hub.datasets._common import DataFile, DatasetError, Manifest

if TYPE_CHECKING:
    import lance
    import pyarrow as pa
    import pyarrow.fs as pafs
    from lance.fragment import FragmentMetadata

    from flock.hub.s3 import S3Credentials

BUCKET = "flock-datasets"

BATCH_BYTES = 64 * 2**20
"""The most a worker reads from a Parquet file at once (estimated from its row groups)."""

TEXT_BLOCK = 16 * 2**20
"""How much of a JSON Lines or CSV file is parsed at once."""

type Mode = Literal["create", "overwrite", "append"]
MODES = ("create", "overwrite", "append")


@dataclass(frozen=True)
class ConvertedSplit:
    """
    A split written to Lance by [`~hub.datasets.convert`].

    Attributes:
        split (`str`):
            The split, such as `train`.
        path (`str`):
            Where it is in `flock-datasets`, such as `web/fineweb-edu/train`.
        uri (`str`):
            The dataset's URI, such as `s3://flock-datasets/web/fineweb-edu/train`.
        version (`int`):
            The Lance version the import committed.
        rows (`int`):
            The rows the import wrote.
    """

    split: str
    path: str
    uri: str
    version: int
    rows: int


@dataclass(frozen=True)
class Task:
    """One source file for a worker to write as Lance fragments."""

    split: str
    index: int
    path: str
    format: str
    uri: str
    schema: pa.Schema
    storage_version: str | None
    storage_options: dict[str, str] | None = None


@dataclass(frozen=True)
class Written:
    """What a worker wrote for one source file."""

    split: str
    index: int
    fragments: list[FragmentMetadata]
    rows: int
    bytes: int


# ---------------------------------------------------------------------- reading source files


def _json_document(path: str) -> pa.Table:
    """A `.json` file that isn't JSON Lines: a list of rows (read whole, as `datasets` does)."""
    import pyarrow as pa

    with pa.input_stream(path) as stream:
        data = json.loads(stream.read())
    if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
        raise DatasetError(f"{path} is neither JSON Lines nor a JSON list of rows")
    return pa.Table.from_pylist(data)


def _json_batches(path: str, schema: pa.Schema | None) -> Iterator[pa.RecordBatch]:
    import pyarrow as pa
    import pyarrow.json as pj

    parse = (
        pj.ParseOptions(explicit_schema=schema, unexpected_field_behavior="error")
        if schema is not None
        else pj.ParseOptions()
    )
    stream = pa.input_stream(path)
    try:
        reader = pj.open_json(
            stream, read_options=pj.ReadOptions(block_size=TEXT_BLOCK), parse_options=parse
        )
    except pa.ArrowInvalid:
        stream.close()
        yield from _json_document(path).to_batches(max_chunksize=1 << 16)
        return
    with stream:
        yield from reader


def batches(path: str, format: str, schema: pa.Schema | None = None) -> Iterator[pa.RecordBatch]:
    """
    Streams a source file as Arrow record batches, each at most about 64 MB.

    Args:
        path (`str`):
            The file.
        format (`str`):
            `parquet`, `json` (JSON Lines, or a JSON list of rows), `csv` or `arrow`.
        schema (`pa.Schema`, *optional*):
            For JSON Lines and CSV, the types to read columns as instead of inferring them.

    Returns:
        `Iterator[pa.RecordBatch]`: the file's rows, in order.
    """
    import pyarrow as pa

    if format == "parquet":
        import pyarrow.parquet as pq

        with pq.ParquetFile(path) as parquet:
            meta = parquet.metadata
            size = sum(meta.row_group(i).total_byte_size for i in range(meta.num_row_groups))
            per_row = max(1, size // max(meta.num_rows, 1))
            rows = max(1, min(BATCH_BYTES // per_row, 1 << 20))
            yield from parquet.iter_batches(batch_size=rows)
    elif format == "arrow":
        with pa.memory_map(path) as source:
            try:
                yield from pa.ipc.open_stream(source)
            except pa.ArrowInvalid:
                source.seek(0)
                with pa.ipc.open_file(source) as reader:
                    for i in range(reader.num_record_batches):
                        yield reader.get_batch(i)
    elif format == "json":
        yield from _json_batches(path, schema)
    elif format == "csv":
        import pyarrow.csv as pcsv

        convert = pcsv.ConvertOptions(column_types=schema) if schema is not None else None
        with pa.input_stream(path) as stream:
            yield from pcsv.open_csv(
                stream,
                read_options=pcsv.ReadOptions(block_size=TEXT_BLOCK),
                convert_options=convert,
            )
    else:
        raise DatasetError(f"{path} is in a format Flock doesn't read ({format})")


def read_schema(path: str, format: str) -> pa.Schema:
    """A source file's schema: Parquet's footer, an Arrow file's header, or what the first
    block of a JSON Lines or CSV file holds."""
    import pyarrow as pa

    if format == "parquet":
        import pyarrow.parquet as pq

        return pq.read_schema(path)
    if format == "arrow":
        with pa.memory_map(path) as source:
            try:
                return pa.ipc.open_stream(source).schema
            except pa.ArrowInvalid:
                source.seek(0)
                return pa.ipc.open_file(source).schema
    for batch in batches(path, format):
        return batch.schema
    if format == "csv":
        import pyarrow.csv as pcsv

        return pcsv.read_csv(path).schema
    return pa.schema([])


def unify(folder: Path, files: list[DataFile]) -> pa.Schema:
    """
    One schema for a split's files, refusing the first file that disagrees with those before
    it. Columns missing from some files are filled with nulls, and numbers widen (an int column
    in one file and a float column in another become float).
    """
    import pyarrow as pa

    unified: pa.Schema | None = None
    for file in files:
        try:
            schema = read_schema(str(folder / file.path), file.format)
        except (OSError, pa.ArrowException) as error:
            raise DatasetError(f"Couldn't read {file.path}: {error}") from None
        if unified is None:
            unified = schema
            continue
        try:
            unified = pa.unify_schemas([unified, schema], promote_options="permissive")
        except (pa.ArrowInvalid, pa.ArrowTypeError) as error:
            raise DatasetError(
                f"{file.path} doesn't match the schema of the files before it, so nothing was "
                f"written: {error}"
            ) from None
    assert unified is not None
    return unified


def conform(batch: pa.RecordBatch, schema: pa.Schema) -> pa.RecordBatch:
    """`batch` with `schema`'s columns, in its order and types (missing ones as nulls)."""
    import pyarrow as pa

    if batch.schema.equals(schema):
        return batch
    columns = []
    for field in schema:
        index = batch.schema.get_field_index(field.name)
        column = batch.column(index) if index >= 0 else pa.nulls(batch.num_rows, field.type)
        columns.append(column if column.type == field.type else column.cast(field.type))
    return pa.RecordBatch.from_arrays(columns, schema=schema)


# ---------------------------------------------------------------------- workers

_report: Callable[[str, int, int], None] | None = None
"""Where a worker sends each batch's rows and bytes, when the parent shows them live."""


def _init_worker(queue: Any) -> None:
    global _report
    os.environ.setdefault("LANCE_LOG", "error")
    _report = (lambda split, rows, size: queue.put((split, rows, size))) if queue else None


def write_file(task: Task) -> Written:
    """
    Writes one source file as Lance fragments of the split's dataset, without committing them.

    Args:
        task (`Task`):
            The file, its split and the dataset.

    Returns:
        `Written`: the fragments' metadata, for the parent to commit, and the rows and bytes.
    """
    import pyarrow as pa
    from lance.fragment import write_fragments

    rows = size = 0

    def stream() -> Iterator[pa.RecordBatch]:
        nonlocal rows, size
        for batch in batches(task.path, task.format, task.schema):
            batch = conform(batch, task.schema)
            rows += batch.num_rows
            size += batch.nbytes
            if _report is not None:
                _report(task.split, batch.num_rows, batch.nbytes)
            yield batch

    try:
        fragments = write_fragments(
            pa.RecordBatchReader.from_batches(task.schema, stream()),
            task.uri,
            schema=task.schema,
            data_storage_version=task.storage_version,
            storage_options=task.storage_options,
        )
    except DatasetError:
        raise
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        raise DatasetError(f"Couldn't convert {task.path}: {error}") from None
    assert isinstance(fragments, list)
    return Written(task.split, task.index, fragments, rows, size)


def default_workers() -> int:
    """The CPUs this process may run on (Slurm's allocation, say), else all of them."""
    affinity = getattr(os, "sched_getaffinity", None)
    if affinity is not None:
        return len(affinity(0))
    return os.cpu_count() or 1


# ---------------------------------------------------------------------- progress


def human(size: float) -> str:
    """A byte count for people: `12.3 MB`."""
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000
    raise AssertionError


class Progress:
    """Rows and bytes per split on one redrawn line while `stream` is a terminal, else a line
    per finished file (for Slurm logs)."""

    def __init__(self, files: dict[str, int], stream: TextIO | None) -> None:
        self.stream = stream
        self.live = stream is not None and stream.isatty()
        self.files = files
        self.done = dict.fromkeys(files, 0)
        self.rows = dict.fromkeys(files, 0)
        self.bytes = dict.fromkeys(files, 0)
        self._drawn = 0.0
        self._lock = threading.Lock()

    def batch(self, split: str, rows: int, size: int) -> None:
        with self._lock:
            self.rows[split] += rows
            self.bytes[split] += size
            self._draw()

    def file(self, written: Written, path: str) -> None:
        with self._lock:
            split = written.split
            self.done[split] += 1
            if self.live:
                self._draw(force=True)
                return
            self.rows[split] += written.rows
            self.bytes[split] += written.bytes
            if self.stream is not None:
                print(
                    f"{split} {self.done[split]}/{self.files[split]}: {path} "
                    f"({written.rows:,} rows, {human(written.bytes)})",
                    file=self.stream,
                    flush=True,
                )

    def _draw(self, force: bool = False) -> None:
        now = time.monotonic()
        if not self.live or self.stream is None or (not force and now - self._drawn < 0.2):
            return
        self._drawn = now
        line = "  ".join(
            f"{split}: {self.rows[split]:,} rows, {human(self.bytes[split])}, "
            f"{self.done[split]}/{self.files[split]} files"
            for split in self.files
        )
        self.stream.write(f"\r{line}\033[K")
        self.stream.flush()

    def end(self) -> None:
        if self.live and self.stream is not None:
            self._draw(force=True)
            self.stream.write("\n")
            self.stream.flush()


# ---------------------------------------------------------------------- destinations


class Target:
    """
    Where datasets are written: `s3://flock-datasets` through the proxy, a bucket the Flock API
    reaches directly, or a local folder (which tests use).

    Args:
        root (`str`):
            The URI datasets' paths are joined to.
        filesystem (`pyarrow.fs.FileSystem`):
            Reaches `base`, for copying dataset cards.
        base (`str`):
            `root` as `filesystem` names it.
        storage_options (`dict[str, str]`, *optional*):
            Lance's options for reaching `root`. Defaults to Lance's own, from the environment.
    """

    def __init__(
        self,
        root: str,
        filesystem: pafs.FileSystem,
        base: str,
        storage_options: dict[str, str] | None = None,
    ) -> None:
        self.root = root.rstrip("/")
        self.filesystem = filesystem
        self.base = base.rstrip("/")
        self.storage_options = storage_options

    @classmethod
    def local(cls, root: str | Path) -> Target:
        import pyarrow.fs as pafs

        path = str(Path(root).resolve())
        return cls(path, pafs.LocalFileSystem(), path)

    @classmethod
    def s3(cls, creds: S3Credentials) -> Target:
        import pyarrow.fs as pafs

        current = creds.current()
        endpoint = urlsplit(current["endpoint"])
        filesystem = pafs.S3FileSystem(
            access_key=current["access_key_id"],
            secret_key=current["secret_access_key"],
            region=current["region"],
            scheme=endpoint.scheme or "https",
            endpoint_override=endpoint.netloc + endpoint.path.rstrip("/"),
            force_virtual_addressing=False,
            allow_bucket_creation=False,
            allow_bucket_deletion=False,
            check_directory_existence_before_creation=False,
        )
        return cls(f"s3://{BUCKET}", filesystem, BUCKET)

    def uri(self, path: str) -> str:
        return f"{self.root}/{path}"

    def put(self, local: Path, path: str) -> None:
        """Copies the local file to `path` under the root."""
        import pyarrow.fs as pafs

        destination = f"{self.base}/{path}"
        if isinstance(self.filesystem, pafs.LocalFileSystem):
            self.filesystem.create_dir(destination.rpartition("/")[0], recursive=True)
        pafs.copy_files(str(local), destination, destination_filesystem=self.filesystem)


def open_dataset(
    uri: str, storage_options: dict[str, str] | None = None
) -> lance.LanceDataset | None:
    """The latest version of the Lance dataset at `uri`, or `None` when there's none."""
    import lance

    try:
        return lance.dataset(uri, storage_options=storage_options)
    except ValueError as error:
        if "not found" in str(error).lower():
            return None
        raise


def check_dest(dest: str) -> str:
    """`dest` as a folder of `flock-datasets`, refusing what isn't one."""
    cleaned = dest.removeprefix(f"s3://{BUCKET}").strip("/")
    parts = cleaned.split("/")
    if not cleaned or any(part in ("", ".", "..") for part in parts):
        raise DatasetError(f"{dest!r} isn't a folder of {BUCKET}, such as web/fineweb-edu")
    return cleaned


def split_paths(manifest: Manifest, dest: str) -> dict[str, str]:
    """Where each split lands: `{dest}/{split}`, or `{dest}/{config}/{split}` when the repo
    has several configs."""
    base = f"{dest}/{manifest.config}" if len(manifest.configs) > 1 else dest
    return {split: f"{base}/{split}" for split in manifest.splits}


# ---------------------------------------------------------------------- converting


def _run_pool(
    tasks: list[Task],
    workers: int,
    progress: Progress,
    paths: dict[tuple[str, int], str],
    worker: Callable[[Task], Written],
    on_file: Callable[[Written], None] | None = None,
) -> list[Written]:
    global _report
    results: list[Written] = []
    if workers == 1:
        previous, _report = _report, (progress.batch if progress.live else None)
        try:
            for task in tasks:
                written = worker(task)
                progress.file(written, paths[task.split, task.index])
                if on_file is not None:
                    on_file(written)
                results.append(written)
        finally:
            _report = previous
        return results

    context = multiprocessing.get_context("spawn")
    queue = context.Queue() if progress.live else None
    drain = None
    if queue is not None:

        def receive() -> None:
            while (item := queue.get()) is not None:
                progress.batch(*item)

        drain = threading.Thread(target=receive, daemon=True)
        drain.start()
    pool = ProcessPoolExecutor(
        workers, mp_context=context, initializer=_init_worker, initargs=(queue,)
    )
    try:
        futures = [pool.submit(worker, task) for task in tasks]
        for future in as_completed(futures):
            written = future.result()
            progress.file(written, paths[written.split, written.index])
            if on_file is not None:
                on_file(written)
            results.append(written)
    except BrokenProcessPool:
        raise DatasetError(
            "A conversion worker stopped unexpectedly (out of memory, or killed), so nothing "
            "was committed. Try again with fewer workers."
        ) from None
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        if queue is not None and drain is not None:
            queue.put(None)
            drain.join(timeout=5)
    return results


def run(
    folder: str | Path,
    dest: str,
    target: Target,
    *,
    workers: int | None = None,
    mode: Mode = "create",
    stream: TextIO | None = None,
    worker: Callable[[Task], Written] = write_file,
    on_file: Callable[[Written], None] | None = None,
) -> list[ConvertedSplit]:
    """[`~hub.datasets.convert`] to any `target`, once credentials are set up, showing progress
    on `stream` (none by default) and handing each file written to `on_file`. An exception
    `on_file` raises stops the conversion before anything is committed."""
    import lance
    import pyarrow as pa

    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
    if workers is not None and workers < 1:
        raise ValueError("workers must be at least 1")
    folder = Path(folder).expanduser().resolve()
    manifest = Manifest.read(folder)
    dest = check_dest(dest)
    for files in manifest.splits.values():
        for file in files:
            if not (folder / file.path).is_file():
                raise DatasetError(f"{folder} is missing {file.path}. Download it again.")

    # Every check happens before any worker writes: schemas, then the destinations.
    schemas = {split: unify(folder, files) for split, files in manifest.splits.items()}
    paths = split_paths(manifest, dest)
    options = target.storage_options
    existing = {split: open_dataset(target.uri(path), options) for split, path in paths.items()}
    taken = [paths[split] for split, found in existing.items() if found is not None]
    if mode == "create" and taken:
        raise DatasetError(
            f"{BUCKET} already has a dataset at {', '.join(taken)}. Choose the mode overwrite "
            "or append to write to it."
        )
    storage: dict[str, str | None] = {}
    for split, found in existing.items():
        storage[split] = found.data_storage_version if found is not None else None
        if mode == "append" and found is not None:
            current = found.schema
            try:
                merged = pa.unify_schemas([current, schemas[split]], promote_options="permissive")
            except pa.ArrowInvalid, pa.ArrowTypeError:
                merged = None
            if merged is None or not merged.remove_metadata().equals(current.remove_metadata()):
                raise DatasetError(
                    f"The files' schema doesn't match the dataset at {paths[split]}, so nothing "
                    "was appended."
                )
            schemas[split] = current

    tasks = [
        Task(
            split,
            index,
            str(folder / file.path),
            file.format,
            target.uri(paths[split]),
            schemas[split],
            storage[split],
            options,
        )
        for split, files in manifest.splits.items()
        for index, file in enumerate(files)
    ]
    count = min(workers or default_workers(), max(len(tasks), 1))
    progress = Progress({split: len(files) for split, files in manifest.splits.items()}, stream)
    names = {
        (split, index): file.path
        for split, files in manifest.splits.items()
        for index, file in enumerate(files)
    }
    try:
        results = _run_pool(tasks, count, progress, names, worker, on_file)
    finally:
        progress.end()

    # Commit each split's fragments in the order of its files, so row i is row i upstream.
    converted = []
    for split in manifest.splits:
        done = sorted((w for w in results if w.split == split), key=lambda w: w.index)
        fragments = [fragment for w in done for fragment in w.fragments]
        found = existing[split]
        source = {
            "hub": "huggingface",
            "repo": manifest.repo,
            "revision": manifest.revision,
            "config": manifest.config,
            "split": split,
        }
        operation: lance.LanceOperation.BaseOperation
        if mode == "append" and found is not None:
            operation = lance.LanceOperation.Append(fragments)
        else:
            operation = lance.LanceOperation.Overwrite(schemas[split], fragments)
        transaction = lance.Transaction(
            found.version if found is not None else 0,
            operation,
            transaction_properties={"flock.source": json.dumps(source)},
        )
        uri = target.uri(paths[split])
        committed = lance.LanceDataset.commit(uri, transaction, storage_options=options)
        converted.append(
            ConvertedSplit(split, paths[split], uri, committed.version, sum(w.rows for w in done))
        )

    if manifest.card is not None and (folder / manifest.card).is_file():
        card = folder / manifest.card
        target.put(card, f"{dest}/README.md")
        for path in paths.values():
            target.put(card, f"{path}/README.md")
    return converted


def convert(
    folder: str | Path,
    dest: str,
    *,
    org: str | None = None,
    workers: int | None = None,
    mode: Mode = "create",
) -> list[ConvertedSplit]:
    """
    Converts a folder that [`~hub.datasets.download_from_hf`] filled to Lance datasets in your
    org's `flock-datasets` bucket, one per split. Needs Flock and no Hugging Face access, so it
    runs on a compute node without internet.

    Each split lands at `{dest}/{split}` (or `{dest}/{config}/{split}` when the repo has
    several configs), with the dataset card at `{dest}/README.md` and in each split's folder.
    Worker processes write Lance fragments straight to S3, and each split is committed at once
    when every file is written, so a split appears in full or not at all. Row `i` is row `i`
    of the source files, in order. Each version's Lance metadata says where it came from
    (`flock.source`).

    Args:
        folder (`str` or `Path`):
            The downloaded folder, with its `flock-import.json`.
        dest (`str`):
            The folder of `flock-datasets` to write to, such as `web/fineweb-edu`.
        org (`str`, *optional*):
            The org whose `flock-datasets` to write to. Defaults to your default org.
        workers (`int`, *optional*):
            Processes writing files at once. Defaults to the CPUs this process may use. `1`
            converts in this process.
        mode (`str`, *optional*, defaults to `"create"`):
            `create` refuses a destination that holds a dataset, `overwrite` replaces its rows
            with a new version, and `append` adds them. Old versions stay in Lance's history.

    Returns:
        `list[ConvertedSplit]`: each split's path, version and rows.

    Raises:
        [`~hub.datasets.DatasetError`]: when the folder has no `flock-import.json`, its files
            don't agree on a schema (naming the first that doesn't), the destination exists in
            `create` mode, or a worker fails. Nothing is committed then.

    Example:

    ```python
    from flock.hub import datasets

    written = datasets.convert("/scratch/fineweb-edu", "web/fineweb-edu", workers=64)
    ```
    """
    from flock.hub import s3

    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
    Manifest.read(Path(folder).expanduser())  # a missing manifest fails before signing in
    # Workers are spawned, so they inherit the renewable credentials this sets up.
    s3.get_process(org=org)
    target = Target.s3(s3.credentials(org))
    return run(folder, dest, target, workers=workers, mode=mode, stream=sys.stderr)
