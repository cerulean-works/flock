"""GPUs of a run, read from the ``wandb-metadata.json`` the wandb SDK uploads, and the UTC
months its usage is accounted in."""

import datetime as dt
import re
from typing import Any

# Accelerator models, as they appear inside vendor device names ("NVIDIA H100 80GB HBM3").
# Alternatives are tried left to right at each position, so the more specific come first.
MODELS = re.compile(
    r"\b("
    r"RTX\s?A\d{4}"  # RTX A6000
    r"|RTX\s?(?:PRO\s)?\d{4}(?:\s?Ti)?"  # RTX 4090, RTX PRO 6000
    r"|G[BH]\d{3}"  # GB200, GH200
    r"|[BH]\d{2,3}[A-Z]?"  # B200, H100, H200, H20
    r"|A\d{2,3}G?"  # A100, A800, A40, A10G
    r"|L\d{1,2}S?"  # L4, L40S
    r"|[VPT]100|P40|T4|K80"
    r"|MI\d{2,3}[A-Z]?"  # MI300X
    r")\b",
    re.IGNORECASE,
)
VENDORS = re.compile(
    r"^(?:(?:NVIDIA|AMD|Tesla|GeForce|Radeon|Instinct|Google|AWS|Intel)\b[\s-]*)+", re.IGNORECASE
)
MEMORY = re.compile(r"[\s-]+\d+\s?GB\b.*$", re.IGNORECASE)
MAX_TYPE_LENGTH = 64


def normalize(name: str) -> str | None:
    """A device name reduced to the key prices are set by: "NVIDIA A100-SXM4-80GB" is
    ``A100``. Unknown models lose their vendor prefix and memory size, nothing else."""
    name = " ".join(str(name).split())
    match = MODELS.search(name)
    if match:
        return " ".join(match.group(0).split()).upper().replace("TI", "Ti")
    name = MEMORY.sub("", VENDORS.sub("", name)).strip()
    return name[:MAX_TYPE_LENGTH] or None


def _count(value: Any) -> int:
    try:
        return max(int(value), 0)
    except TypeError, ValueError:
        return 0


def detect(metadata: Any) -> tuple[str | None, int]:
    """The (type, count) of a run's accelerators from its wandb metadata; (None, 0) if it
    has none (or the metadata can't tell)."""
    if not isinstance(metadata, dict):
        return None, 0

    def first(key: str) -> dict:
        items = metadata.get(key)
        return items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}

    nvidia = metadata.get("gpu_nvidia")
    nvidia = nvidia if isinstance(nvidia, list) else []
    name = metadata.get("gpu") or first("gpu_nvidia").get("name")
    count = _count(metadata.get("gpu_count")) or len(nvidia)
    if name or count:
        return (normalize(name) if name else None), count or 1
    amd = metadata.get("gpu_amd")
    if isinstance(amd, list) and amd:
        name = first("gpu_amd").get("series") or first("gpu_amd").get("model")
        return (normalize(name) if name else None), len(amd)
    for key, count_key in (("tpu", "count"), ("trainium", "neuronDeviceCount")):
        device = metadata.get(key)
        if isinstance(device, dict) and (device.get("name") or device.get(count_key)):
            name = device.get("name")
            label = normalize(name) if name else key.upper()
            if key == "tpu" and label and not label.upper().startswith("TPU"):
                label = f"TPU {label}"
            return label, _count(device.get(count_key)) or 1
    return None, 0


def month_of(timestamp: float) -> dt.date:
    """The first day of the UTC month ``timestamp`` falls in."""
    day = dt.datetime.fromtimestamp(timestamp, dt.UTC)
    return dt.date(day.year, day.month, 1)


def next_month(month: dt.date) -> dt.date:
    return dt.date(month.year + month.month // 12, month.month % 12 + 1, 1)


def month_start(month: dt.date) -> float:
    return dt.datetime(month.year, month.month, 1, tzinfo=dt.UTC).timestamp()


def split_by_month(start: float, end: float) -> list[tuple[dt.date, float]]:
    """The seconds from ``start`` to ``end`` that fall in each UTC month."""
    parts = []
    month = month_of(start)
    while start < end:
        boundary = month_start(next_month(month))
        stop = min(end, boundary)
        parts.append((month, stop - start))
        start, month = stop, next_month(month)
    return parts


def parse_month(value: str) -> dt.date:
    """``YYYY-MM`` as the first day of that month (ValueError otherwise)."""
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("must be a month as YYYY-MM")
    return dt.date(int(value[:4]), int(value[5:]), 1)


def format_month(month: dt.date) -> str:
    return f"{month.year:04d}-{month.month:02d}"
