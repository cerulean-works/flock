"""Copy run files from the local files directory into the s3 files backend's bucket (012, D82).

Files already in the bucket are skipped, so it resumes where it stopped and can be run again
at any time. It is safe while servers run with ``FLOCK_FILES_BACKEND=s3``: they read files
missing from the bucket from the directory until it has copied them. With ``--remove``, each
file is deleted from the directory once copied.

It reads the same ``FLOCK_*`` settings as the server (the S3 upstream, ``FLOCK_FILES_DIR`` and
``FLOCK_FILES_BUCKET``):

    cd app/api
    FLOCK_FILES_BUCKET=flock-files uv run --package flock-app-api \\
        python -m app.api.scripts.migrate_files
"""

import argparse
import asyncio
from collections.abc import Callable
from pathlib import Path

from app.api.src.core.files import S3Files
from app.api.src.settings import Settings


async def migrate_files(
    settings: Settings, remove: bool = False, report: Callable[[str], None] = print
) -> tuple[int, int]:
    """Copy every run file of ``settings.files_dir`` into the bucket; returns how many were
    copied and how many were there already."""
    files = S3Files(settings)
    root = settings.files_dir
    copied = skipped = 0
    try:
        await files.open()
        runs = sorted(int(p.name) for p in root.iterdir() if p.name.isdigit())
        for run_pk in runs:
            run_dir = root / str(run_pk)
            paths = await asyncio.to_thread(
                lambda run_dir=run_dir: sorted(p for p in run_dir.rglob("*") if p.is_file())
            )
            for path in paths:
                name = path.relative_to(run_dir).as_posix()
                if name.endswith(".tmp"):
                    continue  # a write that never finished
                key = files.key(run_pk, name)
                if await files.upstream.head(files.bucket, key) is not None:
                    skipped += 1
                else:
                    await files.put(run_pk, name, await asyncio.to_thread(path.read_bytes))
                    copied += 1
                if remove:
                    await asyncio.to_thread(path.unlink)
            if remove:
                await asyncio.to_thread(_remove_empty, run_dir)
            report(f"  run {run_pk}: {len(paths)} files")
    finally:
        await files.close()
    return copied, skipped


def _remove_empty(directory: Path) -> None:
    for path in sorted(directory.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.partition("\n\n")[0])
    parser.add_argument(
        "--remove", action="store_true", help="delete each file from the directory once copied"
    )
    args = parser.parse_args()
    copied, skipped = asyncio.run(migrate_files(Settings.load(), remove=args.remove))
    print(f"copied {copied} files ({skipped} were in the bucket already)")


if __name__ == "__main__":
    main()
