"""Reading two eval runs' files for an item comparison (025, D185), and naming the tokens of
their per-token values (030)."""

import asyncio
import functools
from dataclasses import dataclass

import httpx
from huggingface_hub import HfApi, constants, hf_hub_url
from huggingface_hub.utils import build_hf_headers
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
    decodes one doc's tokens at a time, and a fetch costs a round trip. Fetched into memory, never
    the disk, and refused past ``FILE_BYTES``: the name comes from an uploaded run.

    Only a repo anyone can see is read: the server's own token, if it has one, then opens a gated
    one such as Llama's, never a private one a viewer couldn't read."""
    if constants.HF_HUB_OFFLINE:
        raise ValueError("the Hub is offline here")
    # Asked without a token: a private repo answers as if it didn't exist.
    if HfApi().model_info(name, revision=revision, token=False).private:
        raise ValueError(f"{name} isn't a public repo")
    url = hf_hub_url(name, "tokenizer.json", revision=revision)
    data = bytearray()
    with httpx.stream("GET", url, headers=build_hf_headers(), follow_redirects=True) as answer:
        answer.raise_for_status()
        for chunk in answer.iter_bytes():
            data += chunk
            if len(data) > FILE_BYTES:
                raise ValueError(f"{name}'s tokenizer.json is over {FILE_BYTES >> 20} MiB")
    return Tokenizer.from_str(data.decode())


def token_texts(
    tokenizer: str, revision: str | None, ids: list[list[int]], choices: list[str]
) -> list[list[str]]:
    """Each choice's continuation token ids as text, a piece per token, decoded with the run's
    tokenizer (``tokenizer.json`` from the Hub at ``revision``). A token that ends inside a
    character has an empty piece; the character goes to the token that completes it.

    Raises:
        ValueError: if the tokenizer can't be fetched, or its pieces don't give back each
            choice's text, so the ids weren't made by it."""
    # Each token's text costs a decode of its prefix, so a long choice costs its length squared;
    # multiple-choice continuations are a few dozen tokens. Checked before any fetch.
    longest = max((len(choice_ids) for choice_ids in ids), default=0)
    if longest > 256:
        raise ValueError(f"a choice of {longest} tokens is too long to name them")
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
