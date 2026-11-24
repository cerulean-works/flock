"""List models and versions, and manage the primary version and tags."""

from dataclasses import replace
from typing import Any

from flock.hub._scope import split_project
from flock.hub.models._common import ModelVersion, Ref, Registry, default_org, parse_ref


def set_primary(ref: str, *, project: str | None = None, org: str | None = None) -> ModelVersion:
    """
    Makes a version primary: what loads when no version is given.

    Args:
        ref (`str`):
            The version, such as `acme/olmo2-1b@14`, `acme/eval/olmo2-1b@checkpoint-12000`
            or `pretrain:sweeps/lr-3e-4@7`.
        project (`str`, *optional*):
            The project the model belongs to, by name or as `org/project`. `ref` is then a path
            inside it, such as `sweeps/lr-3e-4@7`.
        org (`str`, *optional*):
            The project's org. Defaults to your default org.

    Returns:
        [`~hub.models.ModelVersion`]: the version, now primary.

    Raises:
        [`~hub.models.ModelError`]: when the version doesn't exist or Flock refuses.
    """
    parsed = parse_ref(ref, project=project, org=org)
    with Registry() as registry:
        version = registry.resolve(parsed)
        answer = registry.model_call("PUT", parsed, "primary", {"version": version.number})
    return ModelVersion.from_api(answer)


def tag(
    ref: str, tag: str, *, move: bool = False, project: str | None = None, org: str | None = None
) -> ModelVersion:
    """
    Tags a version.

    Args:
        ref (`str`):
            The version, such as `acme/olmo2-1b@12`. `acme/olmo2-1b` alone tags the primary.
        tag (`str`):
            The tag: letters, digits, `.`, `-` and `_`, not only digits, and not `primary`.
        move (`bool`, *optional*, defaults to `False`):
            Move the tag if it's on another version. `checkpoint-{step}` tags never move.
        project (`str`, *optional*):
            The project the model belongs to, as for [`~hub.models.set_primary`].
        org (`str`, *optional*):
            The project's org. Defaults to your default org.

    Returns:
        [`~hub.models.ModelVersion`]: the version, with the tag.

    Raises:
        [`~hub.models.ModelError`]: when the tag is on another version (`body["version"]` says which) and
            `move` isn't set, or the name isn't allowed.
    """
    parsed = parse_ref(ref, project=project, org=org)
    with Registry() as registry:
        version = registry.resolve(parsed)
        body = {"version": version.number, "move": move}
        registry.model_call("PUT", parsed, f"tags/{tag}", body)
        return registry.resolve(replace(parsed, version=str(version.number)))


def untag(name: str, tag: str, *, project: str | None = None, org: str | None = None) -> None:
    """
    Removes a tag from a model. The version it was on stays.

    Args:
        name (`str`):
            The model, as `org/path`, `project:path`, or a path with `project`.
        tag (`str`):
            The tag to remove.
        project (`str`, *optional*):
            The project the model belongs to, as for [`~hub.models.set_primary`].
        org (`str`, *optional*):
            The project's org. Defaults to your default org.
    """
    parsed = parse_ref(name, version=False, project=project, org=org)
    with Registry() as registry:
        registry.model_call("DELETE", parsed, f"tags/{tag}", ok=204)


def versions(
    name: str, *, checkpoints: bool = False, project: str | None = None, org: str | None = None
) -> list[ModelVersion]:
    """
    A model's versions, newest first. Their `files` are `None`: [`~hub.models.download`] or the version's
    ref resolves them.

    Args:
        name (`str`):
            The model, as `org/path`, `project:path`, or a path with `project`.
        checkpoints (`bool`, *optional*, defaults to `False`):
            Only versions with a `checkpoint-{step}` tag.
        project (`str`, *optional*):
            The project the model belongs to, as for [`~hub.models.set_primary`].
        org (`str`, *optional*):
            The project's org. Defaults to your default org.
    """
    parsed = parse_ref(name, version=False, project=project, org=org)
    found: list[ModelVersion] = []
    params: dict[str, Any] = {"checkpoints": "true"} if checkpoints else {}
    with Registry() as registry:
        while True:
            page = registry.model_call("GET", parsed, "versions", params=params)
            found += [ModelVersion.from_api(v) for v in page["versions"]]
            if page.get("cursor") is None:
                return found
            params["before"] = page["cursor"]


def folder_ref(folder: str | None, *, project: str | None = None, org: str | None = None) -> Ref:
    """A folder of models as a [`Ref`] whose `name` is the folder (`""` for a scope's root):
    `org` or `org/folder` for a global one, `project:folder`, or with `project` a folder inside
    it. Without `folder`, the root of `project`, or of `org`'s global models."""
    if folder:
        return Ref.parse(folder, project=project, org=org, default_org=default_org, folder=True)
    if project is not None:
        return Ref.parse("", project=project, org=org, default_org=default_org, folder=True)
    return Ref(org or default_org(), "")


def list_models(
    folder: str | None = None, *, project: str | None = None, org: str | None = None
) -> dict[str, Any]:
    """
    One level of a folder of models, as `GET /api/v1/orgs/{org}/models` lists it, with every
    page joined: the models in it, then its folders.

    Args:
        folder (`str`, *optional*):
            The folder: `acme` or `acme/eval` for the org's global models, `pretrain:sweeps`
            for a project's, or with `project` a folder inside it, such as `sweeps`. Defaults to
            the root of the scope.
        project (`str`, *optional*):
            The project whose models to list, by name or as `org/project`. Defaults to the
            org's global models.
        org (`str`, *optional*):
            The org. Defaults to your default org.

    Returns:
        `dict[str, Any]`: `kind` (`folder`, or `model` when `folder` is a model), and for a
        folder its `models` (each with its `model`, `name`, `versions`, `latest` and `primary`
        numbers, `size` of the primary and `updated_at`) and `folders` (each with its `name`,
        `path` and number of `models`).

    Raises:
        [`~hub.models.ModelError`]: when the folder holds no models, or Flock refuses.
    """
    where = folder_ref(folder, project=project, org=org)
    params: dict[str, Any] = {"folder": where.name, **where.params}
    with Registry() as registry:
        route = f"orgs/{where.org}/models"
        found = registry.call("GET", route, params=params)
        while found.get("kind", "folder") == "folder" and found.get("cursor"):
            params["cursor"] = found["cursor"]
            page = registry.call("GET", route, params=params)
            for key in ("models", "folders"):
                found[key] = found.get(key, []) + page.get(key, [])
            found["cursor"] = page.get("cursor")
    if "kind" not in found:  # a server before 022: the org's models, which have no folders
        found = {"kind": "folder", "folder": "", "project": None, "folders": [], **found}
    return found


def move(
    name: str,
    to: str,
    *,
    to_project: str | None = None,
    to_global: bool = False,
    project: str | None = None,
    org: str | None = None,
) -> dict[str, Any]:
    """
    Renames a model, moves it to another folder, or to another scope (D166): from a project to
    global (promoting it), from global into a project, or between projects. Its versions, tags
    and files come along, and the move is instant whatever their size. The old name stops
    resolving.

    Args:
        name (`str`):
            The model, as `org/path`, `project:path`, or a path with `project`.
        to (`str`):
            Its new path in the scope it ends up in, such as `eval/olmo2-1b`, without an org or
            project.
        to_project (`str`, *optional*):
            Move it into this project of its org, by name or as `org/project`.
        to_global (`bool`, *optional*, defaults to `False`):
            Move it to the org's global models.
        project (`str`, *optional*):
            The project the model belongs to now, by name or as `org/project`.
        org (`str`, *optional*):
            The project's org. Defaults to your default org.

    Returns:
        `dict[str, Any]`: the model as Flock answers for it at its new place, with its `model`
        (`org/path` or `project:path`), `name`, `project` and versions.

    Raises:
        `ValueError`: when `to` isn't a model path, or both `to_project` and `to_global` are given.
        [`~hub.models.ModelError`]: when the new path is taken (a model, a folder, under a
            model, or a deleted model's name), an upload to the model is open, or Flock refuses.

    Example:

    ```python
    from flock.hub import models

    models.move("pretrain:sweeps/lr-3e-4", "released/olmo2-1b-lr", to_global=True)
    ```
    """
    if to_project is not None and to_global:
        raise ValueError("give to_project or to_global, not both")
    parsed = parse_ref(name, version=False, project=project, org=org)
    segments = to.split("/")
    if any(s in ("", ".", "..") or ":" in s or "@" in s for s in segments):
        raise ValueError(f"{to!r} isn't a model path such as eval/olmo2-1b")
    body: dict[str, Any] = {"name": to}
    if to_global:
        body["project"] = None
    elif to_project is not None:
        body["project"] = split_project(to_project, parsed.org)[1]
    with Registry() as registry:
        return registry.model_call("PATCH", parsed, body=body)
