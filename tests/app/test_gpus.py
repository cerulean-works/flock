import datetime as dt

import pytest

from app.api.src.core import gpus


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("NVIDIA H100 80GB HBM3", "H100"),
        ("NVIDIA H200", "H200"),
        ("NVIDIA A100-SXM4-80GB", "A100"),
        ("NVIDIA A10G", "A10G"),
        ("Tesla V100-SXM2-16GB", "V100"),
        ("Tesla T4", "T4"),
        ("NVIDIA L40S", "L40S"),
        ("NVIDIA B200", "B200"),
        ("NVIDIA GH200 480GB", "GH200"),
        ("NVIDIA GeForce RTX 4090", "RTX 4090"),
        ("NVIDIA GeForce RTX 3080 Ti", "RTX 3080 Ti"),
        ("NVIDIA RTX A6000", "RTX A6000"),
        ("AMD Instinct MI300X", "MI300X"),
        ("NVIDIA Quadro Mystery 48GB", "Quadro Mystery"),
        ("Apple M3 Max", "Apple M3 Max"),
        ("NVIDIA", None),
    ],
)
def test_normalize(name, expected):
    assert gpus.normalize(name) == expected


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"gpu": "NVIDIA H100 80GB HBM3", "gpu_count": 8, "cudaVersion": "12.4"}, ("H100", 8)),
        ({"gpu_nvidia": [{"name": "NVIDIA A100-SXM4-40GB"}] * 4}, ("A100", 4)),
        ({"gpu": "NVIDIA L4"}, ("L4", 1)),
        ({"gpu_count": "2"}, (None, 2)),
        ({"gpu_amd": [{"series": "AMD Instinct MI300X", "model": "x"}] * 8}, ("MI300X", 8)),
        ({"tpu": {"name": "v5e", "count": 4}}, ("TPU v5e", 4)),
        ({"trainium": {"name": "Trainium2", "neuronDeviceCount": 16}}, ("Trainium2", 16)),
        ({"cpu_count": 8, "apple": {"name": "Apple M3"}}, (None, 0)),
        ({}, (None, 0)),
        ([1, 2], (None, 0)),
    ],
)
def test_detect(metadata, expected):
    assert gpus.detect(metadata) == expected


def at(*args) -> float:
    return dt.datetime(*args, tzinfo=dt.UTC).timestamp()


def test_split_by_month():
    sep, oct_, nov = dt.date(2026, 9, 1), dt.date(2026, 10, 1), dt.date(2026, 11, 1)
    assert gpus.split_by_month(at(2026, 10, 5), at(2026, 10, 5, 1)) == [(oct_, 3600.0)]
    assert gpus.split_by_month(at(2026, 9, 30, 23), at(2026, 11, 1, 1)) == [
        (sep, 3600.0),
        (oct_, 31 * 86400.0),
        (nov, 3600.0),
    ]
    assert gpus.split_by_month(at(2026, 12, 31, 23), at(2027, 1, 1, 1)) == [
        (dt.date(2026, 12, 1), 3600.0),
        (dt.date(2027, 1, 1), 3600.0),
    ]
    assert gpus.split_by_month(5.0, 5.0) == []


def test_months():
    assert gpus.parse_month("2026-02") == dt.date(2026, 2, 1)
    assert gpus.format_month(dt.date(2026, 2, 1)) == "2026-02"
    for bad in ("2026-2", "2026-13", "feb"):
        with pytest.raises(ValueError):
            gpus.parse_month(bad)
