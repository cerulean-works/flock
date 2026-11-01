"""Small, offline fixtures shared by the training tests."""

import random

import pytest
import torch
from tokenizers import Tokenizer, models, pre_tokenizers
from transformers import TokenizersBackend

from flock.training.core.models.olmo2 import Olmo2Config


@pytest.fixture(autouse=True)
def isolated_random_state(monkeypatch):
    state = random.getstate()
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    devices = list(range(torch.cuda.device_count())) if torch.cuda.is_initialized() else []
    with torch.random.fork_rng(devices=devices):
        torch.random.default_generator.manual_seed(0)
        try:
            yield
        finally:
            random.setstate(state)


@pytest.fixture(autouse=True, scope="module")
def one_thread():
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        yield
    finally:
        torch.set_num_threads(threads)


@pytest.fixture
def config():
    return Olmo2Config(
        vocab_size=32,
        hidden_size=32,
        intermediate_size=48,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=4,
        max_position_embeddings=16,
    )


@pytest.fixture
def tokenizer():
    backend = Tokenizer(
        models.WordLevel(
            {"[UNK]": 0, "[EOS]": 1, "a": 2, "b": 3, "c": 4, "d": 5},
            unk_token="[UNK]",
        )
    )
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    return TokenizersBackend(
        tokenizer_object=backend,
        eos_token="[EOS]",
        pad_token="[EOS]",
        model_max_length=16,
        padding_side="left",
    )


@pytest.fixture
def cuda_device(monkeypatch):
    """GPU selection is explicit: a requested GPU test must never silently skip."""
    monkeypatch.setenv("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    assert torch.cuda.is_available(), (
        "gpu-marked tests require a CUDA-enabled PyTorch build and GPU"
    )
    assert torch.cuda.is_bf16_supported(), "the GPU test runner must support BF16"
    matmul_tf32 = torch.backends.cuda.matmul.allow_tf32
    cudnn_tf32 = torch.backends.cudnn.allow_tf32
    deterministic = torch.are_deterministic_algorithms_enabled()
    warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
    with torch.random.fork_rng(devices=[0]):
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        torch.use_deterministic_algorithms(True)
        try:
            yield torch.device("cuda:0")
        finally:
            torch.backends.cuda.matmul.allow_tf32 = matmul_tf32
            torch.backends.cudnn.allow_tf32 = cudnn_tf32
            torch.use_deterministic_algorithms(deterministic, warn_only=warn_only)
