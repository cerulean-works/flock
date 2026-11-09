"""Copies the Hugging Face datasets the task registry reads to the Flock hub, private, one tagged version per pin, and prints one JSON line per dataset.

Run from the repository root on a machine that reaches both hubs, signed in to Flock as an admin of ORG:

    uv run python flock/eval/scripts/hub_mirror.py ORG [OWNER/NAME ...]

Each dataset goes to `eval/<owner>/<name>` in ORG's `flock-datasets` bucket, holding only the configurations and splits some task reads (scored or few-shot), and its version is tagged with the pinned revision, which `flock eval hub pull` looks up. The path is made private before anything is written, an existing path is checked private before more rows go to it, and a new one is checked after its first write and moved to the trash if any reader but a member can see it; making a dataset public stays a separate decision. A pin already tagged is kept, unless a task now reads a split its version lacks: then a new version holds both, and the tag moves to it.
"""

import json
import sys
from urllib.parse import urlencode

import click
import datasets
import lance

from flock.cli import credentials
from flock.cli.api import Api, error_of
from flock.eval import hub
from flock.eval.tasks import TASKS
from flock.eval.tasks.suite import pins
from flock.hub import s3


@click.command()
@click.argument("org")
@click.argument("repos", nargs=-1)
def main(org: str, repos: tuple[str, ...]) -> None:
    """Mirror REPOS (default: every repo the registry reads) to ORG's hub, private."""
    # Every (subset, split) some task reads, scored or few-shot, per pinned repo.
    pinned = pins(TASKS.values())
    unknown = set(repos) - {repo for repo, _ in pinned}
    if unknown:
        raise click.UsageError(f"not read by any task: {sorted(unknown)}")

    found = credentials.resolve()
    if found is None:
        raise click.UsageError("not signed in to Flock: run flock login")
    member, anonymous = Api(found.host, found.token), Api(found.host)
    s3.get_process(org)

    for (repo, revision), pairs in sorted(pinned.items(), key=lambda pin: pin[0][0].lower()):
        if repos and repo not in repos:
            continue
        path = hub.path(repo)
        uri = f"{hub.BUCKET}/{path}"
        datasets_api = f"orgs/{org}/datasets"
        query = urlencode({"path": path})
        info = f"{datasets_api}/info?{query}"

        # Private before the first write, since the hub's default is public.
        refused = member.request("DELETE", f"{datasets_api}/public?{query}")
        if refused.is_error:
            raise click.ClickException(f"{path}: can't make it private: {error_of(refused)}")
        existed = statuses(anonymous, member, info)
        # A path already written is checked before more rows go to it.
        if existed[1] == 200 and existed[::2] != (404, False):
            raise click.ClickException(
                f"{path} answers anonymous readers with {existed[0]}, public {existed[2]}"
            )

        try:
            tags = lance.dataset(uri).tags.list()
        except ValueError as error:
            if "was not found" not in str(error):
                raise
            tags = {}  # nothing written there yet
        # A tagged version is kept when it holds every pair the registry reads. Otherwise a new
        # version holds its pairs and the new ones, and the tag moves to it (stage 034, D3).
        version, held = None, set()
        if revision in tags:
            version = tags[revision]["version"]
            properties = hub.recorded(lance.dataset(uri, version=version))
            try:
                recorded = hub.check(properties, repo, revision, f"{path} tag {revision}")
            except ValueError as error:
                raise click.ClickException(str(error)) from None
            held = {(pair["subset"], pair["split"]) for pair in recorded}
        if version is None or not pairs <= held:
            # Configurations and splits sort with no configuration first.
            splits = {
                (subset, split): datasets.load_dataset(repo, subset, revision=revision, split=split)
                for subset, split in sorted(pairs | held, key=lambda pair: (pair[0] or "", pair[1]))
            }
            version = hub.write(uri, repo, revision, splits)
            tagged = member.request(
                "PUT",
                f"{datasets_api}/tags/{revision}",
                {"path": path, "version": version, "move": True},
            )
            if tagged.is_error:
                raise click.ClickException(f"{path}: can't tag {revision}: {error_of(tagged)}")

        anonymous_info, member_info, public = statuses(anonymous, member, info)
        record = {
            "repo": repo,
            "revision": revision,
            "path": path,
            "version": version,
            "tag": revision,
            "pairs": len(pairs | held),
            "anonymous_info": anonymous_info,
            "member_info": member_info,
            "public": public,
        }
        print(json.dumps(record), flush=True)
        # A missing path answers 404 too, so a private copy is an anonymous 404 and a member 200.
        if (anonymous_info, member_info, public) != (404, 200, False):
            # A path this run created goes to the trash, where only admins can restore it.
            if existed[1] != 200:
                member.post(f"{datasets_api}/delete", {"path": path})
            sys.exit(
                f"{path} answers anonymous readers with {anonymous_info} and members with "
                f"{member_info}, public {public}, not 404, 200 and False"
                + ("" if existed[1] == 200 else "; moved to the trash")
            )


def statuses(anonymous: Api, member: Api, info: str) -> tuple[int, int, bool | None]:
    """The anonymous and the member status of a path's `info`, and the `public` flag members read there (`None` for a missing path). A private copy answers 404, 200 and `False`; a missing path answers 404 to both."""
    seen = member.get(info)
    public = seen.json().get("public") if seen.status_code == 200 else None
    return anonymous.get(info).status_code, seen.status_code, public


if __name__ == "__main__":
    main()
