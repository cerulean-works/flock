"""Train one scaling-law run from its resolved config, resuming if it has checkpoints.

Launch with `torchrun`, one process per GPU:

```bash
torchrun --nproc-per-node=4 -m flock.training.experiments.scaling_law.train run.json
```
"""

import argparse
import logging
import os
from pathlib import Path
from typing import cast

from flock.training import DatasetConfig, TokenChunkDataset, get_dataset
from flock.training.core.distributed import DistributedRuntime, distributed_run
from flock.training.core.streaming import TextStream

from .config import DataSettings, ExperimentConfig, ModelSettings
from .trainer import TokenBudgetTrainer

logger = logging.getLogger(__name__)


def prepare_cache(data: DataSettings, model: ModelSettings) -> TokenChunkDataset:
    """Tokenize the data source into `data.cache_dir` with the model's tokenizer.

    Raises:
        `FileExistsError`: If the cache already exists.
        `ValueError`: If the data settings name no source.
    """
    if data.source is None or data.cache_dir is None:
        raise ValueError(f"{data.cache_dir} is missing and the data settings name no source")
    tokenizer = model.build(init_seed=0).get_hf_tokenizer()
    if tokenizer is None:
        raise ValueError("The model config needs hf_repo to load a tokenizer")
    return TokenChunkDataset.prepare(
        dataset=get_dataset(DatasetConfig(uri=data.source.uri, version=data.source.version)),
        tokenizer=tokenizer,
        input_column=data.input_column,
        cache_dir=data.cache_dir,
        chunk_length=data.chunk_length,
    )


def build_trainer(
    experiment: ExperimentConfig, runtime: DistributedRuntime | None = None
) -> TokenBudgetTrainer:
    """Build the model, open the token cache or text stream and construct the trainer.

    Raises:
        `FileNotFoundError`: If the token cache has not been prepared or the text is missing.
    """
    data = experiment.data
    location = data.text_path if data.text_path is not None else cast(str, data.cache_dir)
    if not Path(location).exists():
        hint = "" if data.streams else " Run `sweep prepare` first."
        raise FileNotFoundError(f"{location} is missing.{hint}")
    model = experiment.model.build(init_seed=experiment.seed)
    tokenizer = model.get_hf_tokenizer()
    if tokenizer is None:
        raise ValueError("The model config needs hf_repo to load a tokenizer")
    dataset = (
        TextStream(
            path=location,
            input_column=data.input_column,
            version=data.text_version,
            block_rows=data.block_rows,
            chunk_length=data.chunk_length,
            add_bos=data.add_bos,
            add_eos=data.add_eos,
        )
        if data.streams
        else TokenChunkDataset(cache_dir=location, chunk_length=data.chunk_length)
    )
    return TokenBudgetTrainer(
        config=experiment.trainer_config(),
        model=model,
        tokenizer=tokenizer,
        dataset=dataset,
        stop_tokens=experiment.stop_tokens,
        save_tokens=experiment.save_tokens,
        runtime=runtime,
    )


def main(argv: list[str] | None = None) -> None:
    """Train the run described by a JSON or YAML file."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("config", type=Path, help="Resolved run config (JSON or YAML).")
    args = parser.parse_args(argv)
    experiment = ExperimentConfig.load(args.config)

    # The trainer's wandb.init takes no name or group, so set them for it.
    os.environ.setdefault("WANDB_NAME", experiment.name)
    if experiment.wandb_group is not None:
        os.environ.setdefault("WANDB_RUN_GROUP", experiment.wandb_group)

    with distributed_run() as runtime:
        trainer = build_trainer(experiment, runtime)
        resume = bool(trainer.checkpoint_manager.checkpoints())
        if resume and runtime.is_master:
            logger.info("Resuming %s from its latest checkpoint", experiment.name)
        try:
            trainer.train(resume_from_checkpoint=resume)
        finally:
            if trainer.run is not None:
                trainer.run.finish()


if __name__ == "__main__":
    main()
