import torch


def peak_flops_per_second(device: torch.device, dtype: torch.dtype) -> float | None:
    """Return a GPU's dense 16-bit tensor-core peak, or `None` when MFU has no peak to use.

    Values are torchtitan's (`torchtitan/tools/utils.py`, v0.3.0), per GPU and without
    sparsity. There is no peak for the CPU, for FP32 training or for an unlisted GPU.

    Args:
        device (`torch.device`): Training device.
        dtype (`torch.dtype`): Autocast dtype.

    Returns:
        `float` or `None`: Peak FLOPs per second.
    """
    if device.type != "cuda" or dtype not in (torch.bfloat16, torch.float16):
        return None
    name = torch.cuda.get_device_name(device)
    # "GB200" contains "B200", so the superchips come first.
    if "GB200" in name or "GB300" in name:
        return 2.5e15
    if "B200" in name or "B300" in name:
        return 2.25e15
    if "H200" in name:
        return 989e12
    if "H100" in name:
        if "NVL" in name:
            return 835e12
        if "PCIe" in name:
            return 756e12
        return 989e12
    if "A100" in name:
        return 312e12
    return None
