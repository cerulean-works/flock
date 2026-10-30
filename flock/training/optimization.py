from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class AdamWConfig:
    lr: float
    betas: tuple[float, float]


@dataclass(frozen=True, kw_only=True)
class MuonConfig:
    lr: float
    momentum: float


type OptimizerConfig = AdamWConfig | MuonConfig
