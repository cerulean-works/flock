import subprocess
from pathlib import Path

import pytest

from flock.eval import provenance


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def checkout(tmp_path, monkeypatch) -> Path:
    """A one-commit repository laid out like flock, with `provenance` pointed at its copy of the module and `FLOCK_COMMIT` unset."""
    # Inside a git hook these point at the real repository, which `git add` would then change.
    for name in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE"):
        monkeypatch.delenv(name, raising=False)
    module = tmp_path / "flock" / "eval" / "provenance.py"
    module.parent.mkdir(parents=True)
    module.write_text("")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")
    monkeypatch.setattr(provenance, "__file__", str(module))
    monkeypatch.delenv("FLOCK_COMMIT", raising=False)
    return tmp_path


def test_untracked_files_count_only_under_flock(checkout):
    head = git(checkout, "rev-parse", "HEAD")
    assert provenance.flock_commit() == head

    (checkout / "scratch.py").write_text("")
    assert provenance.flock_commit() == head  # a scratch script beside the code didn't run

    # A user setting that hides untracked files from `git status` doesn't hide this one, and
    # neither does a name git would quote.
    git(checkout, "config", "status.showUntrackedFiles", "no")
    (checkout / "flock" / "eval" / "新的 模組.py").write_text("")
    assert provenance.flock_commit() == f"{head}-dirty"


def test_a_changed_tracked_file_makes_the_commit_dirty(checkout):
    (checkout / "flock" / "eval" / "provenance.py").write_text("# changed\n")
    assert provenance.flock_commit().endswith("-dirty")


def test_a_copy_inside_another_repository_has_no_known_commit(checkout, monkeypatch):
    # As a non-editable install in a virtualenv inside some other project.
    module = checkout / ".venv" / "site-packages" / "flock" / "eval" / "provenance.py"
    module.parent.mkdir(parents=True)
    module.write_text("")
    monkeypatch.setattr(provenance, "__file__", str(module))

    assert provenance.flock_commit() == "unknown"


def test_flock_commit_env_wins(checkout, monkeypatch):
    monkeypatch.setenv("FLOCK_COMMIT", "abc123")
    assert provenance.flock_commit() == "abc123"
