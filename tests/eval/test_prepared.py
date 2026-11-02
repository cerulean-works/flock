import copy
import dataclasses

import pytest

from flock.eval.model import ScoringModel
from flock.eval.results import TaskResult
from flock.eval.run import prepare_task, score_prepared, score_task
from flock.eval.tasks import Dataset

from .toy import TASK, TEXT_TASK


def untimed(result: TaskResult) -> dict:
    """Every field but the wall-clock time, which differs between any two calls."""
    return {
        field.name: getattr(result, field.name)
        for field in dataclasses.fields(result)
        if field.name != "seconds"
    }


@pytest.mark.parametrize("task", [TASK, TEXT_TASK], ids=["multiple_choice", "perplexity"])
def test_a_prepared_task_scores_as_score_task_does_at_every_call(scorer, task, tmp_path):
    one_call = score_task(scorer, task, limit=2, out=tmp_path / "one-call")
    prepared = prepare_task(scorer, task, limit=2)
    first = score_prepared(scorer, prepared, out=tmp_path / "step-1")
    second = score_prepared(scorer, prepared, out=tmp_path / "step-2")

    assert untimed(first) == untimed(one_call)
    assert untimed(second) == untimed(one_call)
    assert first.limit == 2 and first.num_docs == 2
    file = f"predictions/{task.name}.jsonl"
    expected = (tmp_path / "one-call" / file).read_bytes()
    assert (tmp_path / "step-1" / file).read_bytes() == expected
    assert (tmp_path / "step-2" / file).read_bytes() == expected


@pytest.mark.parametrize("task", [TASK, TEXT_TASK], ids=["multiple_choice", "perplexity"])
def test_scoring_a_prepared_task_neither_loads_nor_tokenizes(scorer, task, monkeypatch):
    prepared = prepare_task(scorer, task)
    expected = untimed(score_prepared(scorer, prepared))

    def refuse(*args, **kwargs):
        raise AssertionError("scoring a prepared task loaded or tokenized again")

    monkeypatch.setattr(Dataset, "load", refuse)
    monkeypatch.setattr(scorer, "tokenize", refuse)
    monkeypatch.setattr(scorer, "encode_requests", refuse)
    assert untimed(score_prepared(scorer, prepared)) == expected


def test_a_prepared_task_refuses_a_model_with_another_tokenizer(lm, tokenizer, scorer):
    prepared = prepare_task(scorer, TASK)
    # An equal copy is refused too: the check is identity, which costs nothing per step.
    other = ScoringModel(lm, copy.deepcopy(tokenizer), name="other@0")

    with pytest.raises(ValueError, match="prepared with another tokenizer than other@0's"):
        score_prepared(other, prepared)
    # A new model around the same tokenizer object, as a training loop builds, is fine.
    same = ScoringModel(lm, tokenizer, name="tiny@1")
    assert untimed(score_prepared(same, prepared)) == untimed(score_prepared(scorer, prepared))


def test_the_scoring_models_max_length_is_checked_at_every_call(lm, tokenizer, scorer):
    prepared = prepare_task(scorer, TEXT_TASK)
    short = ScoringModel(lm, tokenizer, name="tiny@0", max_length=4)

    with pytest.raises(ValueError, match="window must be from 1 to max_length 4, got 8"):
        score_prepared(short, prepared)


def test_prepare_task_rejects_a_limit_below_one(scorer):
    for task in (TASK, TEXT_TASK):
        with pytest.raises(ValueError, match="limit must be at least 1"):
            prepare_task(scorer, task, limit=0)
