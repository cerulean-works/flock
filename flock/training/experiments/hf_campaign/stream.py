"""Finite admitted source streams with explicit target and restart accounting."""

import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Protocol

from datasets import Dataset


class Encoder(Protocol):
    """Tokenizer interface used after document admission."""

    def encode(self, text: str, *, add_special_tokens: bool) -> list[int]: ...


@dataclass(frozen=True)
class Pool:
    """Ordered immutable membership and a separately approved target quota."""

    name: str
    bucket: str
    rows: Dataset
    targets: int


@dataclass(frozen=True)
class Block:
    """Aligned inputs and next-token targets, with source identities."""

    ordinal: int
    inputs: list[int]
    labels: list[int]
    identities: list[tuple[str, str, int]]
    bucket: str


class TargetStream:
    """Consume ordered admitted documents without implicit repetition or runtime shuffle."""

    def __init__(
        self,
        pools: list[Pool],
        encoder: Encoder,
        *,
        eos_id: int,
        block_size: int,
        manifest_hash: str,
        exclusion_hash: str,
        tokenizer_hash: str,
        forbidden_spans: tuple[str, ...] = (),
    ) -> None:
        if not pools or block_size < 1 or eos_id < 0:
            raise ValueError("nonempty pools, positive block size and EOS are required")
        if len({p.name for p in pools}) != len(pools):
            raise ValueError("duplicate pool names")
        if any(p.bucket not in {"en", "tc_native", "tc_converted"} or p.targets < 1 for p in pools):
            raise ValueError("invalid bucket or quota")
        if not manifest_hash or not exclusion_hash or not tokenizer_hash:
            raise ValueError("pinned manifest and exclusion hashes are required")
        self.pools = {p.name: p for p in pools}
        self.encoder = encoder
        self.eos_id = eos_id
        self.block_size = block_size
        self.forbidden_spans = forbidden_spans
        contract = {
            "schema": 1,
            "manifest": manifest_hash,
            "exclusion": exclusion_hash,
            "tokenizer": tokenizer_hash,
            "eos": eos_id,
            "block": block_size,
            "pools": [(p.name, p.bucket, p.targets, p.rows._fingerprint) for p in pools],
            "forbidden_spans": forbidden_spans,
        }
        self.contract = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
        self.state: dict[str, Any] = {
            "contract": self.contract,
            "positions": {p.name: 0 for p in pools},
            "pending": {p.name: [] for p in pools},
            "consumed": {p.name: 0 for p in pools},
            "eos": {p.name: 0 for p in pools},
            "context": None,
            "ordinal": 0,
            "rejected": 0,
            "seen_families": [],
        }

    def state_dict(self) -> dict[str, Any]:
        """Copy the exact producer position, including partially consumed documents."""
        return copy.deepcopy(self.state)

    def load_state_dict(self, state: dict[str, Any]) -> None:
        """Restore only the identical pinned stream contract."""
        if state.get("contract") != self.contract:
            raise ValueError("stream contract changed")
        self.state = copy.deepcopy(state)

    def _token(self, name: str) -> tuple[int, str, int, bool]:
        pool = self.pools[name]
        pending = self.state["pending"][name]
        while not pending:
            index = self.state["positions"][name]
            if index >= len(pool.rows):
                raise RuntimeError(f"source exhausted: {name}; no implicit repetition")
            row = pool.rows[index]
            self.state["positions"][name] += 1
            text = row["text"]
            # Every rejection happens before tokenizer admission, including derivatives.
            originals = [text, row.get("original_text", text)]
            eligible = (
                row.get("admitted") is True
                and row.get("split") == "train"
                and row.get("rights_receipt")
                and row.get("exclusion_receipt")
                and row.get("id")
                and row.get("text_sha256") == hashlib.sha256(text.encode()).hexdigest()
                and row.get("source_pin")
                and row.get("family_id")
                and row["family_id"] not in self.state["seen_families"]
                and len(text) <= 1_048_576
            )
            if pool.bucket == "tc_converted":
                eligible = (
                    eligible
                    and row.get("conversion_receipt")
                    and isinstance(row.get("original_text"), str)
                    and row.get("original_sha256")
                    == hashlib.sha256(row["original_text"].encode()).hexdigest()
                )
            contaminated = any(span in view for span in self.forbidden_spans for view in originals)
            if not eligible or contaminated:
                self.state["rejected"] += 1
                continue
            ids = self.encoder.encode(text, add_special_tokens=False)
            if not ids:
                continue
            self.state["seen_families"].append(row["family_id"])
            if len(ids) > 1_048_576:
                raise ValueError("document exceeds bounded token buffer")
            pending.append((self.eos_id, row["id"], len(ids), True))
            for offset in range(len(ids) - 1, -1, -1):
                pending.append((ids[offset], row["id"], offset, False))
        return pending.pop()

    def blocks(self, language: str, targets: int) -> list[Block]:
        """Emit one language allocation; callers checkpoint before speculative production."""
        if language not in {"en", "tc"} or targets < 0:
            raise ValueError("invalid language or target count")
        names = [n for n, p in self.pools.items() if (p.bucket == "en") == (language == "en")]
        remaining = sum(self.pools[n].targets - self.state["consumed"][n] for n in names)
        if remaining < targets:
            raise RuntimeError("requested language quota exceeds remaining approved source targets")
        blocks = []
        while targets:
            # Scheduling occurs at block boundaries, never interleaving document tokens.
            active = [n for n in names if self.state["consumed"][n] < self.pools[n].targets]
            name = min(
                active,
                key=lambda n: (Fraction(self.state["consumed"][n], self.pools[n].targets), n),
            )
            count = min(
                targets, self.block_size, self.pools[name].targets - self.state["consumed"][name]
            )
            if self.state["context"] is None:
                self.state["context"] = self._token(name)[0]
            inputs, labels, identities = [], [], []
            for _ in range(count):
                token, document, offset, is_eos = self._token(name)
                inputs.append(self.state["context"])
                labels.append(token)
                identities.append((name, document, offset))
                self.state["context"] = token
                self.state["consumed"][name] += 1
                self.state["eos"][name] += int(is_eos)
            blocks.append(
                Block(self.state["ordinal"], inputs, labels, identities, self.pools[name].bucket)
            )
            self.state["ordinal"] += 1
            targets -= count
        return blocks

    @property
    def counters(self) -> dict[str, int]:
        """Report emitted source targets, separate from an optimizer-committed snapshot."""
        counts = Counter()
        for name, pool in self.pools.items():
            counts[pool.bucket] += self.state["consumed"][name]
        return dict(counts)
