from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import lance
import pyarrow as pa
import pytest
import torch

from flock.training import DatasetConfig
from flock.training.core.models import Olmo2
from flock.training.experiments.proxy.olmo2_proxy import PROXY_SIZES, ProxySize, build_trainer

if TYPE_CHECKING:
    from pathlib import Path

    from transformers import TokenizersBackend

    from flock.training.core.models.olmo2 import Olmo2Config


@pytest.mark.parametrize(
    ("size", "expected"),
    [("76m", 76_308_864), ("190m", 190_354_176)],
)
def test_presets_have_the_ladder_parameter_counts(size: str, expected: int) -> None:
    with torch.device("meta"):
        model = Olmo2(PROXY_SIZES[size].model)
    # The OLMo ladder counts every parameter except the input embedding.
    counted = sum(
        parameter.numel()
        for name, parameter in model.named_parameters()
        if name != "model.embed_tokens.weight"
    )
    assert counted == expected


def test_recipe_trains_two_updates_on_a_local_dataset(
    config: Olmo2Config,
    tokenizer: TokenizersBackend,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    tokenizer.save_pretrained(tmp_path / "tokenizer")
    proxy = ProxySize(model=replace(config, hf_repo=str(tmp_path / "tokenizer")), peak_lr=0.01)
    texts = ["a b c d", "b c", "c d a", "a b c", "d a", "b"]
    lance.write_dataset(pa.table({"text": texts}), tmp_path / "mix.lance")

    trainer = build_trainer(
        proxy,
        DatasetConfig(uri=str(tmp_path / "mix.lance"), version=1),
        run_name="proxy-test",
        batch_size=2,
        warmup_steps=1,
        output_dir=str(tmp_path / "checkpoints"),
        max_steps=2,
        num_workers=1,
        wandb_mode="disabled",
    )
    trainer.train()

    assert int(trainer.state.step) == 2
    # Seed 0 draws rows 2 and 5, then 3 and 0. A row's targets are its words plus EOS, minus
    # one: 3 + 1, then 3 + 4.
    assert int(trainer.state.total_tokens) == 11
    # Cosine ends at 10% of the peak at the capped final update.
    assert trainer.optimizer.param_groups[0]["lr"] == pytest.approx(0.001)
    assert (tmp_path / "checkpoints" / "checkpoint-2").is_dir()
