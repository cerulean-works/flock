from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, cast

from safetensors.torch import load_model

from flock.hub.models import PendingUpload, save_to_hub
from flock.hub.models.files import import_path, read_training_state, weight_files, write_model

if TYPE_CHECKING:
    from flock.training.core.trainer import Trainer


class CheckpointManager:
    """Save and restore a trainer's local checkpoints and protect pending upload sources.

    Args:
        trainer (`Trainer`): Owner of the model, resumable state and persistence settings.
            State validation and restoration are delegated to this trainer.
    """

    def __init__(self, trainer: Trainer) -> None:
        """Bind persistence to a trainer without copying its state or settings."""
        self.trainer = trainer
        self.pending_uploads: dict[Path, PendingUpload] = {}

    def save_model(self, blocking: bool = False) -> None:
        """Write weights and complete resumable state to `checkpoint-<step>`.

        Stage files under the output root, then rename the completed directory
        into place. If configured, start a Hub upload tagged with the step.
        Upload errors are logged; local write errors propagate. Prune local
        checkpoints by `saved_at`, protecting folders still being uploaded.
        Local retention never deletes Hub versions.

        Args:
            blocking (`bool`, *optional*, defaults to `False`):
                Wait for all pending uploads. Reuse an existing current-step
                checkpoint without rewriting or starting another upload.

        Raises:
            `FileExistsError`: If a nonblocking save would replace an existing
                checkpoint and `overwrite_output_dir` is false.

        Returns `None`; asynchronous handles are held in `pending_uploads`.
        A pending older upload can temporarily exceed `save_total_limit`.
        """
        trainer = self.trainer
        for checkpoint, upload in list(self.pending_uploads.items()):
            if upload.done():
                try:
                    upload.result()
                except Exception:
                    trainer.logger.exception(
                        f"Failed to upload checkpoint {checkpoint} to hub {trainer.save_to_hub_repo}"
                    )
                del self.pending_uploads[checkpoint]
        step = int(trainer.state.step.item())

        trainer.logger.info(f"Saving model at step {step}")
        save_path = trainer.output_dir.joinpath(f"checkpoint-{step}")
        if save_path.exists() and not trainer.overwrite_output_dir and not blocking:
            raise FileExistsError(f"Checkpoint already exists: {save_path}")

        if save_path in self.pending_uploads:
            # A folder upload reads in place; finish it before replacing those files.
            upload = self.pending_uploads[save_path]
            try:
                upload.result()
            except Exception:
                trainer.logger.exception(
                    f"Failed to upload checkpoint {save_path} to hub {trainer.save_to_hub_repo}"
                )
            del self.pending_uploads[save_path]

        # A blocking call for an already published step only needs to finish uploads.
        if not (blocking and save_path.exists()):
            # Publish the directory only after weights and training state are fully written.
            with tempfile.TemporaryDirectory(
                prefix=".checkpoint-", dir=trainer.output_dir
            ) as temporary:
                staged = Path(temporary) / "model"
                write_model(
                    trainer.model,
                    staged,
                    shard_size=5 * 2**30,
                    run=f"{trainer.run.entity}/{trainer.run.project}/{trainer.run.id}",
                    step=step,
                    training_state=trainer.state_dict(),
                )
                if save_path.exists():
                    shutil.rmtree(save_path)
                staged.rename(save_path)
            if trainer.save_to_hub_repo is not None:
                try:
                    upload_promise = cast(
                        PendingUpload,
                        save_to_hub(
                            save_path,
                            trainer.save_to_hub_repo,
                            run=trainer.run,
                            step=step,
                            tags=[f"checkpoint-{step}"],
                            blocking=False,
                        ),
                    )
                    self.pending_uploads[save_path] = upload_promise
                except Exception:
                    trainer.logger.exception(
                        f"Failed to save checkpoint {save_path} to hub {trainer.save_to_hub_repo}"
                    )

        if blocking:
            trainer.logger.info("Waiting for pending uploads to finish...")
            for checkpoint in list(self.pending_uploads):
                upload = self.pending_uploads[checkpoint]
                try:
                    upload.result()
                except Exception:
                    trainer.logger.exception(
                        f"Failed to upload checkpoint {checkpoint} to hub {trainer.save_to_hub_repo}"
                    )
                del self.pending_uploads[checkpoint]
            trainer.logger.info("All pending uploads finished")

        if trainer.save_total_limit is not None:
            checkpoints = self._checkpoints_by_save_time()

            for checkpoint_to_delete in checkpoints[: -trainer.save_total_limit]:
                # Retain the newest checkpoints plus any older ones still being read.
                if checkpoint_to_delete in self.pending_uploads:
                    continue
                trainer.logger.info(f"Deleting old checkpoint {checkpoint_to_delete}")
                shutil.rmtree(checkpoint_to_delete)

    def load_checkpoint(self, checkpoint: str | Path) -> None:
        """Restore weights, optimizer, schedule, counters and RNGs from local files.

        Args:
            checkpoint (`str` or `Path`): Folder containing the model and training state.

        Raises:
            `ValueError`: If the model configuration or resume settings differ, or
                training state is absent or invalid.
            `RuntimeError`: If the combined weight shards have missing or extra keys.
            `FileNotFoundError`: If required checkpoint files are absent.

        This method does not run training or construct a resumed loader. Use
        `train(resume_from_checkpoint=checkpoint)` to continue the pass. Dataset
        contents and tokenizer identity are not checked.
        """
        trainer = self.trainer
        checkpoint = Path(checkpoint)
        metadata = json.loads((checkpoint / "flock.json").read_text())
        config = json.loads((checkpoint / "config.json").read_text())

        if metadata["architecture"] != import_path(type(trainer.model)) or config != json.loads(
            json.dumps(asdict(trainer.model.config))
        ):
            raise ValueError("Checkpoint model architecture or config does not match the trainer")

        state = {
            path.stem: read_training_state(path)
            for path in (checkpoint / "training_state").glob("*.safetensors")
        }
        if not state:
            raise ValueError(f"Checkpoint has no training state: {checkpoint}")

        checkpoint_state = trainer.validate_state_dict(state)

        missing = set(trainer.model.state_dict())
        unexpected = set()
        for path in weight_files(checkpoint):
            # Each shard contains only part of the model; check strictness over all shards.
            shard_missing, shard_unexpected = load_model(trainer.model, path, strict=False)
            missing.intersection_update(shard_missing)
            unexpected.update(shard_unexpected)
        if missing or unexpected:
            raise RuntimeError(
                f"Checkpoint weights do not match the model: "
                f"missing keys {sorted(missing)}, unexpected keys {sorted(unexpected)}"
            )

        trainer.load_state_dict(checkpoint_state)

    def checkpoints(self) -> list[Path]:
        """List numbered checkpoint folders in numeric step order, ignoring staging paths."""
        return sorted(
            (
                path
                for path in self.trainer.output_dir.glob("checkpoint-*")
                if path.is_dir() and path.name.removeprefix("checkpoint-").isdigit()
            ),
            key=lambda path: int(path.name.removeprefix("checkpoint-")),
        )

    def _checkpoints_by_save_time(self) -> list[Path]:
        """Order published checkpoints by application save time, breaking ties by step."""

        def saved_at(path: Path) -> str:
            try:
                value = json.loads((path / "flock.json").read_text()).get("saved_at", "")
                return value if isinstance(value, str) else ""
            except (OSError, ValueError, AttributeError) as error:
                self.trainer.logger.warning(
                    f"Failed to read metadata for checkpoint {path}: {error}"
                )
                return ""

        return sorted(self.checkpoints(), key=saved_at)

    def latest_checkpoint(self) -> Path:
        """Return the newest published checkpoint, rejecting an empty checkpoint root."""
        checkpoints = self._checkpoints_by_save_time()
        if not checkpoints:
            raise FileNotFoundError(f"No checkpoints found in {self.trainer.output_dir}")
        return checkpoints[-1]
