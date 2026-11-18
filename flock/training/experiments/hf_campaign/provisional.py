import hashlib
from pathlib import Path

import lance
import numpy as np
import pyarrow as pa
import torch
from torch.utils.data import Dataset
from transformers import PreTrainedTokenizerFast

from flock.training import DatasetConfig, get_dataset
from flock.training.core.chunks import TokenChunkDataset


class ProvisionalMix(Dataset[torch.Tensor]):
    """A provisional, unadmitted hub Lance mix, packed in stored order into fixed windows.

    The admitted path's receipts do not exist for these mixes, so nothing here stands in for admission: every contract, card and summary built from this source carries `provisional: true`. Documents are encoded without specials and each is followed by one EOS, then the stream is cut into windows of `window + 1` tokens that share their boundary token (concatenate-then-split without document masking, as OLMo 3 sec. 3.6.3 and DataDecide pack). Item `u` is optimizer update `u`: `rows_per_update` consecutive windows, so a position in the mix is a number of updates and resuming needs no producer state.

    Args:
        config ([`training.DatasetConfig`]): The mix, pinned to a version, with `text` and `tokens` columns (`tokens` counts the document's EOS).
        tokenizer (`PreTrainedTokenizerFast`): The tokenizer the mix's `tokens` column was counted with.
        window (`int`): Targets per row.
        rows_per_update (`int`): Rows per optimizer update.
    """

    def __init__(
        self,
        config: DatasetConfig,
        tokenizer: PreTrainedTokenizerFast,
        *,
        window: int,
        rows_per_update: int,
    ) -> None:
        if window < 1 or rows_per_update < 1:
            raise ValueError("window and rows per update must be positive")
        if tokenizer.eos_token_id is None:
            raise ValueError("the tokenizer has no EOS token")
        # get_dataset configures read-only S3 credentials, inherited by spawned loader workers.
        get_dataset(config)
        # Workers reopen the mix, so they get an absolute path and a version number: a relative
        # path moves with the working directory and a tag can move between versions.
        uri = config.uri if "://" in config.uri else str(Path(config.uri).resolve())
        pinned = lance.dataset(uri, version=config.version)
        self.rows = get_dataset(DatasetConfig(uri=uri, version=pinned.version))
        tokens = pinned.to_table(columns=["tokens"])
        counts = tokens["tokens"]
        # Every document holds at least its EOS; a zero or negative count would misplace documents.
        if not pa.types.is_integer(counts.type):
            raise ValueError(f"the mix's tokens column is {counts.type}, not integers")
        if len(counts) == 0 or counts.null_count or (counts.to_numpy() < 1).any():
            raise ValueError("every mix row needs a token count of at least 1 (its EOS)")
        # Stream position just past each document; the first token is context only.
        self.ends = np.cumsum(counts.to_numpy(), dtype=np.int64)
        self.tokenizer = tokenizer
        self.window = window
        self.rows_per_update = rows_per_update
        self.contract = {
            "provisional": True,
            "uri": uri,
            "version": pinned.version,
            "rows": len(self.rows),
            "stream_tokens": int(self.ends[-1]),
            "tokenizer": tokenizer.name_or_path,
            # The encoding rules pin the ids, which the name and the token counts do not.
            "tokenizer_sha256": TokenChunkDataset.tokenizer_fingerprint(tokenizer),
            "eos": tokenizer.eos_token_id,
            "window": window,
            "rows_per_update": rows_per_update,
        }

    @property
    def targets_per_update(self) -> int:
        """Targets in one update: every row is a full window, so nothing is padding."""
        return self.window * self.rows_per_update

    def __len__(self) -> int:
        """Whole updates in one pass; a final partial update is never emitted."""
        return (int(self.ends[-1]) - 1) // self.targets_per_update

    def __getitem__(self, index: int) -> torch.Tensor:
        """Return update `index` as `[rows_per_update, window + 1]` token ids."""
        if not 0 <= index < len(self):
            raise IndexError(index)
        start = index * self.targets_per_update
        stop = start + self.targets_per_update + 1
        first = int(np.searchsorted(self.ends, start, side="right"))
        last = int(np.searchsorted(self.ends, stop - 1, side="right"))
        records = self.rows.__getitems__(list(range(first, last + 1)))
        texts = [record["text"] for record in records]
        encoded = self.tokenizer(texts, add_special_tokens=False)["input_ids"]
        pieces = []
        for row, record, ids in zip(range(first, last + 1), records, encoded, strict=True):
            # The mix's token count is the integrity check every mix has; a text hash only some do.
            if len(ids) + 1 != record["tokens"]:
                raise ValueError(
                    f"mix row {row} encodes to {len(ids) + 1} tokens with EOS, "
                    f"the mix records {record['tokens']}"
                )
            digest = record.get("text_sha256")
            if digest is not None and digest != hashlib.sha256(record["text"].encode()).hexdigest():
                raise ValueError(f"mix row {row} text does not match its text_sha256")
            pieces.append(np.asarray(ids, dtype=np.int64))
            pieces.append(np.asarray([self.tokenizer.eos_token_id], dtype=np.int64))
        stream = np.concatenate(pieces)
        offset = start - (int(self.ends[first - 1]) if first else 0)
        span = torch.from_numpy(stream[offset : offset + stop - start])
        # Row r holds stream positions r * window to (r + 1) * window, inclusive.
        return span.unfold(0, self.window + 1, self.window).contiguous()
