"""Metric definitions (``wandb.define_metric``) as the SDK records them in a run's config.

wandb-core keeps them in ``config["_wandb"]["value"]["m"]``: a list of ``MetricRecord`` protos
encoded with field numbers as keys (``core/internal/runmetric/runconfigmetrics.go``,
``corelib.ProtoEncodeToDict``). For example, Hugging Face's ``WandbCallback`` produces::

    [{"1": "train/global_step", "6": [3], "7": []},
     {"2": "*", "5": 1, "6": [1], "7": []}]

- ``"1"`` is the metric's name, or ``"2"`` a glob: a name ending in ``*``, which matches every
  key starting with what comes before it.
- ``"5"`` is the 1-based index, in that list, of the metric's step metric (its x axis). The
  step metric itself gets an entry of its own, possibly an empty one.
- ``"6"`` lists the ``MetricOptions`` flags that are set: 1 ``step_sync``, 2 ``hidden``,
  3 ``defined`` (explicit, rather than expanded from a glob).
- ``"7"`` (summary flags) and ``"8"`` (goal) don't concern plotting.

Older SDKs that write ``"4"`` (the step metric's name) instead of an index are read too.
"""

from typing import Any

HIDDEN = 2


def parse_metric_defs(config: Any) -> list[dict[str, Any]]:
    """The metric definitions in a wandb config, as far as plotting goes, in the SDK's order.

    Each is ``{"key": name}`` or ``{"glob": "prefix*"}``, with ``"step"`` (the step metric's
    key) and ``"hidden": True`` when set. A definition of a key takes precedence over globs
    matching it, so definitions with neither are kept too. Malformed entries are left out.
    """
    if not isinstance(config, dict):
        return []
    wandb = config.get("_wandb")
    if isinstance(wandb, dict) and "value" in wandb:
        wandb = wandb["value"]
    records = wandb.get("m") if isinstance(wandb, dict) else None
    if not isinstance(records, list):
        return []
    names = [r.get("1") or r.get("2") if isinstance(r, dict) else None for r in records]
    out: list[dict[str, Any]] = []
    for record, name in zip(records, names, strict=True):
        if not isinstance(name, str) or not name:
            continue
        glob = not record.get("1")
        if glob and not name.endswith("*"):
            continue
        step = record.get("4")
        index = record.get("5")
        if isinstance(index, int) and not isinstance(index, bool) and 0 < index <= len(names):
            step = names[index - 1]
        options = record.get("6")
        hidden = isinstance(options, list) and HIDDEN in options
        item: dict[str, Any] = {"glob" if glob else "key": name}
        # A metric plotted against itself would be a diagonal line: wandb's default then.
        if isinstance(step, str) and step and step != name:
            item["step"] = step
        if hidden:
            item["hidden"] = True
        out.append(item)
    return out


def step_metrics(defs: list[dict[str, Any]]) -> set[str]:
    """Keys used as a step metric by these definitions."""
    return {d["step"] for d in defs if "step" in d}
