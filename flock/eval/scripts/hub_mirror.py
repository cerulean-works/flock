"""Copies the Hugging Face datasets the task registry reads to the Flock hub, private, one tagged version per pin, and prints one JSON line per dataset.

Run from the repository root on a machine that reaches both hubs, signed in to Flock as an admin of ORG:

    uv run python flock/eval/scripts/hub_mirror.py ORG [OWNER/NAME ...]

Each dataset goes to `eval/<owner>/<name>` in ORG's `flock-datasets` bucket, holding only the configurations and splits some task reads (scored or few-shot), and its version is tagged with the pinned revision, which `flock eval hub pull` looks up. The path is made private before anything is written, so the rows are never public, and making a dataset public stays a separate decision. A pin already tagged is kept, unless a task now reads a split its version lacks: then a new version holds both, and the tag moves to it.
"""

import json
import os
import sys
from collections import defaultdict

import click
import datasets
import httpx
import lance

from flock.cli import credentials
from flock.eval import hub
from flock.eval.tasks import TASKS
from flock.hub import s3


@click.command()
@click.argument("org")
@click.argument("repos", nargs=-1)
def main(org: str, repos: tuple[str, ...]) -> None:
    """Mirror REPOS (default: every repo the registry reads) to ORG's hub, private."""
    # The copy must come from the Hugging Face Hub, not from an earlier staged copy.
    if os.environ.get("FLOCK_EVAL_HUB"):
        raise click.UsageError("unset FLOCK_EVAL_HUB: the mirror reads the Hugging Face Hub")

    # Every (subset, split) some task reads, scored or few-shot, per pinned repo.
    pins: dict[tuple[str, str], set[tuple[str | None, str]]] = defaultdict(set)
    for task in TASKS.values():
        dataset = task.dataset
        pairs = pins[(dataset.repo, dataset.revision)]
        pairs.add((dataset.subset, dataset.split))
        fewshot = getattr(task, "fewshot", None)
        if fewshot is not None and fewshot.split is not None:
            pairs.add((dataset.subset, fewshot.split))

    unknown = set(repos) - {repo for repo, _ in pins}
    if unknown:
        raise click.UsageError(f"not read by any task: {sorted(unknown)}")

    found = credentials.resolve()
    if found is None:
        raise click.UsageError("not signed in to Flock: run flock login")
    api = httpx.Client(
        base_url=f"{found.host}/api/v1/orgs/{org}/datasets",
        headers=found.auth_headers(),
        timeout=60,
    )
    anonymous = httpx.Client(base_url=api.base_url, timeout=60)
    s3.get_process(org)

    for (repo, revision), pairs in sorted(pins.items(), key=lambda pin: pin[0][0].lower()):
        if repos and repo not in repos:
            continue
        path = f"eval/{repo}"
        uri = f"s3://flock-datasets/{path}"

        # Private before the first write, and checked, since the hub's default is public.
        api.delete("/public", params={"path": path}).raise_for_status()

        try:
            tags = lance.dataset(uri).tags.list()
        except ValueError:  # nothing written there yet
            tags = {}
        # A tagged version is kept when it holds every pair the registry reads. Otherwise a new
        # version holds its pairs and the new ones, and the tag moves to it (stage 034, D3).
        version, held = None, set()
        if revision in tags:
            version = tags[revision]["version"]
            properties = hub.recorded(lance.dataset(uri, version=version))
            source = (properties.get("source_repo"), properties.get("source_revision"))
            if source != (repo, revision) or "pairs" not in properties:
                raise click.ClickException(f"{path} tag {revision} is on a version of {source}")
            for pair in json.loads(properties["pairs"]):
                held.add((pair["subset"], pair["split"]))
        if version is None or not pairs <= held:
            # Configurations and splits sort with no configuration first.
            splits = {
                (subset, split): datasets.load_dataset(repo, subset, revision=revision, split=split)
                for subset, split in sorted(pairs | held, key=lambda pair: (pair[0] or "", pair[1]))
            }
            version = hub.write(uri, repo, revision, splits)
            api.put(
                f"/tags/{revision}", json={"path": path, "version": version, "move": True}
            ).raise_for_status()

        record = {
            "repo": repo,
            "revision": revision,
            "path": path,
            "version": version,
            "tag": revision,
            "pairs": len(pairs | held),
            "anonymous_info": anonymous.get("/info", params={"path": path}).status_code,
            "member_info": api.get("/info", params={"path": path}).status_code,
        }
        print(json.dumps(record), flush=True)
        # A missing path answers 404 too, so a private copy is an anonymous 404 and a member 200.
        if (record["anonymous_info"], record["member_info"]) != (404, 200):
            sys.exit(
                f"{path} answers anonymous readers with {record['anonymous_info']} and members "
                f"with {record['member_info']}, not 404 and 200"
            )


if __name__ == "__main__":
    main()
