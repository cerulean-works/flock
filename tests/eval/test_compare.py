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
from flock.eval.tasks import Dataset, Truncation

PROVENANCE = Provenance(
    model="allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf",
    flock_commit="abc123",
    command="flock eval run",
    device="NVIDIA H200",
    dtype="float32",
    batch_tokens=16384,
    max_length=4096,
    add_bos=False,
    attention="sdpa",
    sdpa_backend="EFFICIENT_ATTENTION",
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

    # The file holds the infinity as null, which repeats exactly and breaches against a number.
    assert compare(bank, bank).breaches == []
    assert compare(
        bank, write("new", dataclasses.replace(BANK, tasks=[finite, PIQA]))
    ).breaches == [Breach("arc_easy", "metrics.bpb", None, 1.0)]


def test_a_task_with_no_docs_must_repeat_its_metrics_exactly(write):
    # No docs means no accuracy to move, and no division by zero.
    empty = dataclasses.replace(ARC, num_docs=0)
    bank = write("bank", dataclasses.replace(BANK, tasks=[empty, PIQA]))
    moved = dataclasses.replace(empty, metrics={"acc_raw": 0.7, "acc_per_char": 0.7501})
    new = write("new", dataclasses.replace(BANK, tasks=[moved, PIQA]))

    assert compare(bank, bank).breaches == []
    assert compare(bank, new).breaches == [Breach("arc_easy", "metrics.acc_per_char", 0.75, 0.7501)]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"model": "allenai/OLMo-2-0425-1B@main"}, "provenance.model"),
        ({"dtype": "bfloat16"}, "provenance.dtype"),
        ({"batch_tokens": 8192}, "provenance.batch_tokens"),
        ({"max_length": 2048}, "provenance.max_length"),
        ({"attention": "eager"}, "provenance.attention"),
        ({"model_digest": "f" * 64}, "provenance.model_digest"),
        ({"sdpa_backend": None}, "provenance.sdpa_backend"),
        ({"add_bos": True}, "provenance.add_bos"),
    ],
)
def test_runs_with_other_settings_are_refused(write, change, match):
    rerun = dataclasses.replace(BANK, provenance=dataclasses.replace(PROVENANCE, **change))

    with pytest.raises(ValueError, match=f"different instrument: {match}"):
        compare(write("bank", BANK), write("new", rerun))


@pytest.mark.parametrize("change", [{"task_hash": "5e2d"}, {"limit": 10}])
def test_a_task_scored_on_other_requests_is_listed_with_its_suites_and_the_rest_compared(
    write, change
):
    # PIQA's moved score is not a breach: it was scored on other requests. ARC still compares.
    # The suite's hash moves with PIQA's, as in a real file, and the suite is listed by member.
    piqa = dataclasses.replace(PIQA, metrics={"acc_raw": 0.5}, **change)
    arc = dataclasses.replace(ARC, metrics={"acc_raw": 0.7, "acc_per_char": 0.8})
    suite = dataclasses.replace(SUITE, suite_hash="hash-rc2")
    rerun = dataclasses.replace(BANK, tasks=[arc, piqa], suites=[suite])

    comparison = compare(write("bank", BANK), write("new", rerun))

    assert comparison.changed == [("piqa", next(iter(change))), ("toy:rc", "members")]
    assert comparison.tasks == ["arc_easy"]
    assert comparison.suites == []
    assert comparison.breaches == [Breach("arc_easy", "metrics.acc_per_char", 0.75, 0.8)]


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


def test_cli_exits_0_on_equal_scores_1_on_a_breach_and_2_on_a_change_or_a_refusal(write):
    bank = write("bank", BANK)
    moved = dataclasses.replace(PIQA, metrics={"acc_raw": 0.625})
    breach = write("breach", dataclasses.replace(BANK, tasks=[ARC, moved]))
    arc = dataclasses.replace(ARC, metrics={"acc_raw": 0.7, "acc_per_char": 0.8})
    rehashed = dataclasses.replace(moved, task_hash="5e2d")
    suite = dataclasses.replace(SUITE, suite_hash="hash-rc2")
    changed = write("changed", dataclasses.replace(BANK, tasks=[arc, rehashed], suites=[suite]))
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

    # A change outranks a breach beside it, and both print.
    result = CliRunner().invoke(cli.main, ["compare", str(bank), str(changed)])
    assert result.exit_code == 2
    assert result.output.splitlines() == [
        "changed, not compared: piqa (task_hash), toy:rc (members)",
        "arc_easy  metrics.acc_per_char  0.75  ->  0.8  (+0.05)",
        "compared: tasks 1, suites 0, breaches 1",
    ]

    result = CliRunner().invoke(cli.main, ["compare", str(bank), str(refused)])
    assert result.exit_code == 2
    assert "refused: different instrument: provenance.dtype is 'float32'" in result.output


def test_a_suite_averaging_another_metric_is_listed_as_changed(write):
    rerun = dataclasses.replace(BANK, suites=[dataclasses.replace(SUITE, metric="acc_raw")])

    comparison = compare(write("bank", BANK), write("new", rerun))

    assert comparison.changed == [("toy:rc", "metric")]
    assert comparison.tasks == ["arc_easy", "piqa"]


def test_a_suite_with_another_hash_is_listed_unless_a_file_predates_suite_hashes(write, tmp_path):
    # Change only the suite's own hash: its members and every score are the same.
    rerun = dataclasses.replace(BANK, suites=[dataclasses.replace(SUITE, suite_hash="hash-rc2")])

    comparison = compare(write("bank", BANK), write("new", rerun))
    assert comparison.changed == [("toy:rc", "suite_hash")]
    assert (comparison.suites, comparison.breaches) == ([], [])

    # A schema 4 bank has no suite hashes: its suites are still compared on members and score.
    old = BANK.to_dict()
    old["schema_version"] = 4
    del old["suites"][0]["suite_hash"]
    (tmp_path / "old.json").write_text(json.dumps(old))
    moved = dataclasses.replace(SUITE, suite_hash="hash-rc2", score=0.7)
    new = write("moved", dataclasses.replace(BANK, suites=[moved]))
    assert compare(tmp_path / "old.json", new).breaches == [Breach("toy:rc", "score", 0.675, 0.7)]
    # The same with the old file on the other side.
    assert compare(new, tmp_path / "old.json").breaches == [Breach("toy:rc", "score", 0.7, 0.675)]

    # From schema 5 on a suite without one is a broken file, not an old one.
    old["schema_version"] = 6
    (tmp_path / "old.json").write_text(json.dumps(old))
    with pytest.raises(ValueError, match="not a results file: KeyError\\('suite_hash'\\)"):
        compare(tmp_path / "old.json", new)

    # Every results file has a version, so one without it is broken too.
    del old["schema_version"]
    (tmp_path / "old.json").write_text(json.dumps(old))
    with pytest.raises(ValueError, match="not a results file: KeyError\\('schema_version'\\)"):
        compare(tmp_path / "old.json", new)


def test_a_suite_holding_a_changed_member_at_any_depth_is_listed_in_old_files_too(write, tmp_path):
    # A suite hash covers its members' hashes all the way down, and a file older than schema 5
    # has none, so the member rule must name the same suites. Here the bank's suite lacks the
    # changed task, as when a task joins a suite in the same change that moves its hash.
    outer = SuiteResult("toy", "hash-toy", ["toy:rc"], None, None)
    bank = BANK.to_dict()
    bank["schema_version"] = 4
    bank["suites"][0]["members"] = ["arc_easy"]
    bank["suites"].append(dataclasses.asdict(outer))
    for suite in bank["suites"]:
        del suite["suite_hash"]
    (tmp_path / "old.json").write_text(json.dumps(bank))
    piqa = dataclasses.replace(PIQA, task_hash="5e2d")
    rerun = write("new", Results(PROVENANCE, [ARC, piqa], [SUITE, outer]))

    comparison = compare(tmp_path / "old.json", rerun)

    assert comparison.changed == [("piqa", "task_hash"), ("toy:rc", "members"), ("toy", "members")]
    assert (comparison.tasks, comparison.suites, comparison.breaches) == (["arc_easy"], [], [])


def test_a_suite_holding_a_changed_suite_that_comes_after_it_in_the_bank_is_listed(write, tmp_path):
    # Each file writes a suite after the suites it holds, but only its own: here `toy` holds
    # `toy:rc` in the new file only, so in the bank it comes first.
    bank = BANK.to_dict()
    bank["schema_version"] = 4
    bank["suites"] = [
        {"suite": "toy", "members": ["arc_easy"], "metric": None, "score": None},
        {"suite": "toy:rc", "members": ["piqa"], "metric": None, "score": None},
    ]
    (tmp_path / "old.json").write_text(json.dumps(bank))
    inner = SuiteResult("toy:rc", "hash-rc", ["piqa"], None, None)
    outer = SuiteResult("toy", "hash-toy", ["arc_easy", "toy:rc"], None, None)
    piqa = dataclasses.replace(PIQA, task_hash="5e2d")
    rerun = write("new", Results(PROVENANCE, [ARC, piqa], [inner, outer]))

    comparison = compare(tmp_path / "old.json", rerun)

    assert comparison.changed == [("piqa", "task_hash"), ("toy", "members"), ("toy:rc", "members")]
    assert (comparison.suites, comparison.breaches) == ([], [])


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


def test_cli_prints_the_tokenizers_and_datasets_versions_when_they_differ(write):
    provenance = dataclasses.replace(PROVENANCE, tokenizers="0.24.0", datasets="5.2.0")
    new = write("new", dataclasses.replace(BANK, provenance=provenance))

    result = CliRunner().invoke(cli.main, ["compare", str(write("bank", BANK)), str(new)])

    assert result.exit_code == 0
    assert result.output.splitlines()[:2] == [
        "tokenizers  0.23.3  ->  0.24.0",
        "datasets  5.1.0  ->  5.2.0",
    ]


@pytest.mark.parametrize(
    ("name", "suite", "dtype"),
    [
        ("olmo-2-0425-1b-float32", "base_easy", "float32"),
        ("olmo-2-0425-1b-paloma-float32", "paloma", "float32"),
        ("olmo-2-0425-1b-bfloat16", "base_easy", "bfloat16"),
    ],
)
def test_a_bank_loads_in_the_current_layout_and_compares_equal_to_its_rewrite(
    write, name, suite, dtype
):
    # Offline: a change to what `Results` writes, or to what `compare` reads, that a bank can
    # no longer pass fails here rather than after a GPU rerun. A bank's version may lag the
    # layout's: a bank is refreshed when its scores are meant to change, not in every schema bump
    # (021.D2).
    bank = Path(__file__).parent / "regression" / f"{name}.json"
    data = json.loads(bank.read_text())
    tasks = []
    for task in data["tasks"]:
        fields: dict[str, Any] = dict(task)
        del fields["docs_per_second"]  # derived, not a field
        fields["truncation"] = Truncation(**fields["truncation"])
        fields["dataset"] = Dataset(**fields["dataset"])
        tasks.append(TaskResult(**fields))
    suites = [SuiteResult(**suite) for suite in data["suites"]]
    # Every bank records the pinned kernel, which `compare` refuses to mix across files.
    assert data["provenance"]["sdpa_backend"] == "EFFICIENT_ATTENTION"
    assert data["provenance"]["dtype"] == dtype
    # Every bank records today's provenance fields. A field added later needs a default here
    # until the banks are refreshed, as `add_bos` and `sdpa_backend` had.
    rewritten = Results(Provenance(**data["provenance"]), tasks, suites)

    comparison = compare(bank, write("rewritten", rewritten))

    assert suite in comparison.suites
    assert comparison.changed == []
    assert comparison.breaches == []
    assert comparison.provenance.keys() <= {"schema_version"}


def test_a_file_without_model_digest_compares_with_one_whose_digest_is_none(write):
    # Results written before `model_digest` was added lack the key; a Hugging Face model's is
    # None, so the two compare.
    bank = write("bank", BANK)
    data = json.loads(bank.read_text())
    del data["provenance"]["model_digest"]
    bank.write_text(json.dumps(data))

    assert compare(bank, write("new", BANK)).breaches == []


def test_a_file_without_add_bos_reads_as_no_bos(write):
    # Results written before `add_bos` was added lack the key, and no code before it added BOS.
    bank = write("bank", BANK)
    data = json.loads(bank.read_text())
    del data["provenance"]["add_bos"]
    bank.write_text(json.dumps(data))
    with_bos = dataclasses.replace(PROVENANCE, add_bos=True)

    assert compare(bank, write("new", BANK)).breaches == []
    with pytest.raises(ValueError, match="provenance.add_bos is False in .*, True in"):
        compare(bank, write("new_bos", dataclasses.replace(BANK, provenance=with_bos)))


@pytest.mark.parametrize("backend", ["EFFICIENT_ATTENTION", None])
def test_a_file_without_sdpa_backend_compares_with_any_kernel(write, backend):
    # Results written before the pin lack the key: PyTorch chose the kernel per batch, which is
    # unknown, not none. FP32 scores don't move with the kernel, so the run is not refused.
    bank = write("bank", BANK)
    data = json.loads(bank.read_text())
    del data["provenance"]["sdpa_backend"]
    bank.write_text(json.dumps(data))
    provenance = dataclasses.replace(PROVENANCE, sdpa_backend=backend)

    comparison = compare(bank, write("new", dataclasses.replace(BANK, provenance=provenance)))

    assert comparison.breaches == []
    if backend is not None:  # printed as a likely cause, the missing side read as None
        assert comparison.provenance["sdpa_backend"] == (None, backend)
