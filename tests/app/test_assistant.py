"""The run groups assistant (D224): which candidates and questions each Decisions API request
sends, the thresholds that turn answers into an action, and errors. The API is an httpx mock."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.api.src.core import assistant
from app.api.src.core.assistant import (
    Action,
    AssistantError,
    Column,
    Condition,
    DecisionsClient,
    GroupColumn,
    Reading,
    RunRef,
    assist,
    candidate_columns,
    group_columns,
    pick_primary,
    predict_primary,
    primary_fallback,
    primary_input,
)
from app.api.src.core.errors import BadGateway
from app.api.src.core.group_analysis import RunRow, classify_columns

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

type Answerer = Callable[[dict], dict]


class FakeDecisions:
    """The Decisions API: records each request body and answers it with ``answer(question)``,
    which returns the answer's fields beyond its name."""

    def __init__(self, answer: Answerer) -> None:
        self.answer = answer
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/decisions"
        assert request.headers["authorization"] == "Bearer sk-test"
        body = json.loads(request.content)
        self.requests.append(body)
        answers = [{"name": q["name"], **self.answer(q)} for q in body["questions"]]
        return httpx.Response(200, json={"model": body["model"], "answers": answers})

    def run(
        self,
        request: str,
        columns: Sequence[Column],
        runs: Sequence[RunRef] = (),
        current: Sequence[Condition] = (),
    ) -> Action:
        async def main() -> Action:
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.handler)) as http:
                client = DecisionsClient("sk-test", http=http)
                return await assist(request, columns, runs, current, client)

        return asyncio.run(main())


def predicate(p: float) -> dict:
    return {"type": "predicate", "probability": p}


def choice(probabilities: dict[Any, float]) -> dict:
    top = max(probabilities, key=lambda v: probabilities[v])
    return {
        "type": "choice",
        "choice": top,
        "probabilities": [{"value": v, "probability": p} for v, p in probabilities.items()],
        "confidence": probabilities[top],
    }


def answers(
    action: str | dict,
    columns: dict[str, float] = {},  # noqa: B006
    **named: dict,
) -> Answerer:
    """Answers: the action (a name or a whole answer), column predicates by key (others 0.01),
    and any other question by name."""

    def answer(q: dict) -> dict:
        if q["name"] == "action":
            return action if isinstance(action, dict) else choice({action: 0.9, "none": 0.1})
        if q["name"].startswith("column_"):
            key = q["instructions"].split("`")[1]
            return predicate(columns.get(key, 0.01))
        if q["name"] in named:
            return named[q["name"]]
        if q["type"] == "predicate":
            return predicate(0.01)
        return choice({c["value"]: 1 / len(q["choices"]) for c in q["choices"]})

    return answer


COLUMNS = [
    Column("state", "state", 70, ("finished", "running", "crashed")),
    Column("config.lr", "num", 70, (0.0001, 0.0003, 0.001, 0.003)),
    Column("config.optimizer", "str", 60, ("adam", "sgd")),
    Column("config.qk_norm", "bool", 40, (True, False)),
    Column("summary.val/loss", "num", 65, (2.1, 2.3, 2.4)),
    Column("summary.val/acc", "num", 65, (0.5, 0.6)),
]


def choices(question: dict) -> list:
    return [c["value"] for c in question["choices"]]


def test_first_request_asks_the_action_and_a_predicate_per_candidate_column() -> None:
    fake = FakeDecisions(answers("none"))
    current = [Condition("state", "eq", "finished")]
    assert fake.run("lr below 1e-3", COLUMNS, current=current) == Action("none")
    [body] = fake.requests  # "none" sends no second request
    assert body["model"] == "gpt-6-luna"
    assert "Request: lr below 1e-3" in body["input"]
    assert "Current filters: state eq finished" in body["input"]
    action, *predicates = body["questions"]
    assert action["type"] == "choice" and action["name"] == "action"
    assert choices(action) == ["filter", "add_runs", "drop_runs", "set_best", "none"]
    assert {q["type"] for q in predicates} == {"predicate"}
    names = [q["name"] for q in body["questions"]]
    assert len(set(names)) == len(names)
    per_column = [q for q in predicates if q["name"].startswith("column_")]
    assert {q["instructions"].split("`")[1] for q in per_column} == {c.key for c in COLUMNS}
    # One more per value of the small string and state columns; true/false aren't asked.
    per_value = [q["instructions"] for q in predicates if q["name"].startswith("value_")]
    assert len(per_value) == 5
    assert "Does the request ask for runs whose `state` is `crashed`?" in per_value


def test_candidates_are_top_50_by_runs_then_word_matches_up_to_380_questions() -> None:
    columns = [Column(f"config.k{i:03}", "num", 1000 - i, (1, 2)) for i in range(400)]
    columns += [Column("config.warmup_steps", "num", 1, (1, 2))]
    columns += [Column("config.lr_warmup", "num", 2, (1, 2))]
    chosen = candidate_columns("more warmup please", columns)
    assert [c.key for c in chosen[:50]] == [f"config.k{i:03}" for i in range(50)]
    assert [c.key for c in chosen[50:]] == ["config.lr_warmup", "config.warmup_steps"]
    # "config" and "run" are in every key or request and pick nothing.
    assert len(candidate_columns("config of the run", columns)) == 50

    # Hundreds of columns sharing the request's word stop at 320 questions, keeping 60 for
    # values; small categorical columns' values fill that room and no more.
    many = [Column(f"config.loss_{i}", "num", 1, (1, 2)) for i in range(500)]
    fake = FakeDecisions(answers("none"))
    fake.run("loss", many)
    assert len(fake.requests[0]["questions"]) == 320
    many += [Column(f"config.loss_kind_{i}", "str", 2, ("a", "b", "c")) for i in range(30)]
    fake = FakeDecisions(answers("none"))
    fake.run("loss", many)
    assert len(fake.requests[0]["questions"]) == 380


def test_a_value_question_picks_its_column() -> None:
    # Asked live, "runs that failed" scored `state` low as a column but high as `state` is
    # `failed`; either passing picks the column.
    def answer(q: dict) -> dict:
        if q["name"].startswith("value_") and "`crashed`" in q["instructions"]:
            return predicate(0.95)
        return answers("filter")(q)

    fake = FakeDecisions(answer)
    action = fake.run("runs that crashed", COLUMNS)
    assert action.type == "filter"
    [(chosen, _)] = action.conditions
    assert chosen.condition == Condition("state", "eq", "crashed")
    assert chosen.probability == 0.95
    # The column's value is known, so no operator question follows.
    assert len(fake.requests) == 1


def test_a_value_every_run_shares_is_not_asked_about() -> None:
    columns = [Column("program", "str", 70, ("train.py",)), *COLUMNS]
    fake = FakeDecisions(answers("none"))
    fake.run("runs that crashed", columns)
    asked = [q["instructions"] for q in fake.requests[0]["questions"]]
    assert not any("`program` is" in q for q in asked)


def test_name_columns_are_not_filter_candidates() -> None:
    columns = [*COLUMNS, Column("name", "str", 70, ()), Column("display_name", "str", 70, ())]
    keys = {c.key for c in candidate_columns("runs named foo", columns)}
    assert "name" not in keys and "display_name" not in keys


def test_filter_picks_columns_and_applies_the_top_operator_and_value() -> None:
    fake = FakeDecisions(
        answers(
            "filter",
            {"config.lr": 0.9, "config.optimizer": 0.6, "state": 0.4},
            op_0=choice({"lt": 0.7, "lte": 0.3}),
            value_0=choice({"0.001": 0.8, "0.0003": 0.2}),
            op_1=choice({"eq": 0.96, "ne": 0.04}),
            value_1=choice({"adam": 1.0, "sgd": 0.0}),
        )
    )
    action = fake.run("adam runs with lr below 1e-3", COLUMNS)
    _, second = fake.requests
    # Only columns at 0.5 or more, most likely first; an operator and a value each.
    assert [q["name"] for q in second["questions"]] == ["op_0", "value_0", "op_1", "value_1"]
    op_lr, value_lr, op_opt, value_opt = second["questions"]
    assert "`config.lr`" in op_lr["instructions"]
    assert choices(op_lr) == ["eq", "ne", "gt", "gte", "lt", "lte", "exists", "missing", "unset"]
    # Numbers as text; the request's own 1e-3 first, then the observed values.
    assert choices(value_lr) == ["0.001", "0.0001", "0.0003", "0.003"]
    assert choices(op_opt) == ["eq", "ne", "contains", "exists", "missing", "unset"]
    assert choices(value_opt) == ["adam", "sgd"]
    assert second["input"] == fake.requests[0]["input"]

    assert action.type == "filter"
    (lr, lr_others), (opt, opt_others) = action.conditions
    assert lr == Reading(Condition("config.lr", "lt", 0.001), pytest.approx(0.56))
    # Alternatives by probability, decoded back to numbers.
    assert [(r.condition.op, r.condition.value) for r in lr_others] == [
        ("lte", 0.001),
        ("lt", 0.0003),
        ("lte", 0.0003),
    ]
    assert [r.probability for r in lr_others] == pytest.approx([0.24, 0.14, 0.06])
    assert opt.condition == Condition("config.optimizer", "eq", "adam")
    assert opt_others == ()  # under 0.05


def test_a_column_the_request_sets_no_condition_on_is_left_out() -> None:
    # Picked for being related (0.8), but its operator answer says there's no condition on it.
    fake = FakeDecisions(
        answers(
            "filter",
            {"config.lr": 0.9, "config.optimizer": 0.8},
            op_0=choice({"lt": 0.7, "lte": 0.3}),
            value_0=choice({"0.001": 1.0}),
            op_1=choice({"unset": 0.7, "eq": 0.3}),
            value_1=choice({"adam": 1.0}),
        )
    )
    action = fake.run("lr below 1e-3", COLUMNS)
    assert [c.condition.key for c, _ in action.conditions] == ["config.lr"]
    # "unset" is never offered as an alternative reading either.
    assert all(r.condition.op != "unset" for _, others in action.conditions for r in others)


def test_boolean_values_stay_booleans() -> None:
    fake = FakeDecisions(
        answers(
            "filter",
            {"config.qk_norm": 0.8},
            op_0=choice({"eq": 1.0, "ne": 0.0}),
            value_0=choice({True: 0.9, False: 0.1}),
        )
    )
    action = fake.run("with qk norm", COLUMNS)
    assert choices(fake.requests[1]["questions"][1]) == [True, False]
    assert action.conditions[0][0].condition == Condition("config.qk_norm", "eq", True)


def test_numeric_columns_past_255_values_offer_quantiles() -> None:
    column = Column("config.lr", "num", 70, tuple(i / 1000 for i in range(1000)))
    fake = FakeDecisions(answers("filter", {"config.lr": 0.9}))
    fake.run("lr below 0.5", [column])
    values = choices(fake.requests[1]["questions"][1])
    # The request's 0.5 first, then quantiles in the room left; 0.5 isn't offered twice.
    assert values[0] == "0.5" and values.count("0.5") == 1
    assert 250 <= len(values) <= 255
    assert values[1] == "0.0" and values[-1] == "0.999"
    assert values[1:] == sorted(values[1:], key=float)


def test_lone_values_and_no_values_need_no_value_question() -> None:
    columns = [Column("config.mode", "str", 70, ("train",)), Column("config.x", "json", 3, ())]
    fake = FakeDecisions(
        answers(
            "filter",
            {"config.mode": 0.9, "config.x": 0.8},
            op_0=choice({"eq": 0.9, "ne": 0.1}),
            op_1=choice({"exists": 0.7, "missing": 0.3}),
        )
    )
    action = fake.run("training mode with x", columns)
    assert [q["name"] for q in fake.requests[1]["questions"]] == ["op_0", "op_1"]
    assert choices(fake.requests[1]["questions"][1]) == ["exists", "missing", "unset"]
    assert [c.condition for c, _ in action.conditions] == [
        Condition("config.mode", "eq", "train"),
        Condition("config.x", "exists", None),
    ]


def test_nothing_reaching_one_half_is_no_action() -> None:
    # The action itself under 0.5.
    unsure = choice({"filter": 0.4, "add_runs": 0.3, "none": 0.3})
    fake = FakeDecisions(answers(unsure, {"config.lr": 0.9}))
    assert fake.run("lr", COLUMNS) == Action("none")
    assert len(fake.requests) == 1
    # No column at 0.5: no second request.
    fake = FakeDecisions(answers("filter", {"config.lr": 0.49}))
    assert fake.run("lr", COLUMNS) == Action("none")
    assert len(fake.requests) == 1
    assert Action("none").to_json() == {"type": "none", "conditions": [], "runs": [], "best": None}


def test_refusals_count_as_no() -> None:
    refusal = {"type": "refusal"}
    fake = FakeDecisions(answers(refusal))
    assert fake.run("lr", COLUMNS) == Action("none")
    # A refused column isn't picked; a refused operator drops its condition.
    fake = FakeDecisions(
        answers(
            "filter",
            {"config.lr": 0.9, "config.optimizer": 0.8},
            op_0=refusal,
            op_1=choice({"eq": 1.0, "ne": 0.0}),
            value_1=choice({"sgd": 1.0, "adam": 0.0}),
        )
    )
    action = fake.run("lr with sgd", COLUMNS)
    assert [c.condition for c, _ in action.conditions] == [
        Condition("config.optimizer", "eq", "sgd")
    ]


def test_add_runs_asks_about_runs_narrowed_to_255_by_substring() -> None:
    runs = [RunRef(f"id{i}", f"sweep-{i}") for i in range(300)]
    runs += [RunRef("a", "baseline-small"), RunRef("b", "baseline-large")]

    def answer(q: dict) -> dict:
        if q["name"] == "action":
            return choice({"add_runs": 0.8, "none": 0.2})
        if q["name"].startswith("run_"):
            return predicate(0.9 if "baseline-large" in q["instructions"] else 0.2)
        return predicate(0.0)

    fake = FakeDecisions(answer)
    action = fake.run("add the baseline large run", COLUMNS, runs)
    asked = [q["instructions"].split("`")[1] for q in fake.requests[1]["questions"]]
    assert asked == ["baseline-large", "baseline-small"]  # most words matched first
    assert all(q["type"] == "predicate" for q in fake.requests[1]["questions"])
    assert action == Action("add_runs", runs=((RunRef("b", "baseline-large"), 0.9),))
    assert action.to_json()["runs"] == [{"id": "b", "name": "baseline-large", "probability": 0.9}]

    # Without a matching name, the first 255 are offered.
    fake = FakeDecisions(answer)
    fake.run("drop those", COLUMNS, runs)
    assert len(fake.requests[1]["questions"]) == 255


def test_drop_runs_with_no_run_picked_is_no_action() -> None:
    fake = FakeDecisions(answers("drop_runs"))
    assert fake.run("drop a", COLUMNS, [RunRef("a", "a1")]) == Action("none")


def test_set_best_picks_a_numeric_column_and_direction() -> None:
    fake = FakeDecisions(
        answers(
            "set_best",
            {"summary.val/loss": 0.9, "summary.val/acc": 0.7, "config.optimizer": 0.6},
            best_order=choice({"asc": 0.85, "desc": 0.15}),
            best_key=choice({"summary.val/loss": 0.8, "summary.val/acc": 0.2}),
        )
    )
    action = fake.run("best is lowest val loss", COLUMNS)
    order, key = fake.requests[1]["questions"]
    assert choices(order) == ["asc", "desc"]
    assert choices(key) == ["summary.val/loss", "summary.val/acc"]  # numeric only
    assert action.to_json() == {
        "type": "set_best",
        "conditions": [],
        "runs": [],
        "best": {"key": "summary.val/loss", "order": "asc"},
    }


def test_to_json_matches_the_contract() -> None:
    action = Action(
        "filter",
        conditions=(
            (
                Reading(Condition("config.lr", "lt", 0.001), 0.7),
                (Reading(Condition("config.lr", "lte", 0.001), 0.2),),
            ),
        ),
    )
    assert action.to_json() == {
        "type": "filter",
        "conditions": [
            {
                "key": "config.lr",
                "op": "lt",
                "value": 0.001,
                "probability": 0.7,
                "alternatives": [
                    {"key": "config.lr", "op": "lte", "value": 0.001, "probability": 0.2}
                ],
            }
        ],
        "runs": [],
        "best": None,
    }


def test_only_request_column_names_values_and_run_names_are_sent() -> None:
    fake = FakeDecisions(answers("filter", {"config.optimizer": 0.9}))
    fake.run("sgd only", COLUMNS, [RunRef("r1", "first")])
    sent = json.dumps(fake.requests)
    assert "history" not in sent and "r1" not in sent  # run ids stay home
    assert set(fake.requests[0]) == {"model", "input", "questions"}


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429, json={"error": {"message": "Rate limit reached"}}),
        httpx.Response(500, text="oops"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"answers": []}),  # too few
        httpx.Response(200, json={"answers": [{"name": "other", "type": "predicate"}]}),
    ],
)
def test_api_failures_are_bad_gateway(response: httpx.Response) -> None:
    async def main() -> None:
        transport = httpx.MockTransport(lambda _: response)
        async with httpx.AsyncClient(transport=transport) as http:
            client = DecisionsClient("sk-test", http=http)
            await client.decide("x", [{"type": "predicate", "name": "q", "instructions": "?"}])

    with pytest.raises(AssistantError) as e:
        asyncio.run(main())
    assert isinstance(e.value, BadGateway)
    assert "sk-test" not in str(e.value)
    if response.status_code == 429:
        assert "Rate limit reached" in str(e.value)


def test_unreachable_api_is_bad_gateway() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async def main() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
            await assist("lr", COLUMNS, [], [], DecisionsClient("sk-test", http=http))

    with pytest.raises(AssistantError, match="couldn't be reached"):
        asyncio.run(main())


def test_empty_request_sends_nothing() -> None:
    fake = FakeDecisions(answers("filter"))
    assert fake.run("  ", COLUMNS) == Action("none")
    assert fake.requests == []


def test_numbers_the_request_names_are_offered_as_values_first() -> None:
    # A random search never logged lr = 1e-3, but "lr below 1e-3" means that bound (D224).
    assert assistant._numbers("lr below 1e-3 and dropout above 0.1, bs 64") == [0.001, 0.1, 64.0]
    assert assistant._numbers("sweep-rri5cl9u v2.1") == []
    column = Column("config.lr", "num", 3, (0.00091, 0.0021, 0.0004))
    labels = assistant._value_choices(column, [0.001])
    assert next(iter(labels)) == "0.001"
    assert labels["0.001"] == 0.001
    assert set(labels) == {"0.001", "0.00091", "0.0021", "0.0004"}
    # Text columns aren't offered numbers.
    text = Column("config.optimizer", "str", 2, ("adam", "sgd"))
    assert set(assistant._value_choices(text, [0.001])) == {"adam", "sgd"}


def test_run_attributes_are_always_candidates() -> None:
    # 60 config columns as common as `state` would push it out of the top 50 on the alphabet.
    columns = [Column(f"config.k{i:02}", "num", 87, (1, 2)) for i in range(60)]
    columns += [
        Column("state", "state", 87, ("finished", "failed")),
        Column("tags", "json", 40, ()),
    ]
    keys = [c.key for c in assistant.candidate_columns("runs that failed", columns)]
    assert keys[:2] == ["state", "tags"]
    assert len(keys) == 2 + 50


def test_small_categorical_columns_list_their_values_in_the_input() -> None:
    columns = [
        Column("state", "state", 87, ("finished", "failed")),
        Column("config.lr", "num", 87, (0.1, 0.2)),
        Column("config.path", "str", 87, tuple(f"/runs/{i}" for i in range(20))),
    ]
    text = assistant._input("runs that failed", [], columns)
    assert "`state` (values: finished, failed)" in text
    assert "`config.lr`," in text and "`config.lr` (" not in text  # numbers: name only
    assert "`config.path` (" not in text  # too many values: name only


# ---------------------------------------------------------------------- primary columns


def sweep_rows() -> list[RunRow]:
    """An lr × batch_size sweep with seeds, a fixed model, unique paths and W&B's own keys."""
    rows = []
    for i, (lr, bs) in enumerate([(1e-4, 256), (1e-4, 512), (1e-3, 256), (1e-3, 512)]):
        config = {
            "config.lr": lr,
            "config.batch_size": bs,
            "config.seed": i,
            "config.model": "llama-1b",
            "config.out_dir": f"/ckpt/run-{i}",
        }
        summary = {
            "summary.val/loss": 2.0 + i / 10,
            "summary.tokens_per_sec": 41000.0 + i,
            "summary._runtime": 3600.0,
            "summary.notes": "ok",  # not a metric
        }
        rows.append(RunRow(f"r{i}", f"run-{i}", i, "finished", None, config, summary))
    return rows


def primary(
    answer: Answerer, rows: Sequence[RunRow], best: str | None = "summary.val/loss"
) -> tuple[list[str], FakeDecisions]:
    fake = FakeDecisions(answer)
    columns = group_columns(rows, classify_columns(rows))
    text = primary_input("lr × batch_size", {"config.model": "llama-1b"}, 4, columns, (best, "asc"))

    async def main() -> list[str]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as http:
            client = DecisionsClient("sk-test", http=http)
            return await predict_primary(text, columns, best, client)

    return asyncio.run(main()), fake


def by_key(probabilities: dict[str, float]) -> Answerer:
    """Primary predicates by column key (others 0.01)."""
    return lambda q: predicate(probabilities.get(q["instructions"].split("`")[1], 0.01))


def test_primary_asks_about_varying_config_keys_and_metrics_in_one_request() -> None:
    keys, fake = primary(by_key({}), sweep_rows())
    (body,) = fake.requests
    asked = [q["instructions"].split("`")[1] for q in body["questions"]]
    # Seeds, a key every run shares, paths and W&B's own keys are never primary.
    assert asked == [
        "config.batch_size",
        "config.lr",
        "summary.tokens_per_sec",
        "summary.val/loss",
    ]
    assert all(q["type"] == "predicate" for q in body["questions"])
    text = body["input"]
    assert "Group of runs: lr × batch_size" in text
    assert '{"config.model":"llama-1b"}' in text
    assert "Best run by: `summary.val/loss`, lower is better" in text
    assert "- `config.lr` (config, varies between runs): 0.0001, 0.001" in text
    assert "- `summary.val/loss` (metric): from 2 to 2.3" in text
    assert "`config.seed` (config, the seed" in text
    # Same and unique keys are named, never their values.
    assert "`config.out_dir`" in text and "/ckpt/" not in text and "llama-1b`" not in text
    # No setting picked: the best-by column leads the metrics, the others follow so there's
    # something to compare on.
    assert keys == ["summary.val/loss", "summary.tokens_per_sec"]


def test_primary_keeps_likely_columns_by_probability_with_the_best_by_column() -> None:
    keys, _ = primary(
        by_key({"config.lr": 0.9, "config.batch_size": 0.6, "summary.tokens_per_sec": 0.4}),
        sweep_rows(),
    )
    # Settings first, most likely first; then the metrics, the best-by column leading.
    assert keys == ["config.lr", "config.batch_size", "summary.val/loss", "summary.tokens_per_sec"]
    keys, _ = primary(by_key({"summary.val/loss": 0.95, "config.lr": 0.7}), sweep_rows())
    assert keys == ["config.lr", "summary.val/loss", "summary.tokens_per_sec"]
    keys, _ = primary(
        by_key({"config.lr": 0.9, "summary.tokens_per_sec": 0.3}), sweep_rows(), best=None
    )
    assert keys == ["config.lr", "summary.tokens_per_sec", "summary.val/loss"]


def test_primary_caps_settings_and_metrics_apart() -> None:
    settings = [GroupColumn(f"config.k{i}", "varies", 4, (1, 2)) for i in range(6)]
    metrics = [GroupColumn(f"summary.m{i}", "metric", 4, (1.0, 2.0)) for i in range(6)]
    columns = [*settings, *metrics]
    answers = [predicate(0.9 - i / 100) for i in range(12)]
    picked = pick_primary(columns, answers, "summary.m5")
    assert picked[: assistant.PRIMARY_SETTINGS] == [f"config.k{i}" for i in range(4)]
    # Every metric is likely: the best-by column first, then the likeliest, up to the cap.
    assert picked[assistant.PRIMARY_SETTINGS :] == [
        "summary.m5",
        "summary.m0",
        "summary.m1",
        "summary.m2",
    ]
    # Refused or unlikely everywhere: no settings, the likeliest metrics up to the minimum.
    refused = [{"type": "refusal", "name": f"primary_{i}"} for i in range(12)]
    assert pick_primary(columns, refused, None) == ["summary.m0", "summary.m1", "summary.m2"]
    # A best-by config key leads the metrics instead of taking a setting's place.
    assert pick_primary(columns, answers, "config.k1")[4] == "config.k1"


def test_primary_without_the_model_is_the_varying_keys_and_the_metrics() -> None:
    rows = sweep_rows()
    columns = group_columns(rows, classify_columns(rows))
    assert primary_fallback(columns, "summary.val/loss") == [
        "config.batch_size",
        "config.lr",
        "summary.val/loss",
        "summary.tokens_per_sec",
    ]
    many = [GroupColumn(f"config.k{i}", "varies", 4, (1, 2)) for i in range(9)]
    assert primary_fallback(many, "summary.loss") == [
        *(f"config.k{i}" for i in range(assistant.PRIMARY_SETTINGS)),
        "summary.loss",
    ]


def test_primary_api_failures_are_bad_gateway() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    columns = group_columns(sweep_rows(), classify_columns(sweep_rows()))

    async def main() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DecisionsClient("sk-test", http=http)
            await predict_primary("x", columns, None, client)

    with pytest.raises(AssistantError):
        asyncio.run(main())


def test_primary_cache_is_per_group_and_signature_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(assistant, "_PRIMARY", {})
    monkeypatch.setattr(assistant, "PRIMARY_CACHE_MAX", 2)
    assistant.cache_primary(1, b"a", ["config.lr"])
    assert assistant.cached_primary(1, b"a") == ("config.lr",)
    assert assistant.cached_primary(1, b"b") is None  # its runs changed
    assistant.cache_primary(2, b"a", [])
    assistant.cache_primary(3, b"a", [])
    assert assistant.cached_primary(1, b"a") is None  # the oldest left
    assert assistant.cached_primary(3, b"a") == ()
