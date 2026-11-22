"""`flock datasets` (D122, 022, 023): import Hugging Face datasets to Flock, list, move,
fork and blend them."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click

from flock.hub import datasets
from flock.hub.datasets import DatasetError
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
    except (S3Error, DatasetError) as error:
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


def _amount(text: str) -> int | float:
    """`100000` is rows; `66%` or `0.66` is a fraction."""
    try:
        if text.endswith("%"):
            return float(text[:-1]) / 100
        return float(text) if "." in text else int(text.replace("_", ""))
    except ValueError:
        raise click.BadParameter(f"{text} isn't a row count or a fraction.") from None


@datasets_group.command("fork")
@click.argument("src")
@click.argument("dest")
@click.option("--version", help="A version number or tag (default: the latest).")
@click.option("--org", help="The org (default: your default org).")
def fork_command(src: str, dest: str, version: str | None, org: str | None) -> None:
    """Fork the dataset SRC to DEST: a copy-on-write dataset reading SRC's files in place."""
    with _failing():
        made = datasets.fork(src, dest, version=version, org=org)
    click.echo(f"✓ Forked {src} at version {made['source']['version']} to {made['path']}")


@datasets_group.command("blend")
@click.argument("dest")
@click.option(
    "--source",
    "sources",
    multiple=True,
    required=True,
    metavar="PATH[@VERSION]=AMOUNT",
    help="A source and its rows (100000), or its fraction (66% or 0.66) of --total. Repeat.",
)
@click.option("--total", type=click.IntRange(min=1), help="The blend's rows, for fractions.")
@click.option(
    "--seed", type=click.IntRange(min=0), default=datasets.DEFAULT_SEED, show_default=True
)
@click.option(
    "--columns",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="A JSON column map, as datasets.blend takes (default: match columns by name).",
)
@click.option("--replace", is_flag=True, help="Take rows more than once from small sources.")
@click.option("--preview", is_flag=True, help="Print the column map and problems; write nothing.")
@click.option("--org", help="The org (default: your default org).")
def blend_command(
    dest: str,
    sources: tuple[str, ...],
    total: int | None,
    seed: int,
    columns: Path | None,
    replace: bool,
    preview: bool,
    org: str | None,
) -> None:
    """Blend datasets into DEST, reading their files in place. The same seed picks the same rows."""
    import json

    amounts: dict[str, int | float] = {}
    for spec in sources:
        path, sep, amount = spec.rpartition("=")
        if not sep or not path:
            raise click.BadParameter(f"{spec}: use PATH[@VERSION]=AMOUNT.", param_hint="--source")
        amounts[path] = _amount(amount)
    mapping = json.loads(columns.read_text()) if columns is not None else None
    options: dict[str, Any] = {
        "total": total,
        "seed": seed,
        "columns": mapping,
        "replace": replace,
        "org": org,
    }
    with _failing():
        if preview:
            found = datasets.blend_preview(amounts, dest, **options)
        else:
            made = datasets.blend(amounts, dest, **options)
    if not preview:
        click.echo(f"✓ Blended {made['rows']:,} rows to {made['path']}, version {made['version']}")
        return
    for source in found["sources"]:
        click.echo(f"{source['path']}@{source['version']}\t{source['rows']:,} rows")
    for column in found["columns"]:
        froms = ", ".join(f"{p}: {json.dumps(s)}" for p, s in column["from"].items())
        click.echo(f"{column['name']} ({column['type']})\t{froms}")
    for problem in found["problems"]:
        click.echo(f"✗ {problem}", err=True)
    if found["problems"]:
        raise SystemExit(1)


@datasets_group.command("make-independent")
@click.argument("path")
@click.option("--org", help="The org (default: your default org).")
def independent_command(path: str, org: str | None) -> None:
    """Rewrite the fork or blend PATH into its own files, releasing its sources."""
    with _failing():
        made = datasets.make_independent(path, org=org)
    click.echo(f"✓ {made['path']} is independent, at version {made['version']}")


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
