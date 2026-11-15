import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

import torch
import torch.distributed as dist


@dataclass(frozen=True)
class DistributedRuntime:
    rank: int
    local_rank: int
    world_size: int
    device: torch.device

    @property
    def enabled(self) -> bool:
        return self.world_size > 1

    @property
    def is_master(self) -> bool:
        return self.rank == 0

    @classmethod
    def from_env(cls, *, enable_cpu: bool = False) -> DistributedRuntime:
        env_vars = ["RANK", "LOCAL_RANK", "WORLD_SIZE"]
        env_var_present = (var in os.environ for var in env_vars)
        if any(env_var_present) and (not all(env_var_present)):
            raise RuntimeError(
                f"Missing required environment variables for distributed training: {env_vars}"
            )

        rank, local_rank, world_size = (
            (int(os.environ[var]) for var in env_vars) if all(env_var_present) else (0, 0, 1)
        )

        if torch.cuda.is_available() and not enable_cpu:
            if local_rank >= torch.cuda.device_count():
                raise RuntimeError(
                    f"Local rank {local_rank} exceeds available CUDA devices {torch.cuda.device_count()}"
                )
            device = torch.device("cuda", local_rank)
        else:
            device = torch.device("cpu")

        return cls(rank, local_rank, world_size, device)


@contextmanager
def distributed_run(*, enable_cpu: bool = False) -> Generator[DistributedRuntime]:
    runtime = DistributedRuntime.from_env(enable_cpu=enable_cpu)
    if dist.is_initialized():
        raise RuntimeError("Distributed runtime is initialized out side of the context manager.")
    if runtime.enabled:
        dist.init_process_group(
            backend="nccl" if runtime.device.type == "cuda" else "gloo",
            init_method="env://",
            timeout=timedelta(minutes=10),
            device_id=runtime.device,
        )
    try:
        yield runtime
    finally:
        if runtime.enabled:
            dist.destroy_process_group()
