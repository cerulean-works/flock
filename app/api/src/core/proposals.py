"""Proposed groups (D229): a chat with a model about how to group a project's runs.

On the Auto tab of New group, a model reads the project (its runs, the config keys that vary
with their values, a summary metric, its W&B groups, saved groups and the heuristic
suggestions of D219) and the conversation, and proposes groups through OpenAI's Responses API
with a strict JSON schema. The user refines them in plain language; accepting one creates it
with ``POST .../groups``.

The model only chooses: each proposal's runs (a filter, or a hand list), its name, a sentence
saying what it compares, and the order of its axes. Everything shown about a proposal is
computed here from the runs it ends up with (``finish``): runs a filter matches, the axes and
their values from ``classify_columns``, the sweep's grid, the best run and the seed key, so a
render never depends on what the model claims. Nothing is stored: the client sends the
conversation and the proposals on screen with every turn.
"""

import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
import orjson

from app.api.src.core import group_analysis as analysis
from app.api.src.core.assistant import AssistantError
from app.api.src.core.group_analysis import Kind, RunRow, Value

type Order = Literal["asc", "desc"]
type Role = Literal["user", "assistant"]

MODEL = "gpt-6.2-sol"  # the default of ``Settings.openai_proposal_model``
TIMEOUT = 90.0  # seconds for one turn: the model reasons over the whole project
EFFORT = "medium"  # reasoning effort: "low" answers in half the time with sloppier names
MAX_OUTPUT_TOKENS = 16_000  # hand lists of a few hundred ids fit
TABLE_BUDGET = 40_000  # characters of the runs table (~10k tokens) before it's summarized
MAX_MESSAGES = 24  # turns of the conversation sent, the latest kept
MAX_PROPOSALS = 20  # proposals kept of a reply
MIN_RUNS = 2  # a proposal of fewer runs compares nothing and is dropped
LISTED_VALUES = 12  # values listed per varying key, with their counts
WIDE_KEY = 20  # a key with more distinct values is left out of a summarized table
METRICS = 40  # summary metrics read and named, those with most runs first
GRID_MAX = 24  # an axis with more values gets no grid (a random search's samples)
SHOWN_IDS = 200  # ids of a hand-picked proposal on screen sent back to the model
VALUE_LENGTH = 40  # characters of a string value shown to the model
# The transport of clients ``ResponsesClient`` makes itself; tests set a fake one.
TRANSPORT: httpx.AsyncBaseTransport | None = None

INSTRUCTIONS = """\
You help an ML researcher organize the runs of one project into groups, on the screen \
where they create groups. A group is a saved set of runs that answers one question: it \
compares one controlled change, with everything else held fixed apart from seeds. Typical \
groups are a sweep of one or two settings (lr, or lr × batch_size), a toggle (qk_norm on / \
off), or an ablation (the full model against variants that each remove one part). Flock then \
shows each group's varying settings, its sweep grid and its best run.

You get the project: its runs with the config keys that vary across it, a summary metric, \
its W&B groups, the groups already saved, and suggestions from a simple heuristic (hints \
only: they can be too broad or too narrow). Then the conversation, and the proposals on \
screen when there are some.

How to propose:
- Each proposal compares one controlled change. Prefer one or two axes. Runs that differ in \
many unrelated settings at once are not a comparison: split them by the setting that \
explains most, or leave them out.
- A random or grid search over several settings at once (runs that all sample the same \
settings, often one W&B group) is one group too: its axes are the settings it searches.
- Repeats of one configuration with other seeds belong together in the same group.
- Leave one-off runs (debug runs, a single experiment with no counterpart) ungrouped. Don't \
force every run into a group: two clean groups beat six noisy ones. Say in your message how \
many runs you left out and why, briefly.
- Usually a run is in at most one proposal; a baseline run may be shared by several \
ablations.
- Don't propose a group that is already saved (same runs or same filter), unless the user \
asks for it.
- Prefer a filter to a hand list whenever a clean filter selects exactly the runs: a W&B \
group, a config value, a state, or a combination. When the runs table is summarized, \
always use filters. Use a hand list (runs) only when no simple filter fits; then set \
filter to null.
- Filters are Mongo-style JSON objects, written as a JSON string. Keys are columns: \
"config.<key>" (as listed), "summary.<metric>", "state" (finished, running, crashed, \
failed, ...), "group" (the W&B group), "name" (the run id), "display_name", "tags", \
"created_at" (ISO time). Operators: $eq, $ne, $in, $nin, $gt, $gte, $lt, $lte, $exists, \
$regex, $contains, and $and, $or, $not. A bare value means $eq. Examples: \
{"group": "pretrain-1b"}; {"$and": [{"config.optimizer": "adamw"}, {"state": "finished"}]}; \
{"config.lr": {"$in": [0.0001, 0.0003, 0.001]}}. The server runs the filter and the group \
gets exactly the runs it matches, so check that it matches what you mean.
- name: short, in Flock's style, keys without "config.": "lr × batch_size" for two axes, \
"qk_norm on / off" for a toggle, "data_mix: web-v2, web-v3" for one key with up to 3 \
values, "lr: 6 values" for more. Start with the W&B group's name when the group is one \
and the name is words ("pretrain-1b, lr"). When it is an opaque id such as a hash or \
sweep id ("sweep-x8k2ab3c"), lead with what the group varies and put the id last in \
parentheses ("lr × wd (sweep-x8k2ab3c)"): names are read side by side in chart legends, \
so they must differ in their first words. Add ", finished" when the filter fixes the \
state. At most 60 characters, unique among the proposals and the saved groups.
- why: one plain sentence saying what the group compares and what it holds fixed.
- id: a short lowercase slug. When revising, keep the id of every proposal on screen that \
you keep or change; new proposals get new ids.
- axes: the config keys (with "config.") the group varies on purpose, most important \
first. Leave out seeds.
- best_key and best_order: null, to rank by the project's best-by metric. Set them only \
when the user asks to rank by another metric, or when the project has none and a summary \
metric is clearly the target ("asc" when lower is better).
- message: 1 to 3 plain sentences, no markdown, saying what you proposed or changed. \
Don't count runs in it: the server settles each proposal's runs and the screen shows the \
counts. If a request is ambiguous, make the most likely change and say what you assumed.

The user's requests win over these guidelines: when they ask for a group, make it, and \
name any caveat in the message instead of refusing.

When revising, return the whole set of proposals that should be on screen, not only the \
changes, and leave the proposals the user didn't mention as they were (same id, filter or \
runs, name). If the user asks for something you can't do here, say so in the message and \
return the proposals unchanged.\
"""

# The reply's shape (strict structured outputs: every field required, nulls spelled out).
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["message", "proposals"],
    "properties": {
        "message": {"type": "string"},
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "id", "name", "why", "filter", "runs", "axes", "best_key", "best_order"
                ],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "why": {"type": "string"},
                    "filter": {
                        "type": ["string", "null"],
                        "description": "a Mongo-style filter as JSON text, or null for a "
                        "hand list",
                    },
                    "runs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "run ids, when filter is null; else empty",
                    },
                    "axes": {"type": "array", "items": {"type": "string"}},
                    "best_key": {"type": ["string", "null"]},
                    "best_order": {"anyOf": [{"type": "string", "enum": ["asc", "desc"]},
                                             {"type": "null"}]},
                },
            },
        },
    },
}  # fmt: skip


# ---------------------------------------------------------------------- the API


class ResponsesClient:
    """OpenAI's Responses API (``POST /responses``) with a strict JSON schema as the reply's
    format; ``respond`` returns the parsed reply."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = MODEL,
        tier: str = "standard",
        base_url: str = "https://api.openai.com/v1",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        # OpenAI's service tier (D230): "fast" or "ultrafast", or None for the standard one.
        self.tier = tier if tier != "standard" else None
        self.url = base_url.rstrip("/") + "/responses"
        self.own_http = http is None
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT, connect=10.0), transport=TRANSPORT
        )

    async def aclose(self) -> None:
        if self.own_http:
            await self.http.aclose()

    async def respond(
        self, instructions: str, input: list[dict[str, Any]], name: str, schema: dict[str, Any]
    ) -> dict[str, Any]:
        """The model's reply to ``input`` (message items) as the object ``schema`` describes.
        A refusal, a cut-off reply, or anything else wrong is an ``AssistantError``."""
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": input,
            "text": {"format": {"type": "json_schema", "name": name, "strict": True,
                                "schema": schema}},
            "reasoning": {"effort": EFFORT},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }  # fmt: skip
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.tier:
            # The header and the body name the same tier, as OpenAI asks.
            body["service_tier"] = self.tier
            headers["OpenAI-Service-Tier"] = self.tier
        try:
            response = await self.http.post(
                self.url,
                content=orjson.dumps(body),
                headers=headers,
            )
        except httpx.TimeoutException:
            raise AssistantError("the model took too long to answer") from None
        except httpx.HTTPError as e:
            raise AssistantError(f"the Responses API couldn't be reached: {e}") from None
        if response.is_error:
            raise AssistantError(
                f"the Responses API refused the request ({response.status_code}): "
                f"{_error_message(response)}"
            )
        try:
            reply = orjson.loads(response.content)
        except orjson.JSONDecodeError:
            raise AssistantError("the Responses API sent an unreadable answer") from None
        if not isinstance(reply, dict):
            raise AssistantError("the Responses API sent an unreadable answer")
        if reply.get("status") != "completed":
            reason = (reply.get("incomplete_details") or {}).get("reason") or reply.get("status")
            raise AssistantError(f"the model didn't finish its answer ({reason})")
        texts = []
        for item in reply.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue  # reasoning items
            for part in item.get("content") or []:
                if part.get("type") == "refusal":
                    raise AssistantError(f"the model refused: {part.get('refusal')}")
                if part.get("type") == "output_text":
                    texts.append(part.get("text") or "")
        try:
            parsed = orjson.loads("".join(texts))
        except orjson.JSONDecodeError:
            raise AssistantError("the model's answer isn't the JSON asked for") from None
        if not isinstance(parsed, dict):
            raise AssistantError("the model's answer isn't the JSON asked for")
        return parsed


def _error_message(response: httpx.Response) -> str:
    try:
        return str(orjson.loads(response.content)["error"]["message"])
    except orjson.JSONDecodeError, KeyError, TypeError:
        return response.text[:200] or response.reason_phrase


# ---------------------------------------------------------------------- what the model reads


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class SavedGroup:
    name: str
    filter: Mapping[str, Any]
    runs: tuple[str, ...]  # run ids


@dataclass(frozen=True)
class Shown:
    """A proposal on screen, as the client sends it back."""

    id: str
    name: str
    why: str = ""
    runs: tuple[str, ...] = ()
    filter: Mapping[str, Any] | None = None
    axes: tuple[str, ...] = ()
    best_key: str | None = None
    best_order: Order | None = None


@dataclass(frozen=True)
class Context:
    """The project as one turn reads it."""

    project: str  # "entity/project"
    rows: Sequence[RunRow]  # its live runs, oldest first, with ``metrics`` in their summaries
    best: tuple[str | None, Order | None]  # the project's best by (D217)
    metrics: Sequence[str] = ()  # its summary metrics, most runs first
    saved: Sequence[SavedGroup] = ()
    suggestions: Sequence[analysis.Suggestion] = ()
    kinds: Mapping[str, Kind] = field(default_factory=dict)  # over every run; see ``context``


def context(
    project: str,
    rows: Sequence[RunRow],
    best: tuple[str | None, Order | None],
    metrics: Sequence[str] = (),
    saved: Sequence[SavedGroup] = (),
    suggestions: Sequence[analysis.Suggestion] = (),
) -> Context:
    return Context(
        project, rows, best, metrics, saved, suggestions, analysis.classify_columns(rows)
    )


def _short(value: Value) -> str:
    """A value as the model reads it: floats to 4 significant digits, long text cut."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".4g")
    if isinstance(value, (list, dict)):
        text = orjson.dumps(value).decode()
    else:
        text = str(value)
    text = " ".join(text.split()).replace("|", "/")
    return text if len(text) <= VALUE_LENGTH else text[: VALUE_LENGTH - 1] + "…"


def _sorted(values: Iterable[Value]) -> list[Value]:
    """Distinct values in their natural order (numbers ascending), a missing value last."""
    found = {analysis._canon(v): v for v in values}
    present = sorted(c for c in found if c != ("none",))
    return [found[c] for c in present] + ([None] if ("none",) in found else [])


def _date(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M")


def _filter_text(filter: Mapping[str, Any] | None) -> str:
    return orjson.dumps(filter).decode() if filter else "none"


def _key_line(key: str, rows: Sequence[RunRow]) -> str:
    values = [run.config.get(key) for run in rows]
    present = [v for v in values if v is not None]
    counts = Counter(analysis._canon(v) for v in present)
    missing = len(values) - len(present)
    numbers = all(analysis._is_number(v) for v in present)
    ordered = _sorted(present)
    if len(counts) > LISTED_VALUES and numbers:
        text = f"{len(counts)} numbers from {_short(ordered[0])} to {_short(ordered[-1])}"
    else:
        shown = [f"{_short(v)} ({counts[analysis._canon(v)]})" for v in ordered[:LISTED_VALUES]]
        more = len(ordered) - LISTED_VALUES
        text = ", ".join(shown) + (f", and {more} more" if more > 0 else "")
    return f"- {key}: {text}" + (f"; missing in {missing} runs" if missing else "")


def _table(
    rows: Sequence[RunRow], keys: Sequence[str], metric: str | None, saved: Mapping[str, str]
) -> tuple[str, bool]:
    """The runs, one line each, and whether it had to be summarized to fit ``TABLE_BUDGET``:
    first wide keys (many distinct values) are left out, then runs with equal lines are
    merged with a count, then the last lines are cut."""
    named = any(run.name != run.id for run in rows)

    def header(columns: Sequence[str]) -> str:
        head = ["id", *(["name"] if named else []), "state", "W&B group", "created", "saved in"]
        head += [analysis.display_key(k) for k in columns]
        return " | ".join(head + ([metric] if metric else []))

    def cells(run: RunRow, columns: Sequence[str], with_id: bool = True) -> list[str]:
        out = [run.id] if with_id else []
        if named and with_id:
            out.append(run.name if run.name != run.id else "")
        out += [run.state, run.group or "—"]
        if with_id:
            out.append(_date(run.created_at))
        out.append(saved.get(run.id, "—"))
        out += [_short(run.config.get(k)) for k in columns]
        if metric:
            out.append(_short(analysis._finite(run.summary.get(metric))))
        return out

    lines = [header(keys)] + [" | ".join(cells(run, keys)) for run in rows]
    text = "\n".join(lines)
    if len(text) <= TABLE_BUDGET:
        return text, False

    narrow = [
        k for k in keys
        if len({analysis._canon(run.config.get(k)) for run in rows}) <= WIDE_KEY
    ]  # fmt: skip
    lines = [header(narrow)] + [" | ".join(cells(run, narrow)) for run in rows]
    text = "\n".join(lines)
    if len(text) <= TABLE_BUDGET:
        return text, True

    # Runs with equal lines (but their ids, names and times) merged, most runs first.
    merged: dict[tuple[str, ...], list[RunRow]] = {}
    for run in rows:
        merged.setdefault(tuple(cells(run, narrow, with_id=False)), []).append(run)
    head = ["runs", "ids (first 3)", "state", "W&B group", "saved in"]
    head += [analysis.display_key(k) for k in narrow] + ([metric] if metric else [])
    out = [" | ".join(head)]
    for line, members in sorted(merged.items(), key=lambda item: -len(item[1])):
        ids = ", ".join(run.id for run in members[:3])
        out.append(" | ".join([str(len(members)), ids, *line]))
    kept, size = [], 0
    for line in out:
        if size + len(line) + 1 > TABLE_BUDGET:
            kept.append(f"… and {len(out) - len(kept)} more lines, left out")
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept), True


def describe(ctx: Context) -> str:
    """The project, as the model reads it before the conversation."""
    rows = ctx.rows
    kinds = ctx.kinds
    states = Counter(run.state for run in rows)
    lines = [
        f"Project: {ctx.project}, {len(rows)} live runs ("
        + ", ".join(f"{n} {state}" for state, n in states.most_common())
        + ")."
    ]
    if rows:
        lines.append(
            f"Created from {_date(rows[0].created_at)} to {_date(rows[-1].created_at)} (UTC)."
        )
    key, order = ctx.best
    metric = key
    if key:
        better = "higher" if order == "desc" else "lower"
        lines.append(f"Best by (the project's setting): {key}, {better} is better.")
    else:
        lines.append("Best by: not set for this project.")
        metric = ctx.metrics[0] if ctx.metrics else None
    if ctx.metrics:
        lines.append("Summary metrics: " + ", ".join(ctx.metrics) + ".")

    varying = [k for k, kind in kinds.items() if kind == "varies"]
    repeats = [k for k, kind in kinds.items() if kind == "repeat"]
    same = [k for k, kind in kinds.items() if kind == "same"]
    unique = [k for k, kind in kinds.items() if kind == "unique"]
    lines += ["", "Config keys that vary across the project (value (runs)):"]
    lines += [_key_line(k, rows) for k in varying] or ["- none"]
    if repeats:
        lines.append("Seed keys (runs repeat a configuration with other seeds): "
                     + ", ".join(repeats))  # fmt: skip
    if same:
        lines.append(f"Same in every run (not shown): {len(same)} keys, such as "
                     + ", ".join(analysis.display_key(k) for k in same[:8]))  # fmt: skip
    if unique:
        lines.append("Different in every run (paths, ids; not shown): "
                     + ", ".join(analysis.display_key(k) for k in unique[:8]))  # fmt: skip

    groups = Counter(run.group for run in rows if run.group)
    if groups:
        lines += ["", "W&B groups (runs): "
                  + ", ".join(f"{g} ({n})" for g, n in sorted(groups.items()))]  # fmt: skip

    saved_in: dict[str, list[str]] = {}
    lines += ["", "Saved groups (they exist already; don't propose them again):"]
    for i, group in enumerate(ctx.saved, 1):
        label = f"S{i}"
        for run_id in group.runs:
            saved_in.setdefault(run_id, []).append(label)
        lines.append(
            f'- {label} "{group.name}": {len(group.runs)} runs, filter {_filter_text(group.filter)}'
        )
    if not ctx.saved:
        lines.append("- none")

    lines += ["", "Heuristic suggestions (hints, may be wrong):"]
    for suggestion in ctx.suggestions[:12]:
        how = (
            f"filter {_filter_text(suggestion.filter)}"
            if suggestion.filter
            else "runs "
            + ", ".join(suggestion.run_ids[:30])
            + (" …" if len(suggestion.run_ids) > 30 else "")
        )
        lines.append(f'- "{suggestion.name}": {len(suggestion.run_ids)} runs, {how}')
    if not ctx.suggestions:
        lines.append("- none")

    saved = {run_id: ",".join(labels) for run_id, labels in saved_in.items()}
    table, summarized = _table(rows, varying, metric, saved)
    lines += [
        "",
        "Runs, oldest first"
        + (" (summarized to fit: use filters, not hand lists)" if summarized else "")
        + ":",
        table,
    ]
    return "\n".join(lines)


def _shown_text(shown: Sequence[Shown]) -> str:
    out = []
    for proposal in shown:
        item: dict[str, Any] = {"id": proposal.id, "name": proposal.name, "why": proposal.why}
        if proposal.filter:
            item["filter"] = orjson.dumps(proposal.filter).decode()
            item["run_count"] = len(proposal.runs)
        else:
            item["runs"] = list(proposal.runs[:SHOWN_IDS])
            if len(proposal.runs) > SHOWN_IDS:
                item["runs_left_out"] = len(proposal.runs) - SHOWN_IDS
        item["axes"] = list(proposal.axes)
        if proposal.best_key:
            item["best_key"], item["best_order"] = proposal.best_key, proposal.best_order
        out.append(orjson.dumps(item).decode())
    return "\n".join(out)


def build_input(
    ctx: Context, messages: Sequence[Message], shown: Sequence[Shown] | None
) -> list[dict[str, Any]]:
    """The Responses API's input: the project, then the conversation, with the proposals on
    screen just before the latest user message. No conversation: ask for a first proposal."""
    items: list[dict[str, Any]] = [{"role": "developer", "content": describe(ctx)}]
    history = list(messages)[-MAX_MESSAGES:]
    last_user = max((i for i, m in enumerate(history) if m.role == "user"), default=None)
    for i, message in enumerate(history):
        if i == last_user and shown:
            items.append({"role": "developer", "content": "Proposals on screen now:\n"
                          + _shown_text(shown)})  # fmt: skip
        items.append({"role": message.role, "content": message.content})
    if last_user is None:
        if shown:
            items.append({"role": "developer", "content": "Proposals on screen now:\n"
                          + _shown_text(shown)})  # fmt: skip
        items.append({"role": "user", "content": "Propose how to group this project's runs."})
    return items


# ---------------------------------------------------------------------- what the user sees


type Matcher = Callable[[dict[str, Any]], Awaitable[set[str] | None]]


@dataclass
class Turn:
    message: str
    proposals: list[dict[str, Any]]
    ungrouped: int
    runs: int

    def to_json(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "proposals": self.proposals,
            "ungrouped": self.ungrouped,
            "runs": self.runs,
        }


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")
    return slug or "group"


def _name(text: Any) -> str | None:
    """The model's name, if usable as a group's (1-128 printable characters)."""
    if not isinstance(text, str):
        return None
    text = " ".join(text.split())
    return text[:128] if text and all(ch.isprintable() for ch in text) else None


def _unique(name: str, taken: set[str]) -> str:
    candidate, i = name, 2
    while candidate.lower() in taken:
        candidate = f"{name} ({i})"
        i += 1
    taken.add(candidate.lower())
    return candidate


def _parse_filter(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw or None
    if not isinstance(raw, str) or not raw.strip() or raw.strip() == "null":
        return None
    try:
        parsed = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) and parsed else None


def _repeat_key(members: Sequence[RunRow], kinds: Mapping[str, Kind]) -> str | None:
    """The seed key, when some configuration of the group's runs repeats with other values
    of it."""
    seeds = sorted(k for k, kind in kinds.items() if kind == "repeat")
    if not seeds:
        return None
    varying = sorted(k for k, kind in kinds.items() if kind == "varies")
    configs = Counter(tuple(analysis._canon(run.config.get(k)) for k in varying) for run in members)
    return seeds[0] if any(n > 1 for n in configs.values()) else None


def grid(
    members: Sequence[RunRow],
    axes: Sequence[str],
    kinds: Mapping[str, Kind],
    best: tuple[str | None, Order | None],
) -> dict[str, Any] | None:
    """The sweep's coverage: a cell per value of the first axis (and of the second), with its
    runs and its best (the mean over seeds when the group repeats configurations). None
    with no axis, more than two, or an axis of more than ``GRID_MAX`` values."""
    if not 1 <= len(axes) <= 2:
        return None
    x, y = axes[0], axes[1] if len(axes) == 2 else None
    xs = _sorted(run.config.get(x) for run in members)
    ys = _sorted(run.config.get(y) for run in members) if y else [None]
    if len(xs) > GRID_MAX or len(ys) > GRID_MAX:
        return None
    key, order = best
    cells = []
    for yv in ys:
        for xv in xs:
            inside = [
                run for run in members
                if analysis._canon(run.config.get(x)) == analysis._canon(xv)
                and (y is None or analysis._canon(run.config.get(y)) == analysis._canon(yv))
            ]  # fmt: skip
            found = analysis.best(inside, key, order, kinds) if key and order and inside else None
            cells.append(
                {
                    "x": xv,
                    "y": yv,
                    "runs": len(inside),
                    "best": found.value if found else None,
                    "run": found.run_id if found else None,
                }
            )
    return {"x": x, "y": y, "xs": xs, "ys": ys if y else None, "cells": cells}


async def finish(
    ctx: Context,
    reply: Mapping[str, Any],
    match: Matcher,
    project_best: tuple[str | None, Order | None] | None = None,
) -> Turn:
    """The model's ``reply`` checked and completed: each proposal's runs (a filter's matches,
    else its hand list's live runs), and everything shown about it computed from them.

    Dropped: proposals of fewer than ``MIN_RUNS`` runs, and those with the same runs as an
    earlier proposal or a saved group. A filter the query language refuses falls back to the
    hand list. Ids are made unique slugs; names unique among proposals and saved groups."""
    rows = {run.id: run for run in ctx.rows}
    by_name = Counter(run.name for run in ctx.rows)
    named = {run.name: run.id for run in ctx.rows if by_name[run.name] == 1}
    pool = ctx.kinds
    held = {k: kind for k, kind in pool.items() if kind in ("unique", "repeat")}
    metrics = set(ctx.metrics)
    project_best = project_best or ctx.best

    seen_runs = {frozenset(group.runs) for group in ctx.saved}
    taken_names = {group.name.lower() for group in ctx.saved}
    taken_ids: set[str] = set()
    out: list[dict[str, Any]] = []
    raw = reply.get("proposals")
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or len(out) >= MAX_PROPOSALS:
            continue
        filter = _parse_filter(item.get("filter"))
        ids: list[str] | None = None
        if filter is not None:
            matched = await match(filter)
            if matched is None:
                filter = None  # refused: the hand list is all there is
            else:
                ids = [run.id for run in ctx.rows if run.id in matched]
        if ids is None:
            listed = item.get("runs") if isinstance(item.get("runs"), list) else []
            wanted = {
                rid if rid in rows else named.get(rid) for rid in listed if isinstance(rid, str)
            }
            ids = [run.id for run in ctx.rows if run.id in wanted]
        if len(ids) < MIN_RUNS or frozenset(ids) in seen_runs:
            continue
        seen_runs.add(frozenset(ids))
        members = [rows[rid] for rid in ids]

        found_kinds = analysis.classify_columns(members, held)
        varies = [k for k, kind in found_kinds.items() if kind == "varies"]
        asked = [k for k in item.get("axes") or [] if isinstance(k, str)]
        asked = [k if k in found_kinds else f"config.{k}" for k in asked]
        axes = list(dict.fromkeys([k for k in asked if k in varies] + varies))
        values = {k: _sorted(run.config.get(k) for run in members) for k in axes}

        key, order = project_best
        model_key = item.get("best_key")
        if isinstance(model_key, str) and not model_key.startswith("summary."):
            model_key = f"summary.{model_key}"
        if (
            isinstance(model_key, str)
            and model_key in metrics
            and any(analysis._finite(run.summary.get(model_key)) is not None for run in members)
        ):
            model_order = item.get("best_order")
            key = model_key
            order = model_order if model_order in ("asc", "desc") else "asc"
        best = None
        if key and order:
            found = analysis.best(members, key, order, found_kinds)
            if found.run_id is not None:
                best = {"run": found.run_id, "name": rows[found.run_id].name,
                        "value": found.run_value, "mean": found.value, "std": found.std,
                        "n": found.n}  # fmt: skip

        name = _name(item.get("name")) or analysis.group_name(members, found_kinds, filter)
        name = _unique(name, taken_names)
        pid = _slug(item.get("id") if isinstance(item.get("id"), str) and item["id"] else name)
        base, i = pid, 2
        while pid in taken_ids:
            pid = f"{base}-{i}"
            i += 1
        taken_ids.add(pid)
        why = " ".join(str(item.get("why") or "").split())[:300]
        own_best = (key, order) != project_best
        out.append(
            {
                "id": pid,
                "name": name,
                "why": why,
                "runs": ids,
                "filter": filter,
                "axes": axes,
                "values": values,
                "grid": grid(members, axes, found_kinds, (key, order)),
                "best_key": key,
                "best_order": order if key else None,
                "best": best,
                "repeat_key": _repeat_key(members, found_kinds),
                # The body of ``POST .../groups`` that saves it as shown (D216, D217).
                "create": {
                    "name": name,
                    "runs": ids,
                    "filter": filter,
                    "best_key": key if own_best else None,
                    "best_order": order if own_best and key else None,
                },
            }
        )

    covered = {rid for proposal in out for rid in proposal["runs"]}
    covered |= {rid for group in ctx.saved for rid in group.runs}
    message = " ".join(str(reply.get("message") or "").split())
    return Turn(
        message=message,
        proposals=out,
        ungrouped=sum(1 for run in ctx.rows if run.id not in covered),
        runs=len(ctx.rows),
    )


async def propose(
    ctx: Context,
    messages: Sequence[Message],
    shown: Sequence[Shown] | None,
    match: Matcher,
    client: ResponsesClient,
) -> Turn:
    """One turn: the model's proposals for the project and the conversation, checked."""
    reply = await client.respond(
        INSTRUCTIONS, build_input(ctx, messages, shown), "group_proposals", SCHEMA
    )
    return await finish(ctx, reply, match)
