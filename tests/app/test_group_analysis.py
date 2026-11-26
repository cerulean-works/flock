"""Run groups' analysis (025): column kinds (D185), best (D184), differing keys (D188), names
(D192) and suggestions (D186). Pure; no database."""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING

import pytest

from app.api.src.core.group_analysis import (
    RunRow,
    best,
    classify_columns,
    default_best_key,
    differing_keys,
    group_name,
    is_opaque,
    is_repeat_name,
    signature,
    suggest,
)

if TYPE_CHECKING:
    from typing import Any


def run(
    id: str,
    config: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    *,
    group: str | None = None,
    created_at: float | None = None,
    state: str = "finished",
) -> RunRow:
    return RunRow(
        id=id,
        name=f"name-{id}",
        created_at=created_at
        if created_at is not None
        else float(int(id[1:]) if id[1:].isdigit() else 0),
        state=state,
        group=group,
        config={f"config.{k}": v for k, v in (config or {}).items()},
        summary={f"summary.{k}": v for k, v in (summary or {}).items()},
    )


def runs_of(*configs: dict[str, Any]) -> list[RunRow]:
    return [run(f"r{i}", config) for i, config in enumerate(configs)]


# ---------------------------------------------------------------------- classify_columns


def test_classify_basic_kinds() -> None:
    runs = runs_of(
        *[
            {
                "bs": [16, 16, 32, 32, 64][i],
                "lr": [0.1, 0.01, 0.003, 0.0007, 0.0002][i],  # random search
                "save_dir": f"/ckpt/run-{i}",
                "ids": [i, "x"],
                "model": "llama",
                "seed": i % 2,
            }
            for i in range(5)
        ]
    )
    assert classify_columns(runs) == {
        "config.bs": "varies",
        "config.ids": "unique",
        "config.lr": "varies",
        "config.model": "same",
        "config.save_dir": "unique",
        "config.seed": "repeat",
    }


def test_classify_small_group_unique_strings_vary() -> None:
    mixes = ["web-v1", "web-v2", "web-v3"]
    assert classify_columns(runs_of(*[{"data_mix": m} for m in mixes])) == {
        "config.data_mix": "varies"
    }
    four = runs_of(*[{"data_mix": f"m{i}"} for i in range(4)])
    assert classify_columns(four) == {"config.data_mix": "unique"}


def test_classify_missing_in_some() -> None:
    runs = runs_of({"warmup": 100}, {"warmup": 100}, {}, {"warmup": None, "other": 1})
    kinds = classify_columns(runs)
    assert kinds["config.warmup"] == "varies"  # missing (or None) in some, same in the rest
    assert kinds["config.other"] == "varies"  # present in one run only


def test_classify_unique_strings_missing_in_some_stay_unique() -> None:
    runs = runs_of(*[{"path": f"/p/{i}"} for i in range(4)], {})
    assert classify_columns(runs) == {"config.path": "unique"}


def test_classify_booleans() -> None:
    assert classify_columns(runs_of({"qk": True}, {"qk": False}, {"qk": True}, {"qk": False})) == {
        "config.qk": "varies"
    }
    assert classify_columns(runs_of(*[{"qk": True}] * 4)) == {"config.qk": "same"}
    # distinct in every run (2 runs) is still an axis, not a path
    assert classify_columns(runs_of({"qk": True}, {"qk": False})) == {"config.qk": "varies"}
    # True and 1 are different values
    assert classify_columns(runs_of({"x": True}, {"x": 1})) == {"config.x": "varies"}


def test_classify_lists() -> None:
    shared = runs_of(*[{"layers": [1, 2] if i % 2 else [3]} for i in range(4)])
    assert classify_columns(shared) == {"config.layers": "varies"}
    same = runs_of(*[{"layers": [1, 2]} for _ in range(4)])
    assert classify_columns(same) == {"config.layers": "same"}


def test_classify_numbers_equal_across_types() -> None:
    assert classify_columns(runs_of({"bs": 32}, {"bs": 32.0})) == {"config.bs": "same"}


def test_classify_nan() -> None:
    nan = float("nan")
    assert classify_columns(runs_of({"x": nan}, {"x": nan})) == {"config.x": "same"}
    assert classify_columns(runs_of({"x": nan}, {"x": nan}, {"x": 1.0})) == {"config.x": "varies"}
    assert classify_columns(runs_of({"x": nan}, {"x": 1.0}, {"x": math.inf})) == {
        "config.x": "varies"
    }


@pytest.mark.parametrize(
    ("key", "repeat"),
    [
        ("config.seed", True),
        ("config.data_seed", True),
        ("config.train.seed", True),
        ("config.optim.init_seed", True),
        ("config.random_state", True),
        ("config.rng", True),
        ("config.SEED", True),
        ("config.seeds", False),
        ("config.seed_dir", False),
        ("config.rng_impl", False),
        ("config.seed.value", False),  # matched on the last segment
    ],
)
def test_repeat_names(key: str, repeat: bool) -> None:
    assert is_repeat_name(key) is repeat


def test_classify_repeat_by_name_even_when_same() -> None:
    assert classify_columns(runs_of({"seed": 0}, {"seed": 0})) == {"config.seed": "repeat"}


def test_classify_overrides_win() -> None:
    runs = runs_of({"seed": 1, "lr": 0.1, "a": 1}, {"seed": 2, "lr": 0.1, "a": 2})
    kinds = classify_columns(
        runs, {"config.seed": "varies", "config.lr": "repeat", "config.nope": "varies"}
    )
    assert kinds == {"config.a": "varies", "config.lr": "repeat", "config.seed": "varies"}


def test_classify_empty() -> None:
    assert classify_columns([]) == {}


# ---------------------------------------------------------------------- default_best_key


def test_default_best_key() -> None:
    assert default_best_key(["summary.loss", "summary.eval/loss", "summary.val/loss"]) == (
        "summary.val/loss"
    )
    assert default_best_key(iter(["summary.loss", "summary.eval/loss"])) == "summary.eval/loss"
    assert default_best_key(["summary.loss", "summary.acc"]) == "summary.loss"
    assert default_best_key(["summary.acc", "config.loss"]) is None
    assert default_best_key([]) is None


# ---------------------------------------------------------------------- best

KEY = "summary.loss"


def test_best_without_repeats_each_run_is_a_configuration() -> None:
    runs = [
        run("r1", {"lr": 0.1}, {"loss": 2.0}),
        run("r2", {"lr": 0.2}, {"loss": 1.0}),
        run("r3", {"lr": 0.3}, {"loss": 3.0}),
    ]
    kinds = classify_columns(runs)
    low = best(runs, KEY, "asc", kinds)
    assert (low.run_id, low.value, low.std, low.n) == ("r2", 1.0, None, 1)
    assert [c.run_ids for c in low.configurations] == [("r2",), ("r1",), ("r3",)]
    assert low.configurations[0].values == (("config.lr", 0.2),)
    high = best(runs, KEY, "desc", kinds)
    assert (high.run_id, high.value) == ("r3", 3.0)


def test_best_never_nan_inf_or_missing() -> None:
    runs = [
        run("r1", {}, {"loss": float("nan")}),
        run("r2", {}, {"loss": float("-inf")}),
        run("r3", {}, {}),
        run("r4", {}, {"loss": "1.0"}),
        run("r5", {}, {"loss": True}),
        run("r6", {}, {"loss": 5.0}),
    ]
    result = best(runs, KEY, "asc", {})
    assert (result.run_id, result.value, result.n) == ("r6", 5.0, 1)
    assert result.configurations[0].run_ids == ("r6",)
    assert all(c.value is None for c in result.configurations[1:])
    # configurations without a value are last, by run id
    assert [c.run_ids[0] for c in result.configurations[1:]] == ["r1", "r2", "r3", "r4", "r5"]
    assert best(runs, KEY, "desc", {}).run_id == "r6"


def test_best_with_no_values() -> None:
    runs = [run("r1", {}, {}), run("r2", {}, {"loss": float("inf")})]
    result = best(runs, KEY, "asc", {})
    assert (result.run_id, result.value, result.std, result.n) == (None, None, None, 0)
    assert len(result.configurations) == 2
    empty = best([], KEY, "asc", {})
    assert (empty.run_id, empty.configurations) == (None, ())


def test_best_with_repeats_uses_configuration_means() -> None:
    # lr=0.1 has the luckiest seed (0.5), but lr=0.2 is better on average.
    runs = [
        run("a1", {"lr": 0.1, "seed": 0, "dir": "/a1"}, {"loss": 0.5}),
        run("a2", {"lr": 0.1, "seed": 1, "dir": "/a2"}, {"loss": 2.5}),
        run("b1", {"lr": 0.2, "seed": 0, "dir": "/b1"}, {"loss": 1.0}),
        run("b2", {"lr": 0.2, "seed": 1, "dir": "/b2"}, {"loss": 1.2}),
        run("b3", {"lr": 0.2, "seed": 2, "dir": "/b3"}, {"loss": float("nan")}),
    ]
    kinds = classify_columns(runs)
    assert kinds["config.dir"] == "unique"  # ignored when forming configurations
    result = best(runs, KEY, "asc", kinds)
    assert result.run_id == "b1"
    assert result.value == pytest.approx(1.1)
    assert result.run_value == pytest.approx(1.0)  # b1's own, not its configuration's mean
    assert result.std == pytest.approx(0.1)  # population std
    assert result.n == 2
    top, second = result.configurations
    assert top.run_ids == ("b1", "b2", "b3")
    assert top.values == (("config.lr", 0.2),)
    assert (second.value, second.std, second.n) == (pytest.approx(1.5), pytest.approx(1.0), 2)
    high = best(runs, KEY, "desc", kinds)
    assert (high.run_id, high.value) == ("a2", pytest.approx(1.5))
    assert high.run_value == pytest.approx(2.5)


def test_best_repeat_single_run_configuration_has_no_std() -> None:
    runs = [
        run("a1", {"lr": 0.1, "seed": 0}, {"loss": 1.0}),
        run("b1", {"lr": 0.2, "seed": 0}, {"loss": 2.0}),
        run("b2", {"lr": 0.2, "seed": 1}, {"loss": 2.0}),
    ]
    result = best(runs, KEY, "asc", classify_columns(runs))
    assert (result.run_id, result.value, result.std, result.n) == ("a1", 1.0, None, 1)
    assert result.configurations[1].std == 0.0


def test_best_repeat_groups_missing_values_together() -> None:
    runs = [
        run("a1", {"lr": 0.1, "seed": 0}, {"loss": 1.0}),
        run("a2", {"lr": 0.1, "seed": 1}, {"loss": 1.0}),
        run("b1", {"seed": 0}, {"loss": 3.0}),
        run("b2", {"seed": 1}, {"loss": 5.0}),
    ]
    result = best(runs, KEY, "desc", classify_columns(runs))
    assert result.configurations[0].run_ids == ("b1", "b2")
    assert result.configurations[0].values == (("config.lr", None),)
    assert (result.run_id, result.value) == ("b2", 4.0)


def test_best_ties_go_to_smallest_run_id() -> None:
    runs = [run(i, {}, {"loss": 1.0}) for i in ("r3", "r1", "r2")]
    assert best(runs, KEY, "asc", {}).run_id == "r1"
    assert best(runs, KEY, "desc", {}).run_id == "r1"
    assert [c.run_ids for c in best(runs, KEY, "asc", {}).configurations] == [
        ("r1",),
        ("r2",),
        ("r3",),
    ]
    seeded = [run(i, {"seed": n}, {"loss": 1.0}) for n, i in enumerate(("r3", "r1", "r2"))]
    assert best(seeded, KEY, "asc", classify_columns(seeded)).run_id == "r1"


# ---------------------------------------------------------------------- differing_keys


def test_differing_keys() -> None:
    configs = [
        {"config.lr": 0.1, "config.bs": 32, "config.model": "a"},
        {"config.lr": 0.2, "config.bs": 32.0, "config.model": "a", "config.extra": 1},
        {"config.lr": 0.1, "config.bs": 32, "config.model": "a", "config.nan": float("nan")},
    ]
    assert differing_keys(configs) == ["config.extra", "config.lr", "config.nan"]


def test_differing_keys_edge_cases() -> None:
    assert differing_keys([]) == []
    assert differing_keys([{"a": 1}]) == []
    assert differing_keys([{"a": [1, 2]}, {"a": [1, 2]}]) == []
    assert differing_keys([{"a": None}, {}]) == []  # None is absent
    assert differing_keys([{"a": True}, {"a": 1}]) == ["a"]
    assert differing_keys([{"a": float("nan")}, {"a": float("nan")}]) == []


# ---------------------------------------------------------------------- group_name


def named(*configs: dict[str, Any], filter: dict | None = None, wandb: str | None = None) -> str:
    runs = runs_of(*configs)
    return group_name(runs, classify_columns(runs), filter, wandb)


def test_name_two_keys() -> None:
    assert named(
        {"lr": 0.1, "bs": 32}, {"lr": 0.1, "bs": 64}, {"lr": 0.2, "bs": 32}, {"lr": 0.2, "bs": 64}
    ) == ("bs × lr")


def test_name_one_boolean() -> None:
    assert named({"qk_norm": True}, {"qk_norm": False}, {"qk_norm": True}) == "qk_norm on / off"
    assert named({"qk_norm": True}, {"qk_norm": True}, {}) == "qk_norm on / off"


def test_name_one_key_values() -> None:
    assert (
        named({"data_mix": "web-v3"}, {"data_mix": "web-v2"}, {"data_mix": "web-v3"})
        == "data_mix: web-v2, web-v3"
    )
    assert named({"lr": 0.001}, {"lr": 0.0003}, {"lr": 0.01}) == "lr: 0.0003, 0.001, 0.01"
    assert named(*[{"lr": 10.0**-i} for i in range(1, 6)]) == "lr: 5 values"


def test_name_nested_key_strips_config_prefix() -> None:
    assert named({"optim.wd": 0.1}, {"optim.wd": 0.0}, {"optim.wd": 0.1}) == "optim.wd: 0, 0.1"


def test_name_many_keys() -> None:
    configs = [{"a": i % 2, "b": i % 3, "c": i % 2 == 0, "d": i // 4} for i in range(8)]
    assert named(*configs) == "a × b + 2 more"


def test_name_nothing_varies() -> None:
    assert named({"x": 1}, {"x": 1}) == "2 runs"
    assert named({"x": 1}) == "1 run"
    assert group_name([], {}, None) == "0 runs"
    # repeat and unique keys don't name a group
    assert named(*[{"seed": i, "dir": f"/d/{i}"} for i in range(4)]) == "4 runs"


@pytest.mark.parametrize(
    "filter",
    [
        {"state": "finished"},
        {"state": {"$eq": "finished"}},
        {"state": {"$in": ["finished"]}},
        {"$and": [{"config.lr": {"$gt": 0}}, {"state": "finished"}]},
        {"run.state": "finished"},
    ],
)
def test_name_finished(filter: dict) -> None:
    assert named({"qk": True}, {"qk": False}, filter=filter) == "qk on / off, finished"


@pytest.mark.parametrize(
    "filter",
    [
        None,
        {},
        {"state": {"$ne": "finished"}},
        {"state": {"$in": ["finished", "crashed"]}},
        {"$or": [{"state": "finished"}]},
    ],
)
def test_name_state_not_fixed(filter: dict | None) -> None:
    assert named({"qk": True}, {"qk": False}, filter=filter) == "qk on / off"


def test_name_wandb_group_first() -> None:
    configs = ({"lr": 0.1}, {"lr": 0.2}, {"lr": 0.1})
    assert named(*configs, wandb="pretrain-1b") == "pretrain-1b, lr: 0.1, 0.2"
    assert named({"x": 1}, {"x": 1}, wandb="pretrain-1b") == "pretrain-1b"
    assert (
        named({"x": 1}, {"x": 1}, wandb="pretrain-1b", filter={"state": "finished"})
        == "pretrain-1b, finished"
    )


@pytest.mark.parametrize(
    ("name", "opaque"),
    [
        ("sweep-x8k2ab3c", True),
        ("sweep-3evqlkwy", True),
        ("sweep-ksw0fsl0", True),
        ("3f9a1c7e", True),
        ("run_20a7fe91d", True),
        ("pretrain-1b", False),
        ("ablation-qk-norm", False),
        ("lr-sweep-v2", False),
        ("2024-11-05", False),
        ("warmup2000", False),
        ("llama3b-ft", False),
        ("strength-ablation", False),
        ("bf16-throughput", False),
    ],
)
def test_is_opaque(name: str, opaque: bool) -> None:
    assert is_opaque(name) is opaque


def test_name_opaque_wandb_group_last() -> None:
    # Sweep ids look alike side by side: the varying keys lead, the id follows.
    configs = ({"lr": 0.1}, {"lr": 0.2})
    assert named(*configs, wandb="sweep-x8k2ab3c") == "lr: 0.1, 0.2 (sweep-x8k2ab3c)"
    assert named({"x": 1}, {"x": 1}, wandb="sweep-x8k2ab3c") == "sweep-x8k2ab3c"


# ---------------------------------------------------------------------- suggest


def sweep_runs(prefix: str, start: int, base: dict[str, Any], key: str, values: list) -> list:
    return [
        run(
            f"{prefix}{i}",
            {**base, key: v, "save_dir": f"/ckpt/{prefix}{i}", "seed": i},
            created_at=start + i,
        )
        for i, v in enumerate(values)
    ]


BASE = {"model": "llama", "bs": 32, "lr": 0.1, "warmup": 100, "opt": "adam"}


def test_signature() -> None:
    assert signature(["b", "a"]) == hashlib.sha256(b"a\nb").digest()
    assert signature(["a", "b"]) == signature(("b", "a"))


def test_suggest_wandb_groups_first() -> None:
    wandb = [
        run("w1", {**BASE, "lr": 0.1}, group="pretrain-1b", created_at=1),
        run("w2", {**BASE, "lr": 0.2}, group="pretrain-1b", created_at=2),
        run("w3", {**BASE, "lr": 0.3}, group="ablate", created_at=3),
        run("w4", {**BASE, "lr": 0.3}, group="ablate", created_at=4),
        run("w5", {**BASE, "lr": 0.4}, group="lonely", created_at=5),  # one run: not a group
    ]
    clustered = [
        run(f"c{i}", {**BASE, "lr": lr}, created_at=10 + i) for i, lr in enumerate([0.5, 0.6])
    ]
    result = suggest(wandb + clustered, set(), set())
    assert [(s.source, s.name, s.filter, s.run_ids) for s in result] == [
        ("wandb_group", "ablate", {"group": "ablate"}, ("w3", "w4")),
        ("wandb_group", "pretrain-1b, lr: 0.1, 0.2", {"group": "pretrain-1b"}, ("w1", "w2")),
        ("config_diff", "lr: 0.4, 0.5, 0.6", None, ("w5", "c0", "c1")),
    ]
    assert result[1].signature == signature(["w1", "w2"])


def test_suggest_config_clusters() -> None:
    lr = sweep_runs("l", 0, BASE, "lr", [0.1, 0.01, 0.001, 0.0001])
    bs = sweep_runs("b", 100, {**BASE, "model": "gpt"}, "bs", [16, 32, 64])
    loners = [
        run(
            "x1", {"model": "mamba", "bs": 8, "lr": 0.5, "warmup": 1, "opt": "sgd"}, created_at=200
        ),
        run(
            "x2", {"model": "rwkv", "bs": 4, "lr": 0.7, "warmup": 2, "opt": "lion"}, created_at=201
        ),
    ]
    result = suggest(lr + bs + loners, set(), set())
    assert [(s.source, s.name, s.filter, s.run_ids) for s in result] == [
        ("config_diff", "lr: 4 values", None, ("l0", "l1", "l2", "l3")),
        ("config_diff", "bs: 16, 32, 64", None, ("b0", "b1", "b2")),
    ]


def test_suggest_cluster_allows_two_keys_not_three() -> None:
    two = [
        run(f"t{i}", {**BASE, "lr": lr, "bs": bs}, created_at=i)
        for i, (lr, bs) in enumerate([(0.1, 16), (0.2, 16), (0.1, 32), (0.2, 32)])
    ]
    assert [(s.name, s.run_ids) for s in suggest(two, set(), set())] == [
        ("bs × lr", ("t0", "t1", "t2", "t3"))
    ]
    three = [
        run(f"t{i}", {**BASE, "lr": 0.1 * i, "bs": 8 * i, "warmup": 10 * i}, created_at=i)
        for i in range(1, 4)
    ]
    assert suggest(three, set(), set()) == []


def test_suggest_cluster_ignores_unique_and_repeat_keys() -> None:
    runs = [
        run(f"r{i}", {**BASE, "seed": i, "save_dir": f"/c/{i}", "id": f"id{i}", "lr": 0.1})
        for i in range(4)
    ]
    (only,) = suggest(runs, set(), set())
    assert only.run_ids == ("r0", "r1", "r2", "r3")
    assert only.name == "4 runs"


def test_suggest_needs_three_runs() -> None:
    assert suggest(sweep_runs("l", 0, BASE, "lr", [0.1, 0.2]), set(), set()) == []


def test_suggest_skips_grouped_runs() -> None:
    lr = sweep_runs("l", 0, BASE, "lr", [0.1, 0.2, 0.3, 0.4])
    (only,) = suggest(lr, {"l0"}, set())
    assert only.run_ids == ("l1", "l2", "l3")
    assert suggest(lr, {"l0", "l1"}, set()) == []
    wandb = [run(f"w{i}", group="g", created_at=i) for i in range(3)]
    (group,) = suggest(wandb, {"w0"}, set())
    assert group.run_ids == ("w1", "w2")
    assert suggest(wandb, {"w0", "w1"}, set()) == []


def test_suggest_dismissed() -> None:
    wandb = [run(f"w{i}", {"lr": 0.1}, group="g", created_at=i) for i in range(3)]
    lr = sweep_runs("l", 10, BASE, "lr", [0.1, 0.2, 0.3])
    first = suggest(wandb + lr, set(), set())
    assert [s.source for s in first] == ["wandb_group", "config_diff"]
    dismissed = {first[0].signature}
    second = suggest(wandb + lr, set(), dismissed)
    # a dismissed group's runs stay claimed: they don't join a cluster instead
    assert [s.run_ids for s in second] == [("l0", "l1", "l2")]
    # it comes back when its runs change
    more = [*wandb, run("w9", {"lr": 0.1}, group="g", created_at=9)]
    assert [s.run_ids for s in suggest(more, set(), dismissed)] == [("w0", "w1", "w2", "w9")]


def test_suggest_is_deterministic() -> None:
    runs = sweep_runs("l", 0, BASE, "lr", [0.1, 0.2, 0.3]) + [
        run(f"w{i}", group=g, created_at=50 + i) for i, g in enumerate("bab")
    ]
    first = suggest(runs, set(), set())
    assert suggest(list(reversed(runs)), set(), set()) == first
    assert [s.filter for s in first] == [{"group": "b"}, None]


def test_suggest_never_uses_sweep() -> None:
    assert "sweep" not in RunRow.__dataclass_fields__
    assert suggest([], set(), set()) == []


def test_suggest_grid_is_one_cluster() -> None:
    grid = [
        run(f"g{i}", {**BASE, "lr": lr, "bs": bs}, created_at=i)
        for i, (lr, bs) in enumerate((lr, bs) for lr in (0.1, 0.2, 0.3) for bs in (16, 32, 64))
    ]
    (only,) = suggest(grid, set(), set())
    assert (only.name, len(only.run_ids)) == ("bs × lr", 9)


def test_suggest_lone_outlier_does_not_steal_a_sweep_slot() -> None:
    # o0 changes model only; the oldest run, it would seed a "model × bs" or "model × lr"
    # cluster first if clusters went by age instead of size.
    outlier = run("o0", {**BASE, "model": "gpt"}, created_at=0)
    lr = [run(f"l{i}", {**BASE, "lr": 0.1 * i}, created_at=10 + i) for i in range(1, 5)]
    bs = [run(f"b{i}", {**BASE, "model": "gpt", "bs": 8 * i}, created_at=20 + i) for i in (1, 2)]
    result = suggest([outlier, *lr, *bs], set(), set())
    assert [s.run_ids for s in result] == [("l1", "l2", "l3", "l4"), ("o0", "b1", "b2")]
    assert [s.name for s in result] == ["lr: 4 values", "bs: 8, 16, 32"]
