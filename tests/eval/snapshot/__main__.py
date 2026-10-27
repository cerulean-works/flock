"""Rewrites the snapshot files from the code: `uv run python -m tests.eval.snapshot`."""

import json

from flock.eval.tasks import SUITES, TASKS

from .. import toy
from . import TASKS_FILE, TOY_REQUESTS_FILE, definitions, toy_requests

for path, snapshot in (
    (TASKS_FILE, definitions(TASKS, SUITES)),
    (TOY_REQUESTS_FILE, toy_requests(toy.PROMPT_PATHS)),
):
    # Sorted keys and a two-space indent, so a change reads as a diff in review.
    path.write_text(json.dumps(snapshot, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"wrote {path}")
