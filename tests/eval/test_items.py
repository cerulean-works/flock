from __future__ import annotations

import dataclasses
import json
import math
from typing import TYPE_CHECKING

import pytest
import torch
from click.testing import CliRunner
from transformers import LlamaForCausalLM

from flock.eval import run
from flock.eval.cli import main
from flock.eval.diff import diff
from flock.eval.items import CELLS, items
from flock.eval.model import ScoringModel
from flock.eval.results import read_results
from flock.eval.run import EvalConfig, evaluate
from flock.eval.tasks import MultipleChoiceTask

from .toy import PROMPT_PATHS, PROVENANCE, write_run

if TYPE_CHECKING:
    from pathlib import Path

    from transformers import PreTrainedTokenizerFast


def test_two_models_items_carry_the_scored_text_and_follow_the_diffs_cells(
    monkeypatch: pytest.MonkeyPatch,
    lm: LlamaForCausalLM,
    tokenizer: PreTrainedTokenizerFast,
    tmp_path: Path,
) -> None:
    torch.manual_seed(1)
    other = LlamaForCausalLM(lm.config).eval()
    scorers = {
        "tiny@0": ScoringModel(lm, tokenizer, name="tiny@0"),
        "tiny@1": ScoringModel(other, tokenizer, name="tiny@1"),
    }
    monkeypatch.setattr(ScoringModel, "from_pretrained", lambda name, **kwargs: scorers[name])
    for task in PROMPT_PATHS:
        monkeypatch.setitem(run.TASKS, task.name, task)
    names = tuple(task.name for task in PROMPT_PATHS)
    for name in scorers:
        evaluate(EvalConfig(model=name, tasks=names, out=tmp_path / name))
    a, b = read_results(str(tmp_path / "tiny@0")), read_results(str(tmp_path / "tiny@1"))

    by_name = {task.name: task for task in PROMPT_PATHS}
    for found in diff(a, b).tasks:
        task = by_name[found.task]
        assert isinstance(task, MultipleChoiceTask)
        built = task.requests(*task.load())
        every = items([a], task.name, "all")
        assert [item.id for item in every] == [doc.id for doc in built.docs]

        # The text rebuilds each scored request: the prompt the header holds, the examples the
        # doc was shown, then its question (or its choice's own context) and the choice.
        header = json.loads(
            (tmp_path / "tiny@0/predictions" / f"{task.name}.jsonl").open().readline()
        )
        shots = [shot["text"] for shot in header["prompt"]["shots"]]
        conditional = iter(built.requests)
        for item, shown in zip(every, built.shown, strict=True):
            head = header["prompt"]["description"] + "".join(f"{shots[i]}\n\n" for i in shown)
            for i, choice in enumerate(item.choices):
                context = item.question if item.contexts is None else item.contexts[i]
                request = next(conditional)
                assert (head + context, choice) == (request.context, request.continuation)

            # Each run's pick explains its outcome, its probabilities are a distribution, and
            # its share ranks the choices as the metric does.
            for answer in item.runs:
                assert math.isclose(sum(answer.prob), 1.0)
                if answer.pick is None:  # acc_greedy ranks no choices
                    assert answer.share is None
                    continue
                assert (answer.pick == item.gold) == answer.correct
                assert answer.share is not None
                assert answer.share[answer.pick] == max(answer.share)
                if item.metric == "acc_raw":
                    assert answer.share == pytest.approx(answer.prob)

        # Two runs: each cell holds exactly the diff's docs of it, in doc order.
        cells = {cell: [item.id for item in items([a, b], task.name, cell)] for cell in CELLS}
        assert cells["only_a"] == found.only_a
        assert cells["only_b"] == found.only_b
        assert len(cells["both_right"]) == found.both_right
        assert len(cells["both_wrong"]) == found.both_wrong
        for item in items([a, b], task.name, "only_b"):
            assert [answer.model for answer in item.runs] == ["tiny@0", "tiny@1"]
            assert [answer.correct for answer in item.runs] == [False, True]


def test_one_runs_docs_are_kept_by_outcome_cut_and_sampled(tmp_path: Path) -> None:
    outcomes = [True, False, False, True, False, False, True, False]
    run_a = read_results(write_run(tmp_path / "a", {"t": ("h", outcomes)}))

    assert [item.id for item in items([run_a], "t", "wrong")] == [
        "d-1", "d-2", "d-4", "d-5", "d-7"
    ]  # fmt: skip
    assert [item.id for item in items([run_a], "t", "right")] == ["d-0", "d-3", "d-6"]
    assert len(items([run_a], "t", "all")) == 8
    assert [item.id for item in items([run_a], "t", "wrong", limit=2)] == ["d-1", "d-2"]

    # A seeded sample is the same every time, in doc order, and of the kept docs only.
    sample = [item.id for item in items([run_a], "t", "wrong", limit=3, seed=7)]
    assert sample == [item.id for item in items([run_a], "t", "wrong", limit=3, seed=7)]
    assert sample == sorted(sample, key=lambda doc: int(doc[2:]))
    assert len(sample) == 3 and set(sample) <= {"d-1", "d-2", "d-4", "d-5", "d-7"}
    # A limit past the kept docs keeps them all.
    assert len(items([run_a], "t", "right", limit=10, seed=7)) == 3

    item = items([run_a], "t", "right", limit=1)[0]
    assert item.to_dict() == {
        "task": "t",
        "metric": "acc_raw",
        "id": "d-0",
        "question": "Question: t 0?\nAnswer:",
        "contexts": None,
        "choices": [" yes", " no"],
        "gold": 0,
        "runs": [
            {
                "model": PROVENANCE.model,
                "pick": 0,
                "correct": True,
                "logprob": [-1.0, -2.0],
                "prob": pytest.approx([1 / (1 + math.exp(-1)), 1 / (1 + math.exp(1))]),
                "share": pytest.approx([1 / (1 + math.exp(-1)), 1 / (1 + math.exp(1))]),
            }
        ],
    }


def test_runs_without_text_or_outside_the_diffs_rules_are_refused(tmp_path: Path) -> None:
    a = read_results(write_run(tmp_path / "a", {"t": ("h", [True, False]), "u": ("h", None)}))
    old = read_results(write_run(tmp_path / "old", {"t": ("h", [True, False])}, schema=10))
    rehashed = read_results(write_run(tmp_path / "rehashed", {"t": ("other", [True, False])}))
    other = dataclasses.replace(PROVENANCE, dtype="bfloat16")
    elsewhere = read_results(write_run(tmp_path / "fp32", {"t": ("h", [True, False])}, other))

    with pytest.raises(ValueError, match="schema 10, whose predictions hold no text: score"):
        items([old], "t", "wrong")
    with pytest.raises(ValueError, match="has no task v"):
        items([a], "v", "wrong")
    with pytest.raises(ValueError, match="has no predictions for u"):
        items([a], "u", "wrong")
    with pytest.raises(ValueError, match="t was scored differently"):
        items([a, rehashed], "t", "only_a")
    with pytest.raises(ValueError, match="different instrument: provenance.dtype"):
        items([a, elsewhere], "t", "only_a")
    with pytest.raises(ValueError, match="keep one of only_a"):
        items([a, a], "t", "wrong")
    with pytest.raises(ValueError, match="keep one of wrong"):
        items([a], "t", "only_a")
    with pytest.raises(ValueError, match="one run or two, not 3"):
        items([a, a, a], "t", "both_right")


def test_damaged_or_mixed_up_rows_are_refused(tmp_path: Path) -> None:
    a = read_results(write_run(tmp_path / "a", {"t": ("h", [True, False])}))
    for change, match in [
        ({"choices": [" yes", " maybe"]}, "differing text"),
        ({"pick": 1}, "is damaged"),  # a pick that doesn't explain `correct`
        ({"logprob": [-1.0]}, "is damaged"),
    ]:
        folder = tmp_path / f"b-{len(match)}-{next(iter(change))}"
        b_path = write_run(folder, {"t": ("h", [True, False])})
        lines = (folder / "predictions/t.jsonl").read_text().splitlines()
        row = json.loads(lines[1]) | change
        lines[1] = json.dumps(row)
        (folder / "predictions/t.jsonl").write_text("\n".join(lines) + "\n")
        with pytest.raises(ValueError, match=match):
            items([a, read_results(b_path)], "t", "both_right")


def test_the_items_command_prints_text_or_json_lines(tmp_path: Path) -> None:
    a = write_run(tmp_path / "a", {"t": ("h", [True, False, False])})
    b = write_run(tmp_path / "b", {"t": ("h", [False, True, False])})
    runner = CliRunner()

    shown = runner.invoke(main, ["items", a, "--task", "t"])
    assert shown.exit_code == 0, shown.output
    assert shown.output.splitlines()[:7] == [
        "t  d-1  acc_raw",
        f"a  {PROVENANCE.model}  wrong, picks 1",
        "Question: t 1?",
        "Answer:",
        "       p a    s a  ",
        "*  0   0.269  0.269    yes",
        "   1   0.731  0.731 <  no",
    ]
    assert shown.output.count("acc_raw") == 2  # the two wrong docs

    pair = runner.invoke(main, ["items", a, b, "--task", "t", "--cell", "only_b", "--json"])
    assert pair.exit_code == 0, pair.output
    lines = [json.loads(line) for line in pair.output.splitlines()]
    assert [line["id"] for line in lines] == ["d-1"]
    assert [answer["correct"] for answer in lines[0]["runs"]] == [False, True]

    for args, message in [
        ([a, b, "--task", "t"], "two runs need --cell"),
        ([a, b, "--task", "t", "--only", "all", "--cell", "only_a"], "--only reads one run"),
        ([a, "--task", "t", "--cell", "only_a"], "--cell compares two runs"),
        ([a, b, a, "--task", "t", "--cell", "only_a"], "give one run or two"),
    ]:
        refused = runner.invoke(main, ["items", *args])
        assert refused.exit_code == 2 and message in refused.output

    old = write_run(tmp_path / "old", {"t": ("h", [True])}, schema=10)
    refused = runner.invoke(main, ["items", old, "--task", "t"])
    assert refused.exit_code == 2
    assert "refused:" in refused.output and "score the model again" in refused.output
