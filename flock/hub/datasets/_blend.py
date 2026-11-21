"""Forks and blends of Lance datasets without copying their rows (design doc 023, D171 to
D178): the sampling, the column maps and the commits. The API runs these with its own S3 access
and keeps the pins and links; nothing here knows about Flock's accounts.

A blend's manifest lists the sources' fragments in place, as Lance's multi-base manifests allow,
and a deletion vector per fragment leaves out the rows not sampled. Columns are lined up by
remapping each data file's field ids. A column filled with a value, or cast to another type, is
the only thing written: one small file per fragment holding just that column.
"""

import json
import math
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

import numpy as np

if TYPE_CHECKING:
    import lance
    import pyarrow as pa

MASK = (1 << 64) - 1
BUCKETS = 1 << 16
"""The first pass of the k-smallest search counts keys by their top 16 bits."""

# Casts that keep every value (D175), from the source's type to the output's.
_INTS = ["int8", "int16", "int32", "int64"]
_UINTS = ["uint8", "uint16", "uint32", "uint64"]
SAFE_CASTS: dict[str, set[str]] = {
    **{
        t: set(_INTS[i + 1 :]) | {"double"} | ({"float"} if i < 2 else set())
        for i, t in enumerate(_INTS)
    },
    **{
        t: set(_UINTS[i + 1 :]) | set(_INTS[i + 1 :]) | {"double"} | ({"float"} if i < 2 else set())
        for i, t in enumerate(_UINTS)
    },
    "halffloat": {"float", "double"},
    "float": {"double"},
    "string": {"large_string"},
    "binary": {"large_binary"},
}
SAFE_CASTS["int64"].discard("double")  # int64 doesn't fit a double exactly
SAFE_CASTS["uint64"].discard("double")

LINEAGE = "flock.lineage"
"""The transaction property a blend's first commit records its request in."""


class BlendError(ValueError):
    """A fork or blend that can't be made as asked: the message says why, in plain words."""


# ---------------------------------------------------------------------- sampling (D174)


def fnv1a64(data: bytes) -> int:
    value = 0xCBF29CE484222325
    for byte in data:
        value = ((value ^ byte) * 0x100000001B3) & MASK
    return value


def splitmix64(x: np.ndarray) -> np.ndarray:
    """SplitMix64's finalizer on each element (uint64, wrapping), Flock's own so a blend comes
    out the same whatever NumPy's generators do in later releases."""
    with np.errstate(over="ignore"):
        z = x + np.uint64(0x9E3779B97F4A7C15)
        z = (z ^ (z >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        z = (z ^ (z >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        return z ^ (z >> np.uint64(31))


def salt(path: str, version: int) -> int:
    """What sets one source's keys apart from another's: its path and version."""
    return fnv1a64(f"{path}@{version}".encode())


def row_keys(seed: int, source_salt: int, fragment_id: int, offsets: np.ndarray) -> np.ndarray:
    """Each row's sampling key: ``splitmix64(seed ^ salt ^ (fragment_id << 32 | offset))``."""
    base = np.uint64((seed ^ source_salt ^ (fragment_id << 32)) & MASK)
    return splitmix64(offsets.astype(np.uint64) ^ base)


FRACTION_SLACK = 0.001
"""How far a blend's fractions may add up from 1: 0.1%."""


def split_counts(fractions: Sequence[float], total: int) -> list[int]:
    """Fractions of ``total`` as whole rows that add up to it: each gets ``floor(f × total)``,
    and the rows left go one each to the largest remainders, ties by the order given. The
    fractions may be off 1 by up to 0.1% (66.6% and 33.3%); they're scaled to add up to it."""
    if total < 1:
        raise BlendError("The total must be at least 1 row.")
    added = sum(fractions)
    if any(f < 0 for f in fractions) or not math.isclose(added, 1.0, abs_tol=FRACTION_SLACK + 1e-9):
        raise BlendError(f"The percentages add up to {added * 100:.4g}%, not 100%.")
    exact = [f / added * total for f in fractions]
    counts = [math.floor(e) for e in exact]
    left = total - sum(counts)
    order = sorted(range(len(exact)), key=lambda i: (-(exact[i] - counts[i]), i))
    for i in order[:left]:
        counts[i] += 1
    return counts


def live_offsets(fragment: Any) -> np.ndarray:
    """The offsets of a fragment's rows that aren't deleted, without reading any column."""
    if fragment.metadata.deletion_file is None:
        return np.arange(fragment.physical_rows, dtype=np.uint64)
    table = fragment.to_table(columns=[], with_row_address=True)
    return (table["_rowaddr"].to_numpy() & 0xFFFFFFFF).astype(np.uint64)


def smallest(
    ds: lance.LanceDataset,
    seed: int,
    source_salt: int,
    k: int,
    progress: Callable[[int], None] | None = None,
) -> dict[int, np.ndarray]:
    """The ``k`` live rows of ``ds`` with the smallest keys, as each fragment's offsets
    (sorted). Two passes, so memory stays bounded at billions of rows: a histogram of the keys'
    top 16 bits finds the bucket the k-th key falls in, then rows below it are kept and the
    bucket's own rows are sorted."""
    fragments = ds.get_fragments()
    if k <= 0:
        return {}
    histogram = np.zeros(BUCKETS, dtype=np.int64)
    for fragment in fragments:
        keys = row_keys(seed, source_salt, fragment.fragment_id, live_offsets(fragment))
        histogram += np.bincount((keys >> np.uint64(48)).astype(np.int64), minlength=BUCKETS)
        if progress is not None:
            progress(len(keys))
    cumulative = np.cumsum(histogram)
    bucket = int(np.searchsorted(cumulative, k))
    below = int(cumulative[bucket - 1]) if bucket else 0
    threshold = np.uint64(bucket << 48)
    kept: dict[int, list[np.ndarray]] = {}
    edge: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []  # keys, fragment ids, offsets
    for fragment in fragments:
        offsets = live_offsets(fragment)
        keys = row_keys(seed, source_salt, fragment.fragment_id, offsets)
        top = keys >> np.uint64(48)
        under = keys < threshold
        if under.any():
            kept.setdefault(fragment.fragment_id, []).append(offsets[under])
        at = top == np.uint64(bucket)
        if at.any():
            ids = np.full(int(at.sum()), fragment.fragment_id, dtype=np.int64)
            edge.append((keys[at], ids, offsets[at]))
    need = k - below
    if need > 0 and edge:
        keys = np.concatenate([e[0] for e in edge])
        ids = np.concatenate([e[1] for e in edge])
        offsets = np.concatenate([e[2] for e in edge])
        order = np.argsort(keys, kind="stable")[:need]
        for fid in np.unique(ids[order]):
            chosen = offsets[order][ids[order] == fid]
            kept.setdefault(int(fid), []).append(chosen)
    return {fid: np.sort(np.concatenate(parts)) for fid, parts in kept.items()}


# ---------------------------------------------------------------------- column maps (D175)


@dataclass
class SourceInfo:
    """A source as a blend sees it: its path (what the map and the keys name it by), its
    dataset at the pinned version, and how many rows to take."""

    path: str
    ds: lance.LanceDataset
    rows: int = 0

    @property
    def version(self) -> int:
        return self.ds.version

    @property
    def schema(self) -> pa.Schema:
        return self.ds.schema


def type_name(t: pa.DataType) -> str:
    return str(t)


def safe_cast(source: pa.DataType, target: pa.DataType) -> bool:
    return str(target) in SAFE_CASTS.get(str(source), set())


def resolve_type(name: str, schemas: Sequence[pa.Schema]) -> pa.DataType:
    """An output type by its name: an Arrow alias (`int64`, `string`, …), else the type of a
    source column that prints the same (for structs and lists)."""
    import pyarrow as pa

    for schema in schemas:
        for f in schema:
            if str(f.type) == name:
                return f.type
    try:
        return pa.type_for_alias(name)
    except ValueError:
        raise BlendError(f"{name} isn't a column type Flock knows.") from None


def automatic_map(sources: Sequence[SourceInfo]) -> list[dict[str, Any]]:
    """The map both the API and the UI start from (D175): the first source's columns, each
    taken from every other source by the same name and type, else by the same name with a
    safe cast, else null (and the output becomes nullable)."""
    first = sources[0].schema
    columns = []
    for f in first:
        froms: dict[str, Any] = {}
        nullable = f.nullable
        for source in sources:
            schema = source.schema
            index = schema.get_field_index(f.name)
            if index < 0:
                froms[source.path] = {"null": True}
                nullable = True
                continue
            other = schema.field(index)
            if other.type == f.type:
                froms[source.path] = {"column": f.name}
            elif safe_cast(other.type, f.type):
                froms[source.path] = {"column": f.name, "cast": True}
            else:
                froms[source.path] = {"column": f.name}  # a problem the preview names
            nullable = nullable or other.nullable
        columns.append(
            {"name": f.name, "type": type_name(f.type), "nullable": nullable, "from": froms}
        )
    return columns


def unused_columns(
    sources: Sequence[SourceInfo], columns: Sequence[Mapping[str, Any]]
) -> dict[str, list[dict[str, str]]]:
    """Each source's columns no output takes, with their types, for the UI's "Not in the
    blend"."""
    used = {
        (path, spec["column"])
        for c in columns
        for path, spec in c["from"].items()
        if "column" in spec
    }
    return {
        s.path: [
            {"name": f.name, "type": type_name(f.type)}
            for f in s.schema
            if (s.path, f.name) not in used
        ]
        for s in sources
    }


@dataclass
class Plan:
    """A checked column map: the output schema, and for each source how each output is made."""

    schema: pa.Schema
    columns: list[dict[str, Any]]
    problems: list[str] = field(default_factory=list)
    # Per source path: output name → ("column", source name) | ("cast", source name) |
    # ("copy", source name) | ("null",) | ("value", value)
    ways: dict[str, dict[str, tuple[Any, ...]]] = field(default_factory=dict)


def check_map(sources: Sequence[SourceInfo], columns: Sequence[Mapping[str, Any]]) -> Plan:
    """Checks a column map against the sources. Problems are collected, not raised, so the
    preview can show them all; ``Plan.schema`` is only meaningful when there are none."""
    import pyarrow as pa

    problems: list[str] = []
    fields: list[pa.Field] = []
    ways: dict[str, dict[str, tuple[Any, ...]]] = {s.path: {} for s in sources}
    names: set[str] = set()
    schemas = [s.schema for s in sources]
    # Each source column a data file already gives an output: a second output from it is
    # written out as a copy, since one field id can only be one column.
    read: set[tuple[str, str]] = set()
    if not columns:
        problems.append("A blend needs at least one column.")
    for column in columns:
        name = str(column.get("name", "")).strip()
        if not name:
            problems.append("Every column needs a name.")
            continue
        if name in names:
            problems.append(f"Two columns are named {name}.")
            continue
        names.add(name)
        try:
            out_type = resolve_type(str(column.get("type", "")), schemas)
        except BlendError as e:
            problems.append(f"{name}: {e}")
            continue
        nullable = bool(column.get("nullable", True))
        froms = column.get("from") or {}
        for source in sources:
            spec = froms.get(source.path)
            if spec is None:
                problems.append(f"{name}: say where {source.path} gets it from.")
                continue
            if spec.get("null"):
                if not nullable:
                    problems.append(
                        f"{name} can't be null, but {source.path} gives it null. Make it "
                        "nullable, or take a column or a value."
                    )
                ways[source.path][name] = ("null",)
            elif "value" in spec:
                if pa.types.is_nested(out_type):
                    problems.append(f"{name}: a {out_type} column can't be filled with a value.")
                    continue
                try:
                    pa.scalar(spec["value"], type=out_type)
                except pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError, OverflowError:
                    problems.append(
                        f"{name}: {spec['value']!r} for {source.path} isn't a {out_type} value."
                    )
                    continue
                ways[source.path][name] = ("value", spec["value"])
            elif "column" in spec:
                index = source.schema.get_field_index(str(spec["column"]))
                if index < 0:
                    problems.append(f"{name}: {source.path} has no column {spec['column']}.")
                    continue
                found = source.schema.field(index)
                if found.nullable and not nullable:
                    problems.append(
                        f"{name} can't be null, but {found.name} in {source.path} can hold "
                        "nulls. Make it nullable."
                    )
                if found.type == out_type and (source.path, found.name) in read:
                    ways[source.path][name] = ("copy", found.name)
                elif found.type == out_type:
                    ways[source.path][name] = ("column", found.name)
                    read.add((source.path, found.name))
                elif safe_cast(found.type, out_type):
                    ways[source.path][name] = ("cast", found.name)
                else:
                    problems.append(
                        f"{name}: {found.name} in {source.path} is {found.type}, which can't "
                        f"become {out_type} without losing values."
                    )
            else:
                problems.append(f"{name}: say where {source.path} gets it from.")
        fields.append(pa.field(name, out_type, nullable=nullable))
    if columns and not problems:
        for source in sources:
            if all(way[0] == "null" for way in ways[source.path].values()):
                problems.append(
                    f"{source.path} gives the blend only nulls. Take at least one of its "
                    "columns, or a value."
                )
    return Plan(pa.schema(fields), [dict(c) for c in columns], problems, ways)


# ---------------------------------------------------------------------- field ids


def _lance_ids(field: Any) -> list[int]:
    """A Lance field's id and its children's, depth first."""
    out = [field.id()]
    for child in field.children():
        out += _lance_ids(child)
    return out


def _arrow_ids(t: pa.DataType, start: int) -> tuple[list[int], int]:
    """The ids Lance gives a new dataset's field of type ``t`` and its children, numbering depth
    first from ``start``."""
    import pyarrow as pa

    ids = [start]
    next_id = start + 1
    children: list[pa.DataType] = []
    if pa.types.is_struct(t):
        children = [t.field(i).type for i in range(t.num_fields)]
    elif pa.types.is_list(t) or pa.types.is_large_list(t) or pa.types.is_fixed_size_list(t):
        children = [t.value_type]
    for child in children:
        got, next_id = _arrow_ids(child, next_id)
        ids += got
    return ids, next_id


def output_ids(schema: pa.Schema) -> dict[str, list[int]]:
    """Each output column's ids (itself and its children, depth first) in a new dataset."""
    out: dict[str, list[int]] = {}
    next_id = 0
    for f in schema:
        out[f.name], next_id = _arrow_ids(f.type, next_id)
    return out


# ---------------------------------------------------------------------- building


@dataclass(frozen=True)
class Listing:
    """One pass over a source's fragments: each fragment's offsets kept (``None``: every live
    row). Upsampling lists a source more than once (D174)."""

    source: int
    kept: dict[int, np.ndarray | None]


def plan_rows(
    sources: Sequence[SourceInfo],
    seed: int,
    replace: bool,
    progress: Callable[[int], None] | None = None,
) -> list[Listing]:
    """Which rows of each source the blend takes."""
    listings: list[Listing] = []
    for index, source in enumerate(sources):
        live = source.ds.count_rows()
        k = source.rows
        if k > live and not replace:
            raise BlendError(
                f"{source.path} has {live:,} rows, fewer than the {k:,} asked for. Ask for fewer, "
                "or allow taking a source more than once."
            )
        if live == 0 and k > 0:
            raise BlendError(f"{source.path} has no rows.")
        full, rest = divmod(k, live) if live else (0, 0)
        for _ in range(full):
            listings.append(
                Listing(index, {f.fragment_id: None for f in source.ds.get_fragments()})
            )
            if progress is not None:
                progress(live)
        if rest:
            chosen = smallest(source.ds, seed, salt(source.path, source.version), rest, progress)
            listings.append(Listing(index, dict(chosen)))
    return listings


def _bases(sources: Sequence[SourceInfo]) -> tuple[list[Any], dict[tuple[int, int | None], int]]:
    """The blend's base paths: every source's root and every base its manifest already names
    (a fork's original, say), each once, by path. Returns them and a map from (source, the
    source's base id or None for its root) to the blend's id."""
    from lance import DatasetBasePath

    by_path: dict[str, int] = {}
    bases: list[Any] = []
    mapping: dict[tuple[int, int | None], int] = {}

    def add(path: str) -> int:
        if path not in by_path:
            by_path[path] = len(bases) + 1
            bases.append(
                DatasetBasePath(
                    path, name=f"b{len(bases) + 1}", is_dataset_root=True, id=len(bases) + 1
                )
            )
        return by_path[path]

    for index, source in enumerate(sources):
        mapping[index, None] = add(str(source.ds.uri))
        for base_id, base in source.ds.base_paths().items():
            mapping[index, base_id] = add(str(base.path))
    return bases, mapping


def _shuffled(seed: int, count: int) -> list[int]:
    """A seeded order of ``count`` fragments, so a scan in order isn't all of A then all of B."""
    keys = splitmix64(
        np.arange(count, dtype=np.uint64) ^ np.uint64((seed ^ fnv1a64(b"order")) & MASK)
    )
    return np.argsort(keys, kind="stable").tolist()


def _full_column(fragment: Any, column: str, physical: int) -> pa.Array:
    """A fragment's column with one value per physical row, deleted rows null (they're deleted
    in the blend too), so it lines up with the fragment's offsets."""
    import pyarrow as pa

    table = fragment.to_table(columns=[column], with_row_address=True)
    offsets = table["_rowaddr"].to_numpy() & 0xFFFFFFFF
    if len(offsets) == physical:
        return table[column].combine_chunks()
    index = np.full(physical, -1, dtype=np.int64)
    index[offsets] = np.arange(len(offsets))
    mask = index < 0
    index[mask] = 0
    taker = pa.array(index, mask=mask)
    return table[column].combine_chunks().take(taker)


def build(
    sources: Sequence[SourceInfo],
    columns: Sequence[Mapping[str, Any]],
    dest: str,
    *,
    seed: int,
    replace: bool = False,
    lineage: Mapping[str, Any] | None = None,
    storage_options: dict[str, str] | None = None,
    progress: Callable[[int], None] | None = None,
    stop: Callable[[], bool] | None = None,
) -> lance.LanceDataset:
    """Makes the blend at ``dest`` (which must hold nothing): version 1 lists the sampled
    fragments with the map's field ids and fill files, version 2 deletes the rows not sampled
    and is tagged ``blend``. ``stop`` is asked between fragments, raising ``BlendError`` when
    it says so; what was written then is the caller's to remove."""
    import lance
    import pyarrow as pa
    from lance.file import LanceFileWriter
    from lance.fragment import DataFile, FragmentMetadata

    plan = check_map(sources, columns)
    if plan.problems:
        raise BlendError(" ".join(plan.problems))
    listings = plan_rows(sources, seed, replace, progress)
    if not any(len(listing.kept) for listing in listings):
        raise BlendError("A blend needs at least one row.")
    out_ids = output_ids(plan.schema)
    bases, base_ids = _bases(sources)

    # Each source's field-id map: its column's ids → the output's, for the columns it gives.
    remaps: list[dict[int, int]] = []
    fills: list[list[str]] = []  # per source: the outputs written in a fill file, in order
    for source in sources:
        remap: dict[int, int] = {}
        filled: list[str] = []
        lance_fields = {f.name(): f for f in source.ds.lance_schema.fields()}
        for out in plan.schema:
            way = plan.ways[source.path][out.name]
            if way[0] == "column":
                src_ids = _lance_ids(lance_fields[way[1]])
                dst_ids = out_ids[out.name]
                if len(src_ids) != len(dst_ids):
                    raise BlendError(
                        f"{out.name} in {source.path} doesn't line up with the output."
                    )
                remap.update(zip(src_ids, dst_ids, strict=True))
            elif way[0] in ("cast", "copy", "value"):
                filled.append(out.name)
        remaps.append(remap)
        fills.append(filled)

    root = dest.rstrip("/")
    written: dict[tuple[int, int], tuple[str, list[int]]] = {}  # (source, fragment) → fill file

    def fill_file(index: int, fragment: Any) -> tuple[str, list[int]] | None:
        """The fill file of a source fragment, written once and shared by its copies."""
        names = fills[index]
        if not names:
            return None
        key = (index, fragment.fragment_id)
        if key in written:
            return written[key]
        physical = fragment.physical_rows
        arrays, out_fields = [], []
        for name in names:
            out = plan.schema.field(name)
            way = plan.ways[sources[index].path][name]
            if way[0] == "value":
                arrays.append(pa.array([way[1]] * physical, type=out.type))
            else:
                arrays.append(_full_column(fragment, way[1], physical).cast(out.type))
            out_fields.append(pa.field(name, out.type, nullable=True))
        table = pa.Table.from_arrays(arrays, schema=pa.schema(out_fields))
        name = f"flock-fill-{uuid.uuid4().hex}.lance"
        with LanceFileWriter(
            f"{root}/data/{name}", table.schema, storage_options=storage_options
        ) as writer:
            writer.write_batch(table)
        written[key] = (name, [out_ids[n][0] for n in names])
        return written[key]

    # Each fragment of the blend: its metadata, the offsets kept (None: every live row), and
    # the source and source fragment it lists.
    pieces: list[tuple[FragmentMetadata, np.ndarray | None, int, int]] = []
    for listing in listings:
        index = listing.source
        source = sources[index]
        for fid, kept in sorted(listing.kept.items()):
            if stop is not None and stop():
                raise BlendError("The blend was cancelled.")
            if kept is not None and len(kept) == 0:
                continue
            fragment = source.ds.get_fragment(fid)
            assert fragment is not None
            meta = fragment.metadata
            files = []
            for df in meta.files:
                mapped = [remaps[index].get(x, -2) for x in df.fields]
                if all(x == -2 for x in mapped):
                    continue  # no column of this file is in the blend
                files.append(
                    DataFile(
                        df.path,
                        mapped,
                        df.column_indices,
                        df.file_major_version,
                        df.file_minor_version,
                        df.file_size_bytes,
                        base_id=base_ids[index, df.base_id],
                    )
                )
            extra = fill_file(index, fragment)
            if extra is not None:
                major = meta.files[0].file_major_version if meta.files else 2
                minor = meta.files[0].file_minor_version if meta.files else 0
                files.append(
                    DataFile(extra[0], extra[1], list(range(len(extra[1]))), major, minor, None)
                )
            if not files:
                raise BlendError(f"{source.path} gives none of the blend's columns from its files.")
            pieces.append(
                (
                    FragmentMetadata(id=0, files=files, physical_rows=meta.physical_rows),
                    kept,
                    index,
                    fid,
                )
            )

    order = _shuffled(seed, len(pieces))
    fragments = []
    for new_id, at in enumerate(order):
        meta = pieces[at][0]
        fragments.append(
            FragmentMetadata(id=new_id, files=meta.files, physical_rows=meta.physical_rows)
        )
    properties = {LINEAGE: json.dumps(dict(lineage or {}), sort_keys=True)}
    first = lance.LanceDataset.commit(
        root,
        lance.Transaction(
            0,
            lance.LanceOperation.Overwrite(plan.schema, fragments, initial_bases=bases),
            transaction_properties=properties,
        ),
        storage_options=storage_options,
    )
    got = {f.name(): _lance_ids(f) for f in first.lance_schema.fields()}
    if got != out_ids:
        raise BlendError("Lance numbered the blend's columns differently than expected.")

    # Version 2: every row not sampled, and every row the source had deleted, is deleted.
    # Lance refuses deletion files in an Overwrite, even one creating the dataset, and a
    # detached commit can't create one, so the deletions are a version of their own.
    updated, emptied = [], []
    for new_id, at in enumerate(order):
        _, kept, index, fid = pieces[at]
        fragment = first.get_fragment(new_id)
        assert fragment is not None
        keep = live_offsets(sources[index].ds.get_fragment(fid)) if kept is None else kept
        dead = np.setdiff1d(np.arange(fragment.physical_rows, dtype=np.uint64), keep)
        if len(dead):
            meta = fragment.delete_rows(dead.tolist())
            if meta is None:
                emptied.append(new_id)  # every row of it deleted
            else:
                updated.append(meta)
    blend = first
    if updated or emptied:
        blend = lance.LanceDataset.commit(
            root,
            lance.LanceOperation.Delete(updated, emptied, "flock blend"),
            read_version=first.version,
            storage_options=storage_options,
        )
    blend.tags.create("blend", blend.version)
    return blend


# ---------------------------------------------------------------------- forks and independence


def fork(
    source: lance.LanceDataset, version: int, dest: str, storage_options: dict[str, str] | None
) -> lance.LanceDataset:
    """A shallow clone of ``source`` at ``version`` (D171). Lance's Clone transaction records
    where it came from."""
    return source.shallow_clone(dest, version, storage_options=storage_options)


def make_independent(
    uri: str,
    storage_options: dict[str, str] | None,
    progress: Callable[[int], None] | None = None,
) -> lance.LanceDataset:
    """Rewrites the dataset's live rows into its own files as a new version, then removes its
    earlier versions and their tags, so nothing in it reads another dataset (D178)."""
    import lance
    import pyarrow as pa

    ds = lance.dataset(uri, storage_options=storage_options)

    def batches() -> Iterator[pa.RecordBatch]:
        for batch in ds.to_batches(batch_size=8192):
            if progress is not None:
                progress(batch.num_rows)
            yield batch

    reader = pa.RecordBatchReader.from_batches(ds.schema, batches())
    lance.write_dataset(
        reader,
        uri,
        schema=ds.schema,
        mode="overwrite",
        storage_options=storage_options,
        data_storage_version=cast(Any, ds.data_storage_version),
        transaction_properties={"flock.independent": "true"},
    )
    now = lance.dataset(uri, storage_options=storage_options)
    for tag in list(now.tags.list()):
        now.tags.delete(tag)
    now.cleanup_old_versions(older_than=timedelta(0), delete_unverified=True)
    return lance.dataset(uri, storage_options=storage_options)


def references(ds: lance.LanceDataset) -> bool:
    """Whether the dataset's latest version reads files of another dataset."""
    return any(df.base_id is not None for f in ds.get_fragments() for df in f.metadata.files)
