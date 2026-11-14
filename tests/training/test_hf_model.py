"""`HFCompatibleModel` (017, D125 to D128) on CPU, with tiny transformers models saved to a local
folder that stands in for a Hub repository."""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Any, cast

import pytest
import torch
from transformers import AutoModelForCausalLM, LlamaConfig, Olmo2Config, Qwen3Config

from flock.hub.models.files import build, write_model
from flock.training.core.lr_scheduler import CosineConfig
from flock.training.core.models import HFCompatibleConfig, HFCompatibleModel, registry
from flock.training.core.optimization import AdamWConfig
from flock.training.core.trainer import Trainer, TrainerConfig

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from lance.torch.data import SafeLanceDataset
    from transformers import PretrainedConfig, PreTrainedModel, TokenizersBackend

    from flock.training.core.data_utils import Batch

SIZES: dict[str, Any] = {
    "vocab_size": 32,
    "hidden_size": 16,
    "intermediate_size": 32,
    "num_hidden_layers": 2,
    "num_attention_heads": 2,
    "num_key_value_heads": 1,
    "max_position_embeddings": 16,
    "bos_token_id": 1,
    "eos_token_id": 1,
    "pad_token_id": 1,
}
CONFIGS: dict[str, Callable[[], PretrainedConfig]] = {
    "llama": lambda: LlamaConfig(**SIZES),
    "qwen3": lambda: Qwen3Config(**SIZES, head_dim=8, tie_word_embeddings=True),
    "olmo2": lambda: Olmo2Config(**SIZES),
}


def make_batch(input_ids: torch.Tensor, attention_mask: torch.Tensor | None = None) -> Batch:
    return {
        "input_ids": input_ids,
        "attention_mask": torch.ones_like(input_ids) if attention_mask is None else attention_mask,
        "labels": torch.full_like(input_ids, -100),
    }


def same_weights(a: torch.nn.Module, b: torch.nn.Module) -> bool:
    left, right = a.state_dict(), b.state_dict()
    return left.keys() == right.keys() and all(torch.equal(left[k], right[k]) for k in left)


@pytest.fixture(params=list(CONFIGS))
def repo(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Path, PreTrainedModel]:
    """A tiny transformers model saved as a repository would hold it, and the model itself."""
    torch.manual_seed(0)
    model = AutoModelForCausalLM.from_config(CONFIGS[request.param]()).eval()
    folder = tmp_path / request.param
    model.save_pretrained(folder)
    return folder, model


def test_hf_is_registered() -> None:
    assert registry["hf"] is HFCompatibleModel


def test_the_same_seed_gives_the_same_weights(repo: tuple[Path, PreTrainedModel]) -> None:
    folder, _ = repo
    torch.manual_seed(1)
    first = HFCompatibleModel.from_hf(str(folder))
    torch.randn(100)
    torch.manual_seed(999)
    second = HFCompatibleModel.from_hf(str(folder))
    assert same_weights(first, second)
    other = HFCompatibleModel.from_hf(str(folder), init_seed=7)
    assert other.config.init_seed == 7
    embeddings = "model.model.embed_tokens.weight"
    assert not torch.equal(first.state_dict()[embeddings], other.state_dict()[embeddings])
    assert first.config.hf_repo == str(folder.resolve())
    assert first.max_position_embeddings == 16


def test_initialization_preserves_the_global_rng(repo: tuple[Path, PreTrainedModel]) -> None:
    folder, _ = repo
    config = HFCompatibleModel.from_hf(str(folder)).config
    before = torch.get_rng_state().clone()
    HFCompatibleModel(config)
    assert torch.equal(torch.get_rng_state(), before)


def test_settings_default_to_the_hf_config(tmp_path: Path) -> None:
    hf_config = {**LlamaConfig(**SIZES, initializer_range=0.05).to_dict()}
    model = HFCompatibleModel(HFCompatibleConfig(hf_config=hf_config))
    assert model.initializer_range == 0.05
    assert model.max_position_embeddings == 16
    shorter = HFCompatibleModel(
        HFCompatibleConfig(hf_config=hf_config, max_position_embeddings=8, initializer_range=0.1)
    )
    assert (shorter.max_position_embeddings, shorter.initializer_range) == (8, 0.1)
    assert shorter.model.config.max_position_embeddings == 16  # the architecture is unchanged
    with pytest.raises(ValueError, match="max_position_embeddings"):
        shorter(make_batch(torch.ones(1, 9, dtype=torch.long)))


def test_overrides_change_the_hf_config(repo: tuple[Path, PreTrainedModel]) -> None:
    folder, _ = repo
    model = HFCompatibleModel.from_hf(str(folder), num_hidden_layers=1, hf_repo="acme/tokenizer")
    assert model.config.hf_config["num_hidden_layers"] == 1
    assert model.config.hf_repo == "acme/tokenizer"
    assert len(model.model.model.layers) == 1
    original = json.loads((folder / "config.json").read_text())
    assert original["num_hidden_layers"] == 2


def test_logits_match_transformers_with_left_padding(repo: tuple[Path, PreTrainedModel]) -> None:
    folder, reference = repo
    model = HFCompatibleModel.from_hf(str(folder), pretrained=True).eval()
    assert same_weights(model.model, reference)
    ids = torch.tensor([[5, 6, 7, 8, 9], [3, 4, 5, 6, 7]])
    padded = torch.tensor([[1, 1, 5, 6, 7, 8, 9], [3, 4, 5, 6, 7, 1, 1]])
    mask = torch.tensor([[0, 0, 1, 1, 1, 1, 1], [1, 1, 1, 1, 1, 0, 0]])
    with torch.no_grad():
        expected = reference(input_ids=ids).logits
        torch.testing.assert_close(model(make_batch(ids)), expected)
        actual = model(make_batch(padded, mask))
    torch.testing.assert_close(actual[0, 2:], expected[0])
    torch.testing.assert_close(actual[1, :5], expected[1])
    assert torch.isfinite(actual).all()


def test_tied_embeddings_stay_tied(tmp_path: Path) -> None:
    torch.manual_seed(0)
    AutoModelForCausalLM.from_config(CONFIGS["qwen3"]()).save_pretrained(tmp_path)
    for model in (
        HFCompatibleModel.from_hf(str(tmp_path)),
        HFCompatibleModel.from_hf(str(tmp_path), pretrained=True),
    ):
        assert model.model.lm_head.weight is model.model.get_input_embeddings().weight


def test_write_model_then_build_round_trips(
    repo: tuple[Path, PreTrainedModel], tmp_path: Path
) -> None:
    folder, _ = repo
    model = HFCompatibleModel.from_hf(str(folder), init_seed=3).eval()
    saved = tmp_path / "saved"
    write_model(model, saved, shard_size=2048, run=None, step=None, training_state=None)
    assert (saved / "model.safetensors.index.json").exists()
    config = json.loads((saved / "config.json").read_text())
    assert config == dataclasses.asdict(model.config)
    described = json.loads((saved / "flock.json").read_text())
    assert described["architecture"] == "flock.training.core.models.hf:HFCompatibleModel"
    assert described["config_class"] == "flock.training.core.models.hf:HFCompatibleConfig"

    loaded = cast("HFCompatibleModel", build(saved, "cpu", None)).eval()
    assert isinstance(loaded, HFCompatibleModel) and loaded.config == model.config
    assert same_weights(model, loaded)
    ids = torch.tensor([[2, 3, 4, 5]])
    with torch.no_grad():
        torch.testing.assert_close(loaded(make_batch(ids)), model(make_batch(ids)))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            {"model_type": "my_remote_model", "auto_map": {"AutoModelForCausalLM": "m.M"}},
            "doesn't ship",
        ),
        ({"architectures": ["EvilForCausalLM"]}, "doesn't ship"),
        ({"model_type": "vit"}, "isn't a causal language model"),
    ],
)
def test_architectures_transformers_doesnt_ship_are_refused(
    changes: dict[str, Any], message: str
) -> None:
    hf_config = {**LlamaConfig(**SIZES).to_dict(), **changes}
    with pytest.raises(ValueError, match=message):
        HFCompatibleConfig(hf_config=hf_config)


def test_hf_config_must_be_json() -> None:
    with pytest.raises(ValueError, match="only JSON"):
        HFCompatibleConfig(hf_config={**LlamaConfig(**SIZES).to_dict(), "x": object()})
    with pytest.raises(TypeError, match="must be a dict"):
        HFCompatibleConfig(hf_config=cast("Any", "llama"))


class Run:
    """Stands in for a wandb run."""

    entity, project, id = "flock", "pretrain", "run123"

    def define_metric(self, *args: Any, **kwargs: Any) -> None:
        pass

    def log(self, metrics: dict[str, Any]) -> None:
        pass


def make_trainer(
    model: HFCompatibleModel,
    tokenizer: TokenizersBackend,
    output_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    **overrides: Any,
) -> Trainer:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr("flock.training.core.trainer.wandb.init", lambda **kwargs: Run())
    monkeypatch.setattr("flock.training.core.trainer.wandb.log", lambda *a, **k: None)
    dataset = [{"text": t} for t in ("a b c d", "b c", "c d a", "a b c", "d a")]
    return Trainer(
        config=TrainerConfig.model_validate(
            {
                "run_name": "hf-test",
                "input_column": "text",
                "batch_size": 2,
                "optimizer_config": AdamWConfig(lr=0.01, betas=(0.8, 0.9)),
                "lr_scheduler_config": CosineConfig(warmup_steps=0, min_lr_ratio=0.1),
                "dataloader_num_workers": 0,
                "dataloader_prefetch_factor": None,
                "wandb_project": "offline-test",
                "wandb_mode": "disabled",
                "output_dir": str(output_dir),
                "save_steps": 1,
                **overrides,
            }
        ),
        model=model,
        tokenizer=tokenizer,
        dataset=cast("SafeLanceDataset", dataset),
    )


def test_training_and_resuming_from_a_checkpoint(
    repo: tuple[Path, PreTrainedModel],
    tokenizer: TokenizersBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    folder, _ = repo
    start = HFCompatibleModel.from_hf(str(folder))
    reference = make_trainer(
        HFCompatibleModel(start.config), tokenizer, tmp_path / "reference", monkeypatch
    )
    reference.train()
    assert not same_weights(reference.model, start)  # the train steps changed the weights

    interrupted = make_trainer(
        HFCompatibleModel(start.config), tokenizer, tmp_path / "resumed", monkeypatch
    )
    save = interrupted.save_model

    def save_and_stop(blocking: bool = False) -> None:
        save(blocking)
        if interrupted.state.step.item() == 1:
            raise InterruptedError("stop after the first checkpoint")

    monkeypatch.setattr(interrupted, "save_model", save_and_stop)
    with pytest.raises(InterruptedError):
        interrupted.train()

    resumed = make_trainer(
        HFCompatibleModel(start.config), tokenizer, tmp_path / "resumed", monkeypatch
    )
    resumed.train(resume_from_checkpoint=tmp_path / "resumed" / "checkpoint-1")
    assert same_weights(resumed.model, reference.model)
