from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import torch
from torch import nn
from transformers import AutoTokenizer, TokenizersBackend

from flock.training.core.data_utils import Batch


class BaseModel(nn.Module, ABC):
    """Shared initialization, input preparation and tokenizer loading for causal models.

    Subclasses set ``config`` and take it as their only constructor argument, so a saved model
    can be rebuilt from its ``config.json``.
    """

    config: Any
    """The model's settings: a frozen dataclass (``Olmo2Config`` for ``Olmo2``)."""

    def __init__(
        self,
        *,
        max_position_embeddings: int,
        hf_repo: str | None = None,
        initializer_range: float = 0.02,
        init_seed: int = 42,
    ):
        super().__init__()
        if max_position_embeddings <= 0:
            raise ValueError("max_position_embeddings must be positive")
        if initializer_range <= 0:
            raise ValueError("initializer_range must be positive")
        self.max_position_embeddings = max_position_embeddings
        self.hf_repo = hf_repo
        self.initializer_range = initializer_range
        self.init_seed = init_seed

    @contextmanager
    def _initialize_weights(self) -> Generator[None]:
        """Build modules inside this context to initialize them with the model's seed.

        Preserve default RNG state consumed by module constructors, then use one
        separate stream for all linear and embedding weights.
        """
        device = torch.empty(0).device
        devices = [] if device.type in ("cpu", "meta") else [device.index or 0]
        with torch.random.fork_rng(devices=devices, device_type=device.type):
            yield
            generator = torch.Generator(device="cpu" if device.type == "meta" else device)
            generator.manual_seed(self.init_seed)
            self.apply(lambda module: self._init_weights(module, generator))

    def _init_weights(self, module: nn.Module, generator: torch.Generator):
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(
                module.weight, mean=0.0, std=self.initializer_range, generator=generator
            )

    def get_hf_tokenizer(self) -> TokenizersBackend | None:
        if self.hf_repo is None:
            return None

        tokenizer = AutoTokenizer.from_pretrained(
            self.hf_repo,
            padding_side="left",
            model_max_length=self.max_position_embeddings,
        )
        if not isinstance(tokenizer, TokenizersBackend):
            raise TypeError(
                f"Tokenizer from HF {self.hf_repo} did not produce a TokenizersBackend instance"
            )

        if tokenizer.pad_token_id is None:
            tokenizer.pad_token_id = tokenizer.eos_token_id
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token_id = tokenizer.bos_token_id
        return tokenizer

    def forward(self, batch: Batch) -> torch.Tensor:  # ty: ignore[invalid-mutable-override]
        """Run a collated Batch; attention masks use True/1 for real tokens.

        Labels in a batch are ignored: the trainer owns target alignment and loss.
        Position IDs are contiguous over each row's real tokens.
        """
        input_ids = batch["input_ids"]
        attention_mask = batch["attention_mask"]

        if input_ids.ndim != 2:
            raise ValueError("input_ids must have shape [batch, sequence]")

        seq_len = input_ids.shape[1]
        if not 0 < seq_len <= self.max_position_embeddings:
            raise ValueError("sequence length must be between 1 and max_position_embeddings")

        if attention_mask.shape != input_ids.shape:
            raise ValueError("attention_mask must have the same shape as input_ids")

        attention_mask = attention_mask.to(device=input_ids.device, dtype=torch.bool)
        position_ids = (attention_mask.long().cumsum(-1) - 1).clamp_min(0)
        causal = torch.ones(seq_len, seq_len, dtype=torch.bool, device=input_ids.device).tril()
        # casual[None, None] makes [T, T] -> [1, 1, T, T]
        causal_mask = causal[None, None] & attention_mask[:, None, None, :]
        return self._forward(input_ids, causal_mask, position_ids)

    @abstractmethod
    def _forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
    ) -> torch.Tensor:
        """Run the model with validated inputs and a complete causal mask."""
        raise NotImplementedError
