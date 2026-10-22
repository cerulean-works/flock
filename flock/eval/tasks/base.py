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
        """Returns the rows of `split`, defaulting to the scored split."""
        rows = datasets.load_dataset(
            self.repo, self.subset, revision=self.revision, split=split or self.split
        )

        return list(rows)


@dataclass(frozen=True, slots=True)
class Fewshot:
    """
    Fixed few-shot examples, shown in the order of `ids`.

    Args:
        split (`str`):
            The dataset split the examples come from.
        ids (`tuple[str, ...]`):
            The examples' [`MCDoc`] ids, as `to_doc` returns them.
    """

    split: str
    ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MCDoc:
    """
    One multiple-choice question.

    Args:
        id (`str`):
            The question's id in its dataset, unique within a split. [`Fewshot`] picks examples by it, and the predictions file records it.
        question (`str`):
            The question text.
        choices (`tuple[str, ...]`):
            The answer choices as plain text, without a leading space: the task adds one when it scores them.
        gold (`int`):
            The index of the correct choice in `choices`.
    """

    id: str
    question: str
    choices: tuple[str, ...]
    gold: int


@dataclass(frozen=True, slots=True)
class Truncation:
    """
    How much of a task's scoring saw contexts cut from the left to fit the model's `max_length`.

    Args:
        requests (`int`):
            Requests whose context lost tokens, conditional and unconditional alike.
        docs (`int`):
            Docs with at least one such request, whose metrics may have changed.
        max_tokens_cut (`int`):
            The most tokens cut from any one request.
    """

    requests: int
    docs: int
    max_tokens_cut: int


@dataclass(frozen=True, slots=True)
class MCRequests:
    """
    A multiple-choice task's requests for a list of docs, and how their scores map back.

    Args:
        docs (`list[MCDoc]`):
            The scored docs, in order.
        requests (`list[Request]`):
            Each doc's choices in doc order, followed by the same choices after the unconditional context when `has_uncond` is set.
        hash (`str`):
            sha256 of exactly what is scored, as [Results files](results#the-task-hash) defines it.
        has_uncond (`bool`):
            Whether the unconditional requests for pointwise mutual information (PMI) follow the conditional ones.
    """

    docs: list[MCDoc]
    requests: list[Request]
    hash: str
    has_uncond: bool

    def choice_scores(self, scores: Scores) -> ChoiceScores:
        """
        Lays `scores` (one per request, in order) out per doc as `[docs, max_choices]`.

        Raises:
            `ValueError`: if there are no docs, or not one request per choice (two with `has_uncond`).
        """
        num_choices = self._check_layout()
        device = scores.logprob.device
        index = self._choice_index().to(device)
        valid = index >= 0
        # Padded cells read request 0. Every metric masks them out with `valid`.
        gather_index = index.clamp(min=0)

        num_chars, num_bytes = self._continuation_lengths(num_choices, device)
        gold = torch.tensor([doc.gold for doc in self.docs], dtype=torch.int64, device=device)

        logprob_uncond = None
        if self.has_uncond:
            # The unconditional requests follow all conditional ones, in the same order.
            logprob_uncond = scores.logprob[gather_index + num_choices]

        return ChoiceScores(
            logprob=scores.logprob[gather_index],
            num_tokens=scores.num_tokens[gather_index],
            num_chars=num_chars[gather_index],
            num_bytes=num_bytes[gather_index],
            valid=valid,
            gold=gold,
            logprob_uncond=logprob_uncond,
        )

    def truncation(self, scores: Scores) -> Truncation:
        """
        Counts the requests and docs whose contexts `scores` says were cut, copying three numbers to the host once.

        Raises:
            `ValueError`: if there are no docs, or not one request per choice (two with `has_uncond`).
        """
        num_choices = self._check_layout()
        tokens_cut = scores.tokens_cut
        device = tokens_cut.device

        # Each conditional request's doc, read off the same layout `choice_scores` uses.
        choice_index = self._choice_index()
        valid = choice_index >= 0
        docs_of_cells = torch.arange(len(self.docs))[:, None].expand_as(choice_index)
        doc_index = torch.empty(num_choices, dtype=torch.int64)
        doc_index[choice_index[valid]] = docs_of_cells[valid]
        if self.has_uncond:
            # The unconditional requests follow all conditional ones, in the same order.
            doc_index = doc_index.repeat(2)
        index = doc_index.to(device, non_blocking=True)

        truncated = tokens_cut > 0
        per_doc = torch.zeros(len(self.docs), dtype=torch.int64, device=device)
        per_doc.index_add_(0, index, truncated.long())
        requests, docs, max_tokens_cut = torch.stack(
            [truncated.sum(), (per_doc > 0).sum(), tokens_cut.max()]
        ).tolist()

        return Truncation(requests=requests, docs=docs, max_tokens_cut=max_tokens_cut)

    def _check_layout(self) -> int:
        """
        Returns the number of choices over all docs, after checking that there is one request per choice (two with `has_uncond`).

        Raises:
            `ValueError`: if there are no docs, or the request count doesn't match the choices.
        """
        if not self.docs:
            raise ValueError("no docs to score")
        num_choices = sum(len(doc.choices) for doc in self.docs)
        if len(self.requests) != (2 if self.has_uncond else 1) * num_choices:
            raise ValueError(f"{len(self.requests)} requests for {num_choices} choices")

        return num_choices

    def _continuation_lengths(
        self, num_choices: int, device: torch.device
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Returns each choice's character and UTF-8 byte counts as int64 `[num_choices]`, in request order."""
        # Counted on the conditional requests: the unconditional ones repeat the same continuations.
        continuations = [request.continuation for request in self.requests[:num_choices]]

        num_chars = torch.tensor(
            [len(text) for text in continuations], dtype=torch.int64, device=device
        )
        num_bytes = torch.tensor(
            [len(text.encode()) for text in continuations], dtype=torch.int64, device=device
        )

        return num_chars, num_bytes

    def _choice_index(self) -> torch.Tensor:
        """Returns the request index of every choice as int64 `[docs, max_choices]`, -1 past a doc's last choice."""
        width = max(len(doc.choices) for doc in self.docs)
        rows = []
        start = 0
        for doc in self.docs:
            # Each doc's choices are consecutive requests, right after the previous doc's.
            end = start + len(doc.choices)
            rows.append(list(range(start, end)) + [-1] * (width - len(doc.choices)))
            start = end

        return torch.tensor(rows, dtype=torch.int64)


@dataclass(frozen=True, slots=True)
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
            Turns one dataset row into an [`~eval.tasks.MCDoc`].
        primary_metric (`str`):
            The metric reported first, one of [`~eval.metrics.multiple_choice`]'s names.
        fewshot (`Fewshot`, *optional*):
            The few-shot examples, from the same dataset. Zero-shot if not set.
        question_prefix (`str`, *optional*, defaults to `"Question: "`):
            Written before each question.
        answer_prefix (`str`, *optional*, defaults to `"Answer:"`):
            Written on the line after each question.
        unconditional_context (`str`, *optional*, defaults to `"Answer:"`):
            The context for the unconditional scores of pointwise mutual information (PMI). PMI is skipped if `None`. On by default, as in OLMES, whose multiple-choice tasks score every choice after `"Answer:"` too unless a task overrides `unconditioned_prompt` with `None` (`oe_eval/tasks/base_task.py`). That doubles the requests, but the unconditional contexts are short.

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
        Loads the scored split, keeping its first `limit` docs (all if `None`), and the few-shot examples.

        Returns:
            `tuple[list[MCDoc], list[MCDoc]]`: the docs to score, then the few-shot examples.

        Raises:
            `ValueError`: if `limit` is less than 1.
            `KeyError`: if a few-shot id isn't in its split.
        """
        if limit is not None and limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")

        docs = [self.to_doc(row) for row in self.dataset.load()[:limit]]

        return docs, self._load_shots()

    def _load_shots(self) -> list[MCDoc]:
        """
        Returns the few-shot examples in `fewshot`'s order, or none if the task is zero-shot.

        Raises:
            `KeyError`: if a few-shot id isn't in its split.
        """
        if self.fewshot is None:
            return []

        rows = self.dataset.load(self.fewshot.split)
        pool = {doc.id: doc for doc in map(self.to_doc, rows)}

        missing = [shot_id for shot_id in self.fewshot.ids if shot_id not in pool]
        if missing:
            raise KeyError(f"{self.name}: few-shot ids not in {self.fewshot.split}: {missing}")

        return [pool[shot_id] for shot_id in self.fewshot.ids]

    def prompt(self, doc: MCDoc) -> str:
        """Renders the question as the model sees it, ending right before the answer."""
        return f"{self.question_prefix}{doc.question}\n{self.answer_prefix}"

    def context(self, doc: MCDoc, shots: Sequence[MCDoc]) -> str:
        """
        Renders the few-shot examples followed by `doc`'s prompt.

        Each example is its prompt and gold choice, and examples are separated by a blank line, as in OLMES's cloze format (OLMES, sec. 3.1 and 3.2).
        """
        examples = "".join(f"{self.prompt(shot)} {shot.choices[shot.gold]}\n\n" for shot in shots)

        return examples + self.prompt(doc)

    def requests(self, docs: list[MCDoc], shots: Sequence[MCDoc]) -> MCRequests:
        """Builds every request needed to score `docs`, with a hash of exactly what is scored."""
        conditional = []
        for doc in docs:
            context = self.context(doc, shots)
            for choice in doc.choices:
                conditional.append(Request(context, f" {choice}"))

        unconditional = []
        if self.unconditional_context is not None:
            for request in conditional:
                unconditional.append(Request(self.unconditional_context, request.continuation))

        # `MCRequests.choice_scores` relies on this order: all conditional requests, then the
        # unconditional ones in the same order.
        requests = conditional + unconditional

        return MCRequests(
            docs=docs,
            requests=requests,
            hash=self._hash(docs, requests),
            has_uncond=self.unconditional_context is not None,
        )

    def _hash(self, docs: list[MCDoc], requests: list[Request]) -> str:
        """Returns the sha256 of the definition's data fields, every request and every gold index."""
        # `to_doc` is a function and can't be serialized. What it produces is still covered: every
        # request and gold index below comes from its docs.
        fields = {
            field.name: getattr(self, field.name)
            for field in dataclasses.fields(self)
            if field.name != "to_doc"
        }
        # `default` turns the nested `Dataset` and `Fewshot` dataclasses into dicts for `json`.
        digest = hashlib.sha256(
            json.dumps(fields, default=dataclasses.asdict, sort_keys=True).encode()
        )

        for request in requests:
            digest.update(json.dumps([request.context, request.continuation]).encode())
        digest.update(json.dumps([doc.gold for doc in docs]).encode())

        return digest.hexdigest()
