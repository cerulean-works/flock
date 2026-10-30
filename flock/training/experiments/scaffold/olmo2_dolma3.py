import logging

import torch

from flock.training import (
    AdamWConfig,
    DatasetConfig,
    Trainer,
    TrainerConfig,
    WSDConfig,
    get_dataset,
)
from flock.training.core.models import OLMO2_1B_CONFIG, Olmo2

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    ds_config = DatasetConfig(uri="s3://flock-datasets/finepdfs", version=1)
    dataset = get_dataset(ds_config)

    model = Olmo2(OLMO2_1B_CONFIG).to(dtype=torch.bfloat16)
    tokenizer = model.get_hf_tokenizer()
    assert tokenizer is not None

    optim_conf = AdamWConfig(lr=1e-3, betas=(0.95, 0.99))
    lr_sched_conf = WSDConfig(warmup_steps=100, decay_steps=100)

    trainer_conf = TrainerConfig(
        input_column="text",
        batch_size=2,
        optimizer_config=optim_conf,
        lr_scheduler_config=lr_sched_conf,
        dataloader_num_workers=2,
        dataloader_prefetch_factor=1,
        wandb_project="test",
        wandb_mode="offline",
    )

    trainer = Trainer(config=trainer_conf, model=model, tokenizer=tokenizer, dataset=dataset)
    trainer.train()
