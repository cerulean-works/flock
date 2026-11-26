"""Run groups' analysis (026): column kinds (D218), a group's best (D217), the keys that differ
between configurations (D221), default names (D225) and suggested groups (D219).

Pure functions over ``RunRow``s; the caller loads runs from ``run_values`` and stores nothing
computed here. A value of None counts as absent, as in ``run_values`` (``values.py``).
"""

import hashlib
import heapq
import math
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from itertools import pairwise
from typing import Any, Literal

type Value = int | float | str | bool | list | None
type Kind = Literal["varies", "repeat", "same", "unique"]
type Order = Literal["asc", "desc"]

# D217: the project's default best-by column, the first of these it has (lowest is best).
BEST_KEYS = ("summary.val/loss", "summary.eval/loss", "summary.loss")

# D218: a group with fewer runs than this treats strings distinct in every run as Varies (3 runs
# with 3 ``data_mix`` values is an axis, not a path).
UNIQUE_MIN_RUNS = 4

# D218: names of repeat keys, matched on the key's last dotted segment.
REPEAT_NAMES = frozenset({"seed", "random_state", "rng"})
REPEAT_SUFFIX = "_seed"

# D219: the fewest runs a W&B group needs to be suggested. A lone run in a W&B group isn't a
# group; it falls through to config clustering.
MIN_WANDB_GROUP = 2
# D219: config clusters differ in at most this many keys and need at least this many runs.
MAX_CLUSTER_KEYS = 2
MIN_CLUSTER = 3
# D219: a cluster takes a wider set of differing keys only when it brings in this many more runs.
MIN_GAIN = 2

# D225: one key is named by its values when it has at most this many.
MAX_NAMED_VALUES = 3


@dataclass(frozen=True)
class RunRow:
    id: str  # Flock run id (wandb run name)
    name: str  # display name
    created_at: float  # epoch seconds
    state: str
    group: str | None  # W&B group attribute
    config: Mapping[str, Value]  # flattened, keys like "config.lr"; a missing key is absent
    summary: Mapping[str, Value]  # keys like "summary.val/loss"


# ---------------------------------------------------------------------- values


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _canon(value: Any) -> tuple:
    """A hashable, totally ordered stand-in for ``value``: equal values (``1`` and ``1.0``,
    every NaN, equal lists) map to equal tuples, and a type tag keeps ``True`` apart from 1."""
    if value is None:
        return ("none",)
    if isinstance(value, bool):
        return ("bool", value)
    if _is_number(value):
        return ("nan",) if math.isnan(value) else ("num", value)
    if isinstance(value, str):
        return ("str", value)
    if isinstance(value, (list, tuple)):
        return ("list", tuple(_canon(item) for item in value))
    if isinstance(value, dict):
        return ("dict", tuple(sorted((str(k), _canon(v)) for k, v in value.items())))
    return ("other", repr(value))


def _present(run: RunRow, key: str) -> bool:
    return run.config.get(key) is not None


def _finite(value: Value) -> float | None:
    """``value`` as a float when it's a finite number; NaN, infinities and the rest are None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def display_key(key: str) -> str:
    """A key as names show it, without the ``config.`` prefix (D225)."""
    return key.removeprefix("config.")


def _format(value: Value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, "g")
    if isinstance(value, list):
        return "[" + ", ".join(_format(item) for item in value) + "]"
    return str(value)


# ---------------------------------------------------------------------- kinds (D218)


def is_repeat_name(key: str) -> bool:
    """Whether ``key`` is named like a seed (D218): its last dotted segment after ``config.`` is
    ``seed``, ``*_seed``, ``random_state`` or ``rng`` (case-insensitive)."""
    name = display_key(key).rsplit(".", 1)[-1].lower()
    return name in REPEAT_NAMES or name.endswith(REPEAT_SUFFIX)


def _kind(values: Sequence[Value], runs: int) -> Kind:
    """The kind of a key from its (non-None) values in a set of ``runs`` runs."""
    counts = Counter(_canon(v) for v in values)
    if len(counts) <= 1:
        # One value: Same everywhere when every run has it; missing from some and the same in
        # the rest is Varies, shown with "—" for the missing. A key with no values at all has
        # nothing to show, so it's Same.
        return "same" if len(values) == runs or not values else "varies"
    if max(counts.values()) >= 2:
        return "varies"  # some value is shared by 2 or more runs
    # Distinct in every run that has it. Numbers (and booleans) are a random search's samples;
    # strings and lists are paths, ids and timestamps, unless the group is too small to tell.
    if all(isinstance(v, (str, list, tuple, dict)) for v in values) and runs >= UNIQUE_MIN_RUNS:
        return "unique"
    return "varies"


def classify_columns(runs: Sequence[RunRow], overrides: Mapping[str, Kind] = {}) -> dict[str, Kind]:
    """Each config key seen in any of ``runs`` (with a non-None value), sorted, mapped to its
    kind (D218). A repeat name wins over the value rules, and ``overrides`` win over both;
    overrides for keys no run has are left out."""
    seen: dict[str, list[Value]] = defaultdict(list)
    for run in runs:
        for key, value in run.config.items():
            if value is not None:
                seen[key].append(value)
    kinds: dict[str, Kind] = {}
    for key in sorted(seen):
        if key in overrides:
            kinds[key] = overrides[key]
        elif is_repeat_name(key):
            kinds[key] = "repeat"
        else:
            kinds[key] = _kind(seen[key], len(runs))
    return kinds


def _of_kind(kinds: Mapping[str, Kind], kind: Kind) -> list[str]:
    return sorted(key for key, found in kinds.items() if found == kind)


# ---------------------------------------------------------------------- best (D217)


def default_best_key(summary_keys: Iterable[str]) -> str | None:
    """The first of ``BEST_KEYS`` among ``summary_keys``, or None: the setting stays unset until
    someone picks one (D217)."""
    keys = set(summary_keys)
    return next((key for key in BEST_KEYS if key in keys), None)


@dataclass(frozen=True)
class Configuration:
    values: tuple[tuple[str, Value], ...]  # the varying keys' values that define it (by key)
    run_ids: tuple[str, ...]
    value: float | None  # mean over runs with a finite value (None if none)
    std: float | None  # population std when >= 2 finite values, else None
    n: int  # runs with a finite value


@dataclass(frozen=True)
class Best:
    run_id: str | None  # best single run (with repeats: the best run in the best configuration)
    value: float | None  # the best configuration's value (the mean with repeats)
    std: float | None
    n: int
    configurations: tuple[Configuration, ...]  # best first; configurations without a value last
    run_value: float | None = None  # the best run's own value (differs from ``value`` with repeats)


def _metric(run: RunRow, key: str) -> float | None:
    """The run's finite value of ``key``: a summary column, or any other column of the run."""
    value = run.summary.get(key) if key in run.summary else run.config.get(key)
    return _finite(value)


def best(runs: Sequence[RunRow], key: str, order: Order, kinds: Mapping[str, Kind]) -> Best:
    """The best of ``runs`` by ``key`` in ``order`` (D217).

    NaN, infinities and a missing value are never best. Without a repeat key each run is its
    own configuration. With one, runs sharing every Varies value form a configuration whose
    value is the mean over its finite values (± population std); best is the best
    configuration, and its run the best run inside it. Ties go to the smallest run id.
    """
    sign = 1.0 if order == "asc" else -1.0
    varying = _of_kind(kinds, "varies")
    repeats = bool(_of_kind(kinds, "repeat"))
    ordered = sorted(runs, key=lambda run: run.id)

    buckets: dict[tuple, list[RunRow]] = {}
    for run in ordered:
        signature = (
            tuple(_canon(run.config.get(k)) for k in varying) if repeats else ("run", run.id)
        )
        buckets.setdefault(signature, []).append(run)

    configs = []
    for members in buckets.values():
        found = [v for v in (_metric(run, key) for run in members) if v is not None]
        configs.append(
            Configuration(
                values=tuple((k, members[0].config.get(k)) for k in varying),
                run_ids=tuple(run.id for run in members),
                value=statistics.fmean(found) if found else None,
                std=statistics.pstdev(found) if len(found) >= 2 else None,
                n=len(found),
            )
        )
    configs.sort(
        key=lambda c: (c.value is None, sign * c.value if c.value is not None else 0, c.run_ids)
    )

    top = configs[0] if configs and configs[0].value is not None else None
    if top is None:
        return Best(None, None, None, 0, tuple(configs))
    by_id = {run.id: run for run in runs}
    scored = [(sign * v, rid) for rid in top.run_ids if (v := _metric(by_id[rid], key)) is not None]
    run_value, run_id = min(scored)
    return Best(run_id, top.value, top.std, top.n, tuple(configs), sign * run_value)


# ---------------------------------------------------------------------- Compare (D221)


def differing_keys(configs: Sequence[Mapping[str, Value]]) -> list[str]:
    """The keys whose values aren't the same in every one of ``configs``, sorted (D221). A key
    missing (or None) in some and present in others differs."""
    keys = sorted({key for config in configs for key in config})
    return [key for key in keys if len({_canon(config.get(key)) for config in configs}) > 1]


# ---------------------------------------------------------------------- names (D225)


def _fixed_state(filter: Mapping | None) -> str | None:
    """The single state ``filter`` fixes: ``{"state": "finished"}``, ``{"$eq": ...}``, a
    one-element ``$in``, or any of those inside a top-level ``$and``."""
    if not isinstance(filter, Mapping):
        return None
    for key, cond in filter.items():
        if key == "$and" and isinstance(cond, list):
            for sub in cond:
                if (state := _fixed_state(sub)) is not None:
                    return state
        elif key in ("state", "run.state"):
            if isinstance(cond, str):
                return cond
            if isinstance(cond, Mapping):
                if isinstance(eq := cond.get("$eq"), str):
                    return eq
                options = cond.get("$in")
                if isinstance(options, list) and len(options) == 1 and isinstance(options[0], str):
                    return options[0]
    return None


def _template(runs: Sequence[RunRow], kinds: Mapping[str, Kind]) -> str | None:
    """The name from the Varies keys, or None when nothing varies."""
    keys = _of_kind(kinds, "varies")
    names = [display_key(k) for k in keys]
    if not keys:
        return None
    if len(keys) == 1:
        key, name = keys[0], names[0]
        found = {_canon(v): v for run in runs if (v := run.config.get(key)) is not None}
        if found and all(isinstance(v, bool) for v in found.values()):
            return f"{name} on / off"
        if not found:
            return name
        if len(found) <= MAX_NAMED_VALUES:
            return f"{name}: " + ", ".join(_format(found[c]) for c in sorted(found))
        return f"{name}: {len(found)} values"
    if len(keys) == 2:
        return " × ".join(names)
    return f"{names[0]} × {names[1]} + {len(keys) - 2} more"


def is_opaque(name: str) -> bool:
    """Whether a W&B group's name is an id rather than words: a hash or sweep id such as
    ``sweep-x8k2ab3c`` or ``3f9a1c7e``. Such names all look alike side by side."""
    for token in re.split(r"[-_./: ]+", name):
        if len(token) < 6:
            continue
        # Letters and digits interleaved (x8k2ab3c), unlike words with a number (warmup2000).
        switches = sum(a.isdigit() != b.isdigit() for a, b in pairwise(token))
        # Random letters pile up consonants no word has (3evqlkwy: "vqlkwy").
        run = max((len(r) for r in re.findall(r"[b-df-hj-np-tv-z]+", token.lower())), default=0)
        digit = any(c.isdigit() for c in token)
        # W&B's own ids (runs, sweeps) are 8 lowercase letters and digits.
        wandb_id = len(token) == 8 and digit and token.isalnum() and token == token.lower()
        if wandb_id or switches >= 3 or (digit and run >= 4) or run >= 5:
            return True
        hexed = all(c in "0123456789abcdefABCDEF" for c in token)
        if len(token) >= 7 and hexed and any(c.isdigit() for c in token):
            return True
    return False


def group_name(
    runs: Sequence[RunRow],
    kinds: Mapping[str, Kind],
    filter: Mapping | None,
    wandb_group: str | None = None,
) -> str:
    """A new group's default name from its Varies keys and filter (D225), ``config.`` stripped.

    - two keys: ``lr × batch_size``; three or more: the first two (by key) and
      ``+ N more``, as ``lr × wd + 2 more``;
    - one boolean key: ``qk_norm on / off``;
    - one key: ``data_mix: web-v2, web-v3`` with up to 3 values (sorted), else
      ``lr: 8 values``;
    - nothing varies: ``N runs`` (``1 run``).

    A W&B group's name comes first, as ``pretrain-1b, lr × batch_size`` (or the group's name
    alone when nothing varies). An opaque one (a hash or sweep id, ``is_opaque``) goes last
    instead, as ``lr × batch_size (sweep-x8k2ab3c)``, so names differ where they're read
    first. ``, finished`` (or whichever state) is added when the filter fixes ``state``.
    """
    template = _template(runs, kinds)
    if wandb_group and template and is_opaque(wandb_group):
        name = f"{template} ({wandb_group})"
    elif wandb_group:
        name = f"{wandb_group}, {template}" if template else wandb_group
    elif template:
        name = template
    else:
        name = f"{len(runs)} run" + ("" if len(runs) == 1 else "s")
    if (state := _fixed_state(filter)) is not None:
        name += f", {state}"
    return name


# ---------------------------------------------------------------------- suggestions (D219)


@dataclass(frozen=True)
class Suggestion:
    signature: bytes  # sha256 of the sorted run ids joined by "\n" (D219)
    source: Literal["wandb_group", "config_diff"]
    name: str
    filter: dict | None  # {"group": name} for a W&B group; None for a cluster (hand picks)
    run_ids: tuple[str, ...]


def signature(run_ids: Iterable[str]) -> bytes:
    """A suggestion's signature (D219): sha256 of its sorted run ids joined by newlines. It
    changes when the runs do, so a dismissed suggestion comes back only with other runs."""
    return hashlib.sha256("\n".join(sorted(run_ids)).encode()).digest()


def _diff(a: Mapping[str, tuple], b: Mapping[str, tuple]) -> frozenset[str]:
    return frozenset(key for key in a.keys() | b.keys() if a.get(key) != b.get(key))


def _around(
    seed: int, free: Iterable[int], canon: Sequence[Mapping[str, tuple]]
) -> tuple[frozenset[str], list[int]]:
    """The cluster around run ``seed``: the free runs equal to it on every key outside a set
    ``K`` of at most ``MAX_CLUSTER_KEYS`` keys (D219), and that ``K``.

    ``K`` grows from nothing, and takes a larger set only when it brings in at least
    ``MIN_GAIN`` more runs (the smallest such set, then the one bringing the most). So a key
    that only one run changes doesn't get a sweep's second slot: in an ``lr`` sweep, a run
    that also changes ``model`` stays out.
    """
    diffs = [
        (j, d)
        for j in free
        if j != seed and len(d := _diff(canon[seed], canon[j])) <= MAX_CLUSTER_KEYS
    ]
    singles = sorted({key for _, d in diffs if len(d) == 1 for key in d})
    options = {d for _, d in diffs if d} | {
        frozenset((a, b)) for i, a in enumerate(singles) for b in singles[i + 1 :]
    }

    def members(keys: frozenset[str]) -> list[int]:
        return sorted([seed, *(j for j, d in diffs if d <= keys)])

    keys: frozenset[str] = frozenset()
    size = len(members(keys))
    while True:
        larger = [(k, len(members(k))) for k in options if k > keys]
        larger = [(k, n) for k, n in larger if n >= size + MIN_GAIN]
        if not larger:
            return keys, members(keys)
        keys, size = min(larger, key=lambda item: (len(item[0]), -item[1], sorted(item[0])))


def _clusters(runs: Sequence[RunRow], ignored: AbstractSet[str]) -> list[list[RunRow]]:
    """Greedy config clusters of ``runs`` (D219): the largest cluster around any run
    (``_around``; ties to fewer keys, then the earlier run in ``runs``) is taken first, then
    the largest among the runs left, until none has ``MIN_CLUSTER`` runs. Clusters come
    largest first, each in ``runs``' order."""
    canon = [
        {key: _canon(v) for key, v in run.config.items() if v is not None and key not in ignored}
        for run in runs
    ]
    free = set(range(len(runs)))
    heap = []
    for i in range(len(runs)):
        keys, members = _around(i, free, canon)
        heap.append((-len(members), len(keys), i))
    heapq.heapify(heap)
    out = []
    # Lazy greedy: a stored size is an upper bound (clusters only shrink as runs are taken), so
    # an entry whose recomputed size still matches is the largest left.
    while heap:
        entry = heapq.heappop(heap)
        i = entry[2]
        if i not in free:
            continue
        keys, members = _around(i, free, canon)
        if len(members) < MIN_CLUSTER:
            continue
        if (fresh := (-len(members), len(keys), i)) != entry:
            heapq.heappush(heap, fresh)
            continue
        free.difference_update(members)
        out.append([runs[j] for j in members])
    return out


def suggest(
    runs: Sequence[RunRow], grouped: AbstractSet[str], dismissed: AbstractSet[bytes]
) -> list[Suggestion]:
    """Suggested groups (D219) from the runs in no saved group (``grouped``: run ids).

    Sources in order, each run placed by the first that claims it:

    1. W&B groups of at least ``MIN_WANDB_GROUP`` runs, by group name, filtered by
       ``{"group": name}``;
    2. greedy config clusters of the rest (``_clusters``), largest first, with no filter.
       Keys of kind Different every run or Repeat, over these runs, are ignored.

    Dismissed suggestions (by ``signature``) are left out, but their runs stay claimed, so
    dismissing one doesn't reshuffle the others. The ``sweep`` attribute is never used (W&B
    sweeps aren't supported yet). Runs within a suggestion are ordered by creation, then id.
    """
    free = sorted(
        (run for run in runs if run.id not in grouped), key=lambda r: (r.created_at, r.id)
    )
    out: list[Suggestion] = []

    by_group: dict[str, list[RunRow]] = defaultdict(list)
    for run in free:
        if run.group:
            by_group[run.group].append(run)
    claimed: set[str] = set()
    for name in sorted(by_group):
        members = by_group[name]
        if len(members) < MIN_WANDB_GROUP:
            continue
        claimed.update(run.id for run in members)
        out.append(
            Suggestion(
                signature=signature(run.id for run in members),
                source="wandb_group",
                name=group_name(members, classify_columns(members), None, wandb_group=name),
                filter={"group": name},
                run_ids=tuple(run.id for run in members),
            )
        )

    rest = [run for run in free if run.id not in claimed]
    pool = classify_columns(rest)
    ignored = {key for key, kind in pool.items() if kind in ("unique", "repeat")}
    for cluster in _clusters(rest, ignored):
        # Name from the cluster's own kinds, but keep the pool's verdict on paths and seeds: a
        # 3-run cluster would otherwise call its unique paths Varies.
        kinds = classify_columns(cluster, {key: pool[key] for key in ignored})
        out.append(
            Suggestion(
                signature=signature(run.id for run in cluster),
                source="config_diff",
                name=group_name(cluster, kinds, None),
                filter=None,
                run_ids=tuple(run.id for run in cluster),
            )
        )
    return [s for s in out if s.signature not in dismissed]
