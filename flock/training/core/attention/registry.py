from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

import torch

from .flash_attention import flash_attention_3, flash_attention_4
from .masking import (
    prepare_packed_sdpa_mask,
    prepare_packed_varlen_metadata,
    prepare_sdpa_mask,
    prepare_varlen_metadata,
)
from .sdpa import sdpa_attention


class AttentionFunction[MetadataT](Protocol):
    """Compute attention from prepared Q/K/V in [batch, heads, sequence, head_dim] order.

    Return the same layout and handle GQA. `MetadataT` is the metadata kind the
    function reads, as returned by its paired mask preparer.
    """

    def __call__(
        self,
        q: torch.Tensor,
        k: torch.Tensor,
        v: torch.Tensor,
        *,
        attention_metadata: MetadataT | None = None,
        is_causal: bool = False,
    ) -> torch.Tensor: ...


class _MaskPreparationFunction[MetadataT](Protocol):
    def __call__(
        self, padding_mask: torch.Tensor | None, *, is_causal: bool
    ) -> MetadataT | None: ...


class _PackedPreparationFunction[MetadataT](Protocol):
    def __call__(
        self, cu_seq_lens: torch.Tensor, *, batch_size: int, seq_len: int, is_causal: bool = True
    ) -> MetadataT: ...


@dataclass(frozen=True)
class _AttentionBackend[MetadataT]:
    function: AttentionFunction[MetadataT]
    mask_preparer: _MaskPreparationFunction[MetadataT]
    packed_preparer: _PackedPreparationFunction[MetadataT] | None = None


@dataclass(frozen=True)
class AttentionMetadataPreparer:
    """Prepare one attention implementation's metadata for padded or packed batches.

    A model calls it once per forward and passes the result to every layer. Without
    `cu_seq_lens`, the backend's mask preparer reads `padding_mask`. With them, the
    batch is packed: the backend's packed preparer keeps attention inside each
    document and `padding_mask` is unused.
    """

    attn_implementation: str
    backend: _AttentionBackend[Any]

    def __call__(
        self,
        padding_mask: torch.Tensor | None,
        *,
        batch_size: int,
        seq_len: int,
        is_causal: bool,
        cu_seq_lens: torch.Tensor | None = None,
    ) -> Any:
        """Return the metadata every layer passes to the attention function.

        Args:
            padding_mask (`torch.Tensor` or `None`): Boolean `[batch, sequence]` mask;
                `True` marks a real token and `None` means every token is real.
            batch_size (`int`): Rows in the batch.
            seq_len (`int`): Positions in each row.
            is_causal (`bool`): Whether to prevent attention to future tokens.
            cu_seq_lens (`torch.Tensor`, *optional*): int32 document boundaries of a
                packed batch in the flattened `[batch * sequence]` axis.

        Raises:
            `ValueError`: If the batch is packed and the implementation registers no
                packed preparer.
        """
        if cu_seq_lens is None:
            return self.backend.mask_preparer(padding_mask, is_causal=is_causal)
        if self.backend.packed_preparer is None:
            raise ValueError(f"{self.attn_implementation} does not support packed batches")
        return self.backend.packed_preparer(
            cu_seq_lens, batch_size=batch_size, seq_len=seq_len, is_causal=is_causal
        )


class AttentionInterface:
    """Register attention functions and their mask preparers together by name.

    A backend accepts packed batches only if it also registers a packed preparer.
    """

    _global_mapping: ClassVar[dict[str, _AttentionBackend[Any]]] = {
        "sdpa": _AttentionBackend(sdpa_attention, prepare_sdpa_mask, prepare_packed_sdpa_mask),
        "flash_attention_3": _AttentionBackend(
            flash_attention_3, prepare_varlen_metadata, prepare_packed_varlen_metadata
        ),
        "flash_attention_4": _AttentionBackend(
            flash_attention_4, prepare_varlen_metadata, prepare_packed_varlen_metadata
        ),
    }

    @classmethod
    def register[MetadataT](
        cls,
        name: str,
        function: AttentionFunction[MetadataT],
        *,
        mask_preparer: _MaskPreparationFunction[MetadataT],
        packed_preparer: _PackedPreparationFunction[MetadataT] | None = None,
    ) -> None:
        """Register or replace an attention function with an explicit mask preparer.

        Pass `packed_preparer` to accept packed batches as well.
        """
        cls._global_mapping[name] = _AttentionBackend(function, mask_preparer, packed_preparer)

    @classmethod
    def get_interface(cls, attn_implementation: str) -> AttentionFunction[Any]:
        """Resolve an implementation name to its attention function."""
        return cls._get_backend(attn_implementation).function

    @classmethod
    def get_mask_preparer(cls, attn_implementation: str) -> _MaskPreparationFunction[Any]:
        """Resolve an implementation name to its model-level mask preparer."""
        return cls._get_backend(attn_implementation).mask_preparer

    @classmethod
    def get_metadata_preparer(cls, attn_implementation: str) -> AttentionMetadataPreparer:
        """Resolve an implementation name to one preparer for padded and packed batches.

        The preparer keeps the backend registered now, so a later `register` call
        under the same name does not change existing models.
        """
        return AttentionMetadataPreparer(attn_implementation, cls._get_backend(attn_implementation))

    @classmethod
    def _get_backend(cls, attn_implementation: str) -> _AttentionBackend[Any]:
        try:
            return cls._global_mapping[attn_implementation]
        except KeyError:
            available = ", ".join(sorted(cls._global_mapping))
            raise ValueError(
                f"Unknown attention implementation {attn_implementation!r}; available: {available}"
            ) from None
