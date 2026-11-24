"""Score a run's current weights on Flock perplexity suites, split across its ranks, before saves.

Scoring is Flock's own: tasks come from `flock.eval.tasks`, are tokenized by `prepare_task`, and
each document is scored by `ScoringModel.rolling_loglikelihood` around the training model,
wrapped in `FlockCausalLM`. Each rank scores a share of the documents; the per-task sums of
log-probability, tokens and UTF-8 bytes are then added across ranks and turned into the same
bits per byte `flock eval run` reports, with `flock.eval.metrics.bits_per_byte` and
`Suite.score`.
"""

import heapq
import json
import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import torch
import torch.distributed as dist
from torch import nn

from flock.eval.metrics import bits_per_byte
from flock.eval.model import DEFAULT_BATCH_TOKENS, FlockCausalLM, ScoringModel
from flock.eval.provenance import flock_commit
from flock.eval.run import PreparedPerplexity, prepare_task
from flock.eval.tasks import SUITES
from flock.eval.tasks.perplexity import PerplexityTask
from flock.eval.tasks.suite import Suite
from flock.training.core.attention import AttentionInterface
from flock.training.core.attention.base import GroupedAttentionWithRoPE
from flock.training.core.distributed import DistributedRuntime

logger = logging.getLogger(__name__)

DEFAULT_SUITES = {"paloma": "paloma", "tw_text": "tw_text_v0"}
"""Flock suites scored before each save, and the label each one's metrics are logged under."""


@contextmanager
def fp32_sdpa_scoring(model: nn.Module) -> Iterator[None]:
    """Score `model` in float32 with SDPA attention, then restore its attention and precision.

    FlashAttention has no float32 kernels, so every attention module and mask preparer is
    switched to SDPA, and the model's `config` names SDPA, as a Flock model loaded from a
    checkpoint whose config names SDPA does for `flock eval run`. Autocast is disabled and
    float32 matrix products run at full precision, so the training run's float32 master
    weights are scored as they are.
    """
    saved: list[tuple[Any, str, Any]] = []
    for module in model.modules():
        if isinstance(module, GroupedAttentionWithRoPE):
            saved.append((module, "attention_fn", module.attention_fn))
            module.attention_fn = AttentionInterface.get_interface("sdpa")
        if hasattr(module, "mask_preparer"):
            saved.append((module, "mask_preparer", module.mask_preparer))
            module.mask_preparer = AttentionInterface.get_mask_preparer("sdpa")
    config = getattr(model, "config", None)
    if config is not None and hasattr(config, "attn_implementation"):
        saved.append((model, "config", config))
        model.config = replace(config, attn_implementation="sdpa")
    precision = torch.get_float32_matmul_precision()
    torch.set_float32_matmul_precision("highest")
    device_type = next(model.parameters()).device.type
    try:
        with torch.autocast(device_type, enabled=False):
            yield
    finally:
        torch.set_float32_matmul_precision(precision)
        for owner, name, value in reversed(saved):
            setattr(owner, name, value)


@dataclass(frozen=True)
class EvalOutcome:
    """What one save-time evaluation produced.

    Args:
        metrics (`dict[str, float]`): W&B metrics, empty when it failed.
        record (`dict[str, Any]`): Everything scored, for `evals.jsonl`.
        error (`str`, *optional*): Why it failed, if it did.
    """

    metrics: dict[str, float]
    record: dict[str, Any]
    error: str | None = None


def shard_documents(sizes: Sequence[Sequence[int]], world_size: int) -> list[list[tuple[int, int]]]:
    """Split `(task, document)` pairs across ranks so each scores about the same tokens.

    Longest documents are placed first, each on the least loaded rank; ties break by rank,
    so every rank computes the same split.
    """
    items = sorted(
        ((size, task, doc) for task, docs in enumerate(sizes) for doc, size in enumerate(docs)),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    loads = [(0, rank) for rank in range(world_size)]
    shards: list[list[tuple[int, int]]] = [[] for _ in range(world_size)]
    for size, task, doc in items:
        load, rank = heapq.heappop(loads)
        shards[rank].append((task, doc))
        heapq.heappush(loads, (load + max(size, 1), rank))
    return [sorted(shard) for shard in shards]


def task_metrics(logprob: float, num_tokens: int, num_bytes: int) -> dict[str, float]:
    """Return a perplexity task's metrics from its summed scores, as `flock eval run` does."""
    logprob_t = torch.tensor(logprob, dtype=torch.float64)
    bpb = float(bits_per_byte(logprob_t, torch.tensor(num_bytes)))
    loss = -logprob / max(num_tokens, 1)
    return {"bits_per_byte": bpb, "loss_per_token": loss}


class SaveEvaluator:
    """Score Flock perplexity suites on every rank's share of the documents.

    Tasks are loaded and tokenized on first use and kept for later saves. Each rank scores
    its share without communicating; ranks then agree whether every rank succeeded before
    adding their sums, so a failure on one rank cannot leave the others waiting.

    Args:
        suites (`Mapping[str, str]`): Flock suite name to the label its metrics use, such as
            `{"paloma": "paloma", "tw_text": "tw_text_v0"}`.
        runtime (`DistributedRuntime`): The run's ranks and device.
        batch_tokens (`int`, *optional*): `ScoringModel` padded-token budget per batch.
        limit (`int`, *optional*): Score at most this many documents per task, for tests.
    """

    def __init__(
        self,
        suites: Mapping[str, str],
        runtime: DistributedRuntime,
        *,
        batch_tokens: int = DEFAULT_BATCH_TOKENS,
        limit: int | None = None,
    ) -> None:
        """Resolve the suites; nothing is loaded until the first evaluation."""
        self.suites: dict[str, Suite] = {}
        for name, label in suites.items():
            if name not in SUITES:
                raise ValueError(f"Unknown Flock suite {name!r}")
            self.suites[label] = SUITES[name]
        self.runtime = runtime
        self.batch_tokens = batch_tokens
        self.limit = limit
        self.tasks: list[tuple[str, PerplexityTask]] = []
        for label, suite in self.suites.items():
            for task in suite.tasks:
                if not isinstance(task, PerplexityTask):
                    raise TypeError(f"{task.name} is not a perplexity task")
                self.tasks.append((label, task))
        self._prepared: list[PreparedPerplexity] | None = None
        self._bytes: list[list[int]] = []
        self._shard: list[tuple[int, int]] = []

    def _prepare(self, scorer: ScoringModel) -> None:
        # Tasks of one source share a cached split, so prepare them in suite order.
        prepared = []
        for _, task in self.tasks:
            item = prepare_task(scorer, task, limit=self.limit)
            assert isinstance(item, PreparedPerplexity)
            prepared.append(item)
        self._bytes = [[len(doc.text.encode()) for doc in item.docs] for item in prepared]
        sizes = [[len(ids) for ids in item.token_ids] for item in prepared]
        self._shard = shard_documents(sizes, self.runtime.world_size)[self.runtime.rank]
        self._prepared = prepared

    def _score_shard(self, scorer: ScoringModel) -> torch.Tensor:
        """Return this rank's `[task, (logprob, tokens, bytes)]` sums in float64."""
        assert self._prepared is not None
        sums = torch.zeros((len(self.tasks), 3), dtype=torch.float64, device=self.runtime.device)
        by_window: dict[int, list[tuple[int, int]]] = {}
        for task, doc in self._shard:
            by_window.setdefault(self._prepared[task].task.window, []).append((task, doc))
        for window, pairs in by_window.items():
            texts = [self._prepared[t].docs[d].text for t, d in pairs]
            ids = [self._prepared[t].token_ids[d] for t, d in pairs]
            scores = scorer.rolling_loglikelihood(texts, window, ids)
            index = torch.tensor([t for t, _ in pairs], device=sums.device)
            sums[:, 0].index_add_(0, index, scores.logprob.to(sums.device, torch.float64))
            sums[:, 1].index_add_(0, index, scores.num_tokens.to(sums.device, torch.float64))
            nbytes = torch.tensor(
                [self._bytes[t][d] for t, d in pairs], dtype=torch.float64, device=sums.device
            )
            sums[:, 2].index_add_(0, index, nbytes)
        return sums

    def evaluate(self, model: nn.Module, tokenizer: Any, *, name: str) -> EvalOutcome:
        """Score `model` on every rank and return the summed metrics on every rank.

        Every rank must call this at the same point. It never raises for a scoring failure;
        the failure is returned instead, so training can continue.
        """
        start = time.monotonic()
        prepared_before = self._prepared is not None
        error: str | None = None
        sums: torch.Tensor | None = None
        try:
            with fp32_sdpa_scoring(model):
                scorer = ScoringModel(
                    FlockCausalLM(model), tokenizer, name=name, batch_tokens=self.batch_tokens
                )
                if self._prepared is None:
                    self._prepare(scorer)
                prepare_seconds = time.monotonic() - start
                sums = self._score_shard(scorer)
        except Exception as exception:  # An eval must never stop training.
            error = f"rank {self.runtime.rank}: {type(exception).__name__}: {exception}"
            logger.exception("Save-time evaluation failed on rank %d", self.runtime.rank)
            prepare_seconds = 0.0
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        failed = torch.tensor([0.0 if error is None else 1.0], device=self.runtime.device)
        if self.runtime.enabled:
            dist.all_reduce(failed, op=dist.ReduceOp.MAX)
        seconds = time.monotonic() - start
        record: dict[str, Any] = {
            "name": name,
            "flock_commit": flock_commit(),
            "seconds": seconds,
            "prepare_seconds": 0.0 if prepared_before else prepare_seconds,
            "world_size": self.runtime.world_size,
            "dtype": "float32",
            "attention": "sdpa",
            "batch_tokens": self.batch_tokens,
        }
        if failed.item() > 0 or sums is None:
            record["error"] = error or "another rank's evaluation failed"
            return EvalOutcome(
                {"eval/failed": 1.0, "eval/seconds": seconds}, record, record["error"]
            )
        if self.runtime.enabled:
            dist.all_reduce(sums)
        assert self._prepared is not None
        return self._outcome(sums.cpu().tolist(), record, seconds)

    def _outcome(
        self, sums: list[list[float]], record: dict[str, Any], seconds: float
    ) -> EvalOutcome:
        per_task: dict[str, dict[str, float]] = {}
        tasks_record = {}
        assert self._prepared is not None
        for (label, task), prepared, (logprob, tokens, nbytes) in zip(
            self.tasks, self._prepared, sums, strict=True
        ):
            metrics = task_metrics(logprob, round(tokens), round(nbytes))
            per_task[task.name] = metrics
            tasks_record[task.name] = {
                "suite": label,
                "logprob": logprob,
                "num_tokens": round(tokens),
                "num_bytes": round(nbytes),
                "num_docs": len(prepared.docs),
                "task_hash": prepared.task_hash,
                "dataset": f"{task.dataset.repo}@{task.dataset.revision}",
                **metrics,
            }
        log: dict[str, float] = {"eval/failed": 0.0, "eval/seconds": seconds}
        suites_record = {}
        for label, suite in self.suites.items():
            for member in suite.suites:
                if not member.scored:
                    continue
                score = member.score(per_task)
                key = "score" if member is suite else member.name.split(":", 1)[-1]
                log[f"eval/{label}/{key}"] = score
                suites_record[member.name] = score
            for task in suite.tasks:
                log[f"eval/{label}/tasks/{task.name}"] = per_task[task.name]["bits_per_byte"]
        record.update({"suites": suites_record, "tasks": tasks_record})
        return EvalOutcome(log, record)


def append_record(path: Path, record: Mapping[str, Any]) -> None:
    """Append one evaluation record to a JSON Lines file."""
    with path.open("a") as file:
        file.write(json.dumps(record, sort_keys=True) + "\n")
