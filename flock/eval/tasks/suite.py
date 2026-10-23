"""Suites: named groups of tasks, scored as the unweighted mean of their members."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from flock.eval.tasks.base import MultipleChoiceTask


@dataclass(frozen=True, slots=True)
class Suite:
    """
    A named group of tasks and suites. A scored suite's score is the unweighted mean of its members' scores, as OLMES's `macro` aggregate (`oe_eval/tasks/aggregate_tasks.py`).

    A member task's score is its `metric`, or its primary metric if `metric` isn't set. A member suite's score is its own score, under its own `metric`.

    Args:
        name (`str`):
            The suite's registry name.
        members (`tuple[MultipleChoiceTask | Suite, ...]`):
            The tasks and suites it groups, each name once.
        metric (`str`, *optional*):
            The metric read from every member task. Each task's primary metric if not set.
        scored (`bool`, *optional*, defaults to `True`):
            Whether the suite has a score. An unscored suite only groups suites whose scores don't average, such as an accuracy and a `bpb`, so that one name runs them all.

    Raises:
        `ValueError`: if `members` is empty or repeats a name, a member task doesn't report the metric read from it, or `scored` is `False` with a `metric` or a direct member task. Also if a scored suite holds an unscored one, or two different tasks or suites under it share a name.
    """

    name: str
    members: tuple[MultipleChoiceTask | Suite, ...]
    metric: str | None = None
    scored: bool = True

    def __post_init__(self) -> None:
        names = [member.name for member in self.members]
        if not names:
            raise ValueError(f"{self.name}: a suite needs members")
        if len(set(names)) != len(names):
            raise ValueError(f"{self.name}: members repeat a name: {names}")
        if self.scored:
            self._check_scored_members()
        elif self.metric is not None or any(
            isinstance(member, MultipleChoiceTask) for member in self.members
        ):
            raise ValueError(f"{self.name}: an unscored suite groups only suites and has no metric")
        self._check_names()

    def _check_scored_members(self) -> None:
        """Raises `ValueError` if a member can't give a score under this suite."""
        for member in self.members:
            if isinstance(member, Suite):
                if not member.scored:
                    raise ValueError(f"{self.name}: member {member.name} has no score")
            elif (metric := self.metric or member.primary_metric) not in member.reported_metrics:
                raise ValueError(f"{self.name}: member {member.name} doesn't report {metric}")

    def _check_names(self) -> None:
        """Raises `ValueError` if two different tasks or suites under the suite share a name."""
        # Results are keyed by name, so two definitions under one name would score as one.
        seen: dict[str, MultipleChoiceTask | Suite] = {}
        for item in (*self._walk_tasks(), *self._walk_suites()):
            if seen.setdefault(item.name, item) != item:
                raise ValueError(f"{self.name}: two different definitions are named {item.name}")

    @property
    def tasks(self) -> tuple[MultipleChoiceTask, ...]:
        """Every task under the suite, depth first, each once."""
        return tuple({task.name: task for task in self._walk_tasks()}.values())

    @property
    def suites(self) -> tuple[Suite, ...]:
        """This suite and every suite under it, each after its own members and each once."""
        return tuple({suite.name: suite for suite in self._walk_suites()}.values())

    def score(self, metrics: Mapping[str, Mapping[str, float]]) -> float:
        """
        Returns the mean of the members' scores, given each task's metrics by task name.

        Raises:
            `ValueError`: if the suite is unscored.
            `KeyError`: if `metrics` lacks a member task or the metric read from it.
        """
        if not self.scored:
            raise ValueError(f"{self.name}: an unscored suite has no score")
        member_scores = [
            member.score(metrics)
            if isinstance(member, Suite)
            else metrics[member.name][self.metric or member.primary_metric]
            for member in self.members
        ]

        return sum(member_scores) / len(member_scores)

    def _walk_tasks(self) -> Iterator[MultipleChoiceTask]:
        """Yields every task under the suite, depth first, repeats included."""
        for member in self.members:
            if isinstance(member, Suite):
                yield from member._walk_tasks()
            else:
                yield member

    def _walk_suites(self) -> Iterator[Suite]:
        """Yields every suite under this one, members first, then this one, repeats included."""
        for member in self.members:
            if isinstance(member, Suite):
                yield from member._walk_suites()
        yield self
