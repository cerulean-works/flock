"""The `flock-hub` command line."""

import logging
from pathlib import Path
from typing import Literal

import click

from flock.hub.importer import Stage, import_hf_dataset


@click.group(name="hub")
def main() -> None:
    """Work with Flock's dataset hub."""


@main.command(name="import")
@click.option("--repo", required=True, help="Hugging Face dataset, such as cais/mmlu.")
@click.option("--revision", help="Branch, tag or commit (default: main), pinned to a sha.")
@click.option("--config", help="Config to import (needed when the repo has several).")
@click.option(
    "--data-files",
    "data_files",
    multiple=True,
    help="Glob of repo files to import instead of the config's; repeat for several. Needs --workdir.",
)
@click.option(
    "--urls-file",
    help="File of URLs to download instead of repo files (local, or a path in the repo). Needs --workdir.",
)
@click.option("--target", help="Hub dataset as org/name (default: hf/<owner>--<name>[--<config>]).")
@click.option(
    "--workdir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Staged mode's work directory; rerun with the same one to resume. Without it, the "
    "dataset is loaded in memory (small mode).",
)
@click.option(
    "--stage",
    type=click.Choice(["all", "download", "process", "upload"]),
    default="all",
    show_default=True,
    help="Run one stage of a staged import.",
)
@click.option(
    "--num-proc",
    type=click.IntRange(min=1),
    default=1,
    show_default=True,
    help="Processes converting files in staged mode.",
)
@click.option(
    "--mode",
    type=click.Choice(["overwrite", "append"]),
    default="overwrite",
    show_default=True,
    help="Replace the target's rows, or add to them.",
)
def import_(
    repo: str,
    revision: str | None,
    config: str | None,
    data_files: tuple[str, ...],
    urls_file: str | None,
    target: str | None,
    workdir: Path | None,
    stage: Stage,
    num_proc: int,
    mode: Literal["overwrite", "append"],
) -> None:
    """Import a Hugging Face dataset into the hub as one new version."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        dataset = import_hf_dataset(
            repo,
            config,
            revision,
            target,
            workdir=workdir,
            num_proc=num_proc,
            stage=stage,
            mode=mode,
            data_files=list(data_files) or None,
            urls_file=urls_file,
        )
    except (LookupError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    if dataset is None:
        click.echo(f"stage {stage} done; rerun with --stage all (or the next stage) to continue")
        return
    click.echo(f"{dataset.repo_id} version {dataset.version}: {dataset.num_rows} rows")
