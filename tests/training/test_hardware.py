import pytest
import torch

from flock.training.core.hardware import get_device_peak_flops


@pytest.mark.parametrize(
    ("name", "peak"),
    [
        ("NVIDIA GB200", 2.5e15),
        ("NVIDIA B200", 2.25e15),
        ("NVIDIA H200", 989e12),
        ("NVIDIA H100 80GB HBM3", 989e12),
        ("NVIDIA H100 PCIe", 756e12),
        ("NVIDIA A100-SXM4-80GB", 312e12),
        ("NVIDIA L4", None),
    ],
)
def test_peak_flops_by_gpu(monkeypatch: pytest.MonkeyPatch, name: str, peak: float | None) -> None:
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda device: name)
    assert get_device_peak_flops(torch.device("cuda", 0), torch.bfloat16) == peak


def test_no_peak_for_cpu_or_fp32(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda device: "NVIDIA H200")
    assert get_device_peak_flops(torch.device("cpu"), torch.bfloat16) is None
    assert get_device_peak_flops(torch.device("cuda", 0), torch.float32) is None
