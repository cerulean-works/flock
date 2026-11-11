"""Copies the Hugging Face datasets the task registry reads to the Flock hub, private, one tagged version per pin, and prints one JSON line per dataset.

Run from the repository root on a machine that reaches both hubs, signed in to Flock as an admin of ORG:

    uv run python flock/eval/scripts/hub_mirror.py ORG [OWNER/NAME ...]

Each dataset goes to `eval/<owner>/<name>` in ORG's `flock-datasets` bucket, holding only the configurations and splits some task reads (scored or few-shot), and its version is tagged with the pinned revision, which `flock eval hub pull` looks up. The path is made private before anything is written, an existing path is checked private before more rows go to it, a path whose member status is neither 200 nor 404 is left alone, and a new one is checked straight after its first write, before its tag is set, and moved to the trash if any reader but a member can see it; making a dataset public stays a separate decision. A pin already tagged is kept, unless a task now reads a split its version lacks: then a new version holds both, and the tag moves to it.
"""

import json
from typing import NamedTuple
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
    member = Api(found.host, found.token)
    anonymous = Api(found.host)
    s3.get_process(org)
    datasets_api = f"orgs/{org}/datasets"

    for repo, revision in sorted(pinned, key=lambda pin: pin[0].lower()):
        if repos and repo not in repos:
            continue
        pairs = pinned[(repo, revision)]
        path = hub.path(repo)
        uri = f"{hub.BUCKET}/{path}"
        query = urlencode({"path": path})
        info = f"{datasets_api}/info?{query}"

        # Private before the first write, since the hub's default is public.
        refused = member.request("DELETE", f"{datasets_api}/public?{query}")
        if refused.is_error:
            raise click.ClickException(f"{path}: can't make it private: {error_of(refused)}")

        # A member reads a written path (200) or a missing one (404). Any other answer leaves
        # unknown whether the path holds tagged versions or is public, so nothing is written.
        existed = statuses(anonymous, member, info)
        if existed.member not in (200, 404):
            raise click.ClickException(f"{path} answers members with {existed.member}: try again")
        new = existed.member == 404

        # A path already written is checked before more rows go to it.
        if not new and existed != PRIVATE:
            raise click.ClickException(
                f"{path} answers anonymous readers with {existed.anonymous}, "
                f"public {existed.public}"
            )

        try:
            tags = lance.dataset(uri).tags.list()
        except ValueError as error:
            if "was not found" not in str(error):
                raise
            tags = {}  # nothing written there yet

        # A tagged version is kept when it holds every pair the registry reads. Otherwise a new
        # version holds its pairs and the new ones, and the tag moves to it (stage file 034,
        # 034.D3).
        version = None
        held = set()
        if revision in tags:
            version = tags[revision]["version"]
            properties = hub.recorded(lance.dataset(uri, version=version))
            try:
                stored = hub.check(properties, repo, revision, f"{path} tag {revision}")
            except ValueError as error:
                raise click.ClickException(str(error)) from None
            held = {pair.key for pair in stored}
        wrote = version is None or not pairs <= held
        if wrote:
            # Configurations and splits sort with no configuration first.
            splits = {}
            for subset, split in sorted(pairs | held, key=lambda pair: (pair[0] or "", pair[1])):
                splits[(subset, split)] = datasets.load_dataset(
                    repo, subset, revision=revision, split=split
                )
            version = hub.write(uri, repo, revision, splits)

        # Checked straight after the write and before the tag, so a tag request that fails can't
        # leave a new path unchecked. A missing path answers 404 too, so a private copy is an
        # anonymous 404 and a member 200.
        seen = statuses(anonymous, member, info)
        if seen != PRIVATE:
            # A path that was missing before this write goes to the trash, where only admins can
            # restore it. One that held versions before is left for an admin, since the trash
            # would take them too.
            outcome = ""
            if new:
                trashed = member.post(f"{datasets_api}/delete", {"path": path})
                outcome = (
                    f"; NOT moved to the trash ({error_of(trashed)}): trash it by hand now"
                    if trashed.is_error
                    else "; moved to the trash"
                )
            raise click.ClickException(
                f"{path} answers anonymous readers with {seen.anonymous} and members with "
                f"{seen.member}, public {seen.public}, not 404, 200 and False{outcome}"
            )

        if wrote:
            tagged = member.request(
                "PUT",
                f"{datasets_api}/tags/{revision}",
                {"path": path, "version": version, "move": True},
            )
            if tagged.is_error:
                raise click.ClickException(f"{path}: can't tag {revision}: {error_of(tagged)}")

        record = {
            "repo": repo,
            "revision": revision,
            "path": path,
            "version": version,
            "tag": revision,
            "pairs": len(pairs | held),
            "anonymous_info": seen.anonymous,
            "member_info": seen.member,
            "public": seen.public,
        }
        print(json.dumps(record), flush=True)


class Statuses(NamedTuple):
    """What a path's `info` answers: the status anonymous readers get, the status members get, and the `public` flag members read there (`None` for a missing path)."""

    anonymous: int
    member: int
    public: bool | None


# A private copy. A missing path answers 404 to members too.
PRIVATE = Statuses(anonymous=404, member=200, public=False)


def statuses(anonymous: Api, member: Api, info: str) -> Statuses:
    """Asks a path's `info` as a member and anonymously."""
    seen = member.get(info)
    public = seen.json().get("public") if seen.status_code == 200 else None
    return Statuses(anonymous.get(info).status_code, seen.status_code, public)


if __name__ == "__main__":
    main()
