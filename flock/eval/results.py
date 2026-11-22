"""The versioned results file of an eval run, and the per-doc predictions beside it."""

import dataclasses
import hashlib
import itertools
import json
import math
import os
from collections import Counter
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from types import NoneType
from typing import TYPE_CHECKING, Any

from flock.eval.dataset import Dataset
from flock.eval.provenance import Provenance

# Written rows are built from these; reading a results file needs none of them, so the Flock app
# reads one without torch.
if TYPE_CHECKING:
    import torch

    from flock.eval.metrics import ChoiceScores
    from flock.eval.requests import ContextScores, Scores
    from flock.eval.tasks.base import MCRequests
    from flock.eval.tasks.perplexity import TextDoc

# The layouts `WrittenResults.read` accepts. The Flock app's dashboard reads the same ones, and a
# test holds its list to this one.
READ_SCHEMAS = (8, 9, 10, 11, 12)


def scale(metric: str) -> str:
    """Returns the scale a metric's scores average on: `accuracy` for every `acc_*` metric, otherwise the metric's own name, such as `bpb`. A mean over two scales, an accuracy (higher is better) and a `bpb` (lower is better), has no meaning."""
    return "accuracy" if metric.startswith("acc_") else metric


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
class TaskResult:
    """
    One task's scores.

    Args:
        task (`str`):
            The task's registry name.
        task_hash (`str`):
            sha256 of exactly what was scored, as [Results files](results#the-task-hash) defines it. Equal hashes mean the same requests were scored.
        dataset (`Dataset`):
            The pinned dataset the docs came from. The hash covers it, and this field lets a reader see which one.
        primary_metric (`str`):
            The metric to read first.
        metrics (`dict[str, float]`):
            Each metric over the task's docs: a mean over docs for multiple choice, and a ratio of sums over all docs for a perplexity task.
        num_docs (`int`):
            How many docs were scored.
        limit (`int`, *optional*):
            The doc limit the run used, or `None` for the whole split.
        seconds (`float`):
            Wall time of scoring, excluding data and model loading.
        truncation (`Truncation`):
            How many requests and docs had their contexts cut from the left to fit the model.
    """

    task: str
    task_hash: str
    dataset: Dataset
    primary_metric: str
    metrics: dict[str, float]
    num_docs: int
    limit: int | None
    seconds: float
    truncation: Truncation

    @property
    def docs_per_second(self) -> float | None:
        """Scoring throughput, or `None` if the clock measured no time."""
        return self.num_docs / self.seconds if self.seconds > 0 else None


@dataclass(frozen=True, slots=True)
class SuiteResult:
    """
    One suite's score.

    Args:
        suite (`str`):
            The suite's registry name.
        suite_hash (`str`):
            sha256 of the suite's name, `metric`, `scored` and each member's name and hash (a task's `task_hash`, a suite's `suite_hash`), as [Results files](results#suites) defines it. Equal hashes mean the same members scored the same requests.
        members (`list[str]`):
            Its members' names, tasks and suites, each with an entry of its own in the results.
        metric (`str`, *optional*):
            The metric read from each member task, or `None` for each task's primary metric.
        score (`float`, *optional*):
            The unweighted mean of the members' scores, or `None` for a suite that only groups others.
    """

    suite: str
    suite_hash: str
    members: list[str]
    metric: str | None
    score: float | None


@dataclass(frozen=True, slots=True)
class Results:
    """
    Every task's scores from one run, and its provenance.

    `schema_version`, 12, is the version of the file's layout, bumped when a field changes. It isn't an argument, so a file can't claim a layout it doesn't have.

    Args:
        provenance (`Provenance`):
            What produced the run.
        tasks (`list[TaskResult]`):
            Each task's scores, in run order.
        suites (`list[SuiteResult]`, *optional*):
            Each suite's score, every suite after the suites it holds. Empty for a run of tasks only.
    """

    provenance: Provenance
    tasks: list[TaskResult]
    suites: list[SuiteResult] = field(default_factory=list)
    schema_version: int = field(default=12, init=False)

    def to_dict(self) -> dict[str, Any]:
        """Returns the results as plain JSON-ready data, with each task's `docs_per_second` added."""
        data = dataclasses.asdict(self)
        for task, entry in zip(self.tasks, data["tasks"], strict=True):
            entry["docs_per_second"] = task.docs_per_second

        return data

    def write(self, out: Path) -> Path:
        """
        Writes `results.json` into `out`, creating it if needed, and returns its path.

        Raises:
            `FileExistsError`: if `out` already holds a `results.json`, so two runs never write one file.
        """
        # JSON has no Infinity or NaN (RFC 8259 sec. 6), so a non-finite metric, such as a
        # diverged checkpoint's perplexity, is written as null and stays non-finite in memory.
        data = self.to_dict()
        for entry in data["tasks"]:
            metrics = entry["metrics"]
            for name, value in metrics.items():
                metrics[name] = value if math.isfinite(value) else None
        for entry in data["suites"]:
            if entry["score"] is not None and not math.isfinite(entry["score"]):
                entry["score"] = None

        # Serialized before the file is created, so a failure leaves no empty file to block a rerun.
        text = json.dumps(data, indent=2, allow_nan=False) + "\n"
        out.mkdir(parents=True, exist_ok=True)
        path = out / "results.json"
        with path.open("x") as f:
            f.write(text)
        return path


def write_predictions(
    path: Path,
    requests: MCRequests,
    scores: ChoiceScores,
    correct: torch.Tensor | None = None,
    pick: torch.Tensor | None = None,
    private: bool = False,
) -> None:
    """
    Writes a multiple-choice task's predictions to `path` as JSON lines, creating its directory if needed: first a header, `{"prompt": {"description": ..., "shots": [{"id": ..., "text": ...}, ...]}}`, the text every doc's context starts with, written once, then `prediction_rows`. With `private`, for a task on a private dataset, the file is readable by its owner only, since it holds the dataset's text.

    Raises:
        `FileExistsError`: if `path` exists, so two runs never write one file.
    """
    # Each example as another doc is shown it; a row's `shots` indexes this list.
    prompt = {
        "description": requests.description,
        "shots": [{"id": shot.id, "text": shot.example_text} for shot in requests.shots],
    }
    # Built before the file is created, so a failed check leaves no empty file to block a rerun.
    rows = [{"prompt": prompt}, *prediction_rows(requests, scores, correct, pick)]
    _write_rows(path, rows, mode=0o600 if private else 0o666)


def write_tokens(
    path: Path,
    requests: MCRequests,
    scores: Scores,
    contexts: ContextScores | None = None,
    private: bool = False,
) -> None:
    """
    Writes a multiple-choice task's per-token values to `path`, a Parquet file beside its predictions, creating its directory if needed. With `private`, the file is readable by its owner only: token ids of a private dataset are its text.

    Each row is one token sequence of one doc: its `id`, the `choice` it belongs to, its `part` and `token_ids`, and per token its `logprob`, `rank`, `entropy`, `top1_logprob` and `logprob_std` (see [`~eval.requests.TokenValues`]). Every doc has one `continuation` row per choice, from `scores.continuation_tokens`. From `contexts`, each of a doc's distinct contexts has, under its first choice, a `greedy` row with the tokens greedy decoding writes after it and no values, where the model decodes, and a `context` row with its tokens' values, where they were asked for. The rows of each part follow doc order.

    Args:
        path (`Path`):
            The file to write.
        requests (`MCRequests`):
            The scored docs and their requests.
        scores (`Scores`):
            The requests' scores, with `continuation_tokens`.
        contexts (`ContextScores`, *optional*):
            What [`~eval.model.ScoringModel.score_contexts`] read from the contexts of `requests.context_requests`, in that order.
        private (`bool`, *optional*, defaults to `False`):
            Whether the task's dataset is private.

    Raises:
        `ValueError`: if `scores` has no `continuation_tokens`.
        `FileExistsError`: if `path` exists, so two runs never write one file.
    """
    # Imported here: only writing or reading per-token values needs it.
    import pyarrow as pa
    import pyarrow.parquet as pq

    if scores.continuation_tokens is None:
        raise ValueError("the scores have no continuation_tokens to write")
    # Each conditional request's doc and choice; the unconditional ones aren't written.
    doc_of, choice_of = [], []
    for doc in requests.docs:
        for choice in range(len(doc.choices)):
            doc_of.append(doc.id)
            choice_of.append(choice)
    firsts = requests.context_requests
    value_names = ("logprob", "rank", "entropy", "top1_logprob", "logprob_std")
    schema = pa.schema(
        [
            ("id", pa.string()),
            ("choice", pa.int32()),
            ("part", pa.string()),
            ("token_ids", pa.list_(pa.int32())),
            ("logprob", pa.list_(pa.float32())),
            # Token ids and ranks fit 32 bits, as a vocabulary's size does.
            ("rank", pa.list_(pa.int32())),
            ("entropy", pa.list_(pa.float32())),
            ("top1_logprob", pa.list_(pa.float32())),
            ("logprob_std", pa.list_(pa.float32())),
        ]
    )

    # The conditional requests come first, so their tokens are the arrays' first ones; a
    # context's sequences follow `firsts`.
    parts = [("continuation", range(len(doc_of)), scores.continuation_tokens)]
    if contexts is not None and contexts.values is not None:
        parts.append(("context", firsts, contexts.values))
    tables = []
    for part, selected, values in parts:
        offsets = pa.array([0, *itertools.accumulate(values.lengths[: len(selected)])], pa.int32())
        size = offsets[-1].as_py()
        columns = {}
        # One host copy per array.
        for name in ("token_ids", *value_names):
            flat = getattr(values, name)[:size].cpu().numpy()
            columns[name] = pa.ListArray.from_arrays(
                offsets, pa.array(flat, schema.field(name).type.value_type)
            )
        rows = {"id": [doc_of[i] for i in selected], "choice": [choice_of[i] for i in selected]}
        tables.append(pa.table({**rows, "part": [part] * len(selected), **columns}, schema=schema))
    if contexts is not None and contexts.greedy is not None:
        written = contexts.greedy.cpu().numpy()
        offsets = pa.array(range(0, written.size + 1, written.shape[1]), pa.int32())
        greedy = {
            "id": [doc_of[i] for i in firsts],
            "choice": [choice_of[i] for i in firsts],
            "part": ["greedy"] * len(firsts),
            "token_ids": pa.ListArray.from_arrays(offsets, pa.array(written.ravel(), pa.int32())),
        }
        # Decoded tokens have no values of their own here.
        for name in value_names:
            greedy[name] = pa.nulls(len(firsts), schema.field(name).type)
        tables.append(pa.table(greedy, schema=schema))

    path.parent.mkdir(parents=True, exist_ok=True)
    # Created with its mode, as the predictions file is.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600 if private else 0o666)
    with open(descriptor, "wb") as f:
        pq.write_table(pa.concat_tables(tables), f, compression="zstd")


def write_perplexity_predictions(
    path: Path,
    docs: Sequence[TextDoc],
    scores: Scores,
    num_bytes: Sequence[int],
    window: int,
    source: str | None = None,
    domain: str | None = None,
) -> None:
    """
    Writes `perplexity_prediction_rows` to `path` as JSON lines, creating its directory if needed.

    Raises:
        `FileExistsError`: if `path` exists, so two runs never write one file.
    """
    _write_rows(path, perplexity_prediction_rows(docs, scores, num_bytes, window, source, domain))


def _write_rows(path: Path, rows: Sequence[dict[str, Any]], mode: int = 0o666) -> None:
    """Writes `rows` to `path` as JSON lines, refusing a file that exists, created with `mode` under the umask."""
    path.parent.mkdir(parents=True, exist_ok=True)

    # Created with its mode, so no other user can open it before a later chmod. UTF-8 as itself,
    # so the text of a Chinese task reads as written and takes fewer bytes.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with open(descriptor, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


def prediction_rows(
    requests: MCRequests,
    scores: ChoiceScores,
    correct: torch.Tensor | None = None,
    pick: torch.Tensor | None = None,
) -> list[dict[str, Any]]:
    """
    Returns one row per doc with its id, gold index, outcome, text, and each choice's scores and lengths, enough to recompute every metric and the truncation counts, dropping the padding past each doc's last choice.

    A row's text is the doc's own `question` (the context its choices share) and its `choices` (each continuation exactly as scored, leading space included). A doc whose choices have their own contexts, such as Winogrande's, has `question` null and `contexts`, one per choice. A doc shown other than every few-shot example in order has `shots`, the indices of those it was shown in the header [`write_predictions`] writes.

    A doc that [`~eval.tasks.MultipleChoiceTask.load`] loaded also has its row's `split`, `source_index` (its position in the split) and `doc_hash`, then `native_id`, `subject` and `meta` where the task's `row_fields` gives them. Every row has `prompt_hash`. Both hashes are lm-evaluation-harness's, so its samples of the same rows join on them.

    Args:
        requests (`MCRequests`):
            The scored docs, in order, and the examples each was shown.
        scores (`ChoiceScores`):
            Their choice scores.
        correct (`torch.Tensor`, *optional*):
            `[docs]`, each doc's value of the task's primary metric when that is an accuracy, written as the row's `correct`. Rows have no `correct` if not given.
        pick (`torch.Tensor`, *optional*):
            `[docs]`, the index of the choice the primary metric picks, when it ranks choices, written as the row's `pick`. Rows have no `pick` if not given.

    Raises:
        `ValueError`: if `scores`, `correct` or `pick` doesn't hold one row per doc.
    """
    docs = requests.docs
    # Rows are read by doc position, so extra score rows would be dropped without a word.
    if len(docs) != len(scores.gold):
        raise ValueError(f"{len(docs)} docs but scores for {len(scores.gold)}")
    if correct is not None and len(docs) != len(correct):
        raise ValueError(f"{len(docs)} docs but outcomes for {len(correct)}")
    if pick is not None and len(docs) != len(pick):
        raise ValueError(f"{len(docs)} docs but picks for {len(pick)}")

    # One host copy per field, not per doc.
    fields = {
        "logprob": scores.logprob,
        "num_tokens": scores.num_tokens,
        "num_chars": scores.num_chars,
        "num_bytes": scores.num_bytes,
        "is_greedy": scores.is_greedy,
        "tokens_cut": scores.tokens_cut,
        "logprob_uncond": scores.logprob_uncond,
        "tokens_cut_uncond": scores.tokens_cut_uncond,
    }
    values = {name: tensor.tolist() for name, tensor in fields.items() if tensor is not None}
    # The scorer's own per-doc values, which results.json averages, so a row's outcome and the
    # task's accuracy can't disagree.
    outcomes = correct.bool().tolist() if correct is not None else None
    picks = pick.tolist() if pick is not None else None
    every_shot = tuple(range(len(requests.shots)))

    rows = []
    first_request = 0
    for i, doc in enumerate(docs):
        row: dict[str, Any] = {"id": doc.id, "gold": doc.gold}
        if outcomes is not None:
            row["correct"] = outcomes[i]
        if picks is not None:
            row["pick"] = picks[i]
        contexts = [choice.context for choice in doc.choices]
        row["question"] = contexts[0] if len(set(contexts)) == 1 else None
        if row["question"] is None:
            row["contexts"] = contexts
        row["choices"] = [choice.continuation for choice in doc.choices]
        if requests.shown[i] != every_shot:
            row["shots"] = list(requests.shown[i])
        for name, per_doc in values.items():
            row[name] = per_doc[i][: len(doc.choices)]

        if doc.source is not None:
            row["split"] = doc.source.split
            row["source_index"] = doc.source.index
            if doc.source.native_id is not None:
                row["native_id"] = doc.source.native_id
            if doc.source.subject is not None:
                row["subject"] = doc.source.subject
            if doc.source.meta:
                row["meta"] = doc.source.meta
            row["doc_hash"] = doc.source.doc_hash
        # lm-evaluation-harness's `prompt_hash`: the sha256 of its first request's context, few-shot
        # examples included (`lm_eval/evaluator.py`, v0.4.3). A doc's requests are consecutive.
        context = requests.requests[first_request].context
        row["prompt_hash"] = hashlib.sha256(context.encode()).hexdigest()
        first_request += len(doc.choices)
        rows.append(row)

    return rows


def perplexity_prediction_rows(
    docs: Sequence[TextDoc],
    scores: Scores,
    num_bytes: Sequence[int],
    window: int,
    source: str | None = None,
    domain: str | None = None,
) -> list[dict[str, Any]]:
    """
    Returns one row per doc with its id, summed log-probability, token and UTF-8 byte counts, number of windows and whether every token is the greedy one, so any domain's `bits_per_byte`, `loss_per_token` and `perplexity` can be recomputed. Each row also names the doc's `source` (the dataset subset, a Paloma source) and `domain` (its `subdomain`), each `null` where there is none, so rows of many tasks pool and split again by either.

    Raises:
        `ValueError`: if `scores` or `num_bytes` doesn't hold one entry per doc.
    """
    # One host copy per field, not per doc. `strict`, so a count that doesn't match the docs
    # raises rather than dropping rows.
    per_doc = zip(
        docs,
        scores.logprob.tolist(),
        scores.num_tokens.tolist(),
        num_bytes,
        scores.is_greedy.tolist(),
        strict=True,
    )

    rows = []
    for doc, logprob, num_tokens, doc_bytes, is_greedy in per_doc:
        rows.append(
            {
                "id": doc.id,
                "logprob": logprob,
                "num_tokens": num_tokens,
                "num_bytes": doc_bytes,
                # The last window holds what is left.
                "windows": math.ceil(num_tokens / window),
                "is_greedy": is_greedy,
                "source": source,
                "domain": domain,
            }
        )

    return rows


@dataclass(frozen=True, slots=True)
class WrittenResults:
    """
    A run's `results.json` as read back by [`read_results`], with access to the per-doc predictions beside it.

    Args:
        source (`str`):
            Where it was read: a folder, or a tracker run as `run:entity/project/id`.
        schema_version (`int`):
            The file's layout, 8 to 12. Only rows from schema 9 on hold `correct`, and only schema 9 on records `dataset.private`: an older file's datasets read as not private. Schema 10 adds the provenance's `model_org`, `model_project` and `model_path`, which read as `None` before it. Only rows from schema 11 on hold the doc's text and `pick`. Schema 12 adds each row's source fields and hashes, each perplexity row's `source` and `domain`, and the provenance's `tokenizer` and `tokenizer_hash`, which read as `None` before it. A reader ignores row fields and `meta` keys it doesn't know, so a later schema can add them.
        provenance (`Provenance`):
            What produced the run.
        tasks (`list[TaskResult]`):
            Each task's scores, in run order. A metric written as null (non-finite) reads as NaN.
        suites (`list[SuiteResult]`):
            Each suite's score.
        read_file (`Callable[[str], str | None]`):
            Reads one of the run's files by its name in the folder or on the run, such as `predictions/arc_easy.jsonl`, or returns `None` if there is no such file.
        read_bytes (`Callable[[str], bytes | None]`, *optional*):
            Reads a file as `read_file` does, as bytes, for the per-token values. Without it, [`tokens`] reads none.
    """

    source: str
    schema_version: int
    provenance: Provenance
    tasks: list[TaskResult]
    suites: list[SuiteResult]
    read_file: Callable[[str], str | None]
    read_bytes: Callable[[str], bytes | None] | None = None

    def tokens(self, task: str, ids: Collection[str] | None = None) -> list[dict[str, Any]] | None:
        """
        Returns the per-token rows of `task`'s docs, or of those in `ids`, as `write_tokens` wrote them, or `None` if the run has none for the task: it was written before schema 12, without them, or this reader has no `read_bytes`. Read on demand, one task at a time, as a page shows one doc. On a tracker run, a task on a private dataset is read from under `private/`.

        Raises:
            `ValueError`: if `task` isn't a single file name.
        """
        # Imported here: a results file reads without it.
        import pyarrow as pa
        import pyarrow.parquet as pq

        name = tokens_name(task)
        on_run = self.source.startswith("run:")
        if on_run and any(t.task == task and t.dataset.private for t in self.tasks):
            name = f"private/{name}"
        # Only schema 12 on writes them, and only for a task the run scored: an older run's file
        # of that name, or one beside the run for another task, isn't its own.
        scored = any(t.task == task for t in self.tasks)
        if self.schema_version < 12 or self.read_bytes is None or not scored:
            return None
        data = self.read_bytes(name)
        if data is None:
            return None
        # An empty selection keeps no row: pyarrow can't type an empty filter set.
        if ids is not None and not ids:
            return []
        chosen = [("id", "in", sorted(ids))] if ids is not None else None
        return pq.read_table(pa.BufferReader(data), filters=chosen).to_pylist()

    def predictions(self, task: str) -> list[dict[str, Any]] | None:
        """
        Returns `task`'s prediction rows, one per doc, or `None` if the run has no predictions file for it. The header line a file has from schema 11 on is left out. On a tracker run, a task on a private dataset is read from `private/predictions/<task>.jsonl`.

        Raises:
            `ValueError`: if `task` isn't a single file name, so a name read from a file can't reach outside `predictions/`.
        """
        name = predictions_name(task)
        # A tracker run holds a private dataset's rows under `private/`, which only the
        # organization's members read; a folder keeps every task's rows in `predictions/`.
        on_run = self.source.startswith("run:")
        if on_run and any(t.task == task and t.dataset.private for t in self.tasks):
            name = f"private/{name}"
        text = self.read_file(name)
        if text is None:
            return None
        # Split at newlines only: `splitlines` also splits at U+2028 and the like, which text
        # written unescaped may hold.
        lines = text.removesuffix("\n").split("\n") if text else []
        rows = [json.loads(line) for line in lines]
        # From schema 11 a multiple-choice file opens with the prompt every doc's context starts
        # with; a perplexity file has none. A row always has an id, a header never.
        if self.schema_version >= 11 and rows and isinstance(rows[0], dict) and "prompt" in rows[0]:
            rows = rows[1:]
        return rows

    @classmethod
    def read(
        cls,
        source: str,
        read_file: Callable[[str], str | None],
        read_bytes: Callable[[str], bytes | None] | None = None,
    ) -> WrittenResults:
        """
        Reads a written run's `results.json` with `read_file`, which reads one of the run's files by name, keeping `read_bytes` to read its per-token values. [`read_results`] calls it for a folder or a tracker run; the Flock app calls it with its own store of run files.

        Raises:
            `FileNotFoundError`: if `read_file` finds no `results.json`.
            `ValueError`: if `results.json` isn't a results file of schema 8 to 12.
        """
        text = read_file("results.json")
        if text is None:
            raise FileNotFoundError(f"{source} has no results.json")
        try:
            data = json.loads(text)
            # Schema 8 holds every field these types have but `dataset.private` and schema 10's
            # provenance fields, which default; older files lack other provenance fields.
            if data.get("schema_version") not in READ_SCHEMAS:
                raise ValueError(f"schema {data.get('schema_version')}; only 8 to 12 are read")
            # An object or a string would iterate as nothing, and a diff would read every task as
            # missing from this run.
            if not isinstance(data["tasks"], list) or not isinstance(data["suites"], list):
                raise TypeError("tasks and suites must be arrays")
            tasks = [_read_task(entry, data["schema_version"]) for entry in data["tasks"]]
            # The fields that name the model and the instrument a diff compares. A null on both
            # sides would pass as equal.
            provenance = data["provenance"]
            _check_types(
                provenance,
                {
                    "model": (str,),
                    "dtype": (str,),
                    "attention": (str,),
                    "batch_tokens": (int,),
                    "sdpa_backend": (str, NoneType),
                },
                "provenance.",
            )
            # Tasks are looked up by name, so a repeated one would drop or double a task.
            # One pass, since the Flock app reads files anyone in a project uploads.
            repeated = [n for n, count in Counter(task.task for task in tasks).items() if count > 1]
            if repeated:
                raise ValueError(f"a task name repeats: {sorted(repeated)}")
            written = cls(
                source=source,
                schema_version=data["schema_version"],
                provenance=Provenance(**provenance),
                tasks=tasks,
                suites=[SuiteResult(**entry) for entry in data["suites"]],
                read_file=read_file,
                read_bytes=read_bytes,
            )
        # Not JSON, a missing key, a field these types don't have, or a value of the wrong type.
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"{source} is not a results file: {error!r}") from None
        except ValueError as error:
            raise ValueError(f"{source}: {error}") from None

        return written


def read_results(source: str) -> WrittenResults:
    """
    Reads a written run: a folder holding `results.json` and `predictions/`, or `run:entity/project/id`, a tracker run whose files [`~eval.tracking.log_to_tracker`] uploaded, read as the signed-in Flock user. Predictions are read only when asked for.

    Raises:
        `FileNotFoundError`: if the folder or run has no `results.json`.
        `ValueError`: if `results.json` isn't a results file of schema 8 to 12, `run:` isn't followed by `entity/project/id`, or Flock refuses the read.
        `click.ClickException`: if Flock can't be reached.
    """
    if source.startswith("run:"):
        # Imported here so reading a folder doesn't import wandb.
        from flock.cli import credentials
        from flock.eval.tracking import read_run_bytes, read_run_file

        # Resolved once, so every file of the run comes from the server its results came from.
        credential = credentials.resolve()
        run = source.removeprefix("run:")
        read_file = partial(read_run_file, run, credential=credential)
        read_bytes = partial(read_run_bytes, run, credential=credential)
    else:
        read_bytes = partial(_read_folder_bytes, Path(source))

        def read_file(name: str) -> str | None:
            data = read_bytes(name)
            return data.decode() if data is not None else None

    return WrittenResults.read(source, read_file, read_bytes)


def _read_task(entry: dict[str, Any], schema_version: int) -> TaskResult:
    """One task of a written results file, with the fields a diff looks tasks up and reads metrics by checked."""
    entry = {key: value for key, value in entry.items() if key != "docs_per_second"}
    _check_types(
        entry,
        {"task": (str,), "task_hash": (str,), "primary_metric": (str,), "num_docs": (int,)},
        "a task's ",
    )
    if entry["num_docs"] < 0:
        raise ValueError(f"{entry['task']}: num_docs is {entry['num_docs']}")
    # Schema 9 always writes the mark, so a missing one is damage, not "public".
    if schema_version >= 9:
        _check_types(entry["dataset"], {"private": (bool,)}, f"{entry['task']}: dataset.")

    # `Results.write` writes a non-finite metric as null.
    metrics = {}
    for name, value in entry["metrics"].items():
        # A bool is an int to Python but no score.
        if value is not None and type(value) not in (int, float):
            raise ValueError(f"{entry['task']}: metric {name} is {value!r}, not a number")
        metrics[name] = math.nan if value is None else value
    entry["metrics"] = metrics
    entry["dataset"] = Dataset(**entry["dataset"])
    entry["truncation"] = Truncation(**entry["truncation"])

    return TaskResult(**entry)


def _check_types(record: dict[str, Any], types: dict[str, tuple[type, ...]], where: str) -> None:
    """Refuses a field of `record` missing or not of exactly one of its `types`, named `where` plus its key in the error. Dataclasses check no types, and a bool is an int to Python, so `true` would pass as one doc."""
    for key, allowed in types.items():
        value = record.get(key)
        if type(value) not in allowed:
            names = " or ".join(kind.__name__ for kind in allowed)
            raise TypeError(f"{where}{key} is {value!r}, not {names}")


def stays_inside(name: str) -> bool:
    """Whether `name`, split at `/`, has no empty, `.` or `..` part, so joined to a folder or a run's URL it names something inside it (a client normalizes `..` in a URL). Names read from a file pass this before any read."""
    parts = name.split("/")
    # The platform's own path must see the same parts: on Windows a backslash and a drive split too.
    return all(parts) and not {".", ".."} & set(parts) and Path(name).parts == tuple(parts)


def predictions_name(task: str) -> str:
    """
    Returns the name of `task`'s predictions file in a run's folder and on its tracker run, `predictions/<task>.jsonl`.

    Raises:
        `ValueError`: if `task` isn't a single file name, so a task name read from a file can't name a file outside `predictions/`.
    """
    if "/" in task or not stays_inside(task):
        raise ValueError(f"task name {task!r} can't name a predictions file")
    return f"predictions/{task}.jsonl"


def tokens_name(task: str) -> str:
    """
    Returns the name of `task`'s per-token values in a run's folder and on its tracker run, `predictions/<task>.tokens.parquet`, beside its predictions.

    Raises:
        `ValueError`: as [`predictions_name`].
    """
    return predictions_name(task).removesuffix(".jsonl") + ".tokens.parquet"


def _read_folder_bytes(folder: Path, name: str) -> bytes | None:
    """Reads `folder/name`, or returns `None` if it doesn't exist. A name that leaves the folder, and a link on the way, are refused, so a run's files come from its own folder."""
    if not stays_inside(name):
        raise ValueError(f"{name!r} isn't a file name inside {folder}")
    path = folder
    for part in name.split("/"):
        path = path / part
        if path.is_symlink():
            raise ValueError(f"{path} is a link; a run's files must be in its folder")
    return path.read_bytes() if path.is_file() else None
