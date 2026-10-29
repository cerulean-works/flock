import math
from typing import Any

import pytest
import torch

from flock.eval.metrics import (
    MULTIPLE_CHOICE_METRICS,
    ChoiceScores,
    accuracy,
    bits_per_byte,
    multiple_choice,
)


def test_bits_per_byte_matches_hand_value():
    # " 玉山" is a space plus two 3-byte characters: 7 bytes. -ln(1/128) nats is 7 bits.
    num_bytes = torch.tensor([len(" 玉山".encode())])
    assert num_bytes.item() == 7
    logprob = torch.tensor([math.log(1 / 128)])
    assert bits_per_byte(logprob, num_bytes).item() == pytest.approx(1.0)


def test_bits_per_byte_counts_at_least_one_byte_and_warns(caplog):
    bpb = bits_per_byte(torch.tensor([-math.log(2)] * 3), torch.tensor([0, -1, 1]))

    assert bpb.tolist() == pytest.approx([1, 1, 1])
    assert "2 byte counts are below 1" in caplog.text


def test_bits_per_byte_is_quiet_on_valid_counts(caplog):
    bits_per_byte(torch.tensor([-1.0]), torch.tensor([1]))
    assert not caplog.records


def test_accuracy_ignores_padding_and_breaks_ties_to_first():
    score = torch.tensor([[1.0, 1.0, 0.0], [0.0, 2.0, 9.0]])
    valid = torch.tensor([[True, True, True], [True, True, False]])
    assert accuracy(score, valid, torch.tensor([1, 1])).tolist() == [0.0, 1.0]
    assert accuracy(score, valid, torch.tensor([0, 2])).tolist() == [1.0, 0.0]


def scores(**overrides) -> ChoiceScores:
    """One doc, two choices: " A" (gold, 2 tokens) and " BBBBBBB" (1 token)."""
    fields: dict[str, Any] = {
        "logprob": torch.tensor([[-4.0, -3.0]]),
        "num_tokens": torch.tensor([[2, 1]]),
        "num_chars": torch.tensor([[2, 8]]),
        "num_bytes": torch.tensor([[2, 8]]),
        "valid": torch.tensor([[True, True]]),
        "gold": torch.tensor([0]),
        "tokens_cut": torch.tensor([[0, 0]]),
        "logprob_uncond": None,
    }
    return ChoiceScores(**(fields | overrides))


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # raw: -4 < -3, so the wrong choice wins
        ({}, {"acc_raw": 0.0}),
        # per token: -4/2 = -2 beats -3/1
        ({}, {"acc_per_token": 1.0}),
        # per char: -4/2 = -2 loses to -3/8
        ({}, {"acc_per_char": 0.0}),
        # per byte: same counts as chars here
        ({}, {"acc_per_byte": 0.0}),
        # per byte differs from per char when byte and character counts disagree
        ({"num_bytes": torch.tensor([[2, 1]])}, {"acc_per_byte": 1.0, "acc_per_char": 0.0}),
        # PMI: -4 - (-9) = 5 beats -3 - (-4) = 1
        ({"logprob_uncond": torch.tensor([[-9.0, -4.0]])}, {"acc_uncond": 1.0}),
    ],
)
def test_each_normalization(overrides, expected):
    metrics = multiple_choice(scores(**overrides))
    for name, value in expected.items():
        assert metrics[name].item() == value, name


def test_multiple_choice_reports_gold_bpb_and_skips_uncond_without_it():
    metrics = multiple_choice(scores())
    assert "acc_uncond" not in metrics
    assert metrics["bpb"].item() == pytest.approx(4.0 / math.log(2) / 2)
    assert list(metrics) == ["acc_raw", "acc_per_token", "acc_per_char", "acc_per_byte", "bpb"]


def test_multiple_choice_metrics_lists_every_name_in_order():
    # results.json writes the metrics in this order.
    metrics = multiple_choice(scores(logprob_uncond=torch.tensor([[-5.0, -5.0]])))
    assert list(metrics) == list(MULTIPLE_CHOICE_METRICS)
