from __future__ import annotations

import dataclasses
import json
import os
import shutil
import stat
from typing import TYPE_CHECKING, Any

import pytest
import torch
from click.testing import CliRunner

from flock.eval import run
from flock.eval.cli import main
from flock.eval.model import ScoringModel
from flock.eval.results import read_results
from flock.eval.run import EvalConfig, evaluate, prepare_task, score_prepared, score_task

from .test_model import REQUESTS
from .toy import TASK, TEXT_TASK

if TYPE_CHECKING:
    from pathlib import Path

    from transformers import LlamaForCausalLM, PreTrainedTokenizerFast

    from flock.eval.requests import TokenValues


@pytest.fixture
def offline_model(monkeypatch: pytest.MonkeyPatch, scorer: ScoringModel) -> None:
    monkeypatch.setitem(run.TASKS, TASK.name, TASK)
    monkeypatch.setattr(ScoringModel, "from_pretrained", lambda *args, **kwargs: scorer)
    monkeypatch.setenv("FLOCK_COMMIT", "abc123")


def reference(scorer: ScoringModel, window: list[int]) -> dict[str, torch.Tensor]:
    """Every per-token value of one window, from one unbatched forward pass: the values at each position for the token it predicts."""
    with torch.no_grad():
        logits = scorer.model(torch.tensor([window[:-1]])).logits[0].float()
    logprobs = logits.log_softmax(-1)
    targets = torch.tensor(window[1:])
    probs = logprobs.exp()
    entropy = -(probs * logprobs).sum(-1)
    target_logits = logits.gather(-1, targets[:, None])
    return {
        "token_ids": targets,
        "logprob": logprobs.gather(-1, targets[:, None]).squeeze(-1),
        "rank": (logits > target_logits).sum(-1) + 1,
        "entropy": entropy,
        "top1_logprob": logprobs.max(-1).values,
        "logprob_std": (probs * (logprobs + entropy[:, None]) ** 2).sum(-1).sqrt(),
    }


def sequence(values: TokenValues, i: int) -> dict[str, torch.Tensor]:
    """Sequence `i` of `values`, each array cut to its tokens."""
    start = sum(values.lengths[:i])
    cut = slice(start, start + values.lengths[i])
    names = ("token_ids", "logprob", "rank", "entropy", "top1_logprob", "logprob_std")
    return {name: getattr(values, name)[cut] for name in names}


def test_per_token_values_match_one_unbatched_forward_pass(scorer: ScoringModel) -> None:
    scores = scorer.loglikelihood(REQUESTS, tokens=True)
    assert scores.continuation_tokens is not None

    for i, (context, continuation) in enumerate(scorer.encode_requests(REQUESTS)):
        # A continuation's values are its window's last ones.
        whole = reference(scorer, context + continuation)
        tail = {name: value[-len(continuation) :] for name, value in whole.items()}
        found = sequence(scores.continuation_tokens, i)
        assert found["token_ids"].tolist() == tail["token_ids"].tolist()
        assert found["rank"].tolist() == tail["rank"].tolist()
        for name in ("logprob", "entropy", "top1_logprob", "logprob_std"):
            torch.testing.assert_close(found[name], tail[name], rtol=1e-4, atol=1e-5)
        # Each continuation's values add up to its score.
        summed = sequence(scores.continuation_tokens, i)["logprob"].sum()
        torch.testing.assert_close(summed, scores.logprob[i])


def test_asking_for_values_never_changes_a_score(scorer: ScoringModel) -> None:
    plain = scorer.loglikelihood(REQUESTS)
    asked = scorer.loglikelihood(REQUESTS, tokens=True)
    assert torch.equal(asked.logprob, plain.logprob)
    assert torch.equal(asked.is_greedy, plain.is_greedy)
    assert plain.continuation_tokens is None


def test_values_are_the_same_in_any_batches(
    lm: LlamaForCausalLM, tokenizer: PreTrainedTokenizerFast
) -> None:
    found = []
    for batch_tokens in (1, 80, 4096):
        scorer = ScoringModel(lm, tokenizer, name="tiny@0", batch_tokens=batch_tokens)
        found.append(scorer.loglikelihood(REQUESTS, tokens=True).continuation_tokens)
    first = found[0]
    assert first is not None
    for ours in found[1:]:
        assert ours is not None
        assert ours.lengths == first.lengths
        assert torch.equal(ours.token_ids, first.token_ids)
        assert torch.equal(ours.rank, first.rank)
        torch.testing.assert_close(ours.logprob, first.logprob)
        torch.testing.assert_close(ours.logprob_std, first.logprob_std)


def test_the_sidecar_holds_each_choice(
    offline_model: None, scorer: ScoringModel, tmp_path: Path
) -> None:
    evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path))
    written = read_results(str(tmp_path))
    rows = written.tokens("toy")
    assert rows is not None

    docs, shots = TASK.load()
    requests = TASK.requests(docs, shots)
    encoded = scorer.encode_requests(requests.requests)
    assert {row["part"] for row in rows} == {"continuation"}
    assert [(row["id"], row["choice"]) for row in rows] == [
        ("q-1", 0),
        ("q-1", 1),
        ("q-1", 2),
        ("q-2", 0),
        ("q-2", 1),
        ("q-2", 2),
        ("q-2", 3),
    ]
    for row, (_, continuation) in zip(rows, encoded, strict=False):
        assert row["token_ids"] == continuation
        assert len(row["logprob"]) == len(row["rank"]) == len(continuation)
    # The doc's score is the sum of its tokens' log-probabilities.
    [first] = [r for r in written.predictions("toy") or [] if r["id"] == "q-1"]
    assert sum(rows[0]["logprob"]) == pytest.approx(first["logprob"][0], rel=1e-5)

    # Read for some docs only, as a page shows one.
    assert {row["id"] for row in written.tokens("toy", ids=["q-2"]) or []} == {"q-2"}


def read_tokens(folder: Path, task: str) -> list[dict[str, Any]]:
    """A folder's per-token rows of one task, read without a results file."""
    import pyarrow.parquet as pq

    return pq.read_table(folder / "predictions" / f"{task}.tokens.parquet").to_pylist()


def test_without_tokens_no_sidecar_is_written_and_a_run_reads_without_one(
    offline_model: None, scorer: ScoringModel, tmp_path: Path
) -> None:
    evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path / "off", tokens=False))
    assert not (tmp_path / "off" / "predictions" / "toy.tokens.parquet").exists()
    assert read_results(str(tmp_path / "off")).tokens("toy") is None

    # A training loop scoring a prepared task pays for no values unless it asks.
    score_prepared(scorer, prepare_task(scorer, TASK), tmp_path / "step")
    assert not (tmp_path / "step" / "predictions" / "toy.tokens.parquet").exists()

    # A perplexity task has none, even when asked.
    score_task(scorer, TEXT_TASK, out=tmp_path / "text", tokens=True)
    assert not (tmp_path / "text" / "predictions" / "toy_text.tokens.parquet").exists()


def test_the_scores_are_the_same_with_and_without_the_sidecar(
    offline_model: None, tmp_path: Path
) -> None:
    off = evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path / "off", tokens=False))
    on = evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path / "on"))
    assert on.tasks[0].metrics == off.tasks[0].metrics
    assert on.tasks[0].task_hash == off.tasks[0].task_hash
    read = [(tmp_path / f / "predictions" / "toy.jsonl").read_text() for f in ("on", "off")]
    assert read[0] == read[1]


def test_the_cli_turns_the_sidecar_off(
    offline_model: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `--task` takes registered names only, fixed when the CLI is imported.
    monkeypatch.setitem(run.TASKS, "arc_easy", TASK)
    args = ["run", "--model", "tiny@0", "--task", "arc_easy", "--out", str(tmp_path / "a")]
    assert CliRunner().invoke(main, [*args, "--no-tokens"]).exit_code == 0
    assert not (tmp_path / "a" / "predictions" / "arc_easy.tokens.parquet").exists()


def test_a_private_tasks_sidecar_is_its_owners_only(scorer: ScoringModel, tmp_path: Path) -> None:
    private = dataclasses.replace(TASK, dataset=dataclasses.replace(TASK.dataset, private=True))
    # A known umask, so the public file's mode is the writer's and not the shell's.
    previous = os.umask(0o022)
    try:
        score_task(scorer, private, out=tmp_path / "private", tokens=True)
        score_task(scorer, TASK, out=tmp_path / "public", tokens=True)
    finally:
        os.umask(previous)

    path = tmp_path / "private" / "predictions" / "toy.tokens.parquet"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    public = tmp_path / "public" / "predictions" / "toy.tokens.parquet"
    assert stat.S_IMODE(public.stat().st_mode) == 0o644
    # A run's values are never replaced, as its predictions aren't.
    taken = tmp_path / "taken" / "predictions"
    taken.mkdir(parents=True)
    (taken / "toy.tokens.parquet").write_bytes(b"")
    with pytest.raises(FileExistsError, match="toy.tokens.parquet exists"):
        score_task(scorer, TASK, out=tmp_path / "taken", tokens=True)


def test_reads_of_some_docs_and_files_left_by_other_runs(
    offline_model: None, scorer: ScoringModel, tmp_path: Path
) -> None:
    evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path / "run"))
    written = read_results(str(tmp_path / "run"))
    assert written.tokens("toy", ids=[]) == []  # an empty selection keeps no row
    # A file beside the run for a task it didn't score isn't its own.
    shutil.copy(
        tmp_path / "run/predictions/toy.tokens.parquet",
        tmp_path / "run/predictions/x.tokens.parquet",
    )
    assert written.tokens("x") is None

    # A file of that name beside an older run isn't its own.
    data = json.loads((tmp_path / "run" / "results.json").read_text())
    data["schema_version"] = 11
    (tmp_path / "run" / "results.json").write_text(json.dumps(data))
    assert read_results(str(tmp_path / "run")).tokens("toy") is None

    # A run never writes predictions beside values another run left, even without its own.
    taken = tmp_path / "taken" / "predictions"
    taken.mkdir(parents=True)
    (taken / "toy.tokens.parquet").write_bytes(b"")
    with pytest.raises(FileExistsError, match="toy.tokens.parquet exists"):
        score_task(scorer, TASK, out=tmp_path / "taken")
