import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from flock.eval import cli
from flock.eval.compare import Breach, compare
from flock.eval.provenance import Provenance
from flock.eval.results import Results, SuiteResult, TaskResult
from flock.eval.tasks import TASKS, Dataset, Truncation

PROVENANCE = Provenance(
    model="allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf",
    flock_commit="abc123",
    command="flock-eval run",
    device="NVIDIA H200",
    dtype="float32",
    batch_tokens=16384,
    max_length=4096,
    attention="sdpa",
    torch="2.14.1",
    transformers="5.20.1",
    tokenizers="0.23.3",
    datasets="5.1.0",
    python="3.14.0",
    created_at="2026-10-27T00:00:00+00:00",
)
ARC = TaskResult(
    task="arc_easy",
    task_hash="ad683d6e",
    dataset=Dataset("allenai/ai2_arc", "0" * 40, "ARC-Easy", "test"),
    primary_metric="acc_per_char",
    metrics={"acc_raw": 0.7, "acc_per_char": 0.75},
    num_docs=2376,
    limit=None,
    seconds=10.0,
    truncation=Truncation(requests=0, docs=0, max_tokens_cut=0),
)
PIQA = dataclasses.replace(
    ARC, task="piqa", task_hash="0f1e", primary_metric="acc_raw", metrics={"acc_raw": 0.6}
)
SUITE = SuiteResult("toy:rc", "hash-rc", ["arc_easy", "piqa"], None, 0.675)
BANK = Results(PROVENANCE, [ARC, PIQA], [SUITE])


@pytest.fixture
def write(tmp_path):
    """Writes results as `<name>/results.json` and returns its path."""
    return lambda name, results: results.write(tmp_path / name)


def test_a_rerun_with_equal_scores_passes_whatever_its_timing(write):
    rerun = dataclasses.replace(BANK, tasks=[dataclasses.replace(ARC, seconds=99.0), PIQA])

    comparison = compare(write("bank", BANK), write("new", rerun))

    assert comparison.tasks == ["arc_easy", "piqa"]
    assert comparison.suites == ["toy:rc"]
    assert comparison.breaches == []
    assert comparison.provenance == {}


def test_every_changed_score_is_a_breach(write):
    arc = dataclasses.replace(
        ARC,
        metrics={"acc_raw": 0.7, "acc_per_char": 0.752},
        truncation=Truncation(requests=1, docs=1, max_tokens_cut=3),
    )
    piqa = dataclasses.replace(PIQA, metrics={"acc_raw": 0.6, "bpb": 1.2}, num_docs=1837)
    suite = dataclasses.replace(SUITE, score=0.677)
    rerun = Results(dataclasses.replace(PROVENANCE, flock_commit="def456"), [arc, piqa], [suite])

    comparison = compare(write("bank", BANK), write("new", rerun))

    assert comparison.breaches == [
        Breach("arc_easy", "metrics.acc_per_char", 0.75, 0.752),
        Breach("arc_easy", "truncation.docs", 0, 1),
        Breach("arc_easy", "truncation.max_tokens_cut", 0, 3),
        Breach("arc_easy", "truncation.requests", 0, 1),
        Breach("piqa", "num_docs", 2376, 1837),
        Breach("piqa", "metrics.bpb", None, 1.2),  # a metric added is a breach too
        Breach("toy:rc", "score", 0.675, 0.677),
    ]
    assert comparison.provenance == {"flock_commit": ("abc123", "def456")}


def test_an_accuracy_may_move_by_two_docs_and_bpb_by_a_relative_1e_4(write):
    arc = dataclasses.replace(ARC, metrics={"acc_raw": 0.7, "acc_per_char": 0.75, "bpb": 1.0})
    bank = write("bank", dataclasses.replace(BANK, tasks=[arc, PIQA]))

    def rerun(acc_per_char, bpb):
        moved = dataclasses.replace(
            arc, metrics={"acc_raw": 0.7, "acc_per_char": acc_per_char, "bpb": bpb}
        )
        return write(f"new-{acc_per_char}-{bpb}", dataclasses.replace(BANK, tasks=[moved, PIQA]))

    assert compare(bank, rerun(0.75 + 2 / 2376, 1.0 + 0.9e-4)).breaches == []
    assert compare(bank, rerun(0.75 + 3 / 2376, 1.0 + 1.1e-4)).breaches == [
        Breach("arc_easy", "metrics.acc_per_char", 0.75, 0.75 + 3 / 2376),
        Breach("arc_easy", "metrics.bpb", 1.0, 1.0 + 1.1e-4),
    ]


def test_a_suite_may_move_by_its_members_tolerances_combined_as_independent_errors(write):
    # 2.5 docs (2 flips and half a step of slack) of 2000 and of 1000 are 0.00125 and 0.0025,
    # so sqrt(0.00125**2 + 0.0025**2) / 2 = 0.0013975 for the suite, and the same for a suite
    # holding it alone. Each member is read under its primary metric.
    arc = dataclasses.replace(ARC, num_docs=2000)
    piqa = dataclasses.replace(PIQA, num_docs=1000)
    outer = SuiteResult("toy", "hash-toy", ["toy:rc"], None, 0.675)
    bank = write("bank", Results(PROVENANCE, [arc, piqa], [SUITE, outer]))

    def rerun(score):
        suites = [dataclasses.replace(SUITE, score=score), dataclasses.replace(outer, score=score)]
        return write(f"new-{score}", Results(PROVENANCE, [arc, piqa], suites))

    assert compare(bank, rerun(0.6763)).breaches == []
    assert [breach.name for breach in compare(bank, rerun(0.6765)).breaches] == ["toy:rc", "toy"]


def test_a_bpb_suite_may_move_by_a_relative_1e_4_over_root_members(write):
    # Two members with bpb 1.0 and a mean of 1.0: sqrt(2) * 1e-4 / 2, about 0.707e-4.
    arc = dataclasses.replace(ARC, metrics={"bpb": 1.0}, primary_metric="bpb")
    piqa = dataclasses.replace(PIQA, metrics={"bpb": 1.0}, primary_metric="bpb")
    suite = SuiteResult("toy:bpb", "hash-bpb", ["arc_easy", "piqa"], "bpb", 1.0)
    bank = write("bank", Results(PROVENANCE, [arc, piqa], [suite]))

    def rerun(score):
        moved = dataclasses.replace(suite, score=score)
        return write(f"new-{score}", Results(PROVENANCE, [arc, piqa], [moved]))

    assert compare(bank, rerun(1.00007)).breaches == []
    assert compare(bank, rerun(1.00008)).breaches == [Breach("toy:bpb", "score", 1.0, 1.00008)]


def test_an_infinity_must_repeat_exactly(write):
    inf = dataclasses.replace(ARC, metrics={"acc_per_char": 0.75, "bpb": float("inf")})
    bank = write("bank", dataclasses.replace(BANK, tasks=[inf, PIQA]))
    finite = dataclasses.replace(ARC, metrics={"acc_per_char": 0.75, "bpb": 1.0})

    assert compare(bank, bank).breaches == []
    assert compare(
        bank, write("new", dataclasses.replace(BANK, tasks=[finite, PIQA]))
    ).breaches == [Breach("arc_easy", "metrics.bpb", float("inf"), 1.0)]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"model": "allenai/OLMo-2-0425-1B@main"}, "provenance.model"),
        ({"dtype": "bfloat16"}, "provenance.dtype"),
        ({"batch_tokens": 8192}, "provenance.batch_tokens"),
        ({"max_length": 2048}, "provenance.max_length"),
        ({"attention": "eager"}, "provenance.attention"),
    ],
)
def test_runs_with_other_settings_are_refused(write, change, match):
    rerun = dataclasses.replace(BANK, provenance=dataclasses.replace(PROVENANCE, **change))

    with pytest.raises(ValueError, match=f"different instrument: {match}"):
        compare(write("bank", BANK), write("new", rerun))


@pytest.mark.parametrize(
    ("change", "match"), [({"task_hash": "5e2d"}, "task_hash"), ({"limit": 10}, "limit")]
)
def test_a_task_scored_on_other_requests_is_refused_before_any_score(write, change, match):
    # The changed metric would be a breach, but the refusal comes first.
    piqa = dataclasses.replace(PIQA, metrics={"acc_raw": 0.5}, **change)
    rerun = dataclasses.replace(BANK, tasks=[ARC, piqa])

    with pytest.raises(ValueError, match=f"different instrument: piqa has {match}"):
        compare(write("bank", BANK), write("new", rerun))


def test_tasks_and_suites_in_one_file_are_listed_not_compared(write):
    other = dataclasses.replace(ARC, task="hellaswag", task_hash="77aa")
    rerun = Results(PROVENANCE, [other, ARC], [])

    comparison = compare(write("bank", BANK), write("new", rerun))

    assert comparison.tasks == ["arc_easy"]
    assert comparison.breaches == []
    assert comparison.only_banked == ["piqa", "toy:rc"]
    assert comparison.only_new == ["hellaswag"]


def test_files_with_no_common_task_or_a_missing_key_are_refused(write, tmp_path):
    other = dataclasses.replace(ARC, task="hellaswag")
    bank = write("bank", BANK)

    with pytest.raises(ValueError, match="no task is in both"):
        compare(bank, write("new", dataclasses.replace(BANK, tasks=[other])))

    # A bank from an older layout that lacks a field `compare` reads.
    old = BANK.to_dict()
    del old["tasks"][0]["truncation"]
    (tmp_path / "old.json").write_text(json.dumps(old))
    with pytest.raises(ValueError, match="not a results file: KeyError\\('truncation'\\)"):
        compare(tmp_path / "old.json", bank)

    # Valid JSON of the wrong shape.
    (tmp_path / "list.json").write_text("[]")
    with pytest.raises(ValueError, match="not a results file: TypeError"):
        compare(bank, tmp_path / "list.json")


def test_cli_exits_0_on_equal_scores_1_on_a_breach_and_2_on_a_refusal(write):
    bank = write("bank", BANK)
    moved = dataclasses.replace(PIQA, metrics={"acc_raw": 0.625})
    breach = write("breach", dataclasses.replace(BANK, tasks=[ARC, moved]))
    other = dataclasses.replace(BANK, provenance=dataclasses.replace(PROVENANCE, dtype="bfloat16"))
    refused = write("refused", other)

    result = CliRunner().invoke(cli.main, ["compare", str(bank), str(bank)])
    assert (result.exit_code, result.output) == (
        0,
        "compared: tasks 2, suites 1, breaches 0\n",
    )

    result = CliRunner().invoke(cli.main, ["compare", str(bank), str(breach)])
    assert result.exit_code == 1
    assert result.output.splitlines() == [
        "piqa  metrics.acc_raw  0.6  ->  0.625  (+0.025)",
        "compared: tasks 2, suites 1, breaches 1",
    ]

    result = CliRunner().invoke(cli.main, ["compare", str(bank), str(refused)])
    assert result.exit_code == 2
    assert "refused: different instrument: provenance.dtype is 'float32'" in result.output


def test_a_suite_averaging_another_metric_is_refused(write):
    rerun = dataclasses.replace(BANK, suites=[dataclasses.replace(SUITE, metric="acc_raw")])

    with pytest.raises(ValueError, match="different instrument: toy:rc has metric"):
        compare(write("bank", BANK), write("new", rerun))


def test_a_repeated_nan_is_no_breach_but_a_new_one_is(write):
    nan = dataclasses.replace(PIQA, metrics={"acc_raw": float("nan")})
    bank = write("bank", dataclasses.replace(BANK, tasks=[ARC, nan]))

    assert compare(bank, bank).breaches == []
    [breach] = compare(write("new", BANK), bank).breaches
    assert breach[:3] == ("piqa", "metrics.acc_raw", 0.6)


def test_a_file_that_is_not_json_is_refused_by_name(write, tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text('{"provenance": ')  # a job killed while writing

    with pytest.raises(ValueError, match="broken.json is not a results file"):
        compare(write("bank", BANK), broken)


def test_a_suite_dropped_beside_a_task_of_its_name_is_listed(write):
    # Names are paired within tasks and within suites, never across them.
    bank = Results(PROVENANCE, [ARC], [dataclasses.replace(SUITE, suite="arc_easy")])

    comparison = compare(write("bank", bank), write("new", Results(PROVENANCE, [ARC], [])))

    assert comparison.only_banked == ["arc_easy"]


def test_cli_prints_causes_then_one_sided_names_then_breaches(write):
    piqa = dataclasses.replace(PIQA, metrics={"acc_raw": 0.6, "bpb": 1.25})
    suite = dataclasses.replace(SUITE, members=["arc_easy"])
    hella = dataclasses.replace(ARC, task="hellaswag", task_hash="77aa")
    provenance = dataclasses.replace(PROVENANCE, flock_commit="def456")
    new = write("new", Results(provenance, [ARC, piqa, hella], [suite]))

    result = CliRunner().invoke(cli.main, ["compare", str(write("bank", BANK)), str(new)])

    assert result.exit_code == 1
    assert result.output.splitlines() == [
        "flock_commit  abc123  ->  def456",
        "only in the new run: hellaswag",
        "piqa  metrics.bpb  None  ->  1.25",  # no difference for a missing value
        "toy:rc  members  ['arc_easy', 'piqa']  ->  ['arc_easy']",
        "compared: tasks 2, suites 1, breaches 2",
    ]


def test_the_bank_compares_equal_to_itself_rewritten_in_the_current_layout(write):
    # Offline: a change to what `Results` writes, or to what `compare` reads, that the bank can
    # no longer pass fails here rather than after a GPU rerun.
    bank = Path(__file__).parent / "regression" / "olmo-2-0425-1b-float32.json"
    data = json.loads(bank.read_text())
    tasks = []
    for task in data["tasks"]:
        fields: dict[str, Any] = dict(task)
        del fields["docs_per_second"]  # derived, not a field
        fields["truncation"] = Truncation(**fields["truncation"])
        fields["dataset"] = TASKS[task["task"]].dataset
        tasks.append(TaskResult(**fields))
    # The bank predates schema 5's suite hashes and library versions. `compare` reads neither.
    suites = [SuiteResult(suite_hash="unknown", **suite) for suite in data["suites"]]
    provenance = Provenance(**data["provenance"], tokenizers="unknown", datasets="unknown")
    rewritten = Results(provenance, tasks, suites)

    comparison = compare(bank, write("rewritten", rewritten))

    assert data["provenance"]["dtype"] == "float32"
    assert "base_easy" in comparison.suites
    assert comparison.breaches == []
    # Only the layout differs: a bank from an older schema still passes.
    assert comparison.provenance.keys() <= {"schema_version"}
