"""Reading two eval runs' files for an item comparison (025, D185)."""

import asyncio
from dataclasses import dataclass

from app.api.src.core.store import Run, Store

# Base Easy's biggest file is a few MiB, and a pair reads about 80 MiB in 222 files, so a
# comparison beyond these is refused rather than held or worked through.
FILE_BYTES = 64 << 20
PAIR_BYTES = 1 << 30
PAIR_FILES = 2000


@dataclass
class PairFiles:
    """The files of the two runs one comparison reads, within one budget: a file over 64 MiB,
    more than 1 GiB in all, or more than 2,000 lookups, found or not, raise ``ValueError``.
    ``read`` is called from the diff's own thread and reads on ``loop``."""

    store: Store
    loop: asyncio.AbstractEventLoop
    left: int = PAIR_BYTES
    lookups: int = PAIR_FILES

    def read(self, run: Run, name: str, readable: bool = True) -> str | None:
        """The run's file ``name`` as text, or None when the run has no such file or the caller
        may not read it (``readable`` false), which counts as a lookup all the same."""
        self.lookups -= 1
        if self.lookups < 0:
            raise ValueError("the runs have more files than one comparison reads")
        if not readable:
            return None
        read = self.store.read_file(run, name, max_bytes=min(FILE_BYTES, self.left))
        data = asyncio.run_coroutine_threadsafe(read, self.loop).result()
        if data is None:
            return None
        self.left -= len(data)
        return data.decode()
