import pytest
import torch

from flock.training.core.data_utils import Batch
from flock.training.core.models import BaseModel


class EchoModel(BaseModel):
    """Exercise shared input handling independently of any model architecture."""

    def _forward(self, input_ids, attention_mask, position_ids):
        self.received_mask = attention_mask
        self.received_positions = position_ids
        return input_ids


def test_base_model_prepares_collated_batch():
    model = EchoModel(max_position_embeddings=4)
    batch: Batch = {
        "input_ids": torch.tensor([[0, 2, 3]]),
        "attention_mask": torch.tensor([[0, 1, 1]]),
        "labels": torch.tensor([[-100, 3, 4]]),
    }
    assert model(batch) is batch["input_ids"]
    assert model.received_positions.tolist() == [[0, 0, 1]]
    assert model.received_mask.tolist() == [
        [
            [
                [False, False, False],
                [False, True, False],
                [False, True, True],
            ]
        ]
    ]


def test_base_model_prepares_unpadded_batch():
    model = EchoModel(max_position_embeddings=4)
    batch: Batch = {
        "input_ids": torch.tensor([[1, 2, 3]]),
        "attention_mask": torch.ones(1, 3, dtype=torch.bool),
        "labels": torch.tensor([[2, 3, 4]]),
    }
    model(batch)
    assert model.received_positions.tolist() == [[0, 1, 2]]
    assert model.received_mask[0, 0].tolist() == [
        [True, False, False],
        [True, True, False],
        [True, True, True],
    ]


def test_base_model_rejects_overlong_input_before_forward():
    model = EchoModel(max_position_embeddings=2)
    with pytest.raises(ValueError, match="max_position_embeddings"):
        model(
            {
                "input_ids": torch.ones(1, 3, dtype=torch.long),
                "attention_mask": torch.ones(1, 3, dtype=torch.bool),
                "labels": torch.ones(1, 3, dtype=torch.long),
            }
        )
    assert not hasattr(model, "received_mask")
