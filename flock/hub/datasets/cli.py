"""`flock datasets` (D122): import Hugging Face datasets to Flock, and list what's there."""

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
def convert_command(
    folder: Path, dest: str, workers: int | None, mode: Mode, org: str | None
) -> None:
    """Convert FOLDER, which flock datasets download filled, to Lance datasets in DEST.

    This only needs Flock, so it runs on a compute node without internet.
    """
    with _failing():
        converted = datasets.convert(folder, dest, org=org, workers=workers, mode=mode)
    _report(converted)


@datasets_group.command("list")
@click.argument("path", default="")
@click.option("--org", help="The org (default: your default org).")
def list_command(path: str, org: str | None) -> None:
    """List the datasets and folders at PATH in flock-datasets (default: the root)."""
    with _failing():
        found = datasets.list_datasets(path, org=org)
    if found["kind"] == "dataset":
        click.echo(f"{found['path']} is a dataset, at version {found['version']}.")
        return
    if not (found["folders"] or found["datasets"]):
        where = f"{path.strip('/')} has" if path.strip("/") else "flock-datasets has"
        click.echo(f"{where} no datasets yet. Import one with: flock datasets import REPO DEST")
        return
    for folder in found["folders"]:
        click.echo(f"{folder['path']}/")
    for dataset in found["datasets"]:
        click.echo(f"{dataset['path']}\tversion {dataset['version']}")
