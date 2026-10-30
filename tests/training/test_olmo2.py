from dataclasses import asdict, replace

import pytest
import torch
from tokenizers import Tokenizer, models, pre_tokenizers
from torch.nn import functional as F
from transformers import Olmo2Config as HFConfig
from transformers import Olmo2ForCausalLM, TokenizersBackend

from flock.training.core.data_utils import Batch, LanceBatchCollator
from flock.training.core.models.olmo2 import OLMO2_1B_CONFIG, OLMO2_7B_CONFIG, Olmo2, Olmo2Config


def make_batch(input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None) -> Batch:
    return {
        "input_ids": input_ids,
        "attention_mask": torch.ones_like(input_ids) if attention_mask is None else attention_mask,
        "labels": torch.full_like(input_ids, -100),
    }


@pytest.fixture(autouse=True, scope="module")
def one_thread():
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
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
        models.WordLevel({"[UNK]": 0, "[EOS]": 1, "a": 2, "b": 3, "c": 4}, unk_token="[UNK]")
    )
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    return TokenizersBackend(tokenizer_object=backend, eos_token="[EOS]")


@pytest.mark.parametrize("kv_heads", [4, 2])
@pytest.mark.parametrize("padded", [False, True])
def test_huggingface_forward_and_gradient_parity(config, kv_heads, padded):
    config = replace(config, num_key_value_heads=kv_heads)
    torch.manual_seed(0)
    model = Olmo2(config)
    hf_config = HFConfig(**asdict(config), pad_token_id=None, use_cache=False)
    hf_config._attn_implementation = "eager"
    reference = Olmo2ForCausalLM(hf_config)
    # Strict loading also verifies checkpoint names and all parameter shapes.
    reference.load_state_dict(model.state_dict(), strict=True)

    ids = torch.tensor([[0, 0, 2, 3, 4], [5, 6, 7, 8, 9]])
    mask = torch.tensor([[0, 0, 1, 1, 1], [1, 1, 1, 1, 1]]) if padded else None
    positions = (mask.cumsum(-1) - 1).clamp_min(0) if padded else None
    actual = model(make_batch(ids, mask))
    expected = reference(ids, attention_mask=mask, position_ids=positions).logits
    valid = mask.bool() if padded else torch.ones_like(ids, dtype=torch.bool)
    torch.testing.assert_close(actual[valid], expected[valid], atol=1e-6, rtol=1e-5)

    targets = torch.randint(config.vocab_size, ids.shape)
    F.cross_entropy(actual[valid], targets[valid]).backward()
    F.cross_entropy(expected[valid], targets[valid]).backward()
    for name, parameter in model.named_parameters():
        torch.testing.assert_close(
            parameter.grad,
            reference.get_parameter(name).grad,
            atol=2e-5,
            rtol=2e-4,
            msg=lambda error, name=name: f"{name}: {error}",
        )


def test_causality_and_padding_invariance(config):
    model = Olmo2(config).eval()
    ids = torch.tensor([[2, 3, 4, 5]])
    with torch.no_grad():
        expected = model(make_batch(ids))
        changed = model(make_batch(torch.tensor([[2, 3, 20, 21]])))
        torch.testing.assert_close(expected[:, :2], changed[:, :2])
        for padded, mask, start in (
            ([[0, 0, 2, 3, 4, 5]], [[0, 0, 1, 1, 1, 1]], 2),
            ([[2, 3, 4, 5, 0, 0]], [[1, 1, 1, 1, 0, 0]], 0),
        ):
            actual = model(make_batch(torch.tensor(padded), torch.tensor(mask)))
            torch.testing.assert_close(actual[:, start : start + 4], expected)


def test_collated_training_batch(config, tokenizer, monkeypatch):
    monkeypatch.setattr(
        "flock.training.core.models.base.AutoTokenizer.from_pretrained",
        lambda repo, **kwargs: tokenizer,
    )
    model = Olmo2(replace(config, hf_repo="offline"))
    tokenizer = model.get_hf_tokenizer()
    tokenizer.padding_side = "left"
    batch = LanceBatchCollator(tokenizer, "text").collate_fn([{"text": "a b c"}, {"text": "b c"}])
    assert batch["input_ids"].shape == batch["attention_mask"].shape == batch["labels"].shape
    assert batch["labels"].tolist() == [[3, 4], [-100, 4]]
    logits = model(batch)
    loss = F.cross_entropy(logits.flatten(0, 1), batch["labels"].flatten())
    loss.backward()
    assert torch.isfinite(loss)
    assert all(p.grad is not None and p.grad.isfinite().all() for p in model.parameters())


@pytest.mark.parametrize("dtype", [torch.float32, torch.bfloat16])
def test_fully_masked_rows_are_finite(config, dtype):
    model = Olmo2(config).to(dtype=dtype)
    ids = torch.tensor([[0, 0, 0], [2, 3, 4]])
    logits = model(make_batch(ids, torch.tensor([[0, 0, 0], [1, 1, 1]])))
    assert logits.dtype == dtype
    assert logits.isfinite().all()
    logits.float().square().mean().backward()
    assert all(p.grad.isfinite().all() for p in model.parameters())


@pytest.mark.parametrize(
    "config, expected_count",
    [
        (OLMO2_1B_CONFIG, 1_484_916_736),
        (OLMO2_7B_CONFIG, 7_298_617_344),
    ],
)
def test_presets(config, expected_count):
    # Validate full-size shapes without allocating billions of weights.
    with torch.device("meta"):
        model = Olmo2(config)
    assert sum(p.numel() for p in model.parameters()) == expected_count
    assert model.lm_head.weight is not model.model.embed_tokens.weight
    assert config.max_position_embeddings == 4096


@pytest.mark.parametrize(
    "changes",
    [
        {"num_hidden_layers": 0},
        {"num_key_value_heads": 0},
        {"hidden_size": 31},
        {"num_key_value_heads": 3},
        {"hidden_size": 28},
        {"rms_norm_eps": 0},
    ],
)
def test_invalid_config(config, changes):
    with pytest.raises(ValueError):
        replace(config, **changes)


def test_invalid_input_shapes(config):
    model = Olmo2(config)
    for ids in (torch.ones(2, dtype=torch.long), torch.ones(1, 17, dtype=torch.long)):
        with pytest.raises(ValueError):
            model(make_batch(ids))
    ids = torch.ones(1, 3, dtype=torch.long)
    with pytest.raises(ValueError, match="attention_mask"):
        model(make_batch(ids, attention_mask=torch.ones(1, 4)))


def test_tokenizer_config(config, tokenizer, monkeypatch):
    calls = []

    def load(repo, **kwargs):
        calls.append((repo, kwargs))
        return tokenizer

    monkeypatch.setattr("flock.training.core.models.base.AutoTokenizer.from_pretrained", load)
    assert Olmo2(config).get_hf_tokenizer() is None
    model = Olmo2(replace(config, hf_repo="offline"))
    assert model.get_hf_tokenizer() is tokenizer
    assert tokenizer.pad_token_id == tokenizer.eos_token_id
    assert calls == [("offline", {"padding_side": "left", "model_max_length": 16})]


def test_initialization_is_independent_of_global_rng(config):
    torch.manual_seed(1)
    first = Olmo2(config)
    torch.randn(100)
    torch.manual_seed(999)
    second = Olmo2(config)
    for name, weight in first.state_dict().items():
        assert torch.equal(weight, second.state_dict()[name]), name
    # A single stream gives distinct weights to different layers/projections.
    assert not torch.equal(
        first.model.layers[0].self_attn.q_proj.weight,
        first.model.layers[1].self_attn.q_proj.weight,
    )
    assert not torch.equal(first.model.embed_tokens.weight, first.lm_head.weight)


def test_explicit_init_seed(config):
    first = Olmo2(replace(config, init_seed=123))
    second = Olmo2(replace(config, init_seed=123))
    different = Olmo2(replace(config, init_seed=456))
    for name, weight in first.state_dict().items():
        assert torch.equal(weight, second.state_dict()[name]), name
    assert not torch.equal(first.lm_head.weight, different.lm_head.weight)


@pytest.mark.parametrize("device", ["cpu", "meta"])
def test_initialization_preserves_global_rng(config, device):
    before = torch.get_rng_state().clone()
    with torch.device(device):
        Olmo2(config)
    assert torch.equal(torch.get_rng_state(), before)
