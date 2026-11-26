import pytest
import torch

from flock.training.core.data_utils import Batch
from flock.training.core.models import BaseModel


class EchoModel(BaseModel):
    """Exercise shared input handling independently of any model architecture."""

    def num_flops_per_token(self, seq_len: int) -> int:
        return 0

    def _forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
        *,
        cu_seq_lens: torch.Tensor | None = None,
    ) -> torch.Tensor:
        self.received_mask = attention_mask
        self.received_positions = position_ids
        self.received_boundaries = cu_seq_lens
        return input_ids


def test_base_model_prepares_collated_batch() -> None:
    model = EchoModel(max_position_embeddings=4)
    batch: Batch = {
        "input_ids": torch.tensor([[0, 2, 3]]),
        "attention_mask": torch.tensor([[0, 1, 1]]),
        "labels": torch.tensor([[-100, 3, 4]]),
        "position_ids": torch.tensor([[0, 0, 1]]),
    }
    assert model(batch) is batch["input_ids"]
    assert model.received_positions is batch["position_ids"]
    assert model.received_mask.dtype == torch.bool
    assert model.received_mask.device == batch["input_ids"].device
    assert model.received_mask.tolist() == [[False, True, True]]


def test_base_model_prepares_unpadded_batch() -> None:
    model = EchoModel(max_position_embeddings=4)
    batch: Batch = {
        "input_ids": torch.tensor([[1, 2, 3]]),
        "attention_mask": torch.ones(1, 3, dtype=torch.bool),
        "labels": torch.tensor([[2, 3, 4]]),
        "position_ids": torch.tensor([[0, 1, 2]]),
    }
    model(batch)
    assert model.received_positions is batch["position_ids"]
    assert model.received_mask is batch["attention_mask"]
    assert model.received_boundaries is None


def test_base_model_passes_packed_boundaries() -> None:
    model = EchoModel(max_position_embeddings=4)
    batch: Batch = {
        "input_ids": torch.tensor([[1, 2, 3]]),
        "attention_mask": torch.ones(1, 3, dtype=torch.bool),
        "labels": torch.tensor([[2, -100, 4]]),
        "position_ids": torch.tensor([[0, 1, 0]]),
        "cu_seq_lens": torch.tensor([0, 2, 3], dtype=torch.int32),
    }
    model(batch)
    assert model.received_positions is batch["position_ids"]
    assert model.received_boundaries is batch["cu_seq_lens"]


def test_base_model_rejects_overlong_input_before_forward() -> None:
    model = EchoModel(max_position_embeddings=2)
    with pytest.raises(ValueError, match="max_position_embeddings"):
        model(
            {
                "input_ids": torch.ones(1, 3, dtype=torch.long),
                "attention_mask": torch.ones(1, 3, dtype=torch.bool),
                "labels": torch.ones(1, 3, dtype=torch.long),
                "position_ids": torch.arange(3)[None],
            }
        )
    assert not hasattr(model, "received_mask")
