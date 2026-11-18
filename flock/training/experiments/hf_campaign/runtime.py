"""HF campaign runtimes: one shared update loop, a CPU contract harness and a GPU path."""

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import torch
from accelerate import Accelerator
from torch.nn import functional as F
from torch.nn.attention import SDPBackend, sdpa_kernel
from transformers import Olmo2Config, Olmo2ForCausalLM, PreTrainedTokenizerBase

from .provisional import ProvisionalMix
from .stream import Block, TargetStream


class Experiment:
    """A fresh OLMo 2 model trained by aligned summed CE on a token schedule."""

    def __init__(
        self,
        config: Olmo2Config,
        *,
        accelerator: Accelerator,
        source: dict[str, Any],
        peak_lr: float,
        betas: tuple[float, float],
        weight_decay: float,
        seed: int,
        total_targets: int,
        warmup_targets: int,
    ) -> None:
        if not 0 < warmup_targets < total_targets:
            raise ValueError("warmup must precede the finite target endpoint")
        if accelerator.num_processes != 1:
            raise ValueError("distributed training requires a separately approved GPU gate")
        self.total_targets = total_targets
        self.warmup_targets = warmup_targets
        self.peak_lr = peak_lr
        self.source = source
        self.description = {
            "model": config.to_dict(),
            "attention_backend": config._attn_implementation,
            "precision": accelerator.mixed_precision,
            "optimizer": {
                "name": "AdamW",
                "peak_lr": peak_lr,
                "betas": list(betas),
                "weight_decay": weight_decay,
                "clip": 1.0,
            },
            "total": total_targets,
            "warmup": warmup_targets,
            "seed": seed,
            "source": source,
        }
        self.contract = hashlib.sha256(
            json.dumps(self.description, sort_keys=True).encode()
        ).hexdigest()
        torch.manual_seed(seed)
        self.accelerator = accelerator
        self.model = Olmo2ForCausalLM(config)
        self.optimizer = torch.optim.AdamW(
            self.model.parameters(), lr=peak_lr, betas=betas, weight_decay=weight_decay
        )
        self.model, self.optimizer = self.accelerator.prepare(self.model, self.optimizer)
        self.accelerator.register_for_checkpointing(self)
        self.updates = 0
        self.targets = 0

    def state_dict(self) -> dict:
        """Save the committed update and target counts under the runtime contract."""
        return {"updates": self.updates, "targets": self.targets, "contract": self.contract}

    def load_state_dict(self, state: dict) -> None:
        """Restore committed counts from a checkpoint of the identical runtime contract."""
        if state["contract"] != self.contract:
            raise ValueError("runtime contract changed")
        self.updates = state["updates"]
        self.targets = state["targets"]

    def step(self, micro_batches: list[tuple[torch.Tensor, torch.Tensor]]) -> float:
        """Apply one update over aligned `(inputs, labels)` micro-batches; return its mean loss."""
        total = sum(labels.numel() for _, labels in micro_batches)
        if total == 0:
            raise ValueError("an update needs at least one target")
        if self.targets + total > self.total_targets:
            raise ValueError("update would overshoot the target endpoint")
        # Linear warmup, then cosine to 10 percent of the peak, both by update-start targets.
        if self.targets < self.warmup_targets:
            multiplier = self.targets / self.warmup_targets
        else:
            progress = (self.targets - self.warmup_targets) / (
                self.total_targets - self.warmup_targets
            )
            multiplier = 0.1 + 0.9 * (1 + math.cos(math.pi * progress)) / 2
        for group in self.optimizer.param_groups:
            group["lr"] = self.peak_lr * multiplier
        self.optimizer.zero_grad()
        loss_sum = torch.zeros((), device=self.accelerator.device)
        try:
            for inputs, labels in micro_batches:
                # Summed over targets and divided by the update's total, so accumulating
                # micro-batches gives one batch's gradient. HF model labels would shift these
                # aligned targets a second time, and a named logits tensor would stay alive
                # through the next micro-batch's forward.
                loss = (
                    F.cross_entropy(
                        self.model(input_ids=inputs, use_cache=False).logits.float().flatten(0, 1),
                        labels.flatten(),
                        reduction="sum",
                    )
                    / total
                )
                self.accelerator.backward(loss)
                loss_sum += loss.detach()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0, error_if_nonfinite=True)
            self.optimizer.step()
        except Exception:
            self.optimizer.zero_grad()
            raise
        self.updates += 1
        self.targets += total
        return float(loss_sum)

    def checkpoint(self, destination: Path) -> None:
        """Commit a checksummed Accelerate checkpoint in a new immutable directory."""
        temporary = destination.with_name(destination.name + ".incomplete")
        # mkdir is the single-writer lock. Incomplete saves require explicit inspection.
        temporary.mkdir(parents=True, exist_ok=False)
        if destination.exists():
            temporary.rmdir()
            raise FileExistsError(destination)
        self.accelerator.save_state(str(temporary))
        hashes = {}
        for path in sorted(temporary.iterdir()):
            if path.is_file():
                hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
        manifest = temporary / "COMMITTED.json"
        manifest.write_text(
            json.dumps({"files": hashes, "contract": self.contract}, sort_keys=True)
        )
        with manifest.open("rb") as handle:
            os.fsync(handle.fileno())
        descriptor = os.open(temporary, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        temporary.rename(destination)
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def resume(self, checkpoint: Path) -> None:
        """Verify every checkpoint file before loading trusted local training state."""
        manifest = json.loads((checkpoint / "COMMITTED.json").read_text())
        if manifest["contract"] != self.contract:
            raise ValueError("checkpoint runtime contract changed")
        for name, expected in manifest["files"].items():
            if Path(name).name != name:
                raise ValueError("invalid checkpoint path")
            if hashlib.sha256((checkpoint / name).read_bytes()).hexdigest() != expected:
                raise ValueError("checkpoint checksum mismatch")
        self.accelerator.load_state(str(checkpoint))

    def export(self, destination: Path, tokenizer: PreTrainedTokenizerBase) -> None:
        """Write an HF model, its tokenizer and an `experiment.json` card for evaluation."""
        # Written aside and renamed, so an export directory is always complete.
        temporary = destination.with_name(destination.name + ".incomplete")
        temporary.mkdir(parents=True, exist_ok=False)
        if destination.exists():
            temporary.rmdir()
            raise FileExistsError(destination)
        self.accelerator.unwrap_model(self.model).save_pretrained(temporary)
        tokenizer.save_pretrained(temporary)
        # The card says what trained these weights, including whether the data was provisional.
        card = {
            "provisional": self.source.get("provisional", False),
            "contract": self.contract,
            "updates": self.updates,
            "targets": self.targets,
        }
        (temporary / "experiment.json").write_text(
            json.dumps(card | self.description, indent=2, sort_keys=True)
        )
        temporary.rename(destination)


class CpuExperiment(Experiment):
    """Exercise loss, committed stream state and export on admitted pools, with no launch path."""

    def __init__(
        self,
        config: Olmo2Config,
        stream: TargetStream,
        *,
        seed: int = 0,
        total_targets: int = 64,
        warmup_targets: int = 2,
    ) -> None:
        if (
            config.hidden_size > 64
            or config.num_hidden_layers > 2
            or config.intermediate_size > 256
            or config.vocab_size > 256
            or config.max_position_embeddings > 256
        ):
            raise ValueError("CPU harness accepts tiny synthetic configurations only")
        super().__init__(
            config,
            accelerator=Accelerator(cpu=True, mixed_precision="no"),
            source={"stream": stream.contract},
            peak_lr=1e-3,
            betas=(0.9, 0.999),
            weight_decay=0.01,
            seed=seed,
            total_targets=total_targets,
            warmup_targets=warmup_targets,
        )
        self.stream = stream
        self.committed = stream.state_dict()

    def state_dict(self) -> dict:
        """Save optimizer-matched data state rather than an advanced producer cursor."""
        return super().state_dict() | {"stream": self.committed}

    def load_state_dict(self, state: dict) -> None:
        """Restore the committed stream and discard speculative prefetched blocks."""
        super().load_state_dict(state)
        self.stream.load_state_dict(state["stream"])
        self.committed = self.stream.state_dict()

    def update(self, *, tc_targets: int, en_targets: int) -> tuple[float, list[Block]]:
        """Apply one exact global allocation, one block per micro-batch."""
        if tc_targets < 0 or tc_targets != en_targets or tc_targets == 0:
            raise ValueError("each synthetic update requires equal positive TC and EN targets")
        # Checked before any block is read: an oversized allocation must not consume the stream.
        if self.targets + tc_targets + en_targets > self.total_targets:
            raise ValueError("update would overshoot the target endpoint")
        # Producer state may have advanced through prefetch; only committed input is authoritative.
        self.stream.load_state_dict(self.committed)
        try:
            blocks = self.stream.blocks("tc", tc_targets) + self.stream.blocks("en", en_targets)
            loss = self.step([(torch.tensor([b.inputs]), torch.tensor([b.labels])) for b in blocks])
        except Exception:
            self.stream.load_state_dict(self.committed)
            raise
        self.committed = self.stream.state_dict()
        return loss, blocks


class GpuExperiment(Experiment):
    """Train on one device in BF16 with the flash SDPA kernel, from a provisional mix only."""

    def __init__(
        self,
        config: Olmo2Config,
        mix: ProvisionalMix,
        *,
        peak_lr: float,
        seed: int,
        total_targets: int,
        warmup_targets: int,
        micro_rows: int,
    ) -> None:
        if config._attn_implementation != "sdpa":
            raise ValueError("the GPU runtime trains with SDPA attention only")
        if config.max_position_embeddings < mix.window:
            raise ValueError("the mix's window is longer than the model's context")
        if total_targets % mix.targets_per_update or (
            total_targets > len(mix) * mix.targets_per_update
        ):
            raise ValueError("total targets must be whole updates within one pass over the mix")
        if micro_rows < 1:
            raise ValueError("micro-batches need at least one row")
        super().__init__(
            config,
            accelerator=Accelerator(mixed_precision="bf16"),
            source={"provisional": True, "mix": mix.contract},
            # 141's optimizer for the flock Trainer, so the two trainers differ only in packing,
            # precision and batch: the scaffold's betas (OLMo 2 states none) and PyTorch's 0.01
            # weight decay on every parameter.
            peak_lr=peak_lr,
            betas=(0.95, 0.99),
            weight_decay=0.01,
            seed=seed,
            total_targets=total_targets,
            warmup_targets=warmup_targets,
        )
        self.micro_rows = micro_rows

    def update(self, windows: torch.Tensor) -> float:
        """Apply the mix's update `self.updates` from its `[rows, window + 1]` token ids."""
        windows = windows.to(self.accelerator.device, non_blocking=True)
        # Packed rows are causal with no mask, so the flash kernel applies; pinning it turns a
        # silent fallback to another kernel into an error.
        with sdpa_kernel(SDPBackend.FLASH_ATTENTION):
            return self.step(
                [(rows[:, :-1], rows[:, 1:]) for rows in windows.split(self.micro_rows)]
            )
