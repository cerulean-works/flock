import dataclasses
import json

import pytest
from click.testing import CliRunner

from flock.eval import cli
from flock.eval.compare import Breach, compare
from flock.eval.provenance import Provenance
from flock.eval.results import Results, SuiteResult, TaskResult
from flock.eval.tasks import Truncation

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
    python="3.14.0",
    created_at="2026-10-27T00:00:00+00:00",
)
ARC = TaskResult(
    task="arc_easy",
    task_hash="ad683d6e",
    primary_metric="acc_per_char",
    metrics={"acc_raw": 0.7, "acc_per_char": 0.75},
    num_docs=2376,
    limit=None,
    seconds=10.0,
    truncation=Truncation(requests=0, docs=0, max_tokens_cut=0),
)
PIQA = dataclasses.replace(ARC, task="piqa", task_hash="0f1e", metrics={"acc_raw": 0.6})
SUITE = SuiteResult("toy:rc", ["arc_easy", "piqa"], None, 0.675)
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
        metrics={"acc_raw": 0.7, "acc_per_char": 0.7500001},
        truncation=Truncation(requests=1, docs=1, max_tokens_cut=3),
    )
    piqa = dataclasses.replace(PIQA, metrics={"acc_raw": 0.6, "bpb": 1.2}, num_docs=1837)
    suite = dataclasses.replace(SUITE, score=0.6750001)
    rerun = Results(dataclasses.replace(PROVENANCE, flock_commit="def456"), [arc, piqa], [suite])

    comparison = compare(write("bank", BANK), write("new", rerun))

    assert comparison.breaches == [
        Breach("arc_easy", "metrics.acc_per_char", 0.75, 0.7500001),
        Breach("arc_easy", "truncation.docs", 0, 1),
        Breach("arc_easy", "truncation.max_tokens_cut", 0, 3),
        Breach("arc_easy", "truncation.requests", 0, 1),
        Breach("piqa", "num_docs", 2376, 1837),
        Breach("piqa", "metrics.bpb", None, 1.2),  # a metric added is a breach too
        Breach("toy:rc", "score", 0.675, 0.6750001),
    ]
    assert comparison.provenance == {"flock_commit": ("abc123", "def456")}


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
    with pytest.raises(ValueError, match="has no key 'truncation'"):
        compare(tmp_path / "old.json", bank)


def test_cli_exits_0_on_equal_scores_1_on_a_breach_and_2_on_a_refusal(write):
    bank = write("bank", BANK)
    moved = dataclasses.replace(PIQA, metrics={"acc_raw": 0.625})
    breach = write("breach", dataclasses.replace(BANK, tasks=[ARC, moved]))
    other = dataclasses.replace(BANK, provenance=dataclasses.replace(PROVENANCE, dtype="bfloat16"))
    refused = write("refused", other)

    result = CliRunner().invoke(cli.main, ["compare", str(bank), str(bank)])
    assert (result.exit_code, result.output) == (
        0,
        "2 tasks and 1 suites compared, 0 values differ\n",
    )

    result = CliRunner().invoke(cli.main, ["compare", str(bank), str(breach)])
    assert result.exit_code == 1
    assert result.output.splitlines() == [
        "piqa  metrics.acc_raw  0.6  ->  0.625  (+0.025)",
        "2 tasks and 1 suites compared, 1 values differ",
    ]

    result = CliRunner().invoke(cli.main, ["compare", str(bank), str(refused)])
    assert result.exit_code == 2
    assert "refused: different instrument: provenance.dtype is 'float32'" in result.output
