"""Reading two eval runs' files for an item comparison (025, D185), and naming the tokens of
their per-token values (030)."""

import asyncio
import functools
from dataclasses import dataclass

from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from app.api.src.core.store import Run, Store

# Base Easy's biggest file is a few MiB, and a pair reads about 80 MiB in 222 files, so a
# comparison beyond these is refused rather than held or worked through.
FILE_BYTES = 64 << 20
PAIR_BYTES = 1 << 30
PAIR_FILES = 2000


@dataclass
class PairFiles:
    """The files of the two runs one comparison reads, within one budget: a file over 64 MiB,
    more than 1 GiB in all, or more than 2,000 lookups, found or not, raise ``ValueError``.
    ``read`` is called from the diff's own thread and reads on ``loop``."""

    store: Store
    loop: asyncio.AbstractEventLoop
    left: int = PAIR_BYTES
    lookups: int = PAIR_FILES

    def read(self, run: Run, name: str, readable: bool = True) -> str | None:
        """The run's file ``name`` as text, or None when the run has no such file or the caller
        may not read it (``readable`` false), which counts as a lookup all the same."""
        data = self.read_bytes(run, name, readable)
        return None if data is None else data.decode()

    def read_bytes(self, run: Run, name: str, readable: bool = True) -> bytes | None:
        """The run's file ``name`` as bytes (a per-token sidecar is Parquet), as ``read`` reads it."""
        self.lookups -= 1
        if self.lookups < 0:
            raise ValueError("the runs have more files than one comparison reads")
        if not readable:
            return None
        read = self.store.read_file(run, name, max_bytes=min(FILE_BYTES, self.left))
        data = asyncio.run_coroutine_threadsafe(read, self.loop).result()
        if data is None:
            return None
        self.left -= len(data)
        return data


@functools.lru_cache(maxsize=8)
def _tokenizer(name: str, revision: str | None) -> Tokenizer:
    """The tokenizer ``name`` from the Hub at ``revision``, the last few kept once fetched: the API
    decodes one doc's tokens at a time, and a fetch costs a round trip."""
    return Tokenizer.from_file(hf_hub_download(name, "tokenizer.json", revision=revision))


def token_texts(
    tokenizer: str, revision: str | None, ids: list[list[int]], choices: list[str]
) -> list[list[str]]:
    """Each choice's continuation token ids as text, a piece per token, decoded with the run's
    tokenizer (``tokenizer.json`` from the Hub at ``revision``). A token that ends inside a
    character has an empty piece; the character goes to the token that completes it.

    Raises:
        ValueError: if the tokenizer can't be fetched, or its pieces don't give back each
            choice's text, so the ids weren't made by it."""
    try:
        decoder = _tokenizer(tokenizer, revision)
    # Whatever stops the fetch (a local path, a gated or missing repo, no network) leaves the
    # values without text, not the doc unread.
    except Exception as exc:
        raise ValueError(f"no tokenizer {tokenizer} to name the tokens: {exc}") from exc
    texts = []
    for choice_ids, choice in zip(ids, choices, strict=True):
        # Decoded a prefix at a time: a byte-level token alone can hold half a character, which
        # decodes to U+FFFD until the token that completes it.
        pieces, done = [], ""
        for end in range(1, len(choice_ids) + 1):
            now = decoder.decode(choice_ids[:end], skip_special_tokens=False)
            if now.endswith("\ufffd") or not now.startswith(done):
                pieces.append("")
                continue
            pieces.append(now[len(done) :])
            done = now
        # Whitespace at the ends aside (SentencePiece drops a leading space when decoding), the
        # pieces are the choice.
        if "".join(pieces).strip() != choice.strip():
            raise ValueError(f"{tokenizer} doesn't give back the text of {choice.strip()!r}")
        texts.append(pieces)
    return texts
