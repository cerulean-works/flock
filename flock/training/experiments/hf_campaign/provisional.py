import hashlib
from pathlib import Path

import lance
import numpy as np
import pyarrow as pa
import torch
from torch.utils.data import Dataset
from transformers import PreTrainedTokenizerFast

from flock.hub import s3
from flock.training import DatasetConfig
from flock.training.core.chunks import TokenChunkDataset


class ProvisionalMix(Dataset[torch.Tensor]):
    """A provisional, unadmitted hub Lance mix, packed in stored order into fixed windows.

    The admitted path's receipts do not exist for these mixes, so nothing here stands in for admission: every contract, card and summary built from this source carries `provisional: true`. Documents are encoded without specials and each is followed by one EOS, then the stream is cut into windows of `window + 1` tokens that share their boundary token (concatenate-then-split without document masking, as OLMo 3 sec. 3.6.3 and DataDecide pack). Item `u` is optimizer update `u`: `rows_per_update` consecutive windows, so a position in the mix is a number of updates and resuming needs no producer state.

    Args:
        config ([`training.DatasetConfig`]): The mix, pinned to a version, with `text` and `tokens` columns (`tokens` counts the document's EOS).
        tokenizer (`PreTrainedTokenizerFast`): The tokenizer the mix's `tokens` column was counted with.
        window (`int`): Targets per row.
        rows_per_update (`int`): Global rows per optimizer update.
        rank (`int`): Zero-based rank whose contiguous rows are read.
        world_size (`int`): Number of ranks sharing each update.
    """

    def __init__(
        self,
        config: DatasetConfig,
        tokenizer: PreTrainedTokenizerFast,
        *,
        window: int,
        rows_per_update: int,
        rank: int = 0,
        world_size: int = 1,
    ) -> None:
        if window < 1 or rows_per_update < 1:
            raise ValueError("window and rows per update must be positive")
        if world_size < 1 or not 0 <= rank < world_size:
            raise ValueError("rank must belong to a positive world size")
        if rows_per_update % world_size:
            raise ValueError("rows per update must divide evenly over ranks")
        self.rank = rank
        self.world_size = world_size
        if tokenizer.eos_token_id is None:
            raise ValueError("the tokenizer has no EOS token")
        # A tag can move, and can name a version on another branch, where numbers mean other
        # snapshots; workers reopen the mix, so only a number pins what they read.
        if not isinstance(config.version, int):
            raise TypeError(f"pin the mix by version number, not the tag {config.version!r}")
        endpoint = None
        if config.uri.startswith(("s3://", "s3a://", "s3n://")):
            # Read-only credentials go into the process environment, which spawned loader
            # workers inherit. Two hub endpoints can serve one bucket path, so the endpoint is
            # part of the mix's identity.
            applied = s3.get_process(org="cerulean-labs", read_only=True)
            endpoint = applied["AWS_ENDPOINT_URL"]
        # Workers reopen the mix, so they get an absolute path: a relative one moves with the
        # working directory.
        uri = config.uri if "://" in config.uri else str(Path(config.uri).resolve())
        self.uri = uri
        self.version = config.version
        self.dataset: lance.LanceDataset | None = lance.dataset(uri, version=config.version)
        counts = self.dataset.to_table(columns=["tokens"])["tokens"]
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
            "endpoint": endpoint,
            "uri": uri,
            "version": config.version,
            "rows": len(counts),
            "stream_tokens": int(self.ends[-1]),
            "tokenizer": tokenizer.name_or_path,
            # The encoding rules pin the ids, which the name and the token counts do not.
            "tokenizer_sha256": TokenChunkDataset.tokenizer_fingerprint(tokenizer),
            "eos": tokenizer.eos_token_id,
            "window": window,
            "rows_per_update": rows_per_update,
        }

    def __getstate__(self) -> dict:
        """Pickle for a spawned loader worker, which reopens the pinned mix on first read."""
        # A worker's own handle reads independently of the main process's.
        return self.__dict__ | {"dataset": None}

    @property
    def targets_per_update(self) -> int:
        """Targets in one update: every row is a full window, so nothing is padding."""
        return self.window * self.rows_per_update

    def __len__(self) -> int:
        """Whole updates in one pass; a final partial update is never emitted."""
        return (int(self.ends[-1]) - 1) // self.targets_per_update

    def __getitem__(self, index: int) -> torch.Tensor:
        """Return this rank's contiguous rows of update `index` as token ids."""
        if not 0 <= index < len(self):
            raise IndexError(index)
        local_targets = self.targets_per_update // self.world_size
        start = index * self.targets_per_update + self.rank * local_targets
        stop = start + local_targets + 1
        first = int(np.searchsorted(self.ends, start, side="right"))
        last = int(np.searchsorted(self.ends, stop - 1, side="right"))
        if self.dataset is None:
            self.dataset = lance.dataset(self.uri, version=self.version)
        records = self.dataset.take(list(range(first, last + 1))).to_pylist()
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
