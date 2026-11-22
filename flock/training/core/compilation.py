import functools
from collections.abc import Callable
from typing import Any, Concatenate, Protocol

import torch


class CompileSettings(Protocol):
    """Describe the instance attributes that `compile_if_enabled` reads."""

    @property
    def torch_compile(self) -> bool:
        """Whether decorated methods run compiled."""
        ...

    @property
    def torch_compile_mode(self) -> str:
        """The `torch.compile` mode for decorated methods."""
        ...

    @property
    def _compiled_methods(self) -> dict[Callable[..., Any], Callable[..., Any]]:
        """Each decorated method's compiled function, filled on first call."""
        ...


def compile_if_enabled[T: CompileSettings, **P, R](
    method: Callable[Concatenate[T, P], R],
) -> Callable[Concatenate[T, P], R]:
    """Run `method` through `torch.compile` when its instance sets `torch_compile`.

    The method compiles on its first call with the instance's `torch_compile_mode`,
    and later calls reuse the compiled function. Without `torch_compile`, the method
    runs eagerly.
    """

    @functools.wraps(method)
    def wrapper(self: T, *args: P.args, **kwargs: P.kwargs) -> R:
        if not self.torch_compile:
            return method(self, *args, **kwargs)
        compiled = self._compiled_methods.get(method)
        if compiled is None:
            compiled = torch.compile(method, mode=self.torch_compile_mode)
            self._compiled_methods[method] = compiled
        return compiled(self, *args, **kwargs)

    return wrapper
