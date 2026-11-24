from dataclasses import dataclass

import torch


@dataclass(frozen=True, slots=True, kw_only=True)
class VarlenMetadata:
    """Varlen metadata for one padded or packed batch, built once per model forward.

    Varlen backends such as FA3 and FA4 flatten the batch before their kernel and
    restore its shape afterwards. A padded batch has one sequence per nonempty row,
    and `indices` drops its padding. A packed batch has several sequences (documents)
    per row that cover every token, including each row's padding segment, so it
    needs no `indices`. The `cu_seq_lens_*` and `max_length_*` names match Hugging
    Face's `FlashAttentionKwargs`. Self-attention uses the same lengths for queries
    and keys.

    Args:
        cu_seq_lens_q (`torch.Tensor`): Cumulative int32 query lengths of the
            nonempty sequences, starting at 0.
        cu_seq_lens_k (`torch.Tensor`): Cumulative int32 key lengths, in the same form.
        max_length_q (`int`): Upper bound on any query sequence's length, or 0 when
            every row is empty.
        max_length_k (`int`): Upper bound on any key sequence's length, or 0 when
            every row is empty.
        indices (`torch.Tensor` or `None`): Positions of real tokens in the flattened
            `[batch * seq_len]` axis, or `None` when the sequences cover every position.
        batch_size (`int`): Rows in the batch.
        seq_len (`int`): Sequence length of the batch.
    """

    cu_seq_lens_q: torch.Tensor
    cu_seq_lens_k: torch.Tensor
    max_length_q: int
    max_length_k: int
    indices: torch.Tensor | None
    batch_size: int
    seq_len: int


# Every kind of prepared attention metadata, one type per kind rather than per backend.
type AttentionMetadata = torch.Tensor | VarlenMetadata | None
