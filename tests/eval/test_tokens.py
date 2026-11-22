from __future__ import annotations

import dataclasses
import json
import stat
from typing import TYPE_CHECKING, Any

import pytest
import torch
from click.testing import CliRunner

from flock.eval import run
from flock.eval.cli import main
from flock.eval.model import FlockCausalLM, ScoringModel
from flock.eval.results import read_results
from flock.eval.run import EvalConfig, evaluate, prepare_task, score_prepared, score_task
from flock.eval.tasks import Choice, Dataset, MCDoc, MultipleChoiceTask
from flock.training.core.models import Olmo2, Olmo2Config

from .test_model import REQUESTS
from .toy import TASK

if TYPE_CHECKING:
    from pathlib import Path

    from transformers import LlamaForCausalLM, PreTrainedTokenizerFast

    from flock.eval.requests import TokenValues


def per_choice_doc(row: dict[str, Any]) -> MCDoc:
    """A toy row in Winogrande's shape: each choice has its own context and the same continuation."""
    choices = tuple(Choice(f"{row['question']} {text}?", " Yes") for text in row["choices"]["text"])
    return MCDoc(row["id"], choices, row["choices"]["label"].index(row["answerKey"]))


PER_CHOICE = dataclasses.replace(TASK, name="toy_per_choice", to_doc=per_choice_doc, fewshot=None)


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


def contexts_of(scorer: ScoringModel) -> list[list[int]]:
    """The token ids of `REQUESTS`' contexts."""
    return [context for context, _ in scorer.encode_requests(REQUESTS)]


def test_per_token_values_match_one_unbatched_forward_pass(scorer: ScoringModel) -> None:
    scores = scorer.loglikelihood(REQUESTS, tokens=True)
    read = scorer.score_contexts(contexts_of(scorer), values=True)
    assert scores.continuation_tokens is not None and read.values is not None

    for i, (context, continuation) in enumerate(scorer.encode_requests(REQUESTS)):
        # A continuation's values are its window's last ones; a context is read on its own.
        whole = reference(scorer, context + continuation)
        tail = {name: value[-len(continuation) :] for name, value in whole.items()}
        head = reference(scorer, context) if len(context) > 1 else None
        for part, values in ((tail, scores.continuation_tokens), (head, read.values)):
            found = sequence(values, i)
            if part is None:  # one token: nothing predicts it
                assert found["token_ids"].tolist() == []
                continue
            assert found["token_ids"].tolist() == part["token_ids"].tolist()
            assert found["rank"].tolist() == part["rank"].tolist()
            for name in ("logprob", "entropy", "top1_logprob", "logprob_std"):
                torch.testing.assert_close(found[name], part[name], rtol=1e-4, atol=1e-5)
        # Each continuation's values add up to its score.
        summed = sequence(scores.continuation_tokens, i)["logprob"].sum()
        torch.testing.assert_close(summed, scores.logprob[i])


def test_asking_for_values_never_changes_a_score(scorer: ScoringModel) -> None:
    plain = scorer.loglikelihood(REQUESTS)
    asked = scorer.loglikelihood(REQUESTS, tokens=True)
    assert torch.equal(asked.logprob, plain.logprob)
    assert torch.equal(asked.is_greedy, plain.is_greedy)
    assert plain.continuation_tokens is None


def test_values_and_greedy_tokens_are_the_same_in_any_batches(
    lm: LlamaForCausalLM, tokenizer: PreTrainedTokenizerFast
) -> None:
    found = []
    for batch_tokens in (1, 80, 4096):
        scorer = ScoringModel(lm, tokenizer, name="tiny@0", batch_tokens=batch_tokens)
        scores = scorer.loglikelihood(REQUESTS, tokens=True)
        read = scorer.score_contexts(contexts_of(scorer), num_tokens=6, values=True)
        found.append((scores.continuation_tokens, read.values, read.greedy))
    for values in found[1:]:
        for ours, first in zip(values[:2], found[0][:2], strict=True):
            assert ours is not None and first is not None
            assert ours.lengths == first.lengths
            assert torch.equal(ours.token_ids, first.token_ids)
            assert torch.equal(ours.rank, first.rank)
            torch.testing.assert_close(ours.logprob, first.logprob)
            torch.testing.assert_close(ours.logprob_std, first.logprob_std)
        greedy, first_greedy = values[2], found[0][2]
        assert greedy is not None and first_greedy is not None
        assert torch.equal(greedy, first_greedy)


def uncached_greedy(scorer: ScoringModel, context: list[int], num_tokens: int) -> list[int]:
    """Greedy decoding the slow way: the whole sequence again for every token."""
    ids = list(context)
    for _ in range(num_tokens):
        with torch.no_grad():
            ids.append(int(scorer.model(torch.tensor([ids])).logits[0, -1].argmax()))
    return ids[len(context) :]


def test_greedy_tokens_are_what_full_forward_passes_pick(scorer: ScoringModel) -> None:
    contexts = contexts_of(scorer)
    greedy = scorer.score_contexts(contexts, num_tokens=6).greedy
    assert greedy is not None
    assert greedy.tolist() == [uncached_greedy(scorer, context, 6) for context in contexts]
    # No values unless asked for.
    assert scorer.score_contexts(contexts, num_tokens=1).values is None


def test_a_long_context_is_cut_from_the_left_to_leave_room_for_the_tokens_written(
    lm: LlamaForCausalLM, tokenizer: PreTrainedTokenizerFast
) -> None:
    short = ScoringModel(lm, tokenizer, name="tiny@0", max_length=8)
    context = list(range(3, 23))
    read = short.score_contexts([context], values=True)

    assert read.greedy is not None and read.greedy.shape == (1, 7)  # max_length - 1 tokens
    assert read.greedy.tolist() == [uncached_greedy(short, context[-1:], 7)]
    assert read.values is not None
    assert read.values.token_ids.tolist() == []  # one token kept, which nothing predicts
    # With room for 4 context tokens, the last 3 have values.
    read = short.score_contexts([context], num_tokens=4, values=True)
    assert read.values is not None and read.values.token_ids.tolist() == context[-3:]
    with pytest.raises(ValueError, match="no tokens to decode"):
        short.score_contexts([[1]], num_tokens=0)


def test_a_flock_model_writes_no_greedy_tokens_but_reads_its_contexts(
    tokenizer: PreTrainedTokenizerFast,
) -> None:
    torch.manual_seed(0)
    config = Olmo2Config(
        vocab_size=len(tokenizer),
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        max_position_embeddings=128,
    )
    scorer = ScoringModel(FlockCausalLM(Olmo2(config).eval()), tokenizer, name="flock:acme/m@1")
    contexts = contexts_of(scorer)
    read = scorer.score_contexts(contexts, values=True)

    assert read.greedy is None
    assert read.values is not None
    assert list(read.values.lengths) == [max(len(context) - 1, 0) for context in contexts]


def test_the_sidecar_holds_each_choice_each_context_and_its_greedy_tokens(
    offline_model: None, scorer: ScoringModel, tmp_path: Path
) -> None:
    evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path, context_tokens=True))
    written = read_results(str(tmp_path))
    rows = written.tokens("toy")
    assert rows is not None

    docs, shots = TASK.load()
    requests = TASK.requests(docs, shots)
    encoded = scorer.encode_requests(requests.requests)
    continuations = [row for row in rows if row["part"] == "continuation"]
    assert [(row["id"], row["choice"]) for row in continuations] == [
        ("q-1", 0),
        ("q-1", 1),
        ("q-1", 2),
        ("q-2", 0),
        ("q-2", 1),
        ("q-2", 2),
        ("q-2", 3),
    ]
    for row, (_, continuation) in zip(continuations, encoded, strict=False):
        assert row["token_ids"] == continuation
        assert len(row["logprob"]) == len(row["rank"]) == len(continuation)
    # The doc's score is the sum of its tokens' log-probabilities.
    [first] = [r for r in written.predictions("toy") or [] if r["id"] == "q-1"]
    assert sum(continuations[0]["logprob"]) == pytest.approx(first["logprob"][0], rel=1e-5)

    # One context and one greedy row per doc: its choices share their context.
    for part in ("context", "greedy"):
        assert [(r["id"], r["choice"]) for r in rows if r["part"] == part] == [
            ("q-1", 0),
            ("q-2", 0),
        ]
    greedy = [r for r in rows if r["part"] == "greedy"]
    assert all(len(r["token_ids"]) == 32 and r["logprob"] is None for r in greedy)
    assert greedy[0]["token_ids"] == uncached_greedy(scorer, encoded[0].context, 32)

    # Read for some docs only, as a page shows one.
    assert {row["id"] for row in written.tokens("toy", ids=["q-2"]) or []} == {"q-2"}


def test_each_distinct_context_of_a_doc_gets_its_own_rows(
    scorer: ScoringModel, tmp_path: Path
) -> None:
    score_task(scorer, PER_CHOICE, out=tmp_path, tokens=True, context_tokens=True)
    docs, shots = PER_CHOICE.load()
    requests = PER_CHOICE.requests(docs, shots)
    rows = [json.loads(json.dumps(row)) for row in read_tokens(tmp_path, PER_CHOICE.name)]

    contexts = [(r["id"], r["choice"]) for r in rows if r["part"] == "context"]
    assert len(contexts) == len(requests.context_requests) == sum(len(d.choices) for d in docs)


def read_tokens(folder: Path, task: str) -> list[dict[str, Any]]:
    """A folder's per-token rows of one task, read without a results file."""
    import pyarrow.parquet as pq

    return pq.read_table(folder / "predictions" / f"{task}.tokens.parquet").to_pylist()


def test_without_the_flags_no_sidecar_is_written_and_a_run_reads_without_one(
    offline_model: None, scorer: ScoringModel, tmp_path: Path
) -> None:
    evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path / "off", tokens=False))
    assert not (tmp_path / "off" / "predictions" / "toy.tokens.parquet").exists()
    assert read_results(str(tmp_path / "off")).tokens("toy") is None

    # A training loop scoring a prepared task pays for no values unless it asks.
    score_prepared(scorer, prepare_task(scorer, TASK), tmp_path / "step")
    assert not (tmp_path / "step" / "predictions" / "toy.tokens.parquet").exists()

    # The default writes the choices and the greedy tokens, not the contexts.
    evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path / "on"))
    parts = {row["part"] for row in read_results(str(tmp_path / "on")).tokens("toy") or []}
    assert parts == {"continuation", "greedy"}


def test_the_scores_are_the_same_with_and_without_the_sidecar(
    offline_model: None, tmp_path: Path
) -> None:
    off = evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path / "off", tokens=False))
    on = evaluate(
        EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path / "on", context_tokens=True)
    )
    assert on.tasks[0].metrics == off.tasks[0].metrics
    assert on.tasks[0].task_hash == off.tasks[0].task_hash
    read = [(tmp_path / f / "predictions" / "toy.jsonl").read_text() for f in ("on", "off")]
    assert read[0] == read[1]


def test_context_tokens_need_tokens_and_the_cli_takes_both_flags(
    offline_model: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="needs tokens"):
        EvalConfig(model="m@0", tasks=("toy",), tokens=False, context_tokens=True)

    # `--task` takes registered names only, fixed when the CLI is imported.
    monkeypatch.setitem(run.TASKS, "arc_easy", TASK)
    args = ["run", "--model", "tiny@0", "--task", "arc_easy", "--out", str(tmp_path / "a")]
    assert CliRunner().invoke(main, [*args, "--no-tokens"]).exit_code == 0
    assert not (tmp_path / "a" / "predictions" / "arc_easy.tokens.parquet").exists()
    args[-1] = str(tmp_path / "b")
    refused = CliRunner().invoke(main, [*args, "--no-tokens", "--context-tokens"])
    assert refused.exit_code == 2 and "needs tokens" in refused.output


def test_a_private_tasks_sidecar_is_its_owners_only(scorer: ScoringModel, tmp_path: Path) -> None:
    private = dataclasses.replace(TASK, dataset=dataclasses.replace(TASK.dataset, private=True))
    score_task(scorer, private, out=tmp_path / "private", tokens=True)
    score_task(scorer, TASK, out=tmp_path / "public", tokens=True)

    path = tmp_path / "private" / "predictions" / "toy.tokens.parquet"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    public = tmp_path / "public" / "predictions" / "toy.tokens.parquet"
    assert stat.S_IMODE(public.stat().st_mode) != 0o600
    # A run's values are never replaced, as its predictions aren't.
    taken = tmp_path / "taken" / "predictions"
    taken.mkdir(parents=True)
    (taken / "toy.tokens.parquet").write_bytes(b"")
    with pytest.raises(FileExistsError, match="toy.tokens.parquet exists"):
        score_task(scorer, TASK, out=tmp_path / "taken", tokens=True)


def test_context_requests_name_each_docs_first_choice_per_distinct_context() -> None:
    docs = (
        MCDoc("a", (Choice("x", " 1"), Choice("x", " 2")), 0),
        MCDoc("b", (Choice("y", " 1"), Choice("z", " 1"), Choice("y", " 2")), 0),
    )
    task = MultipleChoiceTask(
        name="t",
        dataset=Dataset("cerulean/toy", "0" * 40, None, "test"),
        to_doc=lambda row: docs[0],
        primary_metric="acc_raw",
    )
    assert task.requests(docs, []).context_requests == (0, 2, 3)
