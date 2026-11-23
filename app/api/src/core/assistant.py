"""The run groups assistant: plain-language requests turned into filters, picks and best-by
settings through OpenAI's Decisions API (D191).

The Decisions API answers typed questions about one input (a predicate's probability, or a
choice among 2 to 255 listed values) and never writes text. So Flock lists the candidates and
the model picks among them, in two requests since the second depends on the first:

1. **What to do:** a choice of action, and a predicate per candidate column asking whether
   the request refers to it. The candidates are the 50 columns with most runs, plus any column
   whose name shares a word with the request, up to 380 questions in all.
2. **Values:** for each column picked (probability >= 0.5), a choice of operator and a choice
   of value among its observed values (quantiles past 255); for ``add_runs`` and ``drop_runs``,
   a predicate per run, among run names narrowed to 255 by substring match; for ``set_best``,
   the column and the direction.

The top answer is applied and the others are kept as alternatives, best first. When nothing
reaches 0.5 the action is ``none``. Only the request, the current filters, column names, their
observed values and run names are sent: never histories, files or logs.

A group's primary columns, shown first in its table, take one more request: a predicate per
varying config key and summary metric of its runs (``predict_primary``).
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import orjson

from app.api.src.core.errors import BadGateway
from app.api.src.core.group_analysis import Kind, RunRow

type Value = int | float | str | bool | list | None
type Op = Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "exists", "missing"]
type ActionType = Literal["filter", "add_runs", "drop_runs", "set_best", "none"]

MODEL = "gpt-6-luna"
TOP_COLUMNS = 50  # columns with most runs always asked about (D191)
MAX_QUESTIONS = 380  # the first request's questions, of the API's 400
VALUE_ROOM = 60  # of those, kept for questions about small categorical columns' values
NAMING_COLUMNS = frozenset({"name", "display_name"})
MAX_REQUEST = 400  # questions the API takes in one request
MAX_CHOICES = 255  # values a choice question takes
PICKED = 0.5  # the probability at which a column, run or action is taken (D191)
ALTERNATIVES = 5  # other readings kept per condition
MIN_ALTERNATIVE = 0.05  # readings less likely than this aren't offered

ACTIONS: dict[ActionType, str] = {
    "filter": "keep only the runs that meet conditions on their columns, such as 'failed runs', "
    "'lr below 1e-3' or 'batch size 64'",
    "add_runs": "add specific runs, named one by one, to the group by hand",
    "drop_runs": "remove specific runs, named one by one, from the group by hand",
    "set_best": "choose which metric ranks the runs and whether lower or higher is better, such "
    "as 'rank by lowest loss'",
    "none": "the request asks for none of these",
}
OPS: dict[Op, str] = {
    "eq": "is equal to",
    "ne": "is not equal to",
    "gt": "is greater than",
    "gte": "is at least",
    "lt": "is less than",
    "lte": "is at most",
    "contains": "contains the text",
    "exists": "has any value",
    "missing": "has no value",
}
NUMERIC = ("num", "time", "duration")
NO_VALUE = ("exists", "missing")
# The operator choice's extra answer: the request sets no condition on the column.
UNSET: Any = "unset"
# Words of a column's key that say nothing about it, so a request sharing them doesn't make it
# a candidate.
KEY_WORDS_IGNORED = frozenset({"config", "summary", "value", "values"})
REQUEST_WORDS_IGNORED = frozenset(
    {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of",
     "on", "or", "run", "runs", "set", "the", "to", "with", "all", "only", "add", "drop",
     "group", "best"}
)  # fmt: skip


class AssistantError(BadGateway):
    """The Decisions API couldn't be reached, refused the request, or answered in a shape
    Flock doesn't understand (HTTP 502)."""


@dataclass(frozen=True)
class Column:
    """A column the assistant may filter on, with what Flock observed in it."""

    key: str  # "state", "config.lr", "summary.val/loss", "tags", ...
    kind: str  # Flock ColumnKind: num | time | duration | user | str | state | bool | json
    runs: int  # runs with a value
    values: tuple[Value, ...]  # observed distinct values (the caller caps them at 1000)


@dataclass(frozen=True)
class RunRef:
    id: str
    name: str


@dataclass(frozen=True)
class Condition:
    """One column compared to a value: the web's ``RunCondition``."""

    key: str
    op: Op
    value: str | int | float | bool | None

    def to_json(self) -> dict[str, Any]:
        return {"key": self.key, "op": self.op, "value": self.value}


@dataclass(frozen=True)
class Reading:
    """A way to read the request as a condition, and how likely the model finds it."""

    condition: Condition
    probability: float

    def to_json(self) -> dict[str, Any]:
        return {**self.condition.to_json(), "probability": self.probability}


@dataclass(frozen=True)
class Action:
    """One assistant turn's result, shown as an action card (D191)."""

    type: ActionType
    conditions: tuple[tuple[Reading, tuple[Reading, ...]], ...] = ()  # chosen, others best first
    runs: tuple[tuple[RunRef, float], ...] = ()  # for add_runs and drop_runs
    best: tuple[str, Literal["asc", "desc"]] | None = None  # for set_best

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "conditions": [
                {**chosen.to_json(), "alternatives": [other.to_json() for other in others]}
                for chosen, others in self.conditions
            ],
            "runs": [
                {"id": run.id, "name": run.name, "probability": probability}
                for run, probability in self.runs
            ],
            "best": {"key": self.best[0], "order": self.best[1]} if self.best else None,
        }


NONE = Action("none")


class DecisionsClient:
    """OpenAI's Decisions API: ``POST /decisions`` with typed questions about one input."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = MODEL,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/decisions"
        self.model = model
        self.own_http = http is None
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def aclose(self) -> None:
        if self.own_http:
            await self.http.aclose()

    async def decide(self, input: str | list, questions: list[dict]) -> list[dict]:
        """The answers to ``questions`` about ``input``, in question order. A refused question
        comes back as ``{"type": "refusal", "name": ...}``; anything else wrong is an
        ``AssistantError``."""
        body = {"model": self.model, "input": input, "questions": questions}
        try:
            response = await self.http.post(
                self.url,
                content=orjson.dumps(body),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        except httpx.HTTPError as e:
            raise AssistantError(f"the Decisions API couldn't be reached: {e}") from None
        if response.is_error:
            raise AssistantError(
                f"the Decisions API refused the request ({response.status_code}): "
                f"{_error_message(response)}"
            )
        try:
            answers = orjson.loads(response.content)["answers"]
        except orjson.JSONDecodeError, KeyError, TypeError:
            raise AssistantError("the Decisions API sent an unreadable answer") from None
        if not isinstance(answers, list) or len(answers) != len(questions):
            raise AssistantError("the Decisions API didn't answer every question")
        for question, answer in zip(questions, answers, strict=True):
            if not isinstance(answer, dict) or answer.get("name") != question["name"]:
                raise AssistantError("the Decisions API answered out of order")
        return answers


def _error_message(response: httpx.Response) -> str:
    """The message of an OpenAI error body, ``{"error": {"message": ...}}``, else the text."""
    try:
        return str(orjson.loads(response.content)["error"]["message"])
    except orjson.JSONDecodeError, KeyError, TypeError:
        return response.text[:200] or response.reason_phrase


async def assist(
    request: str,
    columns: Sequence[Column],
    runs: Sequence[RunRef],
    current: Sequence[Condition],
    client: DecisionsClient,
) -> Action:
    """The action ``request`` asks for, given the project's columns and runs and the draft's
    current filters, in the two requests of D191."""
    request = request.strip()
    if not request:
        return NONE
    candidates = candidate_columns(request, columns)
    input = _input(request, current, candidates)

    # Request 1: the action, which columns the request mentions, and for a small categorical
    # column, which of its values it asks for. Asked live, "failed runs" scored `state` 0.15 as
    # a column but 0.96 as "runs whose `state` is `failed`"; a column is picked when either its
    # own question or one of its values' passes.
    questions = [_choice("action", "What does the request ask to do?", ACTIONS)]
    questions += [
        _predicate(
            f"column_{i}",
            f"Does the request mention the column {_described(column)} or one of its values, "
            "to filter or to rank runs by it?",
        )
        for i, column in enumerate(candidates)
    ]
    value_questions = _value_questions(candidates)[: max(MAX_QUESTIONS - len(questions), 0)]
    questions += [
        _predicate(
            f"value_{n}", f"Does the request ask for runs whose `{column.key}` is `{label}`?"
        )
        for n, (column, label, _) in enumerate(value_questions)
    ]
    answers = await client.decide(input, questions)
    action, probability = _top(answers[0])
    if action not in ACTIONS or action == "none" or probability < PICKED:
        return NONE
    scores = {
        column.key: _probability(answer)
        for column, answer in zip(candidates, answers[1 : 1 + len(candidates)], strict=True)
    }
    asked: dict[str, list[tuple[Value, float]]] = {}  # values the request asks for, by column
    for (column, _, value), answer in zip(
        value_questions, answers[1 + len(candidates) :], strict=True
    ):
        p = _probability(answer)
        scores[column.key] = max(scores[column.key], p)
        if p >= PICKED:
            asked.setdefault(column.key, []).append((value, p))
    picked_columns = sorted(
        (c for c in candidates if scores[c.key] >= PICKED), key=lambda c: -scores[c.key]
    )

    # Request 2: what follows from the action.
    if action == "filter":
        return await _filter(input, picked_columns, client, _numbers(request), asked)
    if action in ("add_runs", "drop_runs"):
        return await _runs(action, request, input, runs, client)
    return await _best(input, picked_columns, client)


def _value_questions(candidates: Sequence[Column]) -> list[tuple[Column, str, Value]]:
    """The values asked about one by one: those of small text and state columns with at least
    two. A column every run shares one value of reads as true for any request (asked live:
    `program` is `train.py`, 1.0), and true/false read as the column itself."""
    questions = []
    for column in candidates:
        if column.kind not in ("state", "str") or _listed(column) is None:
            continue
        values = [v for v in column.values if v is not None and v != ""]
        if len(values) >= 2:
            questions += [(column, str(_label(v)), v) for v in values]
    return questions


def candidate_columns(request: str, columns: Sequence[Column]) -> list[Column]:
    """The columns asked about: the run's own attributes (``state``, ``tags``, ``group``…), then
    the 50 ``config.*``/``summary.*`` columns with most runs, then those sharing a word with the
    request, by runs, up to 319 so the action choice and 60 value questions fit in 380 (D191).
    ``name`` and ``display_name`` are left out: naming runs is what adding and dropping by hand
    are for, and asked about, the model picked ``name`` for almost every request.

    Attributes come first because they're what requests name most ("failed runs", "my runs")
    and every run has them: ranked by runs alone they tie with every config key and lose on
    the alphabet."""
    by_runs = sorted(columns, key=lambda c: (-c.runs, c.key))
    attributes = [
        c
        for c in by_runs
        if not c.key.startswith(("config.", "summary.")) and c.key not in NAMING_COLUMNS
    ]
    rest = [c for c in by_runs if c.key.startswith(("config.", "summary."))]
    chosen = attributes + rest[:TOP_COLUMNS]
    words = _words(request) - REQUEST_WORDS_IGNORED
    chosen += [c for c in rest[TOP_COLUMNS:] if words & (_words(c.key) - KEY_WORDS_IGNORED)]
    return chosen[: MAX_QUESTIONS - 1 - VALUE_ROOM]


def _words(text: str) -> set[str]:
    """Lowercase words of letters and digits, ``config.warmup_steps`` -> warmup, steps."""
    return {w for w in re.split(r"[^0-9a-z]+", text.lower()) if len(w) > 1}


def _input(request: str, current: Sequence[Condition], candidates: Sequence[Column]) -> str:
    """What the model reads: the request, the draft's filters and the candidate columns."""
    filters = "; ".join(
        f"{c.key} {c.op}" + ("" if c.op in NO_VALUE else f" {_label(c.value)}") for c in current
    )
    return (
        f"Request: {request}\n"
        f"Current filters: {filters or 'none'}\n"
        f"Columns: {', '.join(_described(c) for c in candidates)}"
    )


# A categorical column's values are listed beside its name when it has at most this many, each
# short: without them "failed runs" can't be tied to `state` (asked live: 0.01, then 0.98).
LISTED_VALUES = 12
LISTED_VALUE_LENGTH = 40


def _listed(column: Column) -> list[str | bool] | None:
    """A small categorical column's values as choice labels; None for any other column."""
    if column.kind not in ("state", "str", "bool"):
        return None
    values = [v for v in column.values if v is not None and v != ""]
    if not values or len(values) > LISTED_VALUES:
        return None
    labels = [_label(v) for v in values]
    if any(len(str(label)) > LISTED_VALUE_LENGTH for label in labels):
        return None
    return labels


def _described(column: Column) -> str:
    """A column as the model reads it: its name in backticks, and a small categorical
    column's values."""
    labels = _listed(column)
    if labels is None:
        return f"`{column.key}`"
    return f"`{column.key}` (values: {', '.join(str(label) for label in labels)})"


async def _filter(
    input: str,
    picked: Sequence[Column],
    client: DecisionsClient,
    mentioned: Sequence[float] = (),
    asked: Mapping[str, Sequence[tuple[Value, float]]] = {},
) -> Action:
    """Request 2 for a filter: an operator and a value per picked column. A column whose values
    the first request already asked about one by one is equal to the likeliest of them, and
    isn't asked again: asked live, the operator question then still said the request sets no
    condition on `state` for "runs that failed"."""
    direct = {}
    for column in picked:
        if values := sorted(asked.get(column.key, ()), key=lambda v: -v[1]):
            readings = [Reading(Condition(column.key, "eq", v), p) for v, p in values]
            others = tuple(r for r in readings[1:] if r.probability >= MIN_ALTERNATIVE)
            direct[column.key] = (readings[0], others[:ALTERNATIVES])
    rest = [c for c in picked if c.key not in direct][: MAX_REQUEST // 2]
    found = await _operators(input, rest, client, mentioned) if rest else {}
    conditions = [
        condition
        for column in picked
        if (condition := direct.get(column.key) or found.get(column.key)) is not None
    ]
    return Action("filter", conditions=tuple(conditions)) if conditions else NONE


async def _operators(
    input: str, picked: Sequence[Column], client: DecisionsClient, mentioned: Sequence[float]
) -> dict[str, tuple[Reading, tuple[Reading, ...]]]:
    """An operator and a value per column, by key, for the columns the request sets one on."""
    questions: list[dict] = []
    plans: list[tuple[Column, list[Op], dict[str | bool, Value] | None, Value]] = []
    for i, column in enumerate(picked):
        values = _value_choices(column, mentioned)
        ops = _ops(column, has_values=bool(values))
        questions.append(
            _choice(
                f"op_{i}",
                f"How does the request compare the column `{column.key}`?",
                {op: OPS[op] for op in ops} | {UNSET: "the request sets no condition on it"},
            )
        )
        if len(values) >= 2:
            questions.append(
                _choice(
                    f"value_{i}",
                    f"Which listed value of `{column.key}` does the request compare it to? "
                    "Pick the nearest one.",
                    {label: None for label in values},
                )
            )
            plans.append((column, ops, values, None))
        else:  # a lone value needs no question
            plans.append((column, ops, None, next(iter(values.values()), None)))
    answers = {a["name"]: a for a in await client.decide(input, questions)}

    conditions = {}
    for i, (column, ops, values, only) in enumerate(plans):
        op_answer = answers[f"op_{i}"]
        if op_answer["type"] == "refusal":
            continue
        choices = _distribution(op_answer, [*ops, UNSET])
        # A second guard against a column picked only for being related (D191): the operator
        # question can say the request sets no condition on it, and then it's left out.
        if not choices or choices[0][0] == UNSET:
            continue
        op_probs = [(op, p) for op, p in choices if op != UNSET]
        if values is None:
            value_probs: list[tuple[Value, float]] = [(only, 1.0)]
        else:
            value_answer = answers[f"value_{i}"]
            if value_answer["type"] == "refusal":
                value_probs = []
            else:
                value_probs = [
                    (values[label], p) for label, p in _distribution(value_answer, values)
                ]
        readings = sorted(
            _readings(column.key, op_probs, value_probs), key=lambda r: -r.probability
        )
        if not readings:
            continue
        others = tuple(r for r in readings[1:] if r.probability >= MIN_ALTERNATIVE)
        conditions[column.key] = (readings[0], others[:ALTERNATIVES])
    return conditions


def _readings(
    key: str, ops: Sequence[tuple[Op, float]], values: Sequence[tuple[Value, float]]
) -> list[Reading]:
    """Each operator with each value, as likely as both together; ``exists`` and ``missing``
    once, without a value."""
    readings = []
    for op, p_op in ops:
        if op in NO_VALUE:
            readings.append(Reading(Condition(key, op, None), p_op))
            continue
        for value, p_value in values:
            if isinstance(value, (list, dict)):  # json values are matched as text
                value = orjson.dumps(value).decode()
            readings.append(Reading(Condition(key, op, value), p_op * p_value))
    return readings


def _ops(column: Column, *, has_values: bool) -> list[Op]:
    """The operators that make sense for a column of this kind."""
    if not has_values:
        return ["exists", "missing"]
    if column.kind in NUMERIC:
        return ["eq", "ne", "gt", "gte", "lt", "lte", "exists", "missing"]
    if column.kind == "bool":
        return ["eq", "ne", "exists", "missing"]
    if column.kind in ("str", "json"):
        return ["eq", "ne", "contains", "exists", "missing"]
    return ["eq", "ne", "exists", "missing"]  # state, user


def _value_choices(column: Column, mentioned: Sequence[float] = ()) -> dict[str | bool, Value]:
    """A column's candidate values by their choice label: its observed values, or for a numeric
    column with more than 255, 255 of them at even quantiles (D191). A numeric column is also
    offered the numbers the request names, first, since a bound like "lr below 1e-3" is rarely
    an observed value in a random search. Choice values must be strings or booleans, so numbers
    travel as their text and are decoded through this map."""
    values = [v for v in column.values if v is not None and v != ""]
    if column.kind in NUMERIC:
        numbers = sorted(
            {v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)}
        )
        numbers = [v for v in numbers if math.isfinite(v)]
        room = MAX_CHOICES - len(mentioned)
        if len(numbers) > room:
            last = len(numbers) - 1
            numbers = [numbers[round(i * last / (room - 1))] for i in range(room)]
        values = [*mentioned, *numbers]
    labels: dict[str | bool, Value] = {}
    for value in values:
        labels.setdefault(_label(value), value)
        if len(labels) == MAX_CHOICES:
            break
    return labels


# A number as written in a request: 64, 0.1, .5, 1e-3, 3E+4.
_NUMBER = re.compile(
    r"(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?(?![\w.])", re.IGNORECASE
)


def _numbers(request: str) -> list[float]:
    """The distinct numbers a request names, in order, at most 16."""
    out: list[float] = []
    for text in _NUMBER.findall(request):
        number = float(text)
        if math.isfinite(number) and number not in out:
            out.append(number)
    return out[:16]


def _label(value: Value) -> str | bool:
    """A value as a choice: booleans as themselves, anything else as text."""
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return repr(value)
    return orjson.dumps(value).decode()


async def _runs(
    action: Literal["add_runs", "drop_runs"],
    request: str,
    input: str,
    runs: Sequence[RunRef],
    client: DecisionsClient,
) -> Action:
    """Request 2 for picking runs: a predicate per candidate run name."""
    candidates = candidate_runs(request, runs)
    if not candidates:
        return NONE
    verb = "add" if action == "add_runs" else "leave out"
    questions = [
        _predicate(f"run_{i}", f"Does the request ask to {verb} the run named `{run.name}`?")
        for i, run in enumerate(candidates)
    ]
    answers = await client.decide(input, questions)
    picked = sorted(
        (
            (run, p)
            for run, answer in zip(candidates, answers, strict=True)
            if (p := _probability(answer)) >= PICKED
        ),
        key=lambda pick: -pick[1],
    )
    return Action(action, runs=tuple(picked)) if picked else NONE


def candidate_runs(request: str, runs: Sequence[RunRef]) -> list[RunRef]:
    """Up to 255 runs whose names share a substring with the request: those containing the
    most of its words first, else, when no name matches, the first 255 (D191)."""
    if len(runs) <= MAX_CHOICES:
        return list(runs)
    words = _words(request) - REQUEST_WORDS_IGNORED
    scored = [
        (sum(word in run.name.lower() for word in words), i, run) for i, run in enumerate(runs)
    ]
    matching = sorted((s for s in scored if s[0]), key=lambda s: (-s[0], s[1]))
    if not matching:
        return list(runs[:MAX_CHOICES])
    return [run for _, _, run in matching[:MAX_CHOICES]]


async def _best(input: str, picked: Sequence[Column], client: DecisionsClient) -> Action:
    """Request 2 for best-by: which picked numeric column, and whether lower or higher wins."""
    numeric = [c for c in picked if c.kind == "num"][:MAX_CHOICES]
    if not numeric:
        return NONE
    questions = [
        _choice(
            "best_order",
            "Is a lower or a higher value of the metric better, by the request?",
            {"asc": "lower is better", "desc": "higher is better"},
        )
    ]
    if len(numeric) >= 2:
        questions.append(
            _choice(
                "best_key",
                "Which column should rank the runs?",
                {c.key: None for c in numeric},
            )
        )
    answers = await client.decide(input, questions)
    order, _ = _top(answers[0])
    key = _top(answers[1])[0] if len(answers) > 1 else numeric[0].key
    if order not in ("asc", "desc") or key not in {c.key for c in numeric}:
        return NONE
    return Action("set_best", best=(str(key), order))


def _predicate(name: str, instructions: str) -> dict:
    return {"type": "predicate", "name": name, "instructions": instructions}


def _choice(name: str, instructions: str, choices: dict[Any, str | None]) -> dict:
    return {
        "type": "choice",
        "name": name,
        "instructions": instructions,
        "choices": [
            {"value": value} | ({"description": description} if description else {})
            for value, description in choices.items()
        ],
    }


def _probability(answer: dict) -> float:
    """A predicate's probability; a refusal is taken as no."""
    if answer.get("type") != "predicate":
        return 0.0
    return float(answer.get("probability") or 0.0)


def _top(answer: dict) -> tuple[Any, float]:
    """A choice's pick and its probability; a refusal picks nothing."""
    if answer.get("type") != "choice":
        return None, 0.0
    choice = answer.get("choice")
    for item in answer.get("probabilities") or ():
        if item.get("value") == choice and type(item.get("value")) is type(choice):
            return choice, float(item.get("probability") or 0.0)
    return choice, float(answer.get("confidence") or 0.0)


def _distribution[T](answer: dict, allowed: Sequence[T] | dict[T, Any]) -> list[tuple[T, float]]:
    """A choice's probability per listed value, most likely first. String and boolean values
    are distinct, so ``"true"`` never reads as ``True``."""
    keys = {(type(k), k): k for k in allowed}
    found: dict[tuple[type, Any], float] = {}
    for item in answer.get("probabilities") or ():
        value = item.get("value")
        if (type(value), value) in keys:
            found[(type(value), value)] = float(item.get("probability") or 0.0)
    choice = answer.get("choice")
    if not found and (type(choice), choice) in keys:  # no distribution: the pick alone
        found[(type(choice), choice)] = float(answer.get("confidence") or 1.0)
    return sorted(((keys[k], p) for k, p in found.items()), key=lambda vp: -vp[1])


# ---------------------------------------------------------------------- primary columns (D191)
# The group page's table shows a few columns first, wider, as "Suggested": the settings that
# matter most for comparing the group's runs, then its metrics to compare them on. One request
# asks a predicate per candidate column; without the model, the varying config keys, the best-by
# column and the metrics most runs log are shown.

PRIMARY_SETTINGS = 4  # config keys shown first, at most
PRIMARY_METRICS = 4  # metrics after them, at most
PRIMARY_METRICS_MIN = 3  # metrics shown even when the model picks fewer, if the runs have them
PRIMARY_MAX = PRIMARY_SETTINGS + PRIMARY_METRICS
PRIMARY_VALUES = 6  # observed values shown per column
PRIMARY_CACHE_MAX = 512  # groups whose primary columns are kept (``_PRIMARY``)

# Primary columns by group pk, with the signature they were predicted for, oldest first.
_PRIMARY: dict[int, tuple[bytes, tuple[str, ...]]] = {}


@dataclass(frozen=True)
class GroupColumn:
    """A column of a group's runs, as the primary columns question reads it."""

    key: str  # "config.lr", "summary.val/loss"
    kind: str  # D185's varies | repeat | same | unique, or "metric" for a summary column
    runs: int  # runs with a value
    values: tuple[Value, ...]  # distinct values, numbers ascending


def group_columns(rows: Sequence[RunRow], kinds: Mapping[str, Kind]) -> list[GroupColumn]:
    """The config keys of ``rows`` with their kinds, then their summary metrics: the numeric
    ``summary.*`` keys, W&B's own (``summary._step``, ``_runtime``...) left out."""
    found: dict[str, list[Value]] = {}
    for row in rows:
        for key, value in [*row.config.items(), *row.summary.items()]:
            if value is None or (key.startswith("summary.") and not _scalar_number(value)):
                continue
            if key.startswith("summary._"):
                continue
            found.setdefault(key, []).append(value)
    out = []
    for key in sorted(found, key=lambda k: (not k.startswith("config."), k)):
        kind = kinds.get(key, "metric" if key.startswith("summary.") else "same")
        out.append(GroupColumn(key, kind, len(found[key]), _distinct(found[key])))
    return out


def _scalar_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _distinct(values: Sequence[Value]) -> tuple[Value, ...]:
    seen: dict[bytes, Value] = {}
    for value in values:
        seen.setdefault(orjson.dumps(value), value)
    found = list(seen.values())
    if all(_scalar_number(v) for v in found):
        found.sort()  # type: ignore[arg-type]
    return tuple(found)


def primary_candidates(columns: Sequence[GroupColumn]) -> list[GroupColumn]:
    """The columns asked about: the config keys that vary, then the summary metrics by runs.
    Same and unique keys (paths, ids) and seeds are never primary."""
    varies = [c for c in columns if c.kind == "varies"]
    metrics = sorted((c for c in columns if c.kind == "metric"), key=lambda c: (-c.runs, c.key))
    return [*varies, *metrics][:MAX_QUESTIONS]


def _shown(column: GroupColumn) -> str:
    """A column's few observed values: a metric's range, else up to ``PRIMARY_VALUES``."""
    values = column.values
    if column.kind == "metric" and len(values) > 2:
        return f"from {_short(values[0])} to {_short(values[-1])}"
    labels = [_short(v)[:LISTED_VALUE_LENGTH] for v in values[:PRIMARY_VALUES]]
    more = len(values) - PRIMARY_VALUES
    return ", ".join(labels) + (f" and {more} more" if more > 0 else "")


def _short(value: Value) -> str:
    """A value as the model reads it, floats to 4 significant digits."""
    if isinstance(value, float):
        return format(value, ".4g")
    return str(_label(value))


KIND_WORDS = {
    "varies": "config, varies between runs",
    "repeat": "config, the seed: runs repeat with different values",
    "same": "config, the same in every run",
    "unique": "config, different in every run",
    "metric": "metric",
}


def primary_input(
    name: str,
    filter: Mapping[str, Any] | None,
    runs: int,
    columns: Sequence[GroupColumn],
    best: tuple[str | None, str | None],
) -> str:
    """What the model reads: the group, how it ranks runs, and its columns with a few values.
    Same and unique columns are listed by name only, as context."""
    key, order = best
    ranked = "nothing"
    if key:
        ranked = f"`{key}`, " + ("higher is better" if order == "desc" else "lower is better")
    lines = [
        f"Group of runs: {name}",
        f"Built with the filter: {orjson.dumps(filter).decode() if filter else 'none'}",
        f"Runs: {runs}",
        f"Best run by: {ranked}",
        "Columns:",
    ]
    for column in columns:
        if column.kind in ("same", "unique"):
            continue
        lines.append(f"- `{column.key}` ({KIND_WORDS[column.kind]}): {_shown(column)}")
    fixed = [f"`{c.key}`" for c in columns if c.kind in ("same", "unique")][:MAX_CHOICES]
    if fixed:
        lines.append(f"Other config keys, not compared: {', '.join(fixed)}")
    return "\n".join(lines)


def primary_questions(candidates: Sequence[GroupColumn]) -> list[dict]:
    """A predicate per candidate. Asked live, "one of the few columns someone comparing these
    runs would look at first" took throughput and grad norm too (0.7 to 0.8), and naming
    "a swept setting or the headline metric" took every varying key (1.0); the purpose and a
    number keep it to the sweep's axes and its main metrics."""
    return [
        _predicate(
            f"primary_{i}",
            "Someone opens this group to compare its runs and find out which settings gave "
            f"the best result. Is `{column.key}` one of the 3 to 6 columns they would look "
            "at first?",
        )
        for i, column in enumerate(candidates)
    ]


def pick_primary(
    candidates: Sequence[GroupColumn], answers: Sequence[dict], best_key: str | None
) -> list[str]:
    """The settings the model finds likely (probability >= 0.5), most likely first, then the
    metrics: the best-by column, those it finds likely, and its next likeliest up to
    ``PRIMARY_METRICS_MIN``, so the runs always have something to be compared on."""
    scored = sorted(
        (
            (_probability(answer), i, column)
            for i, (column, answer) in enumerate(zip(candidates, answers, strict=True))
        ),
        key=lambda s: (-s[0], s[1]),
    )
    settings = [c.key for p, _, c in scored if c.kind != "metric" and p >= PICKED]
    metrics = [c.key for _, _, c in scored if c.kind == "metric"]
    likely = sum(1 for p, _, c in scored if c.kind == "metric" and p >= PICKED)
    return _suggested(settings, metrics, best_key, max(likely, PRIMARY_METRICS_MIN))


def primary_fallback(columns: Sequence[GroupColumn], best_key: str | None) -> list[str]:
    """Without the model: the varying config keys, in key order, then the best-by column and
    the metrics most runs log."""
    settings = [c.key for c in columns if c.kind == "varies"]
    metrics = [
        c.key
        for c in sorted((c for c in columns if c.kind == "metric"), key=lambda c: (-c.runs, c.key))
    ]
    return _suggested(settings, metrics, best_key, PRIMARY_METRICS_MIN)


def _suggested(
    settings: Sequence[str], metrics: Sequence[str], best_key: str | None, count: int
) -> list[str]:
    """Up to ``PRIMARY_SETTINGS`` settings, then ``count`` metrics (at most ``PRIMARY_METRICS``)
    led by the best-by column. A best-by column that isn't a metric (a config key) leads the
    metrics all the same: it's what the runs are ranked by."""
    shown = list(dict.fromkeys(k for k in settings if k != best_key))[:PRIMARY_SETTINGS]
    ranked = list(dict.fromkeys([*([best_key] if best_key else []), *metrics]))
    ranked = [k for k in ranked if k not in shown]
    return [*shown, *ranked[: min(count, PRIMARY_METRICS)]]


async def predict_primary(
    input: str,
    columns: Sequence[GroupColumn],
    best_key: str | None,
    client: DecisionsClient,
) -> list[str]:
    """The group's primary columns, in one Decisions request."""
    candidates = primary_candidates(columns)
    if not candidates:
        return _suggested([], [], best_key, PRIMARY_METRICS_MIN)
    answers = await client.decide(input, primary_questions(candidates))
    return pick_primary(candidates, answers, best_key)


def cached_primary(group_pk: int, signature: bytes) -> tuple[str, ...] | None:
    found = _PRIMARY.get(group_pk)
    return found[1] if found is not None and found[0] == signature else None


def cache_primary(group_pk: int, signature: bytes, keys: Sequence[str]) -> None:
    _PRIMARY.pop(group_pk, None)
    _PRIMARY[group_pk] = (signature, tuple(keys))
    while len(_PRIMARY) > PRIMARY_CACHE_MAX:
        del _PRIMARY[next(iter(_PRIMARY))]
