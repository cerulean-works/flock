from dataclasses import dataclass

import torch


@dataclass(frozen=True, slots=True, kw_only=True)
class VarlenMetadata:
    """Varlen metadata for one padded batch, built once per model forward.

    Varlen backends such as FA3 and FA4 pack the real tokens before their kernel
    and restore the batch shape afterwards. The `cu_seq_lens_*` and `max_length_*`
    names match Hugging Face's `FlashAttentionKwargs`. Self-attention uses the
    same lengths for queries and keys.

    Args:
        cu_seq_lens_q (`torch.Tensor`): Cumulative int32 query lengths of the
            nonempty rows, starting at 0.
        cu_seq_lens_k (`torch.Tensor`): Cumulative int32 key lengths, in the same form.
        max_length_q (`int`): Length of the longest query row, or 0 when every row is empty.
        max_length_k (`int`): Length of the longest key row, or 0 when every row is empty.
        indices (`torch.Tensor`): Positions of real tokens in the flattened
            `[batch * seq_len]` axis.
        batch_size (`int`): Rows in the padded batch.
        seq_len (`int`): Sequence length of the padded batch.
    """

    cu_seq_lens_q: torch.Tensor
    cu_seq_lens_k: torch.Tensor
    max_length_q: int
    max_length_k: int
    indices: torch.Tensor
    batch_size: int
    seq_len: int


# Every kind of prepared attention metadata, one type per kind rather than per backend.
type AttentionMetadata = torch.Tensor | VarlenMetadata | None
