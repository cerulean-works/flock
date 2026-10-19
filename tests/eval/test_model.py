import copy

import pytest
import torch

from flock.eval import model
from flock.eval.model import ModelRef, ScoringModel
from flock.eval.requests import Request

REQUESTS = [
    Request("Question: Which gas do plants take in?\nAnswer:", " carbon dioxide"),
    Request("Question: Which gas do plants take in?\nAnswer:", " oxygen"),
    Request("Question: 臺灣最高的山是哪一座？\nAnswer:", " 玉山"),
    Request("Answer:", " 繁體中文的評估。"),
    Request("The quick brown fox", " jumps over the lazy dog."),
    Request("", " fox"),
]


def reference(scorer: ScoringModel, request: Request) -> float:
    """The continuation's log-probability from one unbatched, unpadded forward pass."""
    context, continuation = scorer.encode(request)
    ids = (context + continuation)[-(scorer.max_length + 1) :]
    with torch.no_grad():
        logits = scorer.model(torch.tensor([ids[:-1]])).logits[0].float()
    logprobs = logits.log_softmax(-1)
    start = len(ids) - 1 - len(continuation)
    return sum(logprobs[start + i, t].item() for i, t in enumerate(continuation))


def test_model_ref_parse():
    ref = ModelRef.parse("allenai/OLMo-2-0425-1B@a1847dff")
    assert (ref.repo, ref.revision) == ("allenai/OLMo-2-0425-1B", "a1847dff")
    assert str(ref) == "allenai/OLMo-2-0425-1B@a1847dff"


@pytest.mark.parametrize("model_id", ["allenai/OLMo-2-0425-1B", "allenai/OLMo-2-0425-1B@", "@abc"])
def test_model_ref_requires_revision(model_id):
    with pytest.raises(ValueError, match="repo@revision"):
        ModelRef.parse(model_id)


class FakeHub:
    def __init__(self, sha):
        self.sha = sha

    def model_info(self, repo, revision):
        return self


def test_model_ref_resolve_keeps_commit_hash_offline(monkeypatch):
    monkeypatch.setattr(model, "HfApi", None)
    ref = ModelRef("allenai/OLMo-2-0425-1B", "a" * 40)
    assert ref.resolve() is ref


def test_model_ref_resolve_asks_hub_for_branch(monkeypatch):
    monkeypatch.setattr(model, "HfApi", lambda: FakeHub("b" * 40))
    assert ModelRef("org/repo", "main").resolve() == ModelRef("org/repo", "b" * 40)


def test_model_ref_resolve_requires_commit_hash(monkeypatch):
    monkeypatch.setattr(model, "HfApi", lambda: FakeHub(None))
    with pytest.raises(ValueError, match="no commit hash for org/repo@main"):
        ModelRef("org/repo", "main").resolve()


@pytest.mark.parametrize("batch_tokens", [0, -5])
def test_rejects_non_positive_batch_tokens(lm, tokenizer, batch_tokens):
    with pytest.raises(ValueError, match="batch_tokens must be at least 1"):
        ScoringModel(lm, tokenizer, name="tiny@0", batch_tokens=batch_tokens)


def test_encode_splits_at_context_tokens(scorer, tokenizer):
    request = Request("Question: Which gas do plants take in?\nAnswer:", " carbon dioxide")
    context, continuation = scorer.encode(request)
    whole = tokenizer.encode(request.context + request.continuation, add_special_tokens=False)
    assert context == tokenizer.encode(request.context, add_special_tokens=False)
    assert context + continuation == whole
    assert tokenizer.decode(continuation) == " carbon dioxide"


def test_encode_moves_trailing_whitespace_to_continuation(scorer):
    assert scorer.encode(Request("Answer: ", "fox")) == scorer.encode(Request("Answer:", " fox"))


def test_encode_empty_context_uses_eos_without_bos(scorer, tokenizer):
    assert tokenizer.bos_token_id is None
    context, continuation = scorer.encode(Request("", " fox"))
    assert context == [tokenizer.eos_token_id]
    assert continuation == tokenizer.encode(" fox", add_special_tokens=False)
    # A whitespace-only context is empty once its whitespace moves to the continuation.
    assert scorer.encode(Request("  ", "fox")) == (context, tokenizer.encode("  fox"))


def test_encode_empty_context_without_bos_or_eos_raises(lm, tokenizer):
    bare = copy.deepcopy(tokenizer)
    bare.eos_token = None
    with pytest.raises(ValueError, match="neither"):
        ScoringModel(lm, bare, name="tiny@0").encode(Request("", " fox"))


def test_loglikelihood_matches_unbatched_reference(scorer):
    scores = scorer.loglikelihood(REQUESTS)
    expected = torch.tensor([reference(scorer, r) for r in REQUESTS])
    torch.testing.assert_close(scores.logprob, expected, atol=1e-4, rtol=1e-5)
    assert scores.logprob.dtype == torch.float32
    assert scores.num_tokens.tolist() == [len(scorer.encode(r)[1]) for r in REQUESTS]


@pytest.mark.parametrize("batch_tokens", [1, 40, 100, 10_000])
def test_loglikelihood_is_batch_size_and_padding_invariant(scorer, batch_tokens):
    # batch_tokens=1 scores each request alone; larger budgets left-pad short requests next
    # to long ones.
    alone = scorer.loglikelihood(REQUESTS)
    scorer.batch_tokens = batch_tokens
    torch.testing.assert_close(
        scorer.loglikelihood(REQUESTS).logprob, alone.logprob, atol=1e-4, rtol=1e-5
    )


def test_loglikelihood_keeps_request_order(scorer):
    forward = scorer.loglikelihood(REQUESTS).logprob
    backward = scorer.loglikelihood(REQUESTS[::-1]).logprob
    torch.testing.assert_close(backward, forward.flip(0), atol=1e-4, rtol=1e-5)


def test_empty_continuation_raises(scorer):
    with pytest.raises(ValueError, match="no tokens"):
        scorer.loglikelihood([Request("Answer:", ""), REQUESTS[0]])


def test_loglikelihood_restores_each_module_mode(scorer):
    scorer.model.train()
    frozen = scorer.model.model.embed_tokens.eval()
    scorer.loglikelihood(REQUESTS)
    assert scorer.model.training
    assert not frozen.training
    scorer.model.eval()


def test_score_rejects_a_model_that_ignores_logits_to_keep(scorer, monkeypatch):
    forward = scorer.model.forward
    monkeypatch.setattr(
        scorer.model, "forward", lambda **kwargs: forward(**kwargs | {"logits_to_keep": 0})
    )
    with pytest.raises(ValueError, match="ignored logits_to_keep"):
        scorer.loglikelihood(REQUESTS)


@pytest.mark.parametrize("max_length", [0, -1])
def test_scoring_model_rejects_max_length_below_one(lm, tokenizer, max_length):
    with pytest.raises(ValueError, match="max_length must be at least 1"):
        ScoringModel(lm, tokenizer, name="tiny@0", max_length=max_length)


def test_long_inputs_truncate_from_the_left(lm, tokenizer):
    scorer = ScoringModel(lm, tokenizer, name="tiny@0", max_length=8)
    request = Request("The quick brown fox jumps over the lazy dog.", " fox")
    assert sum(map(len, scorer.encode(request))) > 9
    assert scorer.loglikelihood([request]).logprob[0].item() == pytest.approx(
        reference(scorer, request), abs=1e-4
    )


def test_continuation_longer_than_max_length_raises(lm, tokenizer):
    scorer = ScoringModel(lm, tokenizer, name="tiny@0", max_length=2)
    with pytest.raises(ValueError, match="longer than 2 tokens"):
        scorer.loglikelihood([Request("Answer:", " jumps over the lazy dog.")])
