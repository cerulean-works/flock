"""Expose the single-device trainer and immutable experiment configurations.

Import `Trainer`, `TrainerConfig`, dataset helpers and optimizer/scheduler
configurations here. Model implementations and presets live in
`flock.training.core.models`. Standalone experiments are separate entry points.
"""

from .core.data_utils import DatasetConfig, get_dataset
from .core.lr_scheduler import CosineConfig, LRSchedulerConfig, WSDConfig
from .core.models import Olmo2Config
from .core.optimization import AdamWConfig, MuonConfig, OptimizerConfig
from .core.trainer import Trainer, TrainerConfig

__all__ = [
    "AdamWConfig",
    "CosineConfig",
    "DatasetConfig",
    "LRSchedulerConfig",
    "MuonConfig",
    "Olmo2Config",
    "OptimizerConfig",
    "Trainer",
    "TrainerConfig",
    "WSDConfig",
    "get_dataset",
]
