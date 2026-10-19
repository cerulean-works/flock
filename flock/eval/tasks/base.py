"""Multiple-choice tasks as data: a pinned dataset, a row-to-doc function and a prompt format."""

import dataclasses
import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import datasets
import torch

from flock.eval.metrics import MULTIPLE_CHOICE_METRICS, ChoiceScores
from flock.eval.requests import Request, Scores


@dataclass(frozen=True, slots=True)
class Dataset:
    """
    A Hugging Face dataset pinned to a revision.

    Args:
        repo (`str`):
            The dataset repo, for example `allenai/ai2_arc`.
        revision (`str`):
            A commit hash, so the rows can't change under a task.
        subset (`str` or `None`):
            The configuration name, for example `ARC-Easy`.
        split (`str`):
            The split a task is scored on.
    """

    repo: str
    revision: str
    subset: str | None
    split: str

    def load(self, split: str | None = None) -> list[dict[str, Any]]:
        """The rows of `split`, defaulting to the scored split."""
        rows = datasets.load_dataset(
            self.repo, self.subset, revision=self.revision, split=split or self.split
        )
        return list(rows)


@dataclass(frozen=True, slots=True)
class Fewshot:
    """Fixed few-shot examples: the ids of rows in `split`, shown in this order."""

    split: str
    ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MCDoc:
    """One multiple-choice question, with `gold` indexing its correct choice."""

    id: str
    question: str
    choices: tuple[str, ...]
    gold: int


@dataclass(frozen=True)
class MCRequests:
    """
    A multiple-choice task's requests for a list of docs, and how their scores map back.

    `requests` holds each doc's choices in doc order, followed by the same choices after the unconditional context when `has_uncond` is set, for PMI.
    """

    docs: list[MCDoc]
    requests: list[Request]
    hash: str
    has_uncond: bool

    def choice_scores(self, scores: Scores) -> ChoiceScores:
        """
        Lays `scores` (one per request, in order) out as `[docs, max_choices]`.

        Raises:
            `ValueError`: if there are no docs, or not one request per choice (two with `has_uncond`).
        """
        if not self.docs:
            raise ValueError("no docs to score")
        choices = sum(len(doc.choices) for doc in self.docs)
        if len(self.requests) != (2 if self.has_uncond else 1) * choices:
            raise ValueError(f"{len(self.requests)} requests for {choices} choices")
        width = max(len(doc.choices) for doc in self.docs)
        index, num_chars, num_bytes = [], [], []
        start = 0
        for doc in self.docs:
            padding = [-1] * (width - len(doc.choices))
            continuations = [
                r.continuation for r in self.requests[start : start + len(doc.choices)]
            ]
            index.append(list(range(start, start + len(doc.choices))) + padding)
            num_chars.append([len(c) for c in continuations] + padding)
            num_bytes.append([len(c.encode()) for c in continuations] + padding)
            start += len(doc.choices)

        device = scores.logprob.device
        index_t, num_chars_t, num_bytes_t, gold = (
            torch.tensor(x).to(device, non_blocking=True)
            for x in (index, num_chars, num_bytes, [doc.gold for doc in self.docs])
        )
        valid = index_t >= 0
        safe = index_t.clamp(min=0)
        return ChoiceScores(
            logprob=scores.logprob[safe],
            num_tokens=scores.num_tokens[safe],
            num_chars=num_chars_t,
            num_bytes=num_bytes_t,
            valid=valid,
            gold=gold,
            logprob_uncond=scores.logprob[safe + start] if self.has_uncond else None,
        )


@dataclass(frozen=True)
class MultipleChoiceTask:
    """
    A multiple-choice task scored as OLMES's cloze formulation (OLMo 3's "rank choice").

    Each choice is a continuation `" <choice>"` of the prompt `"Question: <question>\\nAnswer:"`, after the few-shot examples, each answered with its gold choice and separated by blank lines (OLMES, sec. 3.1 and 3.2).

    Args:
        name (`str`):
            The task's registry name.
        dataset (`Dataset`):
            Where the scored rows come from.
        to_doc (`Callable[[dict], MCDoc]`):
            Turns one dataset row into an [`MCDoc`].
        primary_metric (`str`):
            The metric reported first, one of [`~metrics.multiple_choice`]'s names.
        fewshot (`Fewshot`, *optional*):
            The few-shot examples, from the same dataset. Zero-shot if not set.
        question_prefix (`str`, *optional*, defaults to `"Question: "`):
            Written before each question.
        answer_prefix (`str`, *optional*, defaults to `"Answer:"`):
            Written on the line after each question.
        unconditional_context (`str`, *optional*, defaults to `"Answer:"`):
            The context for PMI's unconditional scores. PMI is skipped if `None`.

    Raises:
        `ValueError`: if `primary_metric` isn't a metric the task reports.
    """

    name: str
    dataset: Dataset
    to_doc: Callable[[dict[str, Any]], MCDoc]
    primary_metric: str
    fewshot: Fewshot | None = None
    question_prefix: str = "Question: "
    answer_prefix: str = "Answer:"
    unconditional_context: str | None = "Answer:"

    def __post_init__(self) -> None:
        reported = MULTIPLE_CHOICE_METRICS
        if self.unconditional_context is None:
            reported = tuple(name for name in reported if name != "acc_uncond")
        if self.primary_metric not in reported:
            raise ValueError(f"{self.name}: primary_metric must be one of {reported}")

    def load(self, limit: int | None = None) -> tuple[list[MCDoc], list[MCDoc]]:
        """
        Downloads the first `limit` docs (all if `None`) and the few-shot examples.

        Raises:
            `ValueError`: if `limit` is less than 1.
            `KeyError`: if a few-shot id isn't in its split.
        """
        if limit is not None and limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        docs = [self.to_doc(row) for row in self.dataset.load()[:limit]]
        if self.fewshot is None:
            return docs, []
        pool = {doc.id: doc for doc in map(self.to_doc, self.dataset.load(self.fewshot.split))}
        missing = [i for i in self.fewshot.ids if i not in pool]
        if missing:
            raise KeyError(f"{self.name}: few-shot ids not in {self.fewshot.split}: {missing}")
        return docs, [pool[i] for i in self.fewshot.ids]

    def prompt(self, doc: MCDoc) -> str:
        """The question as the model sees it, ending right before the answer."""
        return f"{self.question_prefix}{doc.question}\n{self.answer_prefix}"

    def context(self, doc: MCDoc, shots: Sequence[MCDoc]) -> str:
        """The few-shot examples followed by `doc`'s prompt."""
        examples = "".join(f"{self.prompt(s)} {s.choices[s.gold]}\n\n" for s in shots)
        return examples + self.prompt(doc)

    def requests(self, docs: list[MCDoc], shots: Sequence[MCDoc]) -> MCRequests:
        """Every request needed to score `docs`, with a hash of exactly what is scored."""
        requests = [
            Request(self.context(doc, shots), f" {choice}")
            for doc in docs
            for choice in doc.choices
        ]
        if self.unconditional_context is not None:
            requests += [Request(self.unconditional_context, r.continuation) for r in requests]
        return MCRequests(
            docs=docs,
            requests=requests,
            hash=self._hash(docs, requests),
            has_uncond=self.unconditional_context is not None,
        )

    def _hash(self, docs: list[MCDoc], requests: list[Request]) -> str:
        """sha256 of the definition's data fields, every request and every gold index."""
        fields = {
            f.name: getattr(self, f.name) for f in dataclasses.fields(self) if f.name != "to_doc"
        }
        digest = hashlib.sha256(
            json.dumps(fields, default=dataclasses.asdict, sort_keys=True).encode()
        )
        for request in requests:
            digest.update(json.dumps([request.context, request.continuation]).encode())
        digest.update(json.dumps([doc.gold for doc in docs]).encode())
        return digest.hexdigest()
