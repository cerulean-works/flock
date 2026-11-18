from __future__ import annotations

import json
import logging
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from safetensors.torch import load_model
from torch import nn

from flock.hub.models import PendingUpload, save_to_hub
from flock.hub.models.files import (
    import_path,
    read_training_state,
    weight_files,
    write_model,
)
from flock.hub.models.upload import RunOrPath


class CheckpointManager:
    """Persist checkpoints independently of a trainer.

    Args:
        output_dir (`str` or `Path`): Local checkpoint root, created if absent.
        save_to_hub_repo (`str`, *optional*): Hub destination `org/model`.
        save_total_limit (`int`, *optional*): Positive local retention limit.
        overwrite_output_dir (`bool`, *optional*, defaults to `False`): Allow replacement.
        run (`wandb.Run` or `str`, *optional*): Tracking run or `entity/project/id`.
        logger (`logging.Logger`, *optional*): Logger for retention and upload failures.

    Saves write weights, buffers and training state to local files before
    returning, then delegate background uploads to the Hub. Later training
    updates cannot change those files. Each successful save collects completed
    uploads and prunes old checkpoints according to `save_total_limit`, keeping
    folders with uploads still in progress. Call `finish_checkpoint_uploads()`
    at the end to wait for remaining uploads and apply retention again.
    Local write failures propagate immediately. Hub failures are logged and the
    local checkpoint remains subject to retention.
    """

    def __init__(
        self,
        output_dir: str | Path,
        *,
        save_to_hub_repo: str | None = None,
        save_total_limit: int | None = None,
        overwrite_output_dir: bool = False,
        run: RunOrPath = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if save_total_limit is not None and save_total_limit <= 0:
            raise ValueError("save_total_limit must be positive")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.save_to_hub_repo = save_to_hub_repo
        self.save_total_limit = save_total_limit
        self.overwrite_output_dir = overwrite_output_dir
        self.run = run
        self.logger = logger if logger is not None else logging.getLogger(__name__)
        self.pending_uploads: dict[Path, PendingUpload] = {}

    def save_model(
        self,
        step: int,
        model: nn.Module,
        *,
        training_state: Mapping[str, Any] | None = None,
        blocking: bool = False,
    ) -> None:
        """Save model weights and optional resumable state to `checkpoint-<step>`.

        Args:
            step (`int`): Nonnegative number of completed optimizer updates.
            model (`torch.nn.Module`): Module to save, including persistent buffers.
            training_state (`Mapping[str, Any]`, *optional*): Nested state to serialize.
            blocking (`bool`, *optional*, defaults to `False`): Wait for all Hub
                uploads after saving. Local serialization always completes before
                this method returns.

        A supplied model config must be a frozen dataclass. Plain modules without
        a config can be restored into an existing module using `load_checkpoint`.
        Do not mutate the model concurrently with this call; updates after it
        returns cannot change the saved snapshot. Completed directories are
        published by rename. Staging paths are excluded from checkpoint listing.

        Raises:
            `FileExistsError`: If a checkpoint for this step already exists and
                `overwrite_output_dir` is false.
            `OSError`: If a local write fails.
        """
        if step < 0:
            raise ValueError("step must be nonnegative")
        save_path = self.output_dir / f"checkpoint-{step}"
        if save_path.exists() and not self.overwrite_output_dir:
            raise FileExistsError(f"Checkpoint already exists: {save_path}")
        # A folder upload reads in place; finish it before replacing those files.
        if save_path in self.pending_uploads:
            self._finish_checkpoint_upload(save_path)

        self.logger.info(f"Saving model at step {step}")
        run = self.run
        run_path = (
            run if isinstance(run, str) or run is None else f"{run.entity}/{run.project}/{run.id}"
        )
        with tempfile.TemporaryDirectory(prefix=".checkpoint-", dir=self.output_dir) as temporary:
            staged = Path(temporary) / "model"
            write_model(
                model,
                staged,
                shard_size=5 * 2**30,
                run=run_path,
                step=step,
                training_state=training_state,
            )
            if save_path.exists():
                shutil.rmtree(save_path)
            staged.rename(save_path)
        if self.save_to_hub_repo is not None:
            try:
                self.pending_uploads[save_path] = save_to_hub(
                    save_path,
                    self.save_to_hub_repo,
                    run=self.run,
                    step=step,
                    tags=[f"checkpoint-{step}"],
                    blocking=False,
                )
            except Exception:
                self.logger.exception(
                    f"Failed to save checkpoint {save_path} to hub {self.save_to_hub_repo}"
                )
        if blocking:
            self.finish_checkpoint_uploads()
            return
        for checkpoint, upload in list(self.pending_uploads.items()):
            if upload.done():
                self._finish_checkpoint_upload(checkpoint)
        self._prune_checkpoints()

    def finish_checkpoint_uploads(self) -> None:
        """Wait for Hub uploads, then apply local retention.

        Hub errors are logged. Repeated calls are harmless.
        """
        for checkpoint in list(self.pending_uploads):
            self._finish_checkpoint_upload(checkpoint)
        self._prune_checkpoints()

    def _finish_checkpoint_upload(self, checkpoint: Path) -> None:
        try:
            self.pending_uploads[checkpoint].result()
        except Exception:
            self.logger.exception(
                f"Failed to upload checkpoint {checkpoint} to hub {self.save_to_hub_repo}"
            )
        del self.pending_uploads[checkpoint]

    def _prune_checkpoints(self) -> None:
        if self.save_total_limit is None:
            return
        for checkpoint in self._checkpoints_by_save_time()[: -self.save_total_limit]:
            if checkpoint in self.pending_uploads:
                continue
            self.logger.info(f"Deleting old checkpoint {checkpoint}")
            shutil.rmtree(checkpoint)

    def load_checkpoint(
        self,
        checkpoint: str | Path,
        model: nn.Module,
        *,
        validate_state: Callable[[dict[str, Any]], Any] | None = None,
    ) -> Any:
        """Restore weights into a matching module and return its saved training state.

        Args:
            checkpoint (`str` or `Path`): Local checkpoint directory.
            model (`torch.nn.Module`): Receiving module with matching architecture/config.
            validate_state (`Callable`, *optional*): Validate training state before
                loading any weights. Its return value becomes this method's result.
                Without a callback, return the deserialized state dictionary.

        The caller owns validation and restoration of optimizer, scheduler,
        counters and RNGs. An absent training state returns an empty dictionary.
        Model/config and callback validation run before any weights change.

        Raises:
            `ValueError`: If architecture, config or callback validation fails.
            `RuntimeError`: If weight shards have missing or unexpected keys.
            `FileNotFoundError`: If required checkpoint files are absent.
        """
        checkpoint = Path(checkpoint)
        metadata = json.loads((checkpoint / "flock.json").read_text())
        config = json.loads((checkpoint / "config.json").read_text())
        model_config = getattr(model, "config", None)
        expected = asdict(model_config) if model_config is not None else {}
        if metadata["architecture"] != import_path(type(model)) or config != json.loads(
            json.dumps(expected)
        ):
            raise ValueError("Checkpoint model architecture or config does not match the model")

        state = {
            path.stem: read_training_state(path)
            for path in (checkpoint / "training_state").glob("*.safetensors")
        }
        checkpoint_state = validate_state(state) if validate_state is not None else state
        missing = set(model.state_dict())
        unexpected = set()
        for path in weight_files(checkpoint):
            shard_missing, shard_unexpected = load_model(model, path, strict=False)
            missing.intersection_update(shard_missing)
            unexpected.update(shard_unexpected)
        if missing or unexpected:
            raise RuntimeError(
                f"Checkpoint weights do not match the model: "
                f"missing keys {sorted(missing)}, unexpected keys {sorted(unexpected)}"
            )
        return checkpoint_state

    def checkpoints(self) -> list[Path]:
        """List published checkpoints in numeric step order, ignoring staging paths."""
        return sorted(
            (
                path
                for path in self.output_dir.glob("checkpoint-*")
                if path.is_dir() and path.name.removeprefix("checkpoint-").isdigit()
            ),
            key=lambda path: int(path.name.removeprefix("checkpoint-")),
        )

    def _checkpoints_by_save_time(self) -> list[Path]:
        """Order checkpoints by application save time, breaking ties by step."""

        def saved_at(path: Path) -> str:
            try:
                value = json.loads((path / "flock.json").read_text()).get("saved_at", "")
                return value if isinstance(value, str) else ""
            except (OSError, ValueError, AttributeError) as error:
                self.logger.warning(f"Failed to read metadata for checkpoint {path}: {error}")
                return ""

        return sorted(self.checkpoints(), key=saved_at)

    def latest_checkpoint(self) -> Path:
        """Return the newest published checkpoint, rejecting an empty checkpoint root."""
        checkpoints = self._checkpoints_by_save_time()
        if not checkpoints:
            raise FileNotFoundError(f"No checkpoints found in {self.output_dir}")
        return checkpoints[-1]
