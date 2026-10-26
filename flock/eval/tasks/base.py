"""Multiple-choice tasks as data: a pinned dataset, a row-to-doc function and a prompt format."""

import dataclasses
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import datasets
import huggingface_hub
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
        row_index (`bool`, *optional*, defaults to `False`):
            Whether each row gets its position in its split as `"index"`, for datasets with no id column. A `to_doc` then reads the doc's id as `str(row["index"])`, as OLMES does for MMLU (`oe_eval/tasks/oe_eval_tasks/mmlu.py`). Written-out `Fewshot.rows` don't go through `load`, so they need an `"index"` of their own.
        data_file (`str`, *optional*):
            A JSON file in the repo holding the scored split, read in place of the repo's configurations. For repos with a loading script, which `datasets` no longer runs, such as `allenai/basic-skills`. Takes no `subset`.

    Raises:
        `ValueError`: if `data_file` is given with a `subset`.
    """

    repo: str
    revision: str
    subset: str | None
    split: str
    row_index: bool = False
    data_file: str | None = None

    def __post_init__(self) -> None:
        if self.data_file is not None and self.subset is not None:
            raise ValueError(f"{self.repo}: data_file takes no subset")

    def load(self, split: str | None = None) -> list[dict[str, Any]]:
        """
        Returns the rows of `split`, defaulting to the scored split.

        Raises:
            `ValueError`: if `row_index` is set and the rows already have an `"index"` column, or, from `datasets`, if `data_file` is set and `split` isn't the scored split.
        """
        if self.data_file is None:
            rows = datasets.load_dataset(
                self.repo, self.subset, revision=self.revision, split=split or self.split
            )
        else:
            # Downloaded at the pinned revision into the hub cache, so a cached file loads offline.
            # It holds only the scored split, so asking for another one raises in `datasets`.
            path = huggingface_hub.hf_hub_download(
                self.repo, self.data_file, repo_type="dataset", revision=self.revision
            )
            rows = datasets.load_dataset(
                "json", data_files={self.split: path}, split=split or self.split
            )
        if not self.row_index:
            return list(rows)

        if "index" in rows.column_names:
            where = f"{self.repo} {self.subset or ''} {split or self.split}".replace("  ", " ")
            raise ValueError(f"{where}: the rows already have an index column")

        return [{**row, "index": position} for position, row in enumerate(rows)]


@dataclass(frozen=True, slots=True)
class Fewshot:
    """
    Fixed few-shot examples: dataset rows picked by id, or rows written out in the task's module. Either way they are shown in the order given.

    Args:
        split (`str`, *optional*):
            The dataset split `ids` come from. Required with `ids`, and not taken with `rows`.
        ids (`tuple[str, ...]`, *optional*):
            The examples' [`MCDoc`] ids, as `to_doc` returns them.
        rows (`tuple[dict, ...]`, *optional*):
            Examples that aren't rows of the dataset, in its row format. They go through the same `to_doc`.
        num_shots (`int`, *optional*):
            Show each doc only the first `num_shots` examples that aren't the doc itself. Only for `ids` from the scored split, which [`MultipleChoiceTask`] checks, since an id identifies a doc within one split. All examples are shown if not set.

    Raises:
        `ValueError`: if both or neither of `ids` and `rows` are given, `split` is missing with `ids` or given with `rows`, an id repeats, or `num_shots` is set with `rows` or isn't below the number of examples.
    """

    split: str | None = None
    ids: tuple[str, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    num_shots: int | None = None

    def __post_init__(self) -> None:
        if bool(self.ids) == bool(self.rows):
            raise ValueError("Fewshot takes either ids or rows")
        if self.ids and self.split is None:
            raise ValueError("Fewshot ids need a split")
        if self.rows and self.split is not None:
            raise ValueError("Fewshot rows take no split")

        repeated = sorted(shot_id for shot_id, count in Counter(self.ids).items() if count > 1)
        if repeated:
            raise ValueError(f"few-shot ids repeat: {repeated}")

        if self.num_shots is None:
            return
        if self.rows:
            raise ValueError("num_shots drops a doc's own example by id, so it needs ids")
        # One example more than shown, so a doc that is itself an example still gets `num_shots`.
        if not 1 <= self.num_shots < len(self.ids):
            raise ValueError(f"num_shots must be at least 1 and below the {len(self.ids)} examples")


@dataclass(frozen=True, slots=True)
class Choice:
    """
    One answer choice as the model scores it: the log-probability of `continuation` after the few-shot examples and `context`.

    Args:
        context (`str`):
            The doc's prompt for this choice. Most tasks give every choice the same one.
        continuation (`str`):
            Exactly the scored text, leading space included.
    """

    context: str
    continuation: str


def cloze(
    question: str, answers: Sequence[str], question_prefix: str = "Question: "
) -> tuple[Choice, ...]:
    """
    Returns OLMES's cloze choices: each answer is the continuation `" <answer>"` of `"<question_prefix><question>\\nAnswer:"` (OLMES, sec. 3.1).

    Raises:
        `TypeError`: if `answers` is one string. A doc with one answer passes `(answer,)`.
    """
    # A string is a sequence of its characters, which would render one choice per character.
    if isinstance(answers, str):
        raise TypeError("answers must be a sequence of strings; pass (answer,) for one answer")
    context = f"{question_prefix}{question}\nAnswer:"

    return tuple(Choice(context, f" {answer}") for answer in answers)


@dataclass(frozen=True, slots=True)
class MCDoc:
    """
    One multiple-choice question.

    Args:
        id (`str`):
            The question's id in its dataset, unique within a split. [`Fewshot`] picks examples by it, and the predictions file records it.
        choices (`tuple[Choice, ...]`):
            The answer choices, rendered as the model sees them. A task scored only on its gold text, such as a math solution, has one.
        gold (`int`):
            The index of the correct choice in `choices`.

    Raises:
        `ValueError`: naming the doc, if `choices` is empty or `gold` isn't an index into it.
    """

    id: str
    choices: tuple[Choice, ...]
    gold: int

    def __post_init__(self) -> None:
        if not self.choices:
            raise ValueError(f"doc {self.id}: no choices")
        if not 0 <= self.gold < len(self.choices):
            raise ValueError(
                f"doc {self.id}: gold {self.gold} is not one of {len(self.choices)} choices"
            )


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
        docs (`tuple[MCDoc, ...]`):
            The scored docs, in order.
        requests (`tuple[Request, ...]`):
            Each doc's choices in doc order, followed by the same choices after the unconditional context when `has_uncond` is set.
        hash (`str`):
            sha256 of exactly what is scored, as [Results files](results#the-task-hash) defines it.
        has_uncond (`bool`):
            Whether the unconditional requests for pointwise mutual information (PMI) follow the conditional ones.
    """

    docs: tuple[MCDoc, ...]
    requests: tuple[Request, ...]
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
    A multiple-choice task scored as OLMES scores it: each choice's continuation after the few-shot examples and the choice's context.

    The context of a request is `description`, then each few-shot example's gold choice (its context and continuation) followed by a blank line, then the scored choice's context. This is OLMES's few-shot format (OLMES, sec. 3.1 and 3.2; `fewshot_context` in `oe_eval/tasks/base_task.py`).

    Args:
        name (`str`):
            The task's registry name.
        dataset (`Dataset`):
            Where the scored rows come from.
        to_doc (`Callable[[dict], MCDoc]`):
            Turns one dataset row into an [`~eval.tasks.MCDoc`], rendering its choices. [`~eval.tasks.cloze`] renders OLMES's cloze format.
        primary_metric (`str`):
            The metric reported first, one of `metrics`.
        fewshot (`Fewshot`, *optional*):
            The few-shot examples, from the same dataset or written out. Zero-shot if not set.
        description (`str`, *optional*, defaults to `""`):
            Text before the first few-shot example, such as a subject header.
        unconditional_context (`str`, *optional*, defaults to `"Answer:"`):
            The context for the unconditional scores of pointwise mutual information (PMI). PMI is skipped if `None`. On by default, as in OLMES, whose multiple-choice tasks score every choice after `"Answer:"` too unless a task overrides `unconditioned_prompt` with `None` (`oe_eval/tasks/base_task.py`). That doubles the requests, but the unconditional contexts are short.
        metrics (`tuple[str, ...]`, *optional*):
            The metrics the task reports, from [`~eval.metrics.multiple_choice`]'s names. Every metric the task computes if not set, which leaves out `acc_uncond` when PMI is skipped. A task scored only on its gold text reports `("bpb",)`, since an accuracy over one choice is always 1.

    Raises:
        `ValueError`: if `metrics` is empty or names a metric the task doesn't compute, `primary_metric` isn't reported, a written-out example lacks the `"index"` a `row_index` dataset reads, or `fewshot.num_shots` is set on examples from a split other than the scored one. Also if the dataset is a `data_file` and the examples come from another split.
    """

    name: str
    dataset: Dataset
    to_doc: Callable[[dict[str, Any]], MCDoc]
    primary_metric: str
    fewshot: Fewshot | None = None
    description: str = ""
    unconditional_context: str | None = "Answer:"
    metrics: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        self._check_metrics()
        self._check_fewshot()

    def _check_metrics(self) -> None:
        """Raises `ValueError` if `metrics` or `primary_metric` names something the task won't report."""
        if self.metrics is not None:
            if not self.metrics:
                raise ValueError(
                    f"{self.name}: metrics is empty; leave it unset to report every computed metric"
                )
            unknown = [name for name in self.metrics if name not in self._computed_metrics]
            if unknown:
                raise ValueError(
                    f"{self.name}: metrics must be among {self._computed_metrics}, got {unknown}"
                )
        if self.primary_metric not in self.reported_metrics:
            raise ValueError(f"{self.name}: primary_metric must be one of {self.reported_metrics}")

    def _check_fewshot(self) -> None:
        """Raises `ValueError` if a written-out example lacks the index a numbered dataset reads, the examples' split can't be loaded, or `num_shots` would drop a doc's own example by an id from another split."""
        if self.fewshot is None:
            return
        # Written-out examples skip `load`, so they carry the index themselves.
        if self.dataset.row_index and any("index" not in row for row in self.fewshot.rows):
            raise ValueError(f"{self.name}: row_index needs an index in each written-out example")
        # A data file holds only the scored split, so examples can come from no other.
        other_split = self.fewshot.split not in (None, self.dataset.split)
        if self.dataset.data_file is not None and other_split:
            raise ValueError(
                f"{self.name}: {self.dataset.data_file} holds only the {self.dataset.split!r} "
                f"split, not few-shot split {self.fewshot.split!r}"
            )
        if self.fewshot.num_shots is None:
            return
        # Ids are unique within a split only. Across splits, a doc that shares an id with an
        # example would lose that example, and a doc that is itself an example would keep it.
        if self.fewshot.split != self.dataset.split:
            raise ValueError(
                f"{self.name}: num_shots needs examples from the scored split "
                f"{self.dataset.split!r}, not {self.fewshot.split!r}"
            )

    @property
    def reported_metrics(self) -> tuple[str, ...]:
        """The metrics the task reports, in [`~eval.metrics.multiple_choice`]'s order."""
        if self.metrics is None:
            return self._computed_metrics

        return tuple(name for name in self._computed_metrics if name in self.metrics)

    @property
    def _computed_metrics(self) -> tuple[str, ...]:
        """Every metric `multiple_choice` returns for this task: `acc_uncond` needs PMI."""
        if self.unconditional_context is None:
            return tuple(name for name in MULTIPLE_CHOICE_METRICS if name != "acc_uncond")

        return MULTIPLE_CHOICE_METRICS

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
        if self.fewshot.rows:
            return [self.to_doc(row) for row in self.fewshot.rows]

        rows = self.dataset.load(self.fewshot.split)
        pool = {doc.id: doc for doc in map(self.to_doc, rows)}

        missing = [shot_id for shot_id in self.fewshot.ids if shot_id not in pool]
        if missing:
            raise KeyError(f"{self.name}: few-shot ids not in {self.fewshot.split}: {missing}")

        return [pool[shot_id] for shot_id in self.fewshot.ids]

    def requests(self, docs: Sequence[MCDoc], shots: Sequence[MCDoc]) -> MCRequests:
        """Builds every request needed to score `docs`, with a hash of exactly what is scored."""
        conditional = []
        for doc in docs:
            head = self._head(self._shots_for(doc, shots))
            for choice in doc.choices:
                conditional.append(Request(head + choice.context, choice.continuation))

        unconditional = []
        if self.unconditional_context is not None:
            for request in conditional:
                unconditional.append(Request(self.unconditional_context, request.continuation))

        # `MCRequests.choice_scores` relies on this order: all conditional requests, then the
        # unconditional ones in the same order.
        requests = tuple(conditional + unconditional)

        return MCRequests(
            docs=tuple(docs),
            requests=requests,
            hash=self._hash(docs, requests),
            has_uncond=self.unconditional_context is not None,
        )

    def _head(self, shots: Sequence[MCDoc]) -> str:
        """Renders `description`, then each example's gold choice followed by a blank line."""
        head = self.description
        for shot in shots:
            gold = shot.choices[shot.gold]
            head += f"{gold.context}{gold.continuation}\n\n"

        return head

    def _shots_for(self, doc: MCDoc, shots: Sequence[MCDoc]) -> Sequence[MCDoc]:
        """Returns the examples `doc` is shown: all of them, or with `num_shots` the first that aren't `doc` (OLMES samples one extra and drops the scored doc, `fewshot_context` in `oe_eval/tasks/base_task.py`)."""
        if self.fewshot is None or self.fewshot.num_shots is None:
            return shots

        return [shot for shot in shots if shot.id != doc.id][: self.fewshot.num_shots]

    def _hash(self, docs: Sequence[MCDoc], requests: Sequence[Request]) -> str:
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
