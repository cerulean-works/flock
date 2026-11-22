from __future__ import annotations

import dataclasses
import json
import math
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING

import click
import pytest
import torch
from click.testing import CliRunner
from transformers import LlamaForCausalLM

from flock.eval import run, tracking
from flock.eval.cli import main
from flock.eval.diff import diff
from flock.eval.model import ScoringModel
from flock.eval.results import Results, predictions_name, read_results, stays_inside
from flock.eval.run import EvalConfig, evaluate

from .toy import PROMPT_PATHS, PROVENANCE, task_result

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from transformers import PreTrainedTokenizerFast

    from flock.eval.provenance import Provenance


def write_run(
    folder: Path,
    tasks: dict[str, tuple[str, list[bool] | None]],
    provenance: Provenance = PROVENANCE,
    schema: int = 10,
) -> str:
    """Writes a run whose tasks are read by `acc_raw`, each as `name: (task_hash, outcomes)`, with docs `d-0`, `d-1`, ..., and returns its folder. Outcomes of `None` write no predictions file."""
    results = []
    for name, (task_hash, outcomes) in tasks.items():
        right = sum(outcomes) / len(outcomes) if outcomes else 0.0
        result = dataclasses.replace(task_result(name, {"acc_raw": right}), task_hash=task_hash)
        results.append(dataclasses.replace(result, num_docs=len(outcomes or [])))
    data = Results(provenance, results).to_dict()
    data["schema_version"] = schema
    folder.mkdir(parents=True)
    (folder / "results.json").write_text(json.dumps(data))

    (folder / "predictions").mkdir()
    for name, (_, outcomes) in tasks.items():
        if outcomes is None:
            continue
        rows = []
        for i, right in enumerate(outcomes):
            row: dict[str, Any] = {"id": f"d-{i}", "gold": 0}
            if schema >= 9:
                row["correct"] = right
            rows.append(row)
        text = "".join(json.dumps(row) + "\n" for row in rows)
        (folder / "predictions" / f"{name}.jsonl").write_text(text)
    return str(folder)


def test_two_models_split_each_tasks_docs_into_four_cells(
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
    found = diff(a, b)

    assert (found.a, found.b) == ("tiny@0", "tiny@1")
    # Every accuracy task is compared; the perplexity tasks have no per-doc outcome.
    perplexity = [task.name for task in PROMPT_PATHS if task.name.startswith("toy_text")]
    assert [task.task for task in found.tasks] == [n for n in names if n not in perplexity]
    assert found.not_compared == [(name, "not_accuracy") for name in perplexity]
    for task, result_a, result_b in zip(found.tasks, a.tasks, b.tasks, strict=False):
        cells = task.both_right + task.both_wrong + len(task.only_a) + len(task.only_b)
        assert cells == task.num_docs == result_a.num_docs
        # Each side's right docs are its accuracy over the task's docs.
        assert task.both_right + len(task.only_a) == round(
            result_a.metrics[task.metric] * task.num_docs
        )
        assert task.both_right + len(task.only_b) == round(
            result_b.metrics[task.metric] * task.num_docs
        )
    # A run against itself disagrees nowhere.
    assert all(not (task.only_a or task.only_b) for task in diff(a, a).tasks)


def test_disagreeing_docs_are_listed_by_id_in_a_order(tmp_path: Path) -> None:
    a = write_run(tmp_path / "a", {"t": ("h", [True, True, False, False, True])})
    b = write_run(tmp_path / "b", {"t": ("h", [True, False, True, False, False])})

    [task] = diff(read_results(a), read_results(b)).tasks

    assert (task.both_right, task.both_wrong) == (1, 1)
    assert (task.only_a, task.only_b) == (["d-1", "d-4"], ["d-2"])


def test_tasks_not_scored_the_same_way_are_listed_with_why(tmp_path: Path) -> None:
    a = write_run(
        tmp_path / "a",
        {
            "same": ("h", [True, False]),
            "rehashed": ("h1", [True, False]),
            "unwritten": ("h", None),
            "mine": ("h", [True]),
        },
    )
    b = write_run(
        tmp_path / "b",
        {
            "theirs": ("h", [True]),
            "unwritten": ("h", [True, False]),
            "same": ("h", [False, False]),
            "rehashed": ("h2", [True, False]),
        },
    )

    found = diff(read_results(a), read_results(b))

    assert [task.task for task in found.tasks] == ["same"]
    assert found.not_compared == [
        ("rehashed", "task_hash"),
        ("unwritten", "no_predictions"),
        ("mine", "only_a"),
        ("theirs", "only_b"),
    ]


def test_a_schema_8_run_reads_but_its_rows_have_no_outcome(tmp_path: Path) -> None:
    a = write_run(tmp_path / "a", {"t": ("h", [True, False])}, schema=8)
    b = write_run(tmp_path / "b", {"t": ("h", [True, False])})

    old = read_results(a)
    assert old.schema_version == 8
    assert diff(old, read_results(b)).not_compared == [("t", "no_outcome")]


def test_a_project_models_run_reads_back_with_its_scope(tmp_path: Path) -> None:
    scoped = dataclasses.replace(
        PROVENANCE,
        model="flock:pretrain:sweeps/lr-3e-4@12",
        model_org="acme",
        model_project="pretrain",
        model_path="sweeps/lr-3e-4",
    )
    written = read_results(write_run(tmp_path / "a", {"t": ("h", [True])}, provenance=scoped))
    assert written.schema_version == 10
    assert written.provenance == scoped


@pytest.mark.parametrize("key", ["dtype", "batch_tokens", "attention", "sdpa_backend"])
def test_runs_on_different_instruments_are_refused(tmp_path: Path, key: str) -> None:
    changed = dataclasses.replace(PROVENANCE, **{key: 1 if key == "batch_tokens" else "other"})
    a = write_run(tmp_path / "a", {"t": ("h", [True])})
    b = write_run(tmp_path / "b", {"t": ("h", [True])}, provenance=changed)

    with pytest.raises(ValueError, match=f"different instrument: provenance.{key}"):
        diff(read_results(a), read_results(b))


def test_models_of_different_lengths_and_bos_are_compared(tmp_path: Path) -> None:
    # Each belongs to the model, as OLMES adds BOS for Gemma only.
    gemma = dataclasses.replace(PROVENANCE, model="gemma@0", max_length=8192, add_bos=True)
    a = write_run(tmp_path / "a", {"t": ("h", [True])})
    b = write_run(tmp_path / "b", {"t": ("h", [False])}, provenance=gemma)

    assert diff(read_results(a), read_results(b)).b == "gemma@0"


def test_predictions_that_disagree_with_their_results_are_refused(tmp_path: Path) -> None:
    a = write_run(tmp_path / "a", {"t": ("h", [True, False])})
    b = write_run(tmp_path / "b", {"t": ("h", [True, False])})
    rows = tmp_path / "b" / "predictions" / "t.jsonl"

    # One more doc right than the accuracy says.
    rows.write_text(rows.read_text().replace("false", "true"))
    with pytest.raises(ValueError, match="2 of 2 right, but its acc_raw is 0.5 over 2 docs"):
        diff(read_results(a), read_results(b))

    rows.write_text('{"id": "d-0", "correct": true}\n{"id": "d-0", "correct": false}\n')
    with pytest.raises(ValueError, match="repeat a doc id"):
        diff(read_results(a), read_results(b))

    rows.write_text('{"id": "d-0", "correct": true}\n{"id": "d-9", "correct": false}\n')
    with pytest.raises(ValueError, match="same hash .* but other doc ids"):
        diff(read_results(a), read_results(b))
    # The same ids in another order would pair different questions.
    rows.write_text('{"id": "d-1", "correct": false}\n{"id": "d-0", "correct": true}\n')
    with pytest.raises(ValueError, match="or another order"):
        diff(read_results(a), read_results(b))

    # A lost wrong row would still match the right-doc count.
    rows.write_text('{"id": "d-0", "correct": true}\n')
    with pytest.raises(ValueError, match="1 prediction rows for 2 docs"):
        diff(read_results(a), read_results(b))

    rows.write_text('{"id": "d-0", "correct": 1}\n{"id": "d-1", "correct": false}\n')
    with pytest.raises(ValueError, match="malformed row"):
        diff(read_results(a), read_results(b))
    rows.write_text('{"correct": true}\n{"id": "d-1", "correct": false}\n')
    with pytest.raises(ValueError, match="malformed row"):
        diff(read_results(a), read_results(b))
    # Schema 9 rows always have an outcome, so one without is damaged, not legacy.
    rows.write_text('{"id": "d-0"}\n{"id": "d-1", "correct": false}\n')
    with pytest.raises(ValueError, match="malformed row"):
        diff(read_results(a), read_results(b))

    # A link could bring in rows from anywhere.
    elsewhere = tmp_path / "elsewhere.jsonl"
    elsewhere.write_text('{"id": "d-0", "correct": true}\n{"id": "d-1", "correct": false}\n')
    rows.unlink()
    rows.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="is a link"):
        diff(read_results(a), read_results(b))


def test_a_task_name_from_a_file_cannot_leave_the_predictions_folder(tmp_path: Path) -> None:
    run_a = read_results(write_run(tmp_path / "a", {"t": ("h", [True])}))

    with pytest.raises(ValueError, match="can't name a predictions file"):
        run_a.predictions("../a/predictions/t")
    # Nor can a caller of the reader itself.
    for name in ("../b/results.json", str(tmp_path / "a" / "results.json")):
        with pytest.raises(ValueError, match="isn't a file name inside"):
            run_a.read_file(name)


def test_a_name_windows_would_split_is_refused_there(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flock.eval.results.Path", PureWindowsPath)

    for name in ("..\\b\\results.json", "C:results.json", "a\\b"):
        assert not stays_inside(name)
        with pytest.raises(ValueError, match="can't name a predictions file"):
            predictions_name(name)
    assert stays_inside("predictions/t.jsonl")
    assert predictions_name("t") == "predictions/t.jsonl"


def test_an_accuracy_its_rows_cannot_produce_is_refused(tmp_path: Path) -> None:
    # One doc can't score 0.5, though 0.5 is within half a doc of either outcome.
    a = write_run(tmp_path / "a", {"t": ("h", [True])})
    b = write_run(tmp_path / "b", {"t": ("h", [True])})
    results = tmp_path / "b" / "results.json"
    data = json.loads(results.read_text())
    data["tasks"][0]["metrics"]["acc_raw"] = 0.5
    results.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="1 of 1 right, but its acc_raw is 0.5"):
        diff(read_results(a), read_results(b))


def test_a_task_with_no_rows_is_refused(tmp_path: Path) -> None:
    a = write_run(tmp_path / "a", {"t": ("h", [])})
    b = write_run(tmp_path / "b", {"t": ("h", [])})

    with pytest.raises(ValueError, match="0 prediction rows for 0 docs"):
        diff(read_results(a), read_results(b))


def test_a_null_accuracy_cannot_vouch_for_its_rows(tmp_path: Path) -> None:
    a = write_run(tmp_path / "a", {"t": ("h", [True, False])})
    b = write_run(tmp_path / "b", {"t": ("h", [True, False])})
    results = tmp_path / "b" / "results.json"
    data = json.loads(results.read_text())
    data["tasks"][0]["metrics"]["acc_raw"] = None
    results.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="but its acc_raw is nan"):
        diff(read_results(a), read_results(b))


def test_reading_a_folder_refuses_what_is_not_a_results_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="has no results.json"):
        read_results(str(tmp_path))

    a = write_run(tmp_path / "a", {"t": ("h", [True])}, schema=7)
    with pytest.raises(ValueError, match="schema 7; only 8 to 10 are read"):
        read_results(a)

    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "results.json").write_text('{"schema_version": 9}')
    with pytest.raises(ValueError, match="is not a results file: KeyError"):
        read_results(str(tmp_path / "b"))

    c = write_run(tmp_path / "c", {"t": ("h", [True])})
    data = json.loads((tmp_path / "c" / "results.json").read_text())
    data["tasks"][0]["metrics"]["acc_raw"] = "1.0"
    (tmp_path / "c" / "results.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="t: metric acc_raw is '1.0', not a number"):
        read_results(c)

    data["tasks"][0]["metrics"]["acc_raw"] = 1.0
    data["tasks"][0]["task"] = ["t"]
    (tmp_path / "c" / "results.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="a task's task is \\['t'\\], not str"):
        read_results(c)

    data["tasks"][0]["task"] = "t"
    for damaged in (
        {**data, "tasks": {}},
        {**data, "tasks": [{**data["tasks"][0], "num_docs": True}]},
    ):
        (tmp_path / "c" / "results.json").write_text(json.dumps(damaged))
        with pytest.raises(ValueError, match="must be arrays|num_docs is True"):
            read_results(c)

    (tmp_path / "c" / "results.json").write_text(json.dumps({**data, "tasks": data["tasks"] * 2}))
    with pytest.raises(ValueError, match="a task name repeats: \\['t'\\]"):
        read_results(c)

    for key, value in [
        ("model", None),
        ("dtype", None),
        ("attention", None),
        ("batch_tokens", None),
        ("sdpa_backend", False),
    ]:
        damaged = {**data, "provenance": {**data["provenance"], key: value}}
        (tmp_path / "c" / "results.json").write_text(json.dumps(damaged))
        with pytest.raises(ValueError, match=f"provenance.{key} is {value}"):
            read_results(c)

    del data["tasks"][0]["dataset"]["private"]
    (tmp_path / "c" / "results.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="t: dataset.private is None, not bool"):
        read_results(c)


def test_a_null_metric_reads_back_as_nan(tmp_path: Path) -> None:
    results = Results(PROVENANCE, [task_result("t", {"bpb": math.inf})])
    results.write(tmp_path)

    [task] = read_results(str(tmp_path)).tasks

    assert math.isnan(task.metrics["bpb"])


def test_the_diff_command_prints_a_table_or_the_json_contract(tmp_path: Path) -> None:
    a = write_run(tmp_path / "a", {"t": ("h", [True, False]), "u": ("h1", [True])})
    b = write_run(tmp_path / "b", {"t": ("h", [False, True]), "u": ("h2", [True])})

    table = CliRunner().invoke(main, ["diff", a, b])
    assert table.exit_code == 0, table.output
    assert table.output.splitlines() == [
        "a  tiny@0",
        "b  tiny@0",
        "task  docs  both right  both wrong  only a  only b",
        "t        2           0           0       1       1",
        "not compared: u (task_hash)",
    ]

    as_json = CliRunner().invoke(main, ["diff", a, b, "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output) == {
        "a": "tiny@0",
        "b": "tiny@0",
        "tasks": [
            {
                "task": "t",
                "task_hash": "h",
                "metric": "acc_raw",
                "num_docs": 2,
                "both_right": 0,
                "both_wrong": 0,
                "only_a": ["d-0"],
                "only_b": ["d-1"],
            }
        ],
        "not_compared": [["u", "task_hash"]],
        "schema_version": 1,
    }

    refused = CliRunner().invoke(main, ["diff", a, str(tmp_path / "missing")])
    assert refused.exit_code == 2
    assert "refused:" in refused.output


def test_an_unreachable_tracker_exits_2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    a = write_run(tmp_path / "a", {"t": ("h", [True])})

    def unreachable(run: str, name: str, credential: object = None) -> None:
        raise click.ClickException("Couldn't reach https://flock.test: timed out")

    monkeypatch.setattr(tracking, "read_run_file", unreachable)
    refused = CliRunner().invoke(main, ["diff", a, "run:acme/model-evals/run-1"])

    assert refused.exit_code == 2
    assert "refused: Couldn't reach" in refused.output
