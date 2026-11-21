"""`flock datasets` (D122): import Hugging Face datasets to Flock, and list and move what's
there."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import click

from flock.hub import datasets
from flock.hub.datasets._convert import MODES, ConvertedSplit, Mode, human
from flock.hub.s3 import S3Error

CONFIG_HELP = (
    "A config to import (repeat for more; default: the only one, or the one called default)."
)
SPLIT_HELP = "A split to import from each config that has it (repeat for more; default: all)."
REVISION_HELP = "A branch, tag or commit of the repo (default: main)."
WORKERS_HELP = "Processes converting files at once (default: the CPUs this job may use)."
MODE_HELP = "create refuses an existing dataset, overwrite replaces its rows, append adds them."
ORG_HELP = "The org whose flock-datasets to write to (default: your default org)."
PROJECT_HELP = (
    "The project the datasets belong to, by name or as org/project (default: the org's global "
    "datasets). Paths are then inside it."
)


@contextmanager
def _failing() -> Generator[None]:
    """Refusals and failures as the command's error (exit code 1)."""
    try:
        yield
    except S3Error as error:
        raise click.ClickException(str(error)) from None


def _report(converted: list[ConvertedSplit]) -> None:
    for split in converted:
        click.echo(
            f"✓ {split.config}/{split.split} at {split.path}: version {split.version}, "
            f"{split.rows:,} rows"
        )


@click.group("datasets")
def datasets_group() -> None:
    """Import Hugging Face datasets to Flock, and list Flock's datasets."""


@datasets_group.command("import")
@click.argument("repo")
@click.argument("dest")
@click.option("--config", "configs", multiple=True, help=CONFIG_HELP)
@click.option("--split", "splits", multiple=True, help=SPLIT_HELP)
@click.option("--revision", help=REVISION_HELP)
@click.option("--workers", type=click.IntRange(min=1), help=WORKERS_HELP)
@click.option(
    "--mode", type=click.Choice(MODES), default="create", show_default=True, help=MODE_HELP
)
@click.option("--org", help=ORG_HELP)
@click.option("--project", help=PROJECT_HELP)
@click.option(
    "--keep",
    type=click.Path(file_okay=False, path_type=Path),
    help="Download to this folder and keep it (default: a temporary folder).",
)
def import_command(
    repo: str,
    dest: str,
    configs: tuple[str, ...],
    splits: tuple[str, ...],
    revision: str | None,
    workers: int | None,
    mode: Mode,
    org: str | None,
    project: str | None,
    keep: Path | None,
) -> None:
    """Import the Hugging Face dataset REPO to DEST, a folder of flock-datasets.

    Each split becomes a Lance dataset at DEST/SPLIT, or at DEST/CONFIG/SPLIT when the repo
    has several configs. This downloads and converts in one go, so
    it needs both the Hub and Flock. On a cluster whose compute nodes have no internet, run
    flock datasets download on a login node and flock datasets convert on a compute node.
    """
    with _failing():
        converted = datasets.import_from_hf(
            repo,
            dest,
            config=configs or None,
            splits=splits or None,
            revision=revision,
            to=keep,
            org=org,
            workers=workers,
            mode=mode,
            project=project,
        )
    _report(converted)


@datasets_group.command("download")
@click.argument("repo")
@click.option(
    "--to",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="The folder to download to.",
)
@click.option("--config", "configs", multiple=True, help=CONFIG_HELP)
@click.option("--split", "splits", multiple=True, help=SPLIT_HELP)
@click.option("--revision", help=REVISION_HELP)
def download_command(
    repo: str, to: Path, configs: tuple[str, ...], splits: tuple[str, ...], revision: str | None
) -> None:
    """Download the Hugging Face dataset REPO's files, for flock datasets convert.

    This only needs the Hub, so it runs on a login node without a Flock sign-in. Running it
    again resumes the download.
    """
    with _failing():
        folder = datasets.download_from_hf(
            repo, to, config=configs or None, splits=splits or None, revision=revision
        )
        manifest = datasets.Manifest.read(folder)
    for config, found in manifest.splits.items():
        for split, files in found.items():
            size = sum((folder / f.path).stat().st_size for f in files)
            click.echo(f"{config}/{split}\t{len(files)} files, {human(size)}")
    click.echo(f"✓ Downloaded {repo} ({', '.join(manifest.splits)}) to {folder}")
    click.echo(f"  Convert it with: flock datasets convert {folder} DEST")


@datasets_group.command("convert")
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.argument("dest")
@click.option("--workers", type=click.IntRange(min=1), help=WORKERS_HELP)
@click.option(
    "--mode", type=click.Choice(MODES), default="create", show_default=True, help=MODE_HELP
)
@click.option("--org", help=ORG_HELP)
@click.option("--project", help=PROJECT_HELP)
def convert_command(
    folder: Path,
    dest: str,
    workers: int | None,
    mode: Mode,
    org: str | None,
    project: str | None,
) -> None:
    """Convert FOLDER, which flock datasets download filled, to Lance datasets in DEST.

    This only needs Flock, so it runs on a compute node without internet.
    """
    with _failing():
        converted = datasets.convert(
            folder, dest, org=org, workers=workers, mode=mode, project=project
        )
    _report(converted)


@datasets_group.command("list")
@click.argument("path", default="")
@click.option("--org", help="The org (default: your default org).")
@click.option("--project", help=PROJECT_HELP)
def list_command(path: str, org: str | None, project: str | None) -> None:
    """List the datasets and folders at PATH in flock-datasets (default: the root), or in a
    project's datasets with --project."""
    with _failing():
        try:
            found = datasets.list_datasets(path, project=project, org=org)
        except ValueError as error:
            raise click.BadParameter(str(error)) from None
    if found["kind"] == "dataset":
        click.echo(f"{found['path']} is a dataset, at version {found['version']}.")
        return
    if not (found["folders"] or found["datasets"]):
        scope = f"{project}'s datasets" if project is not None else "flock-datasets"
        where = f"{path.strip('/')} has" if path.strip("/") else f"{scope} has"
        click.echo(f"{where} no datasets yet. Import one with: flock datasets import REPO DEST")
        return
    for folder in found["folders"]:
        click.echo(f"{folder['path']}/")
    for dataset in found["datasets"]:
        click.echo(f"{dataset['path']}\tversion {dataset['version']}")


@datasets_group.command("move")
@click.argument("paths", nargs=-1, required=True)
@click.argument("to")
@click.option("--project", help="The project the datasets belong to now (default: global).")
@click.option("--to-project", help="Move them into this project of the org.")
@click.option("--to-global", is_flag=True, help="Move them to the org's global datasets.")
@click.option("--org", help="The org (default: your default org).")
def move_command(
    paths: tuple[str, ...],
    to: str,
    project: str | None,
    to_project: str | None,
    to_global: bool,
    org: str | None,
) -> None:
    """Move the datasets and folders PATHS into the folder TO, each keeping its last name.

    TO is a folder in the scope they end up in ("" for its root). With --to-project or
    --to-global they change scope, which copies their objects. Above the size limits nothing
    moves, and the aws s3 mv commands to run instead are printed.

    \b
    flock datasets move eval/mmlu-filtered eval --project pretrain --to-global
    """
    if to_project is not None and to_global:
        raise click.UsageError("give --to-project or --to-global, not both")
    try:
        results = datasets.move(
            paths, to, to_project=to_project, to_global=to_global, project=project, org=org
        )
    except S3Error as error:
        command = error.body.get("command") if isinstance(error.body, dict) else None
        message = f"{error}\n{command}" if command else str(error)
        raise click.ClickException(message) from None
    except ValueError as error:
        raise click.BadParameter(str(error)) from None
    failed = False
    for result in results:
        if result["moved"]:
            click.echo(f"✓ Moved {result['path']} to {result['to']}")
        else:
            failed = True
            click.echo(f"✗ {result['path']}: {result['error']}", err=True)
    if failed:
        raise SystemExit(1)
