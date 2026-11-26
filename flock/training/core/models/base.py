from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import torch
from huggingface_hub import try_to_load_from_cache
from torch import nn
from transformers import AutoTokenizer, PreTrainedTokenizerBase, TokenizersBackend

from flock.training.core.data_utils import Batch


def _converted_from_sentencepiece(repo: str, tokenizer: PreTrainedTokenizerBase) -> bool:
    """Detect a SentencePiece conversion without a shipped `tokenizer.json`.

    Read only the local folder or cache, adding no Hub call. Tokenizer classes
    that name no SentencePiece vocabulary file are not considered conversions.
    """
    if not any(
        Path(name).suffix in {".model", ".spm"} for name in tokenizer.vocab_files_names.values()
    ):
        return False

    folder = Path(repo)
    if folder.is_dir():
        return not (folder / "tokenizer.json").exists()

    return not isinstance(try_to_load_from_cache(repo, "tokenizer.json"), str)


class BaseModel(nn.Module, ABC):
    """Provide initialization, input preparation and tokenizer loading for causal models.

    Subclasses set `config` to a frozen dataclass and accept it as their only
    constructor argument, so Hub loading can rebuild them from `config.json`.
    Build subclass modules inside `_initialize_weights()` and implement `_forward`.
    Override `num_flops_per_token` if its default estimate doesn't fit the architecture.

    Args:
        max_position_embeddings (`int`): Positive maximum input sequence length.
        hf_repo (`str`, *optional*): Tokenizer repository or local directory.
        initializer_range (`float`, *optional*, defaults to `0.02`):
            Positive normal-initialization standard deviation for linear and embedding weights.
        init_seed (`int`, *optional*, defaults to `42`): Private initialization seed.

    Raises:
        `ValueError`: If the sequence limit or initialization scale is nonpositive.
    """

    config: Any
    """The model's settings: a frozen dataclass (`Olmo2Config` for `Olmo2`)."""

    def __init__(
        self,
        *,
        max_position_embeddings: int,
        hf_repo: str | None = None,
        initializer_range: float = 0.02,
        init_seed: int = 42,
    ) -> None:
        """Validate and store initialization and tokenizer settings."""
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

    def _init_weights(self, module: nn.Module, generator: torch.Generator) -> None:
        """Initialize linear and embedding weights from the supplied normal RNG stream."""
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(
                module.weight, mean=0.0, std=self.initializer_range, generator=generator
            )

    def get_hf_tokenizer(self) -> TokenizersBackend | None:
        """Load the configured tokenizer with left padding and the model's length limit.

        Returns:
            `TokenizersBackend` or `None`: Loaded tokenizer, or `None` when `hf_repo`
            is unset. Request EOS insertion and use EOS, then BOS, as the padding
            token if no padding token exists. Verify a usable padding token before
            collation if the repository defines neither special token.

        Raises:
            `ValueError`: If loading converts SentencePiece without a shipped
                `tokenizer.json`, which can change tokenization.
            `TypeError`: If loading does not produce a `TokenizersBackend`.

        Loading may download tokenizer files. No model weights are loaded.
        """
        if self.hf_repo is None:
            return None

        tokenizer = AutoTokenizer.from_pretrained(
            self.hf_repo,
            padding_side="left",
            model_max_length=self.max_position_embeddings,
            add_eos_token=True,
        )
        assert tokenizer is not None  # The stubs allow None; loading raises instead.
        # The conversion segments text unlike the model it came from, which makes it a different
        # tokenizer from the one the repo ships (eval stage 028).
        if _converted_from_sentencepiece(self.hf_repo, tokenizer):
            raise ValueError(
                f"{self.hf_repo} loaded without tokenizer.json: {type(tokenizer).__name__} would "
                "be a conversion of its SentencePiece model"
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

    def _check_batch(
        self, batch: Batch
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor | None]:
        """Validate a collated batch and return the inputs `_forward` takes.

        Position IDs and `cu_seq_lens` pass through unchecked, as the collator builds them.

        Args:
            batch (`Batch`): Token IDs, a matching mask and position IDs, shaped
                `[batch, sequence]`. Mask values `True` or `1` identify real tokens.

        Returns:
            `tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor | None]`: The
            token IDs and the mask as booleans on the IDs' device, both `[batch, sequence]`,
            then the batch's position IDs and `cu_seq_lens`, or `None` unless the batch is
            packed.

        Raises:
            `ValueError`: If IDs are not rank two, length is outside the configured
                range, or the attention mask shape differs from the IDs.
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
        return input_ids, attention_mask, batch["position_ids"], batch.get("cu_seq_lens")

    def forward(self, batch: Batch) -> torch.Tensor:  # ty: ignore[invalid-mutable-override]
        """Return full-sequence logits for a collated batch.

        Labels in a batch are ignored: the trainer owns target alignment and loss.
        Subclasses prepare the backend's mask and causal flag in `_forward`.

        A batch with `cu_seq_lens` is packed, and `_forward` receives the boundaries.

        Args:
            batch (`Batch`): Token IDs, a matching mask and position IDs, shaped
                `[batch, sequence]`. Mask values `True` or `1` identify real tokens.

        Returns:
            `torch.Tensor`: Logits shaped `[batch, sequence, vocabulary]`.

        Raises:
            `ValueError`: If IDs are not rank two, length is outside the configured
                range, the attention mask shape differs from the IDs, or the batch is
                packed and the model or its attention implementation does not support it.
        """
        input_ids, attention_mask, position_ids, cu_seq_lens = self._check_batch(batch)
        return self._forward(input_ids, attention_mask, position_ids, cu_seq_lens=cu_seq_lens)

    def num_flops_per_token(self, seq_len: int) -> int:
        """Estimate the training FLOPs of one token, for logging throughput and MFU.

        Follows torchtitan's method: 6 FLOPs per matmul parameter plus full, unmasked
        attention over the sequence, with the shape read from `config`. Override this for architectures
        the estimate doesn't fit, such as mixture-of-experts or sliding-window attention.

        Args:
            seq_len (`int`): Positions in each row of the batch, padding included.

        Returns:
            `int`: Training FLOPs per token.
        """
        config = self.config
        linear_weights = {id(m.weight) for m in self.modules() if isinstance(m, nn.Linear)}
        # Embedding lookups aren't matmuls, unless the output projection shares the weight.
        lookups = {
            id(m.weight): m.weight.numel()
            for m in self.modules()
            if isinstance(m, nn.Embedding) and id(m.weight) not in linear_weights
        }
        parameters = sum(p.numel() for p in self.parameters()) - sum(lookups.values())
        head_dim = getattr(config, "head_dim", None) or (
            config.hidden_size // config.num_attention_heads
        )
        width = config.num_attention_heads * head_dim
        return 6 * parameters + 12 * config.num_hidden_layers * width * seq_len

    @abstractmethod
    def _forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
        *,
        cu_seq_lens: torch.Tensor | None = None,
    ) -> torch.Tensor:
        """Run validated inputs with a 2D boolean padding mask and position IDs.

        The subclass owns causal attention and backend-specific mask preparation.
        `cu_seq_lens` is `None` unless the batch is packed. A model given boundaries
        must keep attention inside each document they bound, or raise.
        """
        raise NotImplementedError
