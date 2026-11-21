"""How `flock.hub.models` names a model (022, D162): global refs with folders, `project=`, and
the `project:path` shorthand, resolved in `org=` or the default org."""

from typing import Any

import pytest

from flock.hub.models._common import Ref


def default_org() -> str:
    return "home"


def parse(ref: str, **options: Any) -> Ref:
    return Ref.parse(ref, default_org=default_org, **options)


def test_global_refs_keep_their_org_and_may_have_folders() -> None:
    assert parse("acme/olmo2-1b") == Ref("acme", "olmo2-1b")
    found = parse("acme/eval/olmo2-1b@12")
    assert (found.org, found.project, found.name, found.version) == (
        "acme",
        None,
        "eval/olmo2-1b",
        "12",
    )
    assert str(found) == "acme/eval/olmo2-1b@12"


def test_project_takes_a_name_in_the_default_org_or_org_slash_project() -> None:
    found = parse("sweeps/lr@12", project="pretrain")
    assert (found.org, found.project, found.name, found.version) == (
        "home",
        "pretrain",
        "sweeps/lr",
        "12",
    )
    assert str(found) == "pretrain:sweeps/lr@12"
    assert parse("sweeps/lr", project="acme/pretrain").org == "acme"
    assert parse("sweeps/lr", project="pretrain", org="acme").org == "acme"
    with pytest.raises(ValueError, match="is in acme, not org='other'"):
        parse("sweeps/lr", project="acme/pretrain", org="other")


def test_the_shorthand_names_a_project_before_the_first_colon() -> None:
    # A project name may hold @ and spaces: the project is everything before the first `:`.
    found = parse("pre train@x:sweeps/lr@checkpoint-1")
    assert (found.org, found.project, found.name, found.version) == (
        "home",
        "pre train@x",
        "sweeps/lr",
        "checkpoint-1",
    )
    assert parse("pretrain:a/b", org="acme").org == "acme"
    with pytest.raises(ValueError, match="names its project"):
        parse("pretrain:a", project="other")
    # A `:` after a `/` isn't a shorthand, and paths can't hold one.
    with pytest.raises(ValueError):
        parse("acme/a:b")
