"""Pretrained Llama 3.2 1B against Hugging Face's implementation, in BF16 on a GPU.

These download Meta's gated checkpoint, so they need a Hugging Face token with access
to it. Run them with `-m e2e`.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import pytest
import torch
from huggingface_hub import snapshot_download
from transformers import AutoTokenizer, LlamaForCausalLM

from flock.training.core.models.llama32 import LLAMA32_1B_CONFIG, Llama32
from tests.training._reference import assert_parity, llama32_hf_config
from tests.training.test_llama32 import LLAMA32_1B_HF_CONFIG

pytestmark = pytest.mark.e2e

REPO = "meta-llama/Llama-3.2-1B"

TEXTS = [
    "The capital of Taiwan is Taipei, which is home to Taipei 101.",
    "臺灣位於東亞，是一個以半導體產業聞名的島嶼。",
    "def fibonacci(n):\n    return n if n < 2 else fibonacci(n - 1) + fibonacci(n - 2)",
    "Hi",
]


class Models(NamedTuple):
    flock: Llama32
    reference: LlamaForCausalLM
    # The same BF16 weights upcast, giving the FP32 answer both BF16 runs are judged by.
    fp32_reference: LlamaForCausalLM
    checkpoint: Path


@pytest.fixture(scope="module")
def models() -> Models:
    checkpoint = Path(snapshot_download(REPO, allow_patterns=["*.json", "*.safetensors"]))
    device = torch.device("cuda:0")
    reference = LlamaForCausalLM.from_pretrained(checkpoint, dtype=torch.bfloat16)
    reference.to(device=device)  # ty: ignore[missing-argument]
    reference.eval()
    fp32_reference = LlamaForCausalLM.from_pretrained(
        checkpoint, dtype=torch.float32, attn_implementation="eager"
    )
    fp32_reference.to(device=device)  # ty: ignore[missing-argument]
    reference.eval()
    with device:
        flock = Llama32(LLAMA32_1B_CONFIG)
    flock.to(torch.bfloat16).eval()
    flock.load_state_dict(reference.state_dict(), strict=True)
    assert flock.lm_head.weight is flock.model.embed_tokens.weight
    return Models(flock, reference, fp32_reference, checkpoint)


def assert_forward_parity(models: Models, ids: torch.Tensor, mask: torch.Tensor) -> None:
    positions = (mask.long().cumsum(-1) - 1).clamp_min(0)
    batch = {
        "input_ids": ids,
        "attention_mask": mask,
        "labels": torch.full_like(ids, -100),
        "position_ids": positions,
    }
    with torch.no_grad():
        actual = models.flock(batch)[mask]
        expected, fp32_expected = (
            reference(ids, attention_mask=mask, position_ids=positions).logits[mask]
            for reference in (models.reference, models.fp32_reference)
        )
    assert actual.dtype == expected.dtype == torch.bfloat16
    assert_parity(actual, expected, torch.bfloat16, "logits", "logits", fp32=fp32_expected)


def test_preset_matches_the_checkpoint_config(models: Models) -> None:
    published = models.reference.config.to_dict()
    ours = llama32_hf_config(LLAMA32_1B_CONFIG).to_dict()
    for name in LLAMA32_1B_HF_CONFIG:
        assert ours[name] == published[name], name


def test_padded_text_forward_matches_huggingface(models: Models, cuda_device: torch.device) -> None:
    tokenizer = AutoTokenizer.from_pretrained(models.checkpoint, padding_side="left")
    assert tokenizer is not None
    tokenizer.pad_token = tokenizer.eos_token
    encoded = tokenizer(TEXTS, padding=True, return_tensors="pt").to(cuda_device)
    mask = encoded["attention_mask"].bool()
    assert not mask.all(), "the batch should include left padding"
    assert_forward_parity(models, encoded["input_ids"], mask)


def test_forward_past_the_original_context_matches_huggingface(
    models: Models, cuda_device: torch.device
) -> None:
    # Positions past 8192 are where the Llama 3 RoPE scaling differs most from plain RoPE.
    # IDs from 128000 on are special tokens, so draw ordinary ones.
    generator = torch.Generator(device=cuda_device).manual_seed(0)
    ids = torch.randint(128000, (1, 9216), generator=generator, device=cuda_device)
    assert_forward_parity(models, ids, torch.ones_like(ids, dtype=torch.bool))
