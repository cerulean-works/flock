"""Forks and blends from Python (023, D180): thin calls to the API, which writes every manifest
(D172)."""

import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any, Self

from flock.hub.datasets._common import DatasetError

DEFAULT_SEED = 42
"""The seed a blend uses when none is given, as the UI fills in."""

Columns = Mapping[str, Mapping[str, Any]] | Sequence[Mapping[str, Any]]


def value(x: Any) -> dict[str, Any]:
    """In a blend's `columns`: fill the output column with `x` for this source."""
    return {"value": x}


def null() -> dict[str, Any]:
    """In a blend's `columns`: leave the output column null for this source."""
    return {"null": True}


def _api_call(org: str | None, method: str, route: str, body: Any = None) -> Any:
    with _Session(org) as session:
        return session.call(method, route, body)


class _Session:
    """One signed-in API client for an org's dataset routes, raising `DatasetError`."""

    def __init__(self, org: str | None) -> None:
        self.org = org
        self.api: Any = None

    def __enter__(self) -> Self:
        from flock.hub.s3 import S3Error
        from flock.hub.s3.client import _Api

        try:
            self.api = _Api(None, None)
            self.org = self.api.default_org(self.org)
        except S3Error as error:
            if self.api is not None:
                self.api.client.close()
            raise DatasetError(str(error), error.status, error.body) from None
        return self

    def __exit__(self, *_: object) -> None:
        self.api.client.close()

    def call(self, method: str, route: str, body: Any = None) -> Any:
        from flock.hub.s3 import S3Error

        try:
            return self.api.call(
                method, f"orgs/{self.org}/datasets/{route}", body, ok=(200, 201, 202)
            )
        except S3Error as error:
            message = str(error)
            if isinstance(error.body, dict) and error.body.get("command"):
                message += f"\nRun instead: {error.body['command']}"
            raise DatasetError(message, error.status, error.body) from None

    def wait(self, job: dict[str, Any]) -> dict[str, Any]:
        """Polls the job until it ends, printing its progress to stderr; its result, or
        `DatasetError` when it failed or was cancelled."""
        shown = ""
        while job["state"] == "running":
            line = f"{job['kind']} {job['dest']}: {job.get('phase') or 'starting'}"
            if job.get("rows_total"):
                line += f", {job['rows_done']:,}/{job['rows_total']:,} rows"
            if line != shown:
                print(line, file=sys.stderr, flush=True)
                shown = line
            time.sleep(2)
            job = self.call("GET", f"jobs/{job['id']}")
        if job["state"] != "finished":
            reason = job.get("error") or job["state"]
            raise DatasetError(f"The {job['kind']} of {job['dest']} {job['state']}: {reason}")
        return job["result"]


def _source(spec: str, amount: float) -> dict[str, Any]:
    path, _, version = spec.partition("@")
    out: dict[str, Any] = {"path": path.strip("/")}
    if version:
        out["version"] = int(version) if version.isdigit() else version
    if isinstance(amount, bool) or not isinstance(amount, int | float):
        raise DatasetError(f"{spec}: give a row count (int) or a fraction (float).")
    out["rows" if isinstance(amount, int) else "fraction"] = amount
    return out


def _body(
    sources: Mapping[str, float],
    to: str | None,
    total: int | None,
    seed: int,
    replace: bool,
    columns: Any,
) -> dict[str, Any]:
    return {
        "sources": [_source(spec, amount) for spec, amount in sources.items()],
        "total": total,
        "seed": seed,
        "replace": replace,
        "to": to,
        "columns": columns,
    }


def _resolve_columns(session: _Session, body: dict[str, Any], columns: Columns | None) -> Any:
    """A full map passes through; a mapping changes the automatic map's outputs it names, and
    adds the ones it doesn't have (typed as the first source column they take, and taking a
    same-named column, else null, from sources it doesn't name). An automatic output whose
    columns were all renamed into others is dropped."""
    if columns is None or not isinstance(columns, Mapping):
        return [dict(c) for c in columns] if columns is not None else None
    preview = session.call("POST", "blend/preview", {**body, "columns": None})
    out: list[dict[str, Any]] = [dict(c) for c in preview["columns"]]
    by_name = {c["name"]: c for c in out}
    paths = {s["path"]: s for s in preview["sources"]}
    has = {p: {f["name"] for f in s["schema"]} for p, s in paths.items()}
    taken: set[tuple[str, str]] = set()
    for name, froms in columns.items():
        specs = {
            path.partition("@")[0].strip("/"): {"column": spec} if isinstance(spec, str) else spec
            for path, spec in froms.items()
        }
        unknown = set(specs) - set(paths)
        if unknown:
            raise DatasetError(f"{name}: {', '.join(sorted(unknown))} isn't a source.")
        taken |= {
            (p, s["column"]) for p, s in specs.items() if "column" in s and s["column"] != name
        }
        column = by_name.get(name)
        if column is None:
            unnamed = {
                p: {"column": name} if name in has[p] else {"null": True}
                for p in paths
                if p not in specs
            }
            column: dict[str, Any] = {"name": name, "type": "", "nullable": True, "from": unnamed}
            out.append(column)
            by_name[name] = column
        column["from"] = {**column.get("from", {}), **specs}
        if not column["type"]:
            column["type"] = next(
                (
                    f["type"]
                    for path, spec in column["from"].items()
                    if "column" in spec
                    for f in paths[path]["schema"]
                    if f["name"] == spec["column"]
                ),
                "string",
            )

    def renamed_away(column: dict[str, Any]) -> bool:
        used = [(p, s["column"]) for p, s in column["from"].items() if "column" in s]
        rest = [s for s in column["from"].values() if "column" not in s]
        return bool(used) and set(used) <= taken and all(s.get("null") for s in rest)

    return [c for c in out if c["name"] in columns or not renamed_away(c)]


def fork(
    path: str, to: str, *, version: str | int | None = None, org: str | None = None
) -> dict[str, Any]:
    """
    Forks a dataset of `flock-datasets`: a new dataset at `to` that reads `path`'s files in
    place, with no data copied, and that you edit without touching `path` (023, D171). The
    version forked is kept until the fork no longer needs it.

    Args:
        path (`str`):
            The dataset to fork, such as `web/fineweb-edu/train`, or `project:path` for a
            project's dataset (022).
        to (`str`):
            Where the fork goes, as a path or `project:path`, so a global dataset can be forked
            into a project. Refused when something is there.
        version (`str` or `int`, *optional*):
            A version number or tag. Defaults to the latest.
        org (`str`, *optional*):
            The org. Defaults to your default org.

    Returns:
        `dict[str, Any]`: the fork's `path` and `version`, and the `source` it was made from.

    Raises:
        [`~hub.datasets.DatasetError`]: without a sign-in, or when Flock refuses.
    """
    body = {"path": path.strip("/"), "to": to.strip("/"), "version": version}
    return _api_call(org, "POST", "fork", body)


def blend_preview(
    sources: Mapping[str, float],
    to: str | None = None,
    *,
    total: int | None = None,
    seed: int = DEFAULT_SEED,
    columns: Columns | None = None,
    replace: bool = False,
    org: str | None = None,
) -> dict[str, Any]:
    """
    What [`blend`] would make, writing nothing: the rows taken from each source, the column
    map, the source columns no output uses, and every `problems` that would refuse it.
    Takes [`blend`]'s arguments.
    """
    with _Session(org) as session:
        body = _body(sources, to, total, seed, replace, None)
        body["columns"] = _resolve_columns(session, body, columns)
        return session.call("POST", "blend/preview", body)


def blend(
    sources: Mapping[str, float],
    to: str,
    *,
    total: int | None = None,
    seed: int = DEFAULT_SEED,
    columns: Columns | None = None,
    replace: bool = False,
    org: str | None = None,
    wait: bool = True,
) -> dict[str, Any]:
    """
    Blends datasets of `flock-datasets` into a new one at `to`, reading the sources' files in
    place (023, D174). The same sources, amounts and seed always pick the same rows.

    ```python
    datasets.blend(
        {"web/fineweb-edu/train": 0.66, "code/stack-v2/train": 0.34},
        "mixes/edu-code",
        total=300_000,
        columns={
            "text": {"code/stack-v2/train": "content"},
            "source": {
                "web/fineweb-edu/train": datasets.value("fineweb-edu"),
                "code/stack-v2/train": datasets.value("stack-v2"),
            },
        },
    )
    ```

    Args:
        sources (`Mapping[str, int | float]`):
            Each source, as `path` or `path@version` (a number or tag), to a row count (`int`)
            or a fraction of `total` (`float`). A project's dataset is `project:path` (022).
        to (`str`):
            Where the blend goes, as a path or `project:path`. Refused when something is there.
        total (`int`, *optional*):
            The blend's rows, when the amounts are fractions.
        seed (`int`, *optional*, defaults to `42`):
            Picks the rows. Another seed gives another sample.
        columns (`Mapping` or `Sequence`, *optional*):
            Changes to the automatic column map (D175): each key is an output column, mapping
            each source to a column name, [`value`] or [`null`]. A column's type changes by
            passing the whole map instead, as a list of `{"name", "type", "nullable", "from"}`
            like [`blend_preview`] returns. Defaults to the automatic map, which matches
            columns by name.
        replace (`bool`, *optional*, defaults to `False`):
            Take a source's rows more than once when it has fewer than asked for.
        org (`str`, *optional*):
            The org. Defaults to your default org.
        wait (`bool`, *optional*, defaults to `True`):
            Wait for the blend, printing its progress to stderr. With `False`, returns the job
            at once.

    Returns:
        `dict[str, Any]`: the blend's `path`, `version` and `rows`, or the job without `wait`.

    Raises:
        [`~hub.datasets.DatasetError`]: naming every problem when it can't be made as asked,
        and when the blend fails.
    """
    with _Session(org) as session:
        body = _body(sources, to.strip("/"), total, seed, replace, None)
        body["columns"] = _resolve_columns(session, body, columns)
        job = session.call("POST", "blend", body)
        return session.wait(job) if wait else job


def make_independent(path: str, *, org: str | None = None, wait: bool = True) -> dict[str, Any]:
    """
    Rewrites a fork or blend into its own files, so its sources can be renamed or deleted
    (023, D178). Its earlier versions are removed.

    Args:
        path (`str`):
            The fork or blend.
        org (`str`, *optional*):
            The org. Defaults to your default org.
        wait (`bool`, *optional*, defaults to `True`):
            Wait for it, printing progress to stderr. With `False`, returns the job at once.

    Returns:
        `dict[str, Any]`: the dataset's `path`, `version` and `rows`, or the job without
        `wait`.

    Raises:
        [`~hub.datasets.DatasetError`]: when Flock refuses (such as over the size limit, with
        the command to run instead) or the rewrite fails.
    """
    with _Session(org) as session:
        job = session.call("POST", "independent", {"path": path.strip("/")})
        return session.wait(job) if wait else job
