import torch
from torch import Tensor


def pack(x: Tensor, indices: Tensor) -> Tensor:
    """Gather real tokens from `[batch, heads, seq_len, head_dim]` into `[total_tokens, heads, head_dim]`."""
    return x.transpose(1, 2).flatten(0, 1).index_select(0, indices)


def unpack(x: Tensor, indices: Tensor, batch: int, seq_len: int) -> Tensor:
    """Scatter `[total_tokens, heads, head_dim]` back to `[batch, heads, seq_len, head_dim]`.

    This inverts `pack`. Padding positions are zero.
    """
    _, heads, features = x.shape
    padded = x.new_zeros((batch * seq_len, heads, features))
    padded.index_copy_(0, indices, x)
    return padded.view(batch, seq_len, heads, features).transpose(1, 2)


def cu_seqlens(lengths: Tensor) -> tuple[Tensor, int]:
    """Return int32 cumulative sequence lengths starting at 0, and the maximum length.

    Empty sequences are omitted. Without a nonempty sequence, return `[0]` and 0.
    """
    nonempty = lengths[lengths > 0].to(torch.int32)
    cumulative = torch.cat((nonempty.new_zeros(1), nonempty.cumsum(0, dtype=torch.int32)))
    maximum = int(nonempty.max()) if nonempty.numel() else 0
    return cumulative, maximum
