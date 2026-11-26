"""A task's dataset: a Hugging Face repo pinned to a commit."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Dataset:
    """
    A Hugging Face dataset pinned to a revision. Rows come from the Hugging Face Hub, or from a copy staged from the Flock hub under `$FLOCK_EVAL_HUB` when that is set (stage file 034); either way the rows and the task hashes are the same.

    Args:
        repo (`str`):
            The dataset repo, for example `allenai/ai2_arc`.
        revision (`str`):
            A commit hash, so the rows can't change under a task.
        subset (`str` or `None`):
            The configuration name, for example `ARC-Easy`.
        split (`str`):
            The split a task is scored on.
        row_index (`bool`, *optional*, defaults to `False`):
            Whether each row gets its position in its split as `"index"`, for datasets with no id column. A `to_doc` then reads the doc's id as `str(row["index"])`, as OLMES does for MMLU (`oe_eval/tasks/oe_eval_tasks/mmlu.py`). Written-out `Fewshot.rows` don't go through `load`, so they need an `"index"` of their own.
        private (`bool`, *optional*, defaults to `False`):
            Whether the repo is private. A run's per-doc predictions of a task on a private dataset are never uploaded to a public tracker project. Task hashes leave it out: it changes no request.
    """

    repo: str
    revision: str
    subset: str | None
    split: str
    row_index: bool = False
    private: bool = False

    def load(self, split: str | None = None) -> list[dict[str, Any]]:
        """
        Returns the rows of `split`, defaulting to the scored split.

        Raises:
            `ValueError`: if `row_index` is set and the rows already have an `"index"` column.
            `FileNotFoundError`, `OSError`, `ValueError`, `KeyError`: if `$FLOCK_EVAL_HUB` is set and the pin isn't staged there, can't be read there, isn't a commit or doesn't hold the split (see `hub.staged_rows`).
        """
        # A job on a node with no network reads a staged copy; the variable makes that a
        # requirement, so a missing copy fails instead of falling back to the network.
        # Imported here so reading a results file, which names its datasets, needs neither.
        import datasets

        from flock.eval import hub

        if root := hub.root():
            rows = hub.staged_rows(root, self.repo, self.revision, self.subset, split or self.split)
        else:
            rows = datasets.load_dataset(
                self.repo, self.subset, revision=self.revision, split=split or self.split
            )
        if not self.row_index:
            return list(rows)

        if "index" in rows.column_names:
            where = f"{self.repo} {self.subset or ''} {split or self.split}".replace("  ", " ")
            raise ValueError(f"{where}: the rows already have an index column")

        return [{**row, "index": position} for position, row in enumerate(rows)]
