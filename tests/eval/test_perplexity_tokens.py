from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pyarrow.parquet as pq
import pytest
import torch

from flock.eval import run
from flock.eval.model import ScoringModel
from flock.eval.results import read_results
from flock.eval.run import EvalConfig, evaluate, prepare_task, score_prepared, score_task
from flock.eval.tokenizer import SentencePieceTokenizer

from .test_model import TEXTS
from .test_tokenizer import train
from .toy import TEXT_ROWS, TEXT_TASK

if TYPE_CHECKING:
    from pathlib import Path

    from transformers import LlamaForCausalLM, PreTrainedTokenizerFast


def test_each_texts_values_are_its_tokens_in_order_and_add_up_to_its_score(
    scorer: ScoringModel,
) -> None:
    scores = scorer.rolling_loglikelihood(TEXTS, 3, tokens=True)
    values = scores.continuation_tokens
    assert values is not None

    ids = scorer.tokenize(TEXTS)
    assert values.lengths == tuple(len(text_ids) for text_ids in ids)
    assert values.token_ids.tolist() == [t for text_ids in ids for t in text_ids]
    start = 0
    for i, length in enumerate(values.lengths):
        summed = values.logprob[start : start + length].double().sum()
        torch.testing.assert_close(summed, scores.logprob[i], rtol=1e-5, atol=1e-4)
        start += length
    # Asking for them changes no score.
    assert torch.equal(scorer.rolling_loglikelihood(TEXTS, 3).logprob, scores.logprob)


def test_byte_ends_place_each_token_in_the_texts_bytes(
    scorer: ScoringModel, tokenizer: PreTrainedTokenizerFast
) -> None:
    for text, ends in zip(TEXTS, scorer.byte_ends(TEXTS), strict=True):
        assert ends == sorted(ends)
        assert ends[-1] <= len(text.encode())
    # In ASCII text every token's bytes are its own text.
    ascii_text = "The quick brown fox jumps over the lazy dog."
    [ends] = scorer.byte_ends([ascii_text])
    ids = scorer.tokenize([ascii_text])[0]
    prefixes = [str(tokenizer.decode(ids[: k + 1])) for k in range(len(ids))]
    assert ends == [len(prefix.encode()) for prefix in prefixes]


def test_a_sentencepiece_tokenizer_places_its_pieces_in_bytes(
    lm: LlamaForCausalLM, tmp_path: Path
) -> None:
    scorer = ScoringModel(lm, SentencePieceTokenizer(train(tmp_path / "t.model")), name="sp@0")
    text = "臺灣最高的山 fox 🏔"
    [ends] = scorer.byte_ends([text])

    assert len(ends) == len(scorer.tokenize([text])[0])
    assert ends == sorted(ends) and ends[-1] == len(text.encode())


@pytest.fixture
def offline_text(monkeypatch: pytest.MonkeyPatch, scorer: ScoringModel) -> None:
    monkeypatch.setitem(run.TASKS, TEXT_TASK.name, TEXT_TASK)
    monkeypatch.setattr(ScoringModel, "from_pretrained", lambda *args, **kwargs: scorer)
    monkeypatch.setenv("FLOCK_COMMIT", "abc123")


def test_a_perplexity_sidecar_has_each_docs_losses_and_byte_ends_but_no_token_ids(
    offline_text: None, tmp_path: Path
) -> None:
    on = EvalConfig(
        model="tiny@0", tasks=("toy_text",), out=tmp_path / "on", perplexity_tokens=True
    )
    evaluate(on)
    evaluate(EvalConfig(model="tiny@0", tasks=("toy_text",), out=tmp_path / "off"))

    written = read_results(str(tmp_path / "on"))
    rows = written.tokens("toy_text")
    assert rows is not None
    assert [row["id"] for row in rows] == [row["id"] for row in TEXT_ROWS]
    assert "token_ids" not in rows[0]  # the token ids are the text
    predictions = written.predictions("toy_text") or []
    for row, prediction in zip(rows, predictions, strict=True):
        assert len(row["logprob"]) == len(row["byte_end"]) == prediction["num_tokens"]
        assert sum(row["logprob"]) == pytest.approx(prediction["logprob"], rel=1e-5)
    # Off by default, and never moving a score.
    assert read_results(str(tmp_path / "off")).tokens("toy_text") is None
    off = read_results(str(tmp_path / "off"))
    assert [t.metrics for t in off.tasks] == [t.metrics for t in written.tasks]
    assert off.predictions("toy_text") == predictions


def test_a_private_texts_sidecar_is_its_owners_only_and_training_writes_none(
    scorer: ScoringModel, tmp_path: Path
) -> None:
    import dataclasses

    private = dataclasses.replace(
        TEXT_TASK, dataset=dataclasses.replace(TEXT_TASK.dataset, private=True)
    )
    score_task(scorer, private, out=tmp_path / "private", perplexity_tokens=True)
    path = tmp_path / "private" / "predictions" / "toy_text.tokens.parquet"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert pq.read_table(path).num_rows == len(TEXT_ROWS)

    score_prepared(scorer, prepare_task(scorer, TEXT_TASK), tmp_path / "step")
    assert not (tmp_path / "step" / "predictions" / "toy_text.tokens.parquet").exists()


def test_byte_ends_refuse_token_ids_other_than_the_ones_scored(scorer: ScoringModel) -> None:
    ids = scorer.tokenize(["fox"])
    assert scorer.byte_ends(["fox"], ids) == scorer.byte_ends(["fox"])
    with pytest.raises(ValueError, match="other tokens than the ones scored"):
        scorer.byte_ends(["fox"], [[*ids[0], ids[0][0]]])
