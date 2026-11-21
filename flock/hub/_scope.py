"""How datasets and models name their scope (D159, D162): global, the org's, or one project's.

The scope is an argument, not part of the name. `project=` takes a project name, in `org=` or
your default org, or `org/project` as wandb writes it. A model ref may name its project itself,
as `project:path`, which is recognised by a `:` before any `/`. Flock's API only ever sees the
`project` query parameter.
"""

PROJECTS = "-projects"
"""The root folder of `flock-datasets` and `flock-models` that holds projects' data (D160)."""


def split_project(project: str, org: str | None = None) -> tuple[str | None, str]:
    """
    `project=` as its org and name: `org/project`, or a name in `org` (`None` for your default
    org).

    Raises:
        `ValueError`: when the project is empty, or `org/project` names another org than `org`.
    """
    owner, slash, name = project.partition("/")
    if not slash:
        owner, name = org, project
    if not name or owner == "":
        raise ValueError(f"project={project!r} isn't a project name or org/project")
    if org is not None and owner != org:
        raise ValueError(f"project={project!r} is in {owner}, not org={org!r}")
    return owner, name


def split_shorthand(ref: str) -> tuple[str, str] | None:
    """`project:path` as the project and the rest, when a `:` comes before any `/`; `None` for
    any other ref. The project is everything before the first `:`, so it may hold `@` and
    spaces."""
    colon = ref.find(":")
    if colon < 0:
        return None
    slash = ref.find("/")
    if 0 <= slash < colon:
        return None
    return ref[:colon], ref[colon + 1 :]


def scope_params(project: str | None) -> dict[str, str]:
    """The `project` query parameter for a scope, absent for global."""
    return {} if project is None else {"project": project}
