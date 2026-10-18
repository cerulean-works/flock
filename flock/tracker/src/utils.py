import base64
import json
from typing import Any

import orjson


def loads_lenient(raw: str | bytes) -> Any:
    """Parse JSON, accepting the ``NaN``/``Infinity`` literals wandb writes for non-finite floats."""
    try:
        return orjson.loads(raw)
    except orjson.JSONDecodeError:
        return json.loads(raw)


def flatten(data: dict, prefix: str = "", out: dict | None = None) -> dict:
    """Flatten nested dicts into dot-separated keys.

    Dicts carrying a ``_type`` key are wandb media/rich values (images, histograms, tables) and
    are kept whole rather than flattened.
    """
    if out is None:
        out = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and "_type" not in value and value:
            flatten(value, f"{name}.", out)
        else:
            out[name] = value
    return out


def encode_id(*parts: str) -> str:
    """A wandb-style GraphQL ID, ``base64("<Type>:<part>:...")``."""
    return base64.b64encode(":".join(parts).encode()).decode()
