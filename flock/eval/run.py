"""Run tasks against a model: the one entry point the CLI and job runners call."""

import hashlib
import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

import torch

from flock.eval.metrics import bits_per_byte, multiple_choice
from flock.eval.model import DEFAULT_BATCH_TOKENS, Encoded, ScoringModel
from flock.eval.provenance import Provenance
from flock.eval.results import (
    Results,
    SuiteResult,
    TaskResult,
    write_perplexity_predictions,
    write_predictions,
)
from flock.eval.tasks import (
    SUITES,
    TASKS,
    MCRequests,
    MultipleChoiceTask,
    PerplexityTask,
    Task,
    TextDoc,
    Truncation,
)
from flock.eval.tasks.suite import by_name
from flock.eval.tokenizer import SentencePieceTokenizer, Tokenizer

# Why `evaluate` and `score_task` refuse an output that is already there, and what to do.
_ONE_RUN = (
    "one directory holds one run, and a run that crashed leaves its predictions behind. "
    "Pass a new out directory or delete this one."
)

# The weights' dtypes `run` loads. Log-softmax always runs in FP32.
Dtype = Literal["bfloat16", "float32"]


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """
    One eval run. Plain data, so it pickles to a remote worker.

    Args:
        model (`str`):
            The model as `repo@revision`, or a version in the Flock registry as `flock:org/name`, `flock:org/name@{number}`, `flock:org/name@{tag}` or `flock:org/name@primary`.
        tasks (`tuple[str, ...]`, *optional*):
            Registry names of the tasks to run, in order.
        out (`Path`, *optional*):
            Where to write `results.json` and `predictions/<task>.jsonl`. Nothing is written if not set.
        limit (`int`, *optional*):
            Score only each task's first `limit` docs, for quick checks.
        batch_tokens (`int`, *optional*):
            Passed to [`~eval.model.ScoringModel`].
        dtype (`Dtype`, *optional*, defaults to `"bfloat16"`):
            The weights' dtype, `"bfloat16"` or `"float32"`. Log-softmax always runs in FP32.
        device (`str`, *optional*):
            Where to run. Defaults to CUDA when available.
        wandb_project (`str`, *optional*):
            Also log the summary to this tracker project through the `wandb` SDK.
        command (`str`, *optional*):
            The command line recorded in the provenance, or `None` if not given. The CLI passes its own command line.
        suites (`tuple[str, ...]`, *optional*):
            Registry names of the suites to run. Their tasks run after `tasks`, each task once, and each suite and the suites it holds are scored.
        add_bos (`bool`, *optional*, defaults to `False`):
            Passed to [`~eval.model.ScoringModel`]. Set it for Gemma models, as OLMES does.

    Raises:
        `ValueError`: if no task or suite is given, a task or suite repeats, or `limit` or `batch_tokens` is less than 1, or `dtype` is not `"bfloat16"` or `"float32"`, before any model loads.
    """

    model: str
    tasks: tuple[str, ...] = ()
    out: Path | None = None
    limit: int | None = None
    batch_tokens: int = DEFAULT_BATCH_TOKENS
    dtype: Dtype = "bfloat16"
    device: str | None = None
    wandb_project: str | None = None
    command: str | None = None
    suites: tuple[str, ...] = ()
    add_bos: bool = False

    def __post_init__(self) -> None:
        if not self.tasks and not self.suites:
            raise ValueError("give at least one task or suite")
        if len(set(self.tasks)) != len(self.tasks):
            raise ValueError(f"tasks repeat: {self.tasks}")
        if len(set(self.suites)) != len(self.suites):
            raise ValueError(f"suites repeat: {self.suites}")
        # `MultipleChoiceTask.load` and `ScoringModel` check these too, but a config is plain data
        # that may be built where no task or model loads, such as before it goes to a worker.
        if self.limit is not None and self.limit < 1:
            raise ValueError(f"limit must be at least 1, got {self.limit}")
        if self.batch_tokens < 1:
            raise ValueError(f"batch_tokens must be at least 1, got {self.batch_tokens}")
        if self.dtype not in get_args(Dtype):
            raise ValueError(f"dtype must be bfloat16 or float32, got {self.dtype}")


def evaluate(config: EvalConfig) -> Results:
    """
    Loads the model, collects the provenance, scores every task and suite and writes the results, then logs them to the tracker if `wandb_project` is set.

    Raises:
        `KeyError`: if a task or suite name isn't in its registry.
        `ValueError`: if two different tasks, or two different suites, to run share a name, a task and a suite share a name, a task is named `suites`, a perplexity task's window is above the model's `max_length`, or a request's continuation changes its context's tokens on a [`~eval.tokenizer.SentencePieceTokenizer`].
        `FileExistsError`: if `config.out` exists and isn't an empty directory.
    """
    _check_names("tasks", config.tasks, TASKS)
    _check_names("suites", config.suites, SUITES)
    _check_out(config.out)

    # The named tasks run first, then each suite's tasks not yet listed, each task once. Every
    # suite reached is scored, each after its members. Both plans are checked before the model
    # loads, so a name clash costs no GPU time.
    suites = [SUITES[name] for name in config.suites]
    tasks = [TASKS[name] for name in config.tasks]
    for suite in suites:
        tasks.extend(suite.tasks)
    tasks = list(by_name(tasks).values())
    held = []
    for suite in suites:
        held.extend(suite.suites)
    held = list(by_name(held).values())
    # The tracker logs suite scores as `suites/<suite>` beside `<task>/<metric>`.
    if any(task.name == "suites" for task in tasks):
        raise ValueError("a task can't be named suites: suite scores are logged under that name")
    # Results and suite hashes look members up by name, so a suite can't share one with a task.
    clashes = sorted({task.name for task in tasks} & {suite.name for suite in held})
    if clashes:
        raise ValueError(f"a task and a suite share the names {clashes}")

    model = ScoringModel.from_pretrained(
        config.model,
        device=config.device,
        dtype=getattr(torch, config.dtype),
        batch_tokens=config.batch_tokens,
        add_bos=config.add_bos,
    )
    # Checked before scoring, so a model too short for a perplexity task fails before any is scored
    # rather than after every task before it.
    for task in tasks:
        if isinstance(task, PerplexityTask) and task.window > model.max_length:
            raise ValueError(
                f"{task.name}: window {task.window} is above {model.name}'s max_length "
                f"{model.max_length}"
            )
        # A resegmented context fails the same way, not after the tasks before it. Tokenizing twice
        # costs less than holding every task's tokens at once, and only this tokenizer refuses.
        if isinstance(model.tokenizer, SentencePieceTokenizer) and not isinstance(
            task, PerplexityTask
        ):
            docs, shots = task.load(config.limit)
            try:
                model.encode_requests(task.requests(docs, shots).requests)
            except ValueError as error:
                raise ValueError(f"{task.name}: {error}") from error
    # Collected before scoring, so the commit's dirty flag describes the tree as scoring starts
    # and `created_at` is when scoring started, as lm-evaluation-harness records `start_date`.
    provenance = Provenance.collect(
        model=model.name,
        model_digest=model.digest,
        command=config.command,
        device=model.device,
        dtype=model.dtype,
        batch_tokens=model.batch_tokens,
        max_length=model.max_length,
        add_bos=model.add_bos,
        attention=model.attention,
        sdpa_backend=model.sdpa_backend,
    )
    task_results = [score_task(model, task, config.limit, config.out) for task in tasks]

    # Suite scores are means of the per-task means already on the host. A suite's hash covers
    # each member's hash, and `held` puts every suite after its members, so equal hashes mean
    # the same members scored the same requests all the way down.
    metrics = {result.task: result.metrics for result in task_results}
    hashes = {result.task: result.task_hash for result in task_results}
    suite_results = []
    for suite in held:
        score = suite.score(metrics) if suite.scored else None
        members = [member.name for member in suite.members]
        definition = {
            "suite": suite.name,
            "metric": suite.metric,
            "scored": suite.scored,
            "members": [[name, hashes[name]] for name in members],
        }
        hashes[suite.name] = hashlib.sha256(
            json.dumps(definition, sort_keys=True).encode()
        ).hexdigest()
        suite_results.append(
            SuiteResult(suite.name, hashes[suite.name], members, suite.metric, score)
        )
    results = Results(provenance, task_results, suite_results)

    if config.out is not None:
        results.write(config.out)
    if config.wandb_project is not None:
        # Imported here so importing this module, as training loops do for `prepare_task` and
        # `score_prepared`, doesn't import wandb.
        from flock.eval.tracking import log_to_tracker

        log_to_tracker(results.to_dict(), config.wandb_project)

    return results


def _check_names(kind: str, names: tuple[str, ...], registry: Mapping[str, object]) -> None:
    """Raises `KeyError` if a name isn't in its registry, before any model loads."""
    # Checked here, not in `EvalConfig`: a config is plain data that may be built where the
    # registry differs from the worker's.
    unknown = [name for name in names if name not in registry]
    if unknown:
        raise KeyError(f"unknown {kind} {unknown}; known: {sorted(registry)}")


def _check_out(out: Path | None) -> None:
    """Raises `FileExistsError` if `out` exists and isn't an empty directory."""
    # Old predictions next to a new results.json would mix two runs.
    if out is not None and out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise FileExistsError(f"{out} is not an empty directory: {_ONE_RUN}")


@dataclass(frozen=True, slots=True)
class PreparedMultipleChoice:
    """
    A multiple-choice task's requests and their token ids, built once by [`prepare_task`].

    Args:
        task (`MultipleChoiceTask`):
            The task.
        limit (`int` or `None`):
            The doc limit it was prepared with, `None` for every doc.
        tokenizer (`PreTrainedTokenizerBase` or `SentencePieceTokenizer`):
            The tokenizer `encoded` comes from.
        add_bos (`bool`):
            Whether `encoded`'s contexts start with BOS, as the model's `add_bos` said.
        requests (`MCRequests`):
            The docs, their requests and the task hash.
        encoded (`tuple[Encoded, ...]`):
            Each request's token ids, in request order.
    """

    task: MultipleChoiceTask
    limit: int | None
    tokenizer: Tokenizer
    add_bos: bool
    requests: MCRequests
    encoded: tuple[Encoded, ...]


@dataclass(frozen=True, slots=True)
class PreparedPerplexity:
    """
    A perplexity task's docs and their token ids, built once by [`prepare_task`].

    Args:
        task (`PerplexityTask`):
            The task.
        limit (`int` or `None`):
            The doc limit it was prepared with, `None` for every doc.
        tokenizer (`PreTrainedTokenizerBase` or `SentencePieceTokenizer`):
            The tokenizer `token_ids` comes from.
        docs (`tuple[TextDoc, ...]`):
            The scored docs, in order.
        task_hash (`str`):
            The task hash over `docs`.
        token_ids (`tuple[list[int], ...]`):
            Each doc's token ids, in doc order.
    """

    task: PerplexityTask
    limit: int | None
    tokenizer: Tokenizer
    docs: tuple[TextDoc, ...]
    task_hash: str
    token_ids: tuple[list[int], ...]


# What `prepare_task` returns for each kind of task.
type PreparedTask = PreparedMultipleChoice | PreparedPerplexity


def score_task(
    model: ScoringModel, task: Task, limit: int | None = None, out: Path | None = None
) -> TaskResult:
    """
    Scores one task with an already loaded model. A training loop that scores the same task at many steps calls [`prepare_task`] once and [`score_prepared`] at each step instead, which gives the same result.

    Args:
        model (`ScoringModel`):
            The model to score.
        task (`MultipleChoiceTask` or `PerplexityTask`):
            The task.
        limit (`int`, *optional*):
            Score only the first `limit` docs.
        out (`Path`, *optional*):
            Write per-doc scores to `out/predictions/<task>.jsonl`. A training loop gives each step its own `out`. No `results.json` is written: that is [`evaluate`]'s job.

    Raises:
        `ValueError`: if `limit` is less than 1, or a request's continuation changes its context's tokens on a [`~eval.tokenizer.SentencePieceTokenizer`].
        `FileExistsError`: if the predictions file already exists, before anything is scored.
    """
    return score_prepared(model, prepare_task(model, task, limit), out)


def prepare_task(model: ScoringModel, task: Task, limit: int | None = None) -> PreparedTask:
    """
    Loads a task's docs and tokenizes everything it scores with `model`'s tokenizer, for [`score_prepared`] to score at every evaluation step without loading or tokenizing again.

    Args:
        model (`ScoringModel`):
            The model whose tokenizer and `add_bos` to use. Any model with the same tokenizer object and `add_bos` can score the result.
        task (`MultipleChoiceTask` or `PerplexityTask`):
            The task.
        limit (`int`, *optional*):
            Prepare only the first `limit` docs.

    Raises:
        `ValueError`: if `limit` is less than 1, or a request's continuation changes its context's tokens on a [`~eval.tokenizer.SentencePieceTokenizer`].
    """
    if isinstance(task, PerplexityTask):
        docs = task.load(limit)
        return PreparedPerplexity(
            task=task,
            limit=limit,
            tokenizer=model.tokenizer,
            docs=tuple(docs),
            task_hash=task.task_hash(docs),
            token_ids=tuple(model.tokenize([doc.text for doc in docs])),
        )

    docs, shots = task.load(limit)
    requests = task.requests(docs, shots)
    return PreparedMultipleChoice(
        task=task,
        limit=limit,
        tokenizer=model.tokenizer,
        add_bos=model.add_bos,
        requests=requests,
        encoded=tuple(model.encode_requests(requests.requests)),
    )


def score_prepared(
    model: ScoringModel, prepared: PreparedTask, out: Path | None = None
) -> TaskResult:
    """
    Scores a task from [`prepare_task`] with an already loaded model, as training loops do between steps. The result is the one [`score_task`] gives.

    Args:
        model (`ScoringModel`):
            The model to score. Its tokenizer must be the object the task was prepared with, and its `add_bos` the same.
        prepared (`PreparedMultipleChoice` or `PreparedPerplexity`):
            The prepared task. It can be scored any number of times.
        out (`Path`, *optional*):
            Write per-doc scores to `out/predictions/<task>.jsonl`. A training loop gives each step its own `out`.

    Raises:
        `ValueError`: if `model`'s tokenizer isn't the one the task was prepared with, or a multiple-choice task was prepared with another `add_bos`.
        `FileExistsError`: if the predictions file already exists, before anything is scored.
    """
    task = prepared.task
    # The token ids are this tokenizer's: another one would score other tokens without an error.
    # Identity, not equality: comparing two vocabularies at every step would cost more than the
    # tokenizing this skips.
    if model.tokenizer is not prepared.tokenizer:
        raise ValueError(
            f"{task.name} was prepared with another tokenizer than {model.name}'s; "
            "prepare it again with this model"
        )
    # A perplexity task is not checked: `add_bos` never changes its windows (024.D2).
    if isinstance(prepared, PreparedMultipleChoice) and model.add_bos != prepared.add_bos:
        raise ValueError(
            f"{task.name} was prepared with add_bos={prepared.add_bos}, but {model.name} has "
            f"add_bos={model.add_bos}; prepare it again with this model"
        )
    predictions = out / "predictions" / f"{task.name}.jsonl" if out is not None else None
    if predictions is not None and predictions.exists():
        raise FileExistsError(f"{predictions} exists: {_ONE_RUN}")
    if isinstance(prepared, PreparedPerplexity):
        return _score_perplexity(model, prepared, predictions)

    requests = prepared.requests
    start = time.perf_counter()
    request_scores = model.loglikelihood(requests.requests, prepared.encoded)
    scores = requests.choice_scores(request_scores)
    reported = task.reported_metrics
    per_doc = {name: values for name, values in multiple_choice(scores).items() if name in reported}
    # One host copy for every metric's mean. It also waits for the GPU, so `seconds` times the
    # scoring.
    means = torch.stack(list(per_doc.values())).mean(dim=1).tolist()
    seconds = time.perf_counter() - start
    # `means` follows the order of `per_doc`'s keys.
    metrics = dict(zip(per_doc, means, strict=True))
    truncation = requests.truncation(request_scores)

    if predictions is not None:
        write_predictions(predictions, requests.docs, scores)

    return TaskResult(
        task=task.name,
        task_hash=requests.hash,
        dataset=task.dataset,
        primary_metric=task.primary_metric,
        metrics=metrics,
        num_docs=len(requests.docs),
        limit=prepared.limit,
        seconds=seconds,
        truncation=truncation,
    )


def _score_perplexity(
    model: ScoringModel, prepared: PreparedPerplexity, predictions: Path | None
) -> TaskResult:
    """[`score_prepared`] for a perplexity task, writing its rows to `predictions` if given."""
    task, docs = prepared.task, prepared.docs
    texts = [doc.text for doc in docs]
    # Each whole text's UTF-8 bytes, as OLMES's `num_bytes` (`loglikelihood_rolling_verbose` in
    # `oe_eval/models/eleuther_huggingface.py`).
    num_bytes = [len(text.encode()) for text in texts]

    start = time.perf_counter()
    scores = model.rolling_loglikelihood(texts, task.window, prepared.token_ids)
    # Summed over all of the task's docs before dividing, as Paloma defines bits per byte
    # (App. B) and OLMES computes it per domain (`PerplexityMetric`), so a doc counts by its
    # bytes, and the loss is averaged over tokens as a trainer averages it over a batch. Every
    # token is predicted once, so a doc's token count is its number of predictions. One host
    # copy for both is the task's one wait for the GPU, so `seconds` times the scoring.
    logprob = scores.logprob.sum()
    total_bytes = torch.tensor(sum(num_bytes))
    loss = -logprob / scores.num_tokens.sum()
    bpb, loss_per_token = torch.stack([bits_per_byte(logprob, total_bytes), loss]).tolist()
    seconds = time.perf_counter() - start
    # `math.exp` raises past about 709.78 nats, which a diverged checkpoint can reach. Its
    # perplexity is infinite, so the run keeps its other results; OLMES writes `1e30`.
    try:
        perplexity = math.exp(loss_per_token)
    except OverflowError:
        perplexity = math.inf

    if predictions is not None:
        write_perplexity_predictions(predictions, docs, scores, num_bytes, task.window)

    return TaskResult(
        task=task.name,
        task_hash=prepared.task_hash,
        dataset=task.dataset,
        primary_metric=task.primary_metric,
        metrics={
            "bits_per_byte": bpb,
            "loss_per_token": loss_per_token,
            "perplexity": perplexity,
        },
        num_docs=len(docs),
        limit=prepared.limit,
        seconds=seconds,
        # Every token is predicted once, in windows that fit the model, so nothing is cut.
        truncation=Truncation(requests=0, docs=0, max_tokens_cut=0),
    )
