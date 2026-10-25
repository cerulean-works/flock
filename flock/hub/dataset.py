"""
`Dataset`: one pinned version of a hub dataset, read through HF `datasets`.

Reading mirrors HF's packaged Lance builder (`datasets/packaged_modules/lance/lance.py`) with a
builder of our own: one HF shard per Lance fragment, Arrow batches from
`fragment.to_batches(..., blob_handling="all_binary")`, and features from the Arrow schema with
the same magic-byte sniffing for binary columns.
`tests/hub/test_drift.py` checks that the builder still works this way.
"""

import hashlib
import json
import shutil
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import datasets
import datasets.config
import lance
import pyarrow as pa
from datasets.builder import Key
from datasets.distributed import split_dataset_by_node as _hf_split_dataset_by_node
from datasets.formatting import PythonFormatter
from datasets.table import InMemoryTable, table_cast

from flock.hub.config import HubConfig

if TYPE_CHECKING:
    from lance.dataset import LanceDataset

DEFAULT_BATCH_SIZE = 256
"""Rows per Arrow batch read from Lance, HF's Lance builder's default."""

CARD_KEY = "flock.card"
"""The table metadata key holding the sha256 of the version's card."""

# Bumped when the rows a cached non-streaming dataset holds would change for the same version.
_CACHE_FORMAT = 1

# HF's Lance builder's `MAGIC_BYTES_EXTENSION_AND_FEATURE_TYPES`, as (magic bytes, feature):
# a binary column whose first row starts with these bytes is decoded as that feature.
_MAGIC_BYTES: list[tuple[str, Callable[[], Any]]] = [
    ("1A 45 DF A3", datasets.Video),
    ("66 74 79 70 69 73 6F 6D", datasets.Video),
    ("66 74 79 70 4D 53 4E 56", datasets.Video),
    ("52 49 46 46", datasets.Video),
    ("00 00 01 BA", datasets.Video),
    ("00 00 01 BA", datasets.Video),
    ("00 00 01 B3", datasets.Video),
    ("89 50 4E 47", datasets.Image),
    ("FF D8", datasets.Image),
    ("49 49", datasets.Image),
    ("47 49 46 38", datasets.Image),
    ("52 49 46 46", datasets.Audio),
    ("49 44 33", datasets.Audio),
    ("66 4C 61 43", datasets.Audio),
]


@dataclass(frozen=True)
class Pin:
    """
    Where one version of a dataset lives. Plain data, so it pickles into DataLoader workers.

    Args:
        uri (`str`):
            The Lance dataset's URI.
        version (`int`):
            The pinned version.
        hub (`HubConfig`):
            Gives the storage options, refreshed as credentials expire.
        dataset_id (`str`, *optional*):
            The dataset's uuid on the server, if known. Keys the cache instead of `uri`.
    """

    uri: str
    version: int
    hub: HubConfig
    dataset_id: str | None = None

    def open(self) -> LanceDataset:
        """The pinned Lance dataset, opened with storage options that are valid for a while."""
        options = self.hub.lance_storage_options()
        key = self._key(options)
        handle = _handles.get(key)
        if handle is None:
            handle = lance.dataset(self.uri, version=self.version, storage_options=options)
        _remember(key, handle)
        return handle

    def remember(self, handle: LanceDataset) -> None:
        """Makes `handle`, just opened at the pinned version, the one [`~Pin.open`] returns."""
        # A load always opens the dataset afresh; caching that handle keeps a dataset recreated
        # at the same URI (direct mode) from being read through an old one.
        _remember(self._key(self.hub.lance_storage_options()), handle)

    def open_latest(self) -> LanceDataset:
        """The latest version of the dataset, opened fresh (for edits)."""
        return lance.dataset(self.uri, storage_options=self.hub.lance_storage_options())

    def _key(self, options: dict[str, str]) -> tuple:
        return (self.uri, self.dataset_id, self.version, tuple(sorted(options.items())))


# Open datasets by (uri, dataset id, version, storage options). A new set of credentials opens
# the dataset again, so a long stream picks up refreshed credentials at the next fragment.
_handles: OrderedDict[tuple, LanceDataset] = OrderedDict()
_MAX_HANDLES = 32


def _remember(key: tuple, handle: LanceDataset) -> None:
    _handles[key] = handle
    _handles.move_to_end(key)
    while len(_handles) > _MAX_HANDLES:
        _handles.popitem(last=False)


def readable_schema(schema: pa.Schema) -> pa.Schema:
    """The schema of rows read with `blob_handling="all_binary"`: blob columns become `large_binary`."""
    # HF's builder passes Lance's schema straight to `Features.from_arrow_schema`, which fails on
    # the `lance.blob.v2` extension type; the rows themselves hold plain bytes.
    fields = []
    for f in schema:
        if isinstance(f.type, pa.ExtensionType) and f.type.extension_name.startswith("lance.blob"):
            f = pa.field(f.name, pa.large_binary(), f.nullable, f.metadata)
        fields.append(f)
    return pa.schema(fields, metadata=schema.metadata)


def infer_features(lance_ds: LanceDataset, columns: list[str] | None) -> datasets.Features:
    """
    The HF features of a Lance dataset's rows, inferred as HF's Lance builder does.

    The Arrow schema, restricted to `columns` in their order, goes through `Features.from_arrow_schema`. Then each binary column whose first row starts with known magic bytes becomes `Image`, `Audio` or `Video`.

    Raises:
        `ValueError`: if a name in `columns` isn't a column.
    """
    schema = readable_schema(lance_ds.schema)
    if columns is not None:
        unknown = [name for name in columns if schema.get_field_index(name) == -1]
        if unknown:
            raise ValueError(f"unknown columns {unknown}; the dataset has {schema.names}")
        schema = pa.schema([schema.field(name) for name in columns], metadata=schema.metadata)

    first_bytes = {}
    has_rows = lance_ds.count_rows() > 0
    for field in schema:
        if has_rows and (pa.types.is_binary(field.type) or pa.types.is_large_binary(field.type)):
            value = _first_bytes(lance_ds, field.name)
            if value is not None:
                first_bytes[field.name] = value

    features = datasets.Features.from_arrow_schema(schema)
    for name, value in first_bytes.items():
        for magic_hex, feature in _MAGIC_BYTES:
            magic = bytes.fromhex(magic_hex)
            # As HF: the magic bytes may sit a little way in.
            if magic in value[: len(magic) * 2]:
                features[name] = feature()
                break
    return features


def _first_bytes(lance_ds: LanceDataset, column: str) -> bytes | None:
    """The first 16 bytes of `column` in the dataset's first row, or `None` if it is null."""
    try:
        blob = lance_ds.take_blobs(column, indices=[0])[0]
        return blob.read(16) if blob is not None else None
    except ValueError:  # not a blob column
        value = lance_ds.take([0], columns=[column]).column(0)[0].as_py()
        return value[:16] if value is not None else None


@dataclass(frozen=True)
class _ReadSpec:
    """What a `Dataset` reads from its pinned version, before any HF transform."""

    repo_id: str
    pin: Pin
    columns: list[str] | None
    filter: str | None
    batch_size: int
    cache_dir: str | None


@dataclass
class LanceVersionConfig(datasets.BuilderConfig):
    """The config of a [`LanceVersionBuilder`]: what to read, and which fragments."""

    spec: _ReadSpec | None = None
    fragment_ids: list[int] | None = None
    features: datasets.Features | None = None


class LanceVersionBuilder(datasets.ArrowBasedBuilder):
    """
    HF's Lance builder, narrowed to one pinned version: one shard per fragment, Arrow batches from `fragment.to_batches`, cast to the features as HF's `_cast_table` does.

    Unlike HF's builder, it opens a given version, gets Lance's `storage_options` from the hub config (renewed between fragments), and pushes `filter` down to Lance.
    """

    BUILDER_CONFIG_CLASS = LanceVersionConfig
    config: LanceVersionConfig

    def _info(self) -> datasets.DatasetInfo:
        return datasets.DatasetInfo(features=self.config.features)

    def _split_generators(self, dl_manager: Any) -> list[datasets.SplitGenerator]:
        # A list in `gen_kwargs` is what HF splits into shards: one per fragment, as in HF's.
        gen_kwargs = {"fragment_ids": self.config.fragment_ids}
        return [datasets.SplitGenerator(name="train", gen_kwargs=gen_kwargs)]

    def _generate_tables(self, **kwargs: Any) -> Iterator[tuple[Key, pa.Table]]:
        fragment_ids: list[int] = kwargs["fragment_ids"]
        spec = self.config.spec
        features = self.info.features
        assert spec is not None and features is not None
        for fragment_index, fragment_id in enumerate(fragment_ids):
            # Opened per fragment so that refreshed credentials apply; the handle is cached.
            fragment = spec.pin.open().get_fragment(fragment_id)
            assert fragment is not None, f"no fragment {fragment_id} in version {spec.pin.version}"
            batches = fragment.to_batches(
                columns=spec.columns,
                batch_size=spec.batch_size,
                filter=spec.filter,
                blob_handling="all_binary",
            )
            for batch_index, batch in enumerate(batches):
                table = table_cast(pa.Table.from_batches([batch]), features.arrow_schema)
                yield Key(fragment_index, batch_index), table


def _builder(
    spec: _ReadSpec, fragment_ids: list[int], features: datasets.Features, config_id: str
) -> LanceVersionBuilder:
    # An explicit config id: HF would otherwise hash the config, hub config included.
    return LanceVersionBuilder(
        cache_dir=spec.cache_dir,
        dataset_name="flock_hub",
        config_id=config_id,
        features=features,
        spec=spec,
        fragment_ids=fragment_ids,
    )


def _streaming_hf(
    spec: _ReadSpec, fragment_ids: list[int], features: datasets.Features
) -> datasets.IterableDataset:
    builder = _builder(spec, fragment_ids, features, "streaming")
    hf = builder.as_streaming_dataset(split="train")
    assert isinstance(hf, datasets.IterableDataset)
    return hf


def cache_fingerprint(spec: _ReadSpec, lance_ds: LanceDataset) -> str:
    """The HF cache key of a non-streaming dataset: the dataset, version, columns and filter."""
    # Versions never change, so this never goes stale. The data file names (uuids) also tell a
    # local dataset apart from an earlier one at the same path.
    files = [[df.path for df in fragment.metadata.files] for fragment in lance_ds.get_fragments()]
    key = {
        "format": _CACHE_FORMAT,
        "dataset": spec.pin.dataset_id or spec.pin.uri,
        "version": spec.pin.version,
        "columns": spec.columns,
        "filter": spec.filter,
        "files": files,
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:32]


def _materialized_hf(
    spec: _ReadSpec,
    fragment_ids: list[int],
    features: datasets.Features,
    fingerprint: str,
    expected_rows: int | None,
) -> datasets.Dataset:
    """Copies the version into HF's cache, or reads the copy made earlier."""
    if not fragment_ids:
        # HF can't prepare a split without data; there is nothing to cache anyway.
        return _empty(features)
    builder = _builder(spec, fragment_ids, features, f"v-{fingerprint}")
    existed = Path(builder.cache_dir).exists()
    try:
        return _prepare(builder, expected_rows)
    except Exception:
        if not existed:
            raise
    # A cached copy that fails to load or doesn't match (a deleted or damaged file) is thrown
    # away and built again.
    shutil.rmtree(builder.cache_dir, ignore_errors=True)
    builder = _builder(spec, fragment_ids, features, f"v-{fingerprint}")
    return _prepare(builder, expected_rows)


def _empty(features: datasets.Features) -> datasets.Dataset:
    table = InMemoryTable(features.arrow_schema.empty_table())
    return datasets.Dataset(table, info=datasets.DatasetInfo(features=features))


def _prepare(builder: LanceVersionBuilder, expected_rows: int | None) -> datasets.Dataset:
    builder.download_and_prepare()
    try:
        hf = builder.as_dataset(split="train")
    except ValueError as e:
        # HF writes no file for a split without rows, here a filter that matches nothing.
        if "corresponds to no data" not in str(e):
            raise
        assert builder.info.features is not None
        hf = _empty(builder.info.features)
    assert isinstance(hf, datasets.Dataset)
    if expected_rows is not None and hf.num_rows != expected_rows:
        raise ValueError(f"the cached copy has {hf.num_rows} rows, not {expected_rows}")
    return hf


# Operations that keep every row, so a streaming dataset's row count carries over.
_ROW_PRESERVING = {
    "rename_column",
    "rename_columns",
    "select_columns",
    "remove_columns",
    "cast_column",
    "shuffle",
    "with_format",
}


class Dataset:
    """
    One pinned version of a hub dataset, wrapping an HF `IterableDataset` (streaming) or `Dataset`.

    Get one from [`load_dataset`]. HF operations (`map`, `filter`, `shuffle`, `batch`, `select_columns`, `rename_column`, `skip`, `take`, ...) are delegated, and their result is wrapped again, so they always return a `flock.hub.Dataset` with the same `repo_id` and `version`. [`~Dataset.to_hf`] gives the HF object.

    Random access (`ds[i]`, `ds[a:b]`, `ds[[i, j]]`, [`~Dataset.take_rows`]) reads the rows from Lance in both modes, in iteration order. Once an HF transform has been applied, a streaming dataset no longer supports it, and a non-streaming one delegates it to HF.

    Every read goes through the pinned version, so commits made after loading are never seen.
    """

    def __init__(
        self,
        hf: datasets.Dataset | datasets.IterableDataset,
        spec: _ReadSpec,
        *,
        num_rows: int | None,
        transformed: bool = False,
    ) -> None:
        self._hf = hf
        self._spec = spec
        self._num_rows = num_rows
        self._transformed = transformed
        self._matches: list[int] | None = None

    @staticmethod
    def _create(
        hf: datasets.Dataset | datasets.IterableDataset,
        spec: _ReadSpec,
        *,
        num_rows: int | None,
        transformed: bool = False,
    ) -> Dataset:
        # Streaming datasets are also torch `IterableDataset`s (as HF's are), so a DataLoader
        # iterates them and HF splits the shards between its workers.
        cls = _StreamingDataset if isinstance(hf, datasets.IterableDataset) else Dataset
        return cls(hf, spec, num_rows=num_rows, transformed=transformed)

    @classmethod
    def _load(
        cls,
        spec: _ReadSpec,
        streaming: bool,
    ) -> Dataset:
        """Builds the HF dataset over `spec`'s pinned version."""
        lance_ds = spec.pin.open()
        features = infer_features(lance_ds, spec.columns)
        fragment_ids = [fragment.fragment_id for fragment in lance_ds.get_fragments()]
        if streaming:
            num_rows = lance_ds.count_rows(spec.filter)
            return cls._create(_streaming_hf(spec, fragment_ids, features), spec, num_rows=num_rows)
        # Unfiltered, the row count is in the manifest, so it checks the cache for free.
        expected = lance_ds.count_rows() if spec.filter is None else None
        fingerprint = cache_fingerprint(spec, lance_ds)
        hf = _materialized_hf(spec, fragment_ids, features, fingerprint, expected)
        return cls._create(hf, spec, num_rows=hf.num_rows)

    # ---- identity ----

    @property
    def repo_id(self) -> str:
        """`org/name`."""
        return self._spec.repo_id

    @property
    def version(self) -> int:
        """The pinned Lance version every read goes through."""
        return self._spec.pin.version

    @property
    def uri(self) -> str:
        """The Lance dataset's URI."""
        return self._spec.pin.uri

    @property
    def streaming(self) -> bool:
        """Whether this wraps an HF `IterableDataset`."""
        return isinstance(self._hf, datasets.IterableDataset)

    @property
    def columns(self) -> list[str] | None:
        """The columns read from Lance, or `None` for all of them."""
        return self._spec.columns

    @property
    def lance_filter(self) -> str | None:
        """The SQL filter `load_dataset` pushed down to Lance, if any."""
        return self._spec.filter

    @property
    def features(self) -> datasets.Features | None:
        """The HF features of the rows."""
        return self._hf.features

    @property
    def column_names(self) -> list[str] | None:
        """The names of the columns."""
        return self._hf.column_names

    @property
    def num_rows(self) -> int | None:
        """
        How many rows the dataset yields.

        Known in both modes when loaded: from the manifest, or by counting the filter's matches. After a streaming transform that can drop rows (`filter`, `batch`, a batched `map`), it is `None`.
        """
        if isinstance(self._hf, datasets.Dataset):
            return self._hf.num_rows
        return self._num_rows

    def __len__(self) -> int:
        if self.num_rows is None:
            raise TypeError("this streaming dataset's length isn't known after the last transform")
        return self.num_rows

    def __repr__(self) -> str:
        return (
            f"Dataset(repo_id={self.repo_id!r}, version={self.version}, "
            f"streaming={self.streaming}, num_rows={self.num_rows}, "
            f"features={list(self.features) if self.features else None})"
        )

    def to_hf(self) -> datasets.Dataset | datasets.IterableDataset:
        """The wrapped HF `IterableDataset` (streaming) or `Dataset`."""
        return self._hf

    # ---- iteration ----

    def __iter__(self) -> Iterator[Any]:
        return iter(self._hf)

    def iter(self, batch_size: int, drop_last_batch: bool = False) -> Iterator[dict[str, list]]:
        """Iterates in batches of `batch_size` rows, as HF's `iter`."""
        return self._hf.iter(batch_size=batch_size, drop_last_batch=drop_last_batch)

    @property
    def num_shards(self) -> int:
        """The number of shards: one per Lance fragment until a transform changes it."""
        if isinstance(self._hf, datasets.IterableDataset):
            return self._hf.num_shards
        return 1

    def state_dict(self) -> dict:
        """The position of a streaming iteration, for [`~Dataset.load_state_dict`]. HF's `state_dict`."""
        return self._iterable("state_dict").state_dict()

    def load_state_dict(self, state_dict: dict) -> None:
        """Resumes a streaming iteration where [`~Dataset.state_dict`] was taken."""
        self._iterable("load_state_dict").load_state_dict(state_dict)

    def set_epoch(self, epoch: int) -> None:
        """Sets the epoch, which reseeds a streaming `shuffle`."""
        self._iterable("set_epoch").set_epoch(epoch)

    def _iterable(self, name: str) -> datasets.IterableDataset:
        if not isinstance(self._hf, datasets.IterableDataset):
            raise TypeError(f"{name} only exists on streaming datasets")
        return self._hf

    # ---- HF operations, wrapped again ----

    def _wrap(self, hf: Any, num_rows: int | None) -> Dataset:
        return Dataset._create(hf, self._spec, num_rows=num_rows, transformed=True)

    def _delegate(self, name: str, *args: Any, **kwargs: Any) -> Dataset:
        result = getattr(self._hf, name)(*args, **kwargs)
        return self._wrap(result, self._num_rows if name in _ROW_PRESERVING else None)

    def map(self, function: Callable | None = None, **kwargs: Any) -> Dataset:
        """HF's `map`. A streaming dataset keeps its row count unless `batched=True`."""
        result = self._hf.map(function, **kwargs)
        return self._wrap(result, None if kwargs.get("batched") else self._num_rows)

    def filter(self, function: Callable | None = None, **kwargs: Any) -> Dataset:
        """HF's `filter`, in Python. Pass `filter=` to [`load_dataset`] to push a filter down to Lance."""
        return self._delegate("filter", function, **kwargs)

    def shuffle(self, seed: int | None = None, **kwargs: Any) -> Dataset:
        """HF's `shuffle`. Streaming, it shuffles the shard order and a buffer of `buffer_size` rows."""
        return self._delegate("shuffle", seed=seed, **kwargs)

    def batch(self, batch_size: int, **kwargs: Any) -> Dataset:
        """HF's `batch`: rows grouped into batches of `batch_size`."""
        return self._delegate("batch", batch_size, **kwargs)

    def select_columns(self, column_names: str | list[str]) -> Dataset:
        """HF's `select_columns`."""
        return self._delegate("select_columns", column_names)

    def remove_columns(self, column_names: str | list[str]) -> Dataset:
        """HF's `remove_columns`."""
        return self._delegate("remove_columns", column_names)

    def rename_column(self, original_column_name: str, new_column_name: str) -> Dataset:
        """HF's `rename_column`."""
        return self._delegate("rename_column", original_column_name, new_column_name)

    def rename_columns(self, column_mapping: dict[str, str]) -> Dataset:
        """HF's `rename_columns`."""
        return self._delegate("rename_columns", column_mapping)

    def cast_column(self, column: str, feature: Any) -> Dataset:
        """HF's `cast_column`."""
        return self._delegate("cast_column", column, feature)

    def with_format(self, type: str | None = None, **kwargs: Any) -> Dataset:
        """HF's `with_format`, for example `"torch"` or `"arrow"`."""
        return self._delegate("with_format", type, **kwargs)

    def skip(self, n: int) -> Dataset:
        """HF's `skip`: all rows but the first `n`."""
        rows = None if self._num_rows is None else max(self._num_rows - n, 0)
        return self._wrap(self._hf.skip(n), rows)

    def take(self, n: int) -> Dataset:
        """HF's `take`: the first `n` rows. For rows by index, see [`~Dataset.take_rows`]."""
        rows = None if self._num_rows is None else min(self._num_rows, n)
        return self._wrap(self._hf.take(n), rows)

    def shard(self, num_shards: int, index: int, **kwargs: Any) -> Dataset:
        """HF's `shard`: the `index`-th of `num_shards` parts."""
        return self._delegate("shard", num_shards=num_shards, index=index, **kwargs)

    # ---- random access ----

    def __getitem__(self, key: int | slice | Iterable[int] | str) -> Any:
        """
        Rows by position, read from Lance: `ds[i]` is a row, `ds[a:b]` and `ds[[i, j]]` are a batch (a dict of lists), as in HF.

        Raises:
            `IndexError`: if a position is out of range.
            `TypeError`: on a streaming dataset after a transform, or for a column name while streaming.
        """
        if self._transformed or isinstance(key, str):
            if isinstance(self._hf, datasets.IterableDataset):
                raise TypeError(
                    "random access on a streaming dataset only works before any transform; "
                    "index the loaded dataset, or load it with streaming=False"
                )
            return self._hf[key]
        if isinstance(key, int):
            return self._read([key], batch=False)
        if isinstance(key, slice):
            return self._read(range(*key.indices(self._loaded_rows())), batch=True)
        return self._read(key, batch=True)

    def __getitems__(self, keys: list[int]) -> list[dict[str, Any]]:
        """Several rows at once, as a list of rows. A DataLoader over a non-streaming dataset calls this."""
        if self._transformed:
            return [self[key] for key in keys]
        batch = self._read(keys, batch=True)
        return [{name: values[i] for name, values in batch.items()} for i in range(len(keys))]

    def take_rows(self, indices: Iterable[int]) -> dict[str, list]:
        """Rows at `indices`, in that order and with repeats, as a batch: the same as `ds[indices]`."""
        return self[list(indices)]

    def _loaded_rows(self) -> int:
        """The row count of the pinned version, after the load filter."""
        assert self._num_rows is not None
        return self._num_rows

    def _read(self, indices: Iterable[int], batch: bool) -> Any:
        count = self._loaded_rows()
        positions = []
        for i in indices:
            if not isinstance(i, int):
                raise TypeError(f"row indices must be ints, got {type(i).__name__}")
            if not -count <= i < count:
                raise IndexError(f"index {i} is out of range for {count} rows")
            positions.append(i % count)

        table = self._take(positions)
        features = self._hf.features
        assert features is not None
        table = table_cast(table, features.arrow_schema)
        formatter = PythonFormatter(features=features)
        return formatter.format_batch(table) if batch else formatter.format_row(table)

    def _take(self, positions: list[int]) -> pa.Table:
        """Rows at `positions` of the pinned version (after the filter), with blobs read as bytes."""
        lance_ds = self._spec.pin.open()
        schema = lance_ds.schema
        names = self._spec.columns or schema.names
        blob_names = {
            f.name
            for f in readable_schema(schema)
            if f.name in names and f.type != schema.field(f.name).type
        }
        plain = [name for name in names if name not in blob_names]

        if self._spec.filter is None:
            table = lance_ds.take(positions, columns=plain)
            blobs = {name: lance_ds.take_blobs(name, indices=positions) for name in blob_names}
        else:
            # `_take_rows` takes row ids: stable ids here, or addresses in a dataset without them.
            row_ids = self._row_ids()
            picked = [row_ids[p] for p in positions]
            table = lance_ds._take_rows(picked, columns=plain)
            blobs = {name: lance_ds.take_blobs(name, ids=picked) for name in blob_names}

        arrays = {name: table.column(name) for name in plain}
        for name, files in blobs.items():
            data = [f.read() if f is not None else None for f in files]
            arrays[name] = pa.chunked_array([pa.array(data, pa.large_binary())])
        return pa.table({name: arrays[name] for name in names})

    def _row_ids(self) -> list[int]:
        """The row ids of the filter's matches, in iteration order, found once."""
        if self._matches is None:
            scan = self._spec.pin.open().scanner(
                columns=[], filter=self._spec.filter, with_row_id=True, scan_in_order=True
            )
            self._matches = scan.to_table().column("_rowid").to_pylist()
        return self._matches

    # ---- versions, tags and the card ----

    def checkout(self, version: int | str | None = None) -> Dataset:
        """The same dataset (columns, filter, streaming) at another version or tag, or the latest."""
        # Imported here: `load` builds `Dataset`s.
        from flock.hub.load import load_dataset

        return load_dataset(
            self.repo_id,
            version,
            columns=self.columns,
            filter=self.lance_filter,
            streaming=self.streaming,
            batch_size=self._spec.batch_size,
            cache_dir=self._spec.cache_dir,
            hub_config=self._spec.pin.hub,
        )

    @property
    def latest_version(self) -> int:
        """The dataset's latest version now, which may be newer than the pinned one."""
        return self._spec.pin.open_latest().version

    @property
    def tags(self) -> dict[str, int]:
        """Every tag of the dataset and the version it points to."""
        listed = self._spec.pin.open().tags.list()
        return {name: info["version"] for name, info in listed.items()}

    @property
    def card(self) -> str | None:
        """The README of the pinned version, or `None` if it has none."""
        from flock.hub.write import read_card

        return read_card(self._spec.pin)

    # ---- edits (in write.py) ----

    def delete(self, where: str, message: str | None = None) -> Dataset:
        """
        Deletes the rows matching the SQL predicate `where` from the latest version.

        Returns:
            [`Dataset`]: the dataset at the version the delete made.
        """
        from flock.hub.write import delete_rows

        return delete_rows(self, where, message)

    def update(
        self, values: dict[str, str], where: str | None = None, message: str | None = None
    ) -> Dataset:
        """
        Sets columns to SQL expressions (`{"label": "label + 1"}`) in the rows matching `where`, or in every row, at the latest version.

        Returns:
            [`Dataset`]: the dataset at the version the update made.
        """
        from flock.hub.write import update_rows

        return update_rows(self, values, where, message)

    def add_columns(self, transforms: Any, message: str | None = None) -> Dataset:
        """
        Adds columns at the latest version, with any `transforms` Lance's `add_columns` takes: SQL expressions by name, Arrow fields (all null), or a batch UDF.

        Returns:
            [`Dataset`]: the dataset at the version that added them.
        """
        from flock.hub.write import add_columns

        return add_columns(self, transforms, message)

    def restore(self, message: str | None = None) -> Dataset:
        """
        Makes the pinned version the latest again, as a new version.

        Returns:
            [`Dataset`]: the dataset at the new version.
        """
        from flock.hub.write import restore

        return restore(self, message)

    def set_card(self, text: str, message: str | None = None) -> Dataset:
        """
        Sets the README, as a new version that points to it.

        Returns:
            [`Dataset`]: the dataset at the new version.
        """
        from flock.hub.write import set_card

        return set_card(self, text, message)

    def tag(self, name: str, version: int | None = None) -> Dataset:
        """Tags `version` (default: the pinned one) as `name`. Tags make no version, so this returns `self`."""
        self._spec.pin.open_latest().tags.create(name, self.version if version is None else version)
        return self

    def untag(self, name: str) -> Dataset:
        """Deletes the tag `name`, and returns `self`."""
        self._spec.pin.open_latest().tags.delete(name)
        return self


class _StreamingDataset(Dataset):
    """A streaming [`Dataset`]; also a torch `IterableDataset` when torch is installed."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        _add_torch_parent(type(self))

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        # Dynamically added bases don't survive pickling into a fresh worker process.
        _add_torch_parent(type(self))


def _add_torch_parent(cls: type) -> None:
    """Makes `cls` a torch `IterableDataset`, as HF does for its own, if torch is installed."""
    if datasets.config.TORCH_AVAILABLE:
        import torch.utils.data

        if torch.utils.data.IterableDataset not in cls.__bases__:
            cls.__bases__ += (torch.utils.data.IterableDataset,)


def split_dataset_by_node(
    dataset: Dataset,
    rank: int,
    world_size: int,
    strategy: Literal["auto", "shards", "examples"] = "auto",
) -> Dataset:
    """
    HF's `split_dataset_by_node` for a `flock.hub.Dataset`: the part for node `rank` of `world_size`.

    Streaming, `"auto"` gives each node whole fragments when `world_size` divides their number, and otherwise every `world_size`-th row.
    """
    hf = dataset.to_hf()
    if isinstance(hf, datasets.Dataset):
        return dataset._wrap(_hf_split_dataset_by_node(hf, rank, world_size), None)
    return dataset._wrap(_hf_split_dataset_by_node(hf, rank, world_size, strategy), None)
