"""CPU validation runtime for the standalone HF campaign contract."""

import hashlib
import json
import math
import os
from pathlib import Path

import torch
from accelerate import Accelerator
from torch.nn import functional as F
from transformers import Olmo2Config, Olmo2ForCausalLM, PreTrainedTokenizerBase

from .stream import Block, TargetStream


class CpuExperiment:
    """Exercise aligned loss, committed stream state and HF export without a launch path."""

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
        if not 0 < warmup_targets < total_targets:
            raise ValueError("warmup must precede the finite target endpoint")
        self.total_targets = total_targets
        self.warmup_targets = warmup_targets
        self.contract = hashlib.sha256(
            json.dumps(
                {
                    "model": config.to_dict(),
                    "attention_backend": config._attn_implementation,
                    "precision": "FP32 CPU",
                    "stream": stream.contract,
                    "total": total_targets,
                    "warmup": warmup_targets,
                    "optimizer": "AdamW:lr0.001:defaults:clip1.0",
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        torch.manual_seed(seed)
        self.accelerator = Accelerator(cpu=True, mixed_precision="no")
        if self.accelerator.num_processes != 1:
            raise ValueError("distributed training requires a separately approved GPU gate")
        self.model = Olmo2ForCausalLM(config)
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=1e-3)
        self.model, self.optimizer = self.accelerator.prepare(self.model, self.optimizer)
        self.stream = stream
        self.accelerator.register_for_checkpointing(self)
        self.committed = stream.state_dict()
        self.updates = 0
        self.targets = 0

    def state_dict(self) -> dict:
        """Save optimizer-matched data state rather than an advanced producer cursor."""
        return {
            "stream": self.committed,
            "updates": self.updates,
            "targets": self.targets,
            "contract": self.contract,
        }

    def load_state_dict(self, state: dict) -> None:
        """Restore the committed stream and discard speculative prefetched blocks."""
        if state["contract"] != self.contract:
            raise ValueError("runtime contract changed")
        self.stream.load_state_dict(state["stream"])
        self.committed = self.stream.state_dict()
        self.updates = state["updates"]
        self.targets = state["targets"]

    def update(self, *, tc_targets: int, en_targets: int) -> tuple[float, list[Block]]:
        """Apply one exact global allocation with external already-aligned summed CE."""
        if tc_targets < 0 or tc_targets != en_targets or tc_targets == 0:
            raise ValueError("each synthetic update requires equal positive TC and EN targets")
        # Producer state may have advanced through prefetch; only committed input is authoritative.
        self.stream.load_state_dict(self.committed)
        if self.targets + tc_targets + en_targets > self.total_targets:
            raise ValueError("update would overshoot the target endpoint")
        if self.targets < self.warmup_targets:
            multiplier = self.targets / self.warmup_targets
        else:
            progress = (self.targets - self.warmup_targets) / (
                self.total_targets - self.warmup_targets
            )
            multiplier = 0.1 + 0.9 * (1 + math.cos(math.pi * progress)) / 2
        for group in self.optimizer.param_groups:
            group["lr"] = 1e-3 * multiplier
        before = self.stream.state_dict()
        try:
            blocks = self.stream.blocks("tc", tc_targets) + self.stream.blocks("en", en_targets)
            total = tc_targets + en_targets
            loss_value = 0.0
            self.optimizer.zero_grad()
            for block in blocks:
                inputs = torch.tensor([block.inputs], dtype=torch.long)
                labels = torch.tensor([block.labels], dtype=torch.long)
                # HF model labels would shift these targets a second time.
                logits = self.model(input_ids=inputs, use_cache=False).logits
                loss = (
                    F.cross_entropy(
                        logits.float().reshape(-1, logits.shape[-1]),
                        labels.flatten(),
                        reduction="sum",
                    )
                    / total
                )
                self.accelerator.backward(loss)
                loss_value += float(loss.detach())
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0, error_if_nonfinite=True)
            self.optimizer.step()
        except Exception:
            self.stream.load_state_dict(before)
            self.optimizer.zero_grad()
            raise
        self.updates += 1
        self.targets += total
        self.committed = self.stream.state_dict()
        return loss_value, blocks

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
            raise ValueError("checkpoint stream contract changed")
        for name, expected in manifest["files"].items():
            if Path(name).name != name:
                raise ValueError("invalid checkpoint path")
            if hashlib.sha256((checkpoint / name).read_bytes()).hexdigest() != expected:
                raise ValueError("checkpoint checksum mismatch")
        self.accelerator.load_state(str(checkpoint))

    def export(self, destination: Path, tokenizer: PreTrainedTokenizerBase) -> None:
        """Write an HF model and tokenizer for the separate frozen evaluation instrument."""
        destination.mkdir(parents=True, exist_ok=False)
        self.accelerator.unwrap_model(self.model).save_pretrained(destination)
        tokenizer.save_pretrained(destination)
