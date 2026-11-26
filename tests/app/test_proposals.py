"""Proposed groups (D229): what the model reads, the Responses API request and its errors,
and how a reply is checked and completed (runs, axes, values, grid, best, seeds). The API is
an httpx mock; filters are matched by a fake."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.api.src.core import proposals
from app.api.src.core.assistant import AssistantError
from app.api.src.core.errors import BadGateway
from app.api.src.core.group_analysis import RunRow, Suggestion
from app.api.src.core.proposals import (
    Message,
    ResponsesClient,
    SavedGroup,
    Shown,
    build_input,
    context,
    describe,
    finish,
    grid,
    propose,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.api.src.core.proposals import Context, Turn


def run(
    id: str,
    config: dict[str, Any],
    loss: float | None = None,
    state: str = "finished",
    group: str | None = None,
    name: str | None = None,
    at: float = 0.0,
) -> RunRow:
    return RunRow(
        id=id,
        name=name or id,
        created_at=1_790_000_000.0 + at,
        state=state,
        group=group,
        config={f"config.{k}": v for k, v in config.items()},
        summary={} if loss is None else {"summary.loss": loss},
    )


# An lr × batch_size sweep with a repeated configuration, an optimizer toggle, a debug run.
RUNS = [
    run("a1", {"lr": 0.1, "bs": 32, "opt": "adam", "seed": 1}, 0.5, group="sweep", at=1),
    run("a2", {"lr": 0.1, "bs": 32, "opt": "adam", "seed": 2}, 0.7, group="sweep", at=2),
    run("a3", {"lr": 0.01, "bs": 32, "opt": "adam", "seed": 1}, 0.3, group="sweep", at=3),
    run("a4", {"lr": 0.1, "bs": 64, "opt": "adam", "seed": 1}, 0.4, group="sweep", at=4),
    run("b1", {"lr": 0.01, "bs": 32, "opt": "sgd", "seed": 1}, 0.9, name="sgd-run", at=5),
    run("x1", {"lr": 0.5, "bs": 8, "opt": "adam", "seed": 1}, None, state="crashed", at=6),
]


def ctx(**kw: Any) -> Context:
    kw.setdefault("metrics", ["summary.loss"])
    return context("flock/p", RUNS, ("summary.loss", "asc"), **kw)


def matcher(table: dict[str, set[str] | None]) -> Callable:
    """A fake filter matcher: the ids each filter (by its JSON) matches; None for refused."""

    async def match(filter: dict[str, Any]) -> set[str] | None:
        return table[json.dumps(filter, sort_keys=True)]

    return match


def key(filter: dict[str, Any]) -> str:
    return json.dumps(filter, sort_keys=True)


def proposal(**fields: Any) -> dict[str, Any]:
    base = {
        "id": "p",
        "name": "lr × bs",
        "why": "Compares lr and batch size.",
        "filter": None,
        "runs": [],
        "axes": [],
        "best_key": None,
        "best_order": None,
    }
    return base | fields


def finished(reply: dict[str, Any], match: Callable | None = None, **kw: Any) -> Turn:
    return asyncio.run(finish(ctx(**kw), reply, match or matcher({})))


# ---------------------------------------------------------------------- what the model reads


def test_the_project_is_described_with_its_varying_keys_groups_and_runs() -> None:
    saved = [SavedGroup("old", {"group": "sweep"}, ("a1", "a2"))]
    hint = Suggestion(b"x", "config_diff", "lr: 0.01, 0.1", None, ("a1", "a3", "b1"))
    text = describe(ctx(saved=saved, suggestions=[hint]))
    assert "flock/p, 6 live runs (5 finished, 1 crashed)" in text
    assert "Best by (the project's setting): summary.loss, lower is better." in text
    assert "- config.lr: 0.01 (2), 0.1 (3), 0.5 (1)" in text
    assert "- config.opt: adam (5), sgd (1)" in text
    assert "Seed keys (runs repeat a configuration with other seeds): config.seed" in text
    assert "W&B groups (runs): sweep (4)" in text
    assert '- S1 "old": 2 runs, filter {"group":"sweep"}' in text
    assert '- "lr: 0.01, 0.1": 3 runs, runs a1, a3, b1' in text
    lines = text.splitlines()
    header = next(line for line in lines if line.startswith("id | "))
    # Names only when some run has one; the varying keys without "config."; the metric.
    assert header == (
        "id | name | state | W&B group | created | saved in | bs | lr | opt | summary.loss"
    )
    assert "a1 |  | finished | sweep | 2026-09-21 14:13 | S1 | 32 | 0.1 | adam | 0.5" in lines
    assert "b1 | sgd-run | finished | — | 2026-09-21 14:13 | — | 32 | 0.01 | sgd | 0.9" in lines
    assert "x1 |  | crashed | — | 2026-09-21 14:13 | — | 8 | 0.5 | adam | —" in lines


def test_a_big_project_is_summarized_and_asked_for_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        run(f"r{i}", {"lr": i * 1e-4, "opt": ["adam", "sgd"][i % 2], "path": f"/x/{i}"}, at=i)
        for i in range(300)
    ]
    monkeypatch.setattr(proposals, "TABLE_BUDGET", 4000)
    text = describe(context("flock/big", rows, (None, None)))
    assert "Best by: not set for this project." in text
    assert "summarized to fit: use filters, not hand lists" in text
    # A random search's samples: their range, not every value; and out of the table first.
    assert "- config.lr: 300 numbers from 0 to 0.0299" in text
    assert "Different in every run (paths, ids; not shown): path" in text
    table = text.split("Runs, oldest first")[1].splitlines()
    assert table[1] == "runs | ids (first 3) | state | W&B group | saved in | opt"
    assert table[2] == "150 | r0, r2, r4 | finished | — | — | adam"
    assert len(text) < 4000 + 2000


def test_the_input_is_the_project_then_the_conversation_with_the_proposals_on_screen() -> None:
    first = build_input(ctx(), [], None)
    assert [item["role"] for item in first] == ["developer", "user"]
    assert first[1]["content"] == "Propose how to group this project's runs."

    shown = [Shown("sweep", "lr × bs", "Compares lr.", ("a1", "a3"), None, ("config.lr",)),
             Shown("toggle", "opt", "", ("a1", "a2", "b1"), {"group": "sweep"})]  # fmt: skip
    messages = [
        Message("assistant", "Here are two groups."),
        Message("user", "merge those two"),
    ]
    items = build_input(ctx(), messages, shown)
    assert [item["role"] for item in items] == ["developer", "assistant", "developer", "user"]
    assert items[3]["content"] == "merge those two"
    on_screen = items[2]["content"].splitlines()
    assert on_screen[0] == "Proposals on screen now:"
    assert json.loads(on_screen[1]) == {
        "id": "sweep", "name": "lr × bs", "why": "Compares lr.", "runs": ["a1", "a3"],
        "axes": ["config.lr"],
    }  # fmt: skip
    # A filtered proposal is sent as its filter and a count, not its runs.
    assert json.loads(on_screen[2])["filter"] == '{"group":"sweep"}'
    assert json.loads(on_screen[2])["run_count"] == 3


# ---------------------------------------------------------------------- the Responses API


class FakeResponses:
    """The Responses API: records each request body and answers with ``reply``."""

    def __init__(self, reply: dict[str, Any] | httpx.Response) -> None:
        self.reply = reply
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer sk-test"
        self.requests.append(json.loads(request.content))
        if isinstance(self.reply, httpx.Response):
            return self.reply
        return httpx.Response(200, json=self.reply)

    def turn(self, messages: list[Message] = (), match: Callable | None = None) -> Turn:  # type: ignore[assignment]
        async def main() -> Turn:
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.handler)) as http:
                client = ResponsesClient("sk-test", model="gpt-test", http=http)
                return await propose(ctx(), messages, None, match or matcher({}), client)

        return asyncio.run(main())


def completed(content: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "object": "response",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "role": "assistant", "content": content},
        ],
    }


def text(reply: dict[str, Any]) -> dict[str, Any]:
    return completed([{"type": "output_text", "text": json.dumps(reply)}])


def test_the_request_is_a_strict_json_schema_response() -> None:
    reply = {"message": "One sweep.", "proposals": [proposal(runs=["a1", "a3", "a4"])]}
    fake = FakeResponses(text(reply))
    turn = fake.turn([Message("user", "group the sweep")])
    (body,) = fake.requests
    assert body["model"] == "gpt-test"
    assert body["store"] is False
    assert body["instructions"] == proposals.INSTRUCTIONS
    assert body["input"][-1] == {"role": "user", "content": "group the sweep"}
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert fmt["name"] == "group_proposals" and fmt["schema"] == proposals.SCHEMA
    assert turn.message == "One sweep."
    assert [p["runs"] for p in turn.proposals] == [["a1", "a3", "a4"]]


def test_the_strict_schema_requires_every_field_and_forbids_others() -> None:
    def check(schema: dict[str, Any]) -> None:
        if schema.get("type") == "object":
            assert schema["additionalProperties"] is False
            assert set(schema["required"]) == set(schema["properties"])
            for child in schema["properties"].values():
                check(child)
        elif schema.get("type") == "array":
            check(schema["items"])

    check(proposals.SCHEMA)


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (httpx.Response(401, json={"error": {"message": "bad key"}}), "refused the request (401)"),
        (httpx.Response(200, content=b"<html>"), "unreadable answer"),
        (
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
             "output": []},
            "didn't finish its answer (max_output_tokens)",
        ),
        (completed([{"type": "refusal", "refusal": "no"}]), "the model refused: no"),
        (completed([{"type": "output_text", "text": "{not json"}]), "isn't the JSON asked for"),
    ],
)  # fmt: skip
def test_failures_are_bad_gateways(reply: Any, error: str) -> None:
    with pytest.raises(AssistantError, match=error.replace("(", r"\(").replace(")", r"\)")):
        FakeResponses(reply).turn()
    assert issubclass(AssistantError, BadGateway)


# ---------------------------------------------------------------------- checking a reply


def test_unknown_runs_are_dropped_and_display_names_resolved() -> None:
    turn = finished(
        {"message": "ok", "proposals": [proposal(runs=["a1", "nope", "sgd-run", "a1"])]}
    )
    assert turn.proposals[0]["runs"] == ["a1", "b1"]  # creation order, once each


def test_a_filters_runs_are_what_it_matches() -> None:
    sweep = {"group": "sweep"}
    match = matcher({key(sweep): {"a1", "a2", "a3", "a4", "gone"}})
    reply = {"message": "", "proposals": [proposal(filter=json.dumps(sweep), runs=["b1"])]}
    (found,) = finished(reply, match).proposals
    assert found["filter"] == sweep
    assert found["runs"] == ["a1", "a2", "a3", "a4"]  # the model's list is ignored
    assert found["create"] == {
        "name": "lr × bs",
        "runs": ["a1", "a2", "a3", "a4"],
        "filter": sweep,
        "best_key": None,
        "best_order": None,
    }


def test_a_refused_filter_falls_back_to_the_hand_list() -> None:
    bad = {"config.lr": {"$bogus": 1}}
    reply = {"message": "", "proposals": [proposal(filter=json.dumps(bad), runs=["a1", "a3"])]}
    (found,) = finished(reply, matcher({key(bad): None})).proposals
    assert found["filter"] is None and found["runs"] == ["a1", "a3"]
    # Text that isn't a JSON object is no filter either.
    reply = {"message": "", "proposals": [proposal(filter="lr > 1", runs=["a1", "a3"])]}
    assert finished(reply).proposals[0]["runs"] == ["a1", "a3"]


def test_empty_one_run_and_repeated_groups_are_dropped() -> None:
    nothing = {"state": "running"}
    reply = {
        "message": "",
        "proposals": [
            proposal(id="empty", filter=json.dumps(nothing)),
            proposal(id="one", runs=["a1", "nope"]),
            proposal(id="saved", runs=["a2", "a1"]),
            proposal(id="kept", name="x", runs=["a1", "a3"]),
            proposal(id="again", name="y", runs=["a3", "a1"]),
        ],
    }
    saved = [SavedGroup("old", {}, ("a1", "a2"))]
    turn = finished(reply, matcher({key(nothing): set()}), saved=saved)
    assert [p["id"] for p in turn.proposals] == ["kept"]
    # Ungrouped: in no proposal and no saved group.
    assert turn.ungrouped == 3 and turn.runs == 6


def test_ids_are_unique_slugs_and_names_unique_and_never_a_saved_groups() -> None:
    reply = {
        "message": "",
        "proposals": [
            proposal(id="LR Sweep!", name="old", runs=["a1", "a3"]),
            proposal(id="lr-sweep", name="old", runs=["a1", "a4"]),
            proposal(id="", name="\x00", runs=["a2", "a4"]),
        ],
    }
    turn = finished(reply, saved=[SavedGroup("Old", {}, ("b1", "x1"))])
    assert [p["id"] for p in turn.proposals] == ["lr-sweep", "lr-sweep-2", "bs-32-64"]
    # An unusable name takes the D225 template.
    assert [p["name"] for p in turn.proposals] == ["old (2)", "old (3)", "bs: 32, 64"]


def test_axes_values_best_and_seeds_are_computed_from_the_runs() -> None:
    reply = {
        "message": "",
        "proposals": [
            # The model names an axis without the prefix, one that doesn't vary, and the seed.
            proposal(runs=["a1", "a2", "a3", "a4"], axes=["bs", "config.opt", "config.seed"]),
            proposal(id="toggle", name="opt", runs=["a3", "b1"], best_key="loss",
                     best_order="desc"),
        ],
    }  # fmt: skip
    sweep, toggle = finished(reply).proposals
    assert sweep["axes"] == ["config.bs", "config.lr"]  # the model's order first
    assert sweep["values"] == {"config.bs": [32, 64], "config.lr": [0.01, 0.1]}
    assert sweep["repeat_key"] == "config.seed"  # a1 and a2 repeat lr 0.1, bs 32
    # Best is the best configuration: a3 (0.3), not the mean of a1 and a2 (0.6).
    assert sweep["best"] == {"run": "a3", "name": "a3", "value": 0.3, "mean": 0.3, "std": None,
                             "n": 1}  # fmt: skip
    assert (sweep["best_key"], sweep["best_order"]) == ("summary.loss", "asc")
    # The model may rank by another direction or metric when it exists in the runs.
    assert toggle["repeat_key"] is None
    assert (toggle["best_key"], toggle["best_order"]) == ("summary.loss", "desc")
    assert toggle["best"]["run"] == "b1"
    assert toggle["create"]["best_key"] == "summary.loss"
    assert toggle["create"]["best_order"] == "desc"


def test_a_metric_the_runs_dont_have_isnt_taken() -> None:
    reply = {"message": "", "proposals": [proposal(runs=["a1", "a3"], best_key="summary.acc",
                                                   best_order="desc")]}  # fmt: skip
    (found,) = finished(reply).proposals
    assert (found["best_key"], found["best_order"]) == ("summary.loss", "asc")
    assert found["create"]["best_key"] is None


def test_the_grid_covers_the_sweep() -> None:
    members = RUNS[:4]
    kinds = {"config.lr": "varies", "config.bs": "varies", "config.seed": "repeat"}
    found = grid(members, ["config.lr", "config.bs"], kinds, ("summary.loss", "asc"))
    assert found is not None
    assert (found["x"], found["y"]) == ("config.lr", "config.bs")
    assert (found["xs"], found["ys"]) == ([0.01, 0.1], [32, 64])
    assert found["cells"] == [
        {"x": 0.01, "y": 32, "runs": 1, "best": 0.3, "run": "a3"},
        # Two seeds of one configuration: their mean, and the better run.
        {"x": 0.1, "y": 32, "runs": 2, "best": pytest.approx(0.6), "run": "a1"},
        {"x": 0.01, "y": 64, "runs": 0, "best": None, "run": None},  # not run
        {"x": 0.1, "y": 64, "runs": 1, "best": 0.4, "run": "a4"},
    ]
    one = grid(members, ["config.bs"], kinds, (None, None))
    assert one is not None and one["y"] is None and one["ys"] is None
    assert [(c["x"], c["runs"], c["best"]) for c in one["cells"]] == [(32, 3, None), (64, 1, None)]
    # No grid for three axes, or for a random search's samples.
    assert grid(members, ["config.lr", "config.bs", "config.opt"], kinds, (None, None)) is None
    many = [run(f"r{i}", {"lr": i}) for i in range(proposals.GRID_MAX + 1)]
    assert grid(many, ["config.lr"], {"config.lr": "varies"}, (None, None)) is None


def test_a_missing_value_is_its_own_column() -> None:
    members = [run("a", {"lr": 1}), run("b", {"lr": 2}), run("c", {})]
    found = grid(members, ["config.lr"], {"config.lr": "varies"}, (None, None))
    assert found is not None and found["xs"] == [1, 2, None]
    assert found["cells"][2]["runs"] == 1
