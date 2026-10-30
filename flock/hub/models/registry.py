"""The registry's routes that need no files: listing models and versions, the primary version
and tags (D107)."""

from typing import Any

from flock.hub.models._common import ModelVersion, Ref, Registry


def _ref(ref: str) -> Ref:
    parsed = Ref.parse(ref)
    return parsed if parsed.version is not None else Ref(parsed.org, parsed.name, "primary")


def set_primary(ref: str) -> ModelVersion:
    """
    Makes a version primary: what loads when no version is given.

    Args:
        ref (`str`):
            The version, such as `acme/olmo2-1b@14` or `acme/olmo2-1b@checkpoint-12000`.

    Returns:
        [`~hub.models.ModelVersion`]: the version, now primary.

    Raises:
        [`~hub.models.ModelError`]: when the version doesn't exist or Flock refuses.
    """
    parsed = _ref(ref)
    with Registry() as registry:
        version = registry.resolve(parsed)
        answer = registry.call("PUT", f"{parsed.path}/primary", {"version": version.number})
    return ModelVersion.from_api(answer)


def tag(ref: str, tag: str, *, move: bool = False) -> ModelVersion:
    """
    Tags a version.

    Args:
        ref (`str`):
            The version, such as `acme/olmo2-1b@12`. `acme/olmo2-1b` alone tags the primary.
        tag (`str`):
            The tag: letters, digits, `.`, `-` and `_`, not only digits, and not `primary`.
        move (`bool`, *optional*, defaults to `False`):
            Move the tag if it's on another version. `checkpoint-{step}` tags never move.

    Returns:
        [`~hub.models.ModelVersion`]: the version, with the tag.

    Raises:
        [`~hub.models.ModelError`]: when the tag is on another version (`body["version"]` says which) and
            `move` isn't set, or the name isn't allowed.
    """
    parsed = _ref(ref)
    with Registry() as registry:
        version = registry.resolve(parsed)
        registry.call("PUT", f"{parsed.path}/tags/{tag}", {"version": version.number, "move": move})
        return registry.resolve(Ref(parsed.org, parsed.name, str(version.number)))


def untag(name: str, tag: str) -> None:
    """
    Removes a tag from a model. The version it was on stays.

    Args:
        name (`str`):
            The model, as `org/name`.
        tag (`str`):
            The tag to remove.
    """
    parsed = Ref.parse(name, version=False)
    with Registry() as registry:
        registry.call("DELETE", f"{parsed.path}/tags/{tag}", ok=204)


def versions(name: str, *, checkpoints: bool = False) -> list[ModelVersion]:
    """
    A model's versions, newest first. Their `files` are `None`: [`~hub.models.download`] or the version's
    ref resolves them.

    Args:
        name (`str`):
            The model, as `org/name`.
        checkpoints (`bool`, *optional*, defaults to `False`):
            Only versions with a `checkpoint-{step}` tag.
    """
    parsed = Ref.parse(name, version=False)
    found: list[ModelVersion] = []
    params: dict[str, Any] = {"checkpoints": "true"} if checkpoints else {}
    with Registry() as registry:
        while True:
            page = registry.call("GET", f"{parsed.path}/versions", params=params)
            found += [ModelVersion.from_api(v) for v in page["versions"]]
            if page.get("cursor") is None:
                return found
            params["before"] = page["cursor"]


def list_models(org: str | None = None) -> list[dict[str, Any]]:
    """
    An org's models by name, as `GET /api/v1/orgs/{org}/models` lists them: `model`,
    `versions`, `latest` and `primary` (numbers), `size` (of the primary) and `updated_at`.

    Args:
        org (`str`, *optional*):
            The org. Defaults to your default org.
    """
    with Registry() as registry:
        return registry.call("GET", f"orgs/{registry.default_org(org)}/models")["models"]
