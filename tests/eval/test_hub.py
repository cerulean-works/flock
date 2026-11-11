import contextlib
import json
import os
import shutil
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import datasets
import httpx
import lance
import pyarrow as pa
import pytest
from click.testing import CliRunner

from flock.cli import api, credentials
from flock.eval import hub
from flock.eval.cli import main
from flock.eval.model import ScoringModel
from flock.eval.run import EvalConfig, evaluate
from flock.eval.scripts import hub_mirror
from flock.eval.tasks import TASKS, Dataset, PerplexityTask
from flock.eval.tasks.perplexity import _docs_by_domain
from flock.hub import s3

REPO = "cerulean/toy"
FIRST = "1" * 40
SECOND = "2" * 40

QA = datasets.Features(
    {
        "id": datasets.Value("string"),
        "label": datasets.ClassLabel(names=["no", "yes"]),
        "choices": datasets.Sequence(
            {"text": datasets.Value("string"), "label": datasets.Value("string")}
        ),
        "score": datasets.Value("float32"),
    }
)
# More rows than one written batch, so the order across batches is checked too, a `split`
# column of the source's own, as HellaSwag has, and a `score` of another type than `QA`'s, as
# Paloma's `timestamp` differs between sources.
COUNTS = datasets.Features(
    {
        "n": datasets.Value("int32"),
        "note": datasets.Value("string"),
        "split": datasets.Value("string"),
        "score": datasets.Value("string"),
    }
)


@pytest.fixture(autouse=True)
def bucket(tmp_path, monkeypatch):
    """A local folder stands in for the org's bucket, so a copy of `repo` lives at `<tmp_path>/hub/eval/<repo>`, and each sign-in is recorded instead of made."""
    monkeypatch.setattr(hub, "BUCKET", str(tmp_path / "hub"))
    signed_in = []
    monkeypatch.setattr(
        s3, "get_process", lambda org, read_only=False: signed_in.append((org, read_only))
    )
    return signed_in


def toy_splits() -> dict[tuple[str | None, str], datasets.Dataset]:
    """A named configuration and the unnamed one, with different columns and two splits each, as `datasets` gives them."""
    qa = {
        "test": [
            {
                "id": "臺灣-1",
                "label": 1,
                "choices": {"text": ["玉山", "", "雪山"], "label": ["A", "B", "C"]},
                "score": 0.1,
            },
            {"id": "q2", "label": 0, "choices": {"text": [], "label": []}, "score": 2.5},
        ],
        "train": [
            {"id": "shot-1", "label": 1, "choices": {"text": ["é"], "label": ["A"]}, "score": -1.0}
        ],
    }
    counts = {
        "test": [
            {"n": n, "note": None if n % 7 else "七", "split": "indomain", "score": str(n)}
            for n in range(25_000)
        ],
        "validation": [{"n": -1, "note": "only", "split": "zeroshot", "score": "高"}],
    }
    splits = {}
    for split, rows in qa.items():
        splits[("qa", split)] = datasets.Dataset.from_list(rows, features=QA)
    for split, rows in counts.items():
        splits[(None, split)] = datasets.Dataset.from_list(rows, features=COUNTS)
    return splits


def mirrored(tmp_path, *pairs, revision=FIRST) -> str:
    """Writes `pairs` of the toy repo to the stand-in hub as a new version with the pin's tag on it, as the mirror does, and returns the copy's URI."""
    splits = toy_splits()
    uri = str(tmp_path / "hub" / "eval" / REPO)
    version = hub.write(uri, REPO, revision, {pair: splits[pair] for pair in pairs})
    tags = lance.dataset(uri).tags
    set_tag = tags.update if revision in tags.list() else tags.create
    set_tag(revision, version)
    return uri


@pytest.fixture
def read_staged(monkeypatch, real_load):
    """Makes `Dataset.load` the real one, reading the copies staged under the root it is given."""

    def switch(root):
        monkeypatch.setattr(Dataset, "load", real_load)
        monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))

    return switch


@pytest.fixture
def staged(tmp_path, monkeypatch, real_load):
    """A toy repo written to a local stand-in for the hub at two pins and staged at the first, with `datasets.load_dataset` answering from the same tables."""
    splits = toy_splits()
    for revision in (FIRST, SECOND):
        uri = mirrored(tmp_path, *splits, revision=revision)

    monkeypatch.setattr(
        datasets, "load_dataset", lambda repo, subset, revision, split: splits[(subset, split)]
    )
    monkeypatch.setattr(Dataset, "load", real_load)
    root = tmp_path / "staged"
    return uri, root, splits


def test_a_staged_copy_gives_the_rows_the_hugging_face_hub_gives(staged, monkeypatch):
    _, root, splits = staged
    from_hf = {pair: Dataset(REPO, FIRST, pair[0], pair[1]).load() for pair in splits}
    numbered_hf = Dataset(REPO, FIRST, "qa", "test", row_index=True).load()

    folder = hub.pull(root, "acme", REPO, FIRST)
    assert folder == root / REPO / FIRST
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    monkeypatch.setattr(datasets, "load_dataset", None)  # the staged path never calls it

    for (subset, split), rows in from_hf.items():
        assert Dataset(REPO, FIRST, subset, split).load() == rows
    assert Dataset(REPO, FIRST, "qa", "test", row_index=True).load() == numbered_hf
    # The scored split's default and a few-shot split read by name.
    assert Dataset(REPO, FIRST, "qa", "test").load("train") == from_hf[("qa", "train")]

    # A second pull of the same pin leaves the staged copy as it is.
    assert hub.pull(root, "acme", REPO, FIRST) == folder


def test_each_pin_stages_its_own_tagged_version(staged):
    _, root, _ = staged
    first = hub.pull(root, "acme", REPO, FIRST)
    second = hub.pull(root, "acme", REPO, SECOND)

    assert first != second
    for folder, revision in ((first, FIRST), (second, SECOND)):
        table = lance.dataset(str(folder))
        transaction = table.read_transaction(table.version)
        assert transaction is not None and transaction.transaction_properties is not None
        assert transaction.transaction_properties["source_revision"] == revision


def test_a_pull_refuses_a_tag_on_a_version_of_another_revision(staged):
    uri, root, _ = staged
    lance.dataset(uri).tags.create("3" * 40, 1)  # version 1 holds FIRST

    with pytest.raises(ValueError, match=f"holds {REPO}@{FIRST}, not {REPO}@{'3' * 40}"):
        hub.pull(root, "acme", REPO, "3" * 40)
    assert not root.exists()


@pytest.mark.parametrize("revision", ["main", "v1.1", FIRST[:12]])
def test_a_staged_read_needs_a_commit_hash(staged, monkeypatch, revision):
    _, root, _ = staged
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    with pytest.raises(ValueError, match="needs a commit hash"):
        Dataset(REPO, revision, "qa", "test").load()


def test_an_unstaged_pin_names_the_pull_command(staged, monkeypatch):
    _, root, _ = staged
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    with pytest.raises(FileNotFoundError, match=f"flock eval hub pull <org>/{REPO}@{FIRST}"):
        Dataset(REPO, FIRST, "qa", "test").load()


def test_a_staged_copy_of_another_pin_is_refused(staged, monkeypatch):
    _, root, _ = staged
    shutil.copytree(hub.pull(root, "acme", REPO, FIRST), root / REPO / SECOND)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))

    with pytest.raises(ValueError, match=f"holds {REPO}@{FIRST}, not {REPO}@{SECOND}"):
        Dataset(REPO, SECOND, "qa", "test").load()


def test_a_split_the_copy_does_not_hold_is_refused(staged, monkeypatch):
    _, root, _ = staged
    hub.pull(root, "acme", REPO, FIRST)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))

    with pytest.raises(KeyError, match="no split 'validation' of subset 'qa'"):
        Dataset(REPO, FIRST, "qa", "validation").load()


@pytest.mark.parametrize("column", ["hf_subset", "hf_split"])
def test_the_writer_refuses_rows_with_its_bookkeeping_columns(tmp_path, column):
    rows = datasets.Dataset.from_list([{column: "x", "text": "a"}])
    with pytest.raises(ValueError, match=f"the rows have a {{'{column}'}} column"):
        hub.write(str(tmp_path / "hub"), REPO, FIRST, {(None, "test"): rows})


@pytest.mark.parametrize(
    ("ref", "refusal"),
    [
        (f"acme/{REPO}@main", "needs a commit hash"),
        (f"acme/{REPO}@{FIRST[:12]}", "needs a commit hash"),
        (f"{REPO}@{FIRST}", "expected a repo OWNER/NAME"),
        (f"acme//toy@{FIRST}", "expected a repo OWNER/NAME"),
        (f"acme/../toy@{FIRST}", "expected a repo OWNER/NAME"),
        (f"acme/cerulean/..@{FIRST}", "expected a repo OWNER/NAME"),
    ],
)
def test_hub_pull_refuses_a_ref_that_is_not_an_org_a_repo_and_a_commit(
    tmp_path, bucket, ref, refusal
):
    result = CliRunner().invoke(main, ["hub", "pull", ref, "--to", str(tmp_path)])
    assert result.exit_code == 1 and refusal in result.output  # refused, not crashed
    assert bucket == []  # before any sign-in


def test_hub_pull_takes_an_org_a_repo_and_a_commit(monkeypatch, tmp_path, bucket):
    runner = CliRunner()
    result = runner.invoke(main, ["hub", "pull", f"/{REPO}@{FIRST}", "--to", str(tmp_path)])
    assert result.exit_code == 2  # no org
    assert bucket == []  # refused before any sign-in

    pulled = []
    monkeypatch.setattr(hub, "pull", lambda root, org, repo, revision: pulled.append((org, repo)))
    result = runner.invoke(main, ["hub", "pull", f"acme/{REPO}@{FIRST}", "--to", str(tmp_path)])
    assert result.exit_code == 0
    assert pulled == [("acme", REPO)]

    def refuse(root, org, repo, revision):
        raise ValueError("tag on another revision")

    monkeypatch.setattr(hub, "pull", refuse)
    result = runner.invoke(main, ["hub", "pull", f"acme/{REPO}@{FIRST}", "--to", str(tmp_path)])
    assert result.exit_code == 1 and "tag on another revision" in result.output

    def crash(root, org, repo, revision):
        raise RuntimeError("a bug, not a refusal")

    # Only the errors `pull` is known to raise become one line; anything else keeps its traceback.
    monkeypatch.setattr(hub, "pull", crash)
    result = runner.invoke(main, ["hub", "pull", f"acme/{REPO}@{FIRST}", "--to", str(tmp_path)])
    assert isinstance(result.exception, RuntimeError)


def test_a_pull_replaces_a_copy_staged_from_an_earlier_version_of_the_tag(tmp_path, read_staged):
    splits = toy_splits()
    mirrored(tmp_path, ("qa", "test"))
    root = tmp_path / "staged"
    hub.pull(root, "acme", REPO, FIRST)

    # A task now reads the few-shot split too: the mirror writes both and moves the tag.
    mirrored(tmp_path, ("qa", "test"), ("qa", "train"))
    hub.pull(root, "acme", REPO, FIRST)

    read_staged(root)
    assert Dataset(REPO, FIRST, "qa", "train").load() == list(splits[("qa", "train")])
    assert [path.name for path in (root / REPO).iterdir()] == [FIRST]  # nothing left beside it


def test_a_pull_refuses_a_copy_of_another_repo(tmp_path):
    uri = str(tmp_path / "hub" / "eval" / "cerulean" / "other")
    version = hub.write(uri, REPO, FIRST, {("qa", "test"): toy_splits()[("qa", "test")]})
    lance.dataset(uri).tags.create(FIRST, version)
    with pytest.raises(ValueError, match=f"holds {REPO}@{FIRST}, not cerulean/other@{FIRST}"):
        hub.pull(tmp_path / "staged", "acme", "cerulean/other", FIRST)


def test_the_writer_writes_the_rows_a_filtered_dataset_gives(tmp_path, read_staged):
    rows = datasets.Dataset.from_list([{"n": n} for n in range(5)]).filter(lambda row: row["n"] % 2)
    uri = str(tmp_path / "hub" / "eval" / REPO)
    version = hub.write(uri, REPO, FIRST, {(None, "test"): rows, (None, "train"): rows})
    lance.dataset(uri).tags.create(FIRST, version)
    hub.pull(tmp_path / "staged", "acme", REPO, FIRST)

    read_staged(tmp_path / "staged")
    assert Dataset(REPO, FIRST, None, "train").load() == [{"n": 1}, {"n": 3}]


def test_a_staged_copy_is_as_readable_as_its_staging_root(tmp_path):
    mirrored(tmp_path, ("qa", "test"))

    def modes(root):
        """The modes of the repo's folders, of the copy's folders and of its files."""
        copy = root / REPO / FIRST
        between = {(root / REPO).stat().st_mode, (root / REPO).parent.stat().st_mode}
        folders = {copy.stat().st_mode} | {p.stat().st_mode for p in copy.rglob("*") if p.is_dir()}
        files = {p.stat().st_mode for p in copy.rglob("*") if p.is_file()}
        return [{stat.S_IMODE(mode) for mode in group} for group in (between, folders, files)]

    # A missing root is made readable by its owner only, whatever the umask.
    hub.pull(tmp_path / "made", "acme", REPO, FIRST)
    assert modes(tmp_path / "made") == [{0o700}, {0o700}, {0o600}]

    # A root the group can write to without the sticky bit would let it replace a copy.
    root = tmp_path / "shared"
    root.mkdir()
    root.chmod(0o2770)
    with pytest.raises(ValueError, match="set its sticky bit"):
        hub.pull(root, "acme", REPO, FIRST)

    # A group's root, setgid so new files keep its group and sticky so only a copy's owner can
    # move it: the folders between it and a copy take its mode, and the group reads the copy but
    # can't change it, whatever the puller's umask.
    root.chmod(0o3770)
    for umask in (0o077, 0o002):
        shutil.rmtree(root / "cerulean", ignore_errors=True)
        previous = os.umask(umask)
        try:
            hub.pull(root, "acme", REPO, FIRST)
        finally:
            os.umask(previous)
        assert modes(root) == [{0o3770}, {0o2750}, {0o640}]


def test_a_copy_opens_to_its_readers_only_once_its_files_are_protected(staged, monkeypatch):
    _, root, _ = staged
    root.mkdir()
    root.chmod(0o3770)
    chmod = Path.chmod
    open_to_group = []

    def record(self, mode):
        # Whether the group can reach this entry of the new copy through its top folder yet.
        for folder in (self, *self.parents):
            if folder.suffix == ".partial":
                open_to_group.append(bool(stat.S_IMODE(folder.stat().st_mode) & 0o070))
        chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", record)
    previous = os.umask(0o002)  # the copy's files are written group-writable
    try:
        hub.pull(root, "acme", REPO, FIRST)
    finally:
        os.umask(previous)
    assert len(open_to_group) > 1 and not any(open_to_group)


def test_a_pull_waits_for_another_and_keeps_the_copy_it_staged(staged, tmp_path):
    _, root, _ = staged
    elsewhere = hub.pull(tmp_path / "elsewhere", "acme", REPO, FIRST)
    folder = root / REPO / FIRST
    folder.parent.mkdir(parents=True)

    # Another pull holds the repo's lock, and stages its copy before letting go.
    with ThreadPoolExecutor(1) as pool:
        with hub._locked(folder.parent):
            waiting = pool.submit(hub.pull, root, "acme", REPO, FIRST)
            time.sleep(0.5)
            assert not waiting.done()
            shutil.copytree(elsewhere, folder)
            staged_inode = (folder / "_versions").stat().st_ino
        assert waiting.result(timeout=60) == folder

    # The other pull's copy, which a job may already read, is the one left, with nothing beside it.
    assert (folder / "_versions").stat().st_ino == staged_inode
    assert [path.name for path in folder.parent.iterdir()] == [FIRST]


def test_a_failed_pull_leaves_nothing_and_a_broken_copy_is_replaced(staged, monkeypatch, real_load):
    _, root, splits = staged
    folder = root / REPO / FIRST
    folder.mkdir(parents=True)
    (folder / "half-copied").write_text("")

    def fail(folder, mode):
        raise OSError("disk full")

    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    monkeypatch.setattr(Dataset, "load", real_load)
    with pytest.raises(
        OSError, match="can't be read as a staged copy.*flock eval hub pull"
    ) as error:
        Dataset(REPO, FIRST, "qa", "train").load()
    assert not isinstance(error.value, FileNotFoundError)  # it is there, so not "isn't staged"

    with monkeypatch.context() as patched:
        patched.setattr(hub, "_protect", fail)
        with pytest.raises(OSError, match="disk full"):
            hub.pull(root, "acme", REPO, FIRST)
    assert [path.name for path in (root / REPO).iterdir()] == [FIRST]

    assert hub.pull(root, "acme", REPO, FIRST) == folder
    assert Dataset(REPO, FIRST, "qa", "train").load() == list(splits[("qa", "train")])


def test_a_pull_refuses_a_version_that_records_no_pairs(tmp_path):
    uri = str(tmp_path / "hub" / "eval" / REPO)
    written = lance.write_dataset(
        pa.table({"n": [1]}),
        uri,
        transaction_properties={"source_repo": REPO, "source_revision": FIRST},
    )
    lance.dataset(uri).tags.create(FIRST, written.version)
    with pytest.raises(ValueError, match="records no pairs"):
        hub.pull(tmp_path / "staged", "acme", REPO, FIRST)
    assert not (tmp_path / "staged").exists()


def test_a_copy_of_the_same_rows_from_another_org_is_kept(staged):
    _, root, _ = staged
    folder = hub.pull(root, "acme", REPO, FIRST)
    before = (folder / "_versions").stat().st_ino

    # Its source and pairs decide its rows, so a pull through another org keeps it.
    assert hub.pull(root, "other", REPO, FIRST) == folder
    assert (folder / "_versions").stat().st_ino == before
    assert hub.recorded(lance.dataset(str(folder)))["hub_org"] == "acme"


def test_a_stale_copy_that_cannot_be_removed_still_gives_way(tmp_path, read_staged):
    splits = toy_splits()
    mirrored(tmp_path, ("qa", "test"))
    root = tmp_path / "staged"
    folder = hub.pull(root, "acme", REPO, FIRST)
    locked = folder / "locked"
    locked.mkdir()
    (locked / "kept").write_text("")
    locked.chmod(0o500)

    mirrored(tmp_path, ("qa", "test"), ("qa", "train"))
    try:
        assert hub.pull(root, "acme", REPO, FIRST) == folder
        read_staged(root)
        assert Dataset(REPO, FIRST, "qa", "train").load() == list(splits[("qa", "train")])
    finally:
        for stale in (root / REPO).glob(".*.stale"):
            (stale / "locked").chmod(0o700)


def test_a_run_checks_every_staged_copy_before_its_model_loads(staged):
    _, root, _ = staged
    pins = {
        (REPO, FIRST): {("qa", "test"), ("qa", "train")},
        ("cerulean/gone", FIRST): {(None, "test")},
    }
    command = "flock eval hub pull <org>/{}@" + FIRST + f" --to {root}"
    with pytest.raises(FileNotFoundError) as error:
        hub.check_staged(root, pins)
    # One command per missing pin, ready to paste once the org is filled in.
    assert str(error.value).splitlines()[1:] == [
        command.format("cerulean/gone"),
        command.format(REPO),
    ]

    hub.pull(root, "acme", REPO, FIRST)
    hub.check_staged(root, {(REPO, FIRST): {("qa", "test"), ("qa", "train")}})
    with pytest.raises(FileNotFoundError, match=f"split a task reads.*\n.*{REPO}@{FIRST}"):
        hub.check_staged(root, {(REPO, FIRST): {("qa", "validation")}})


def test_a_run_with_an_unstaged_dataset_fails_before_its_model_loads(tmp_path, monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("the model loaded")

    monkeypatch.setattr(ScoringModel, "from_pretrained", never)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="allenai/ai2_arc@"):
        evaluate(EvalConfig(model="tiny@0", tasks=("arc_easy",)))


def test_a_failed_rename_puts_the_staged_copy_back(tmp_path, monkeypatch, read_staged):
    splits = toy_splits()
    mirrored(tmp_path, ("qa", "test"))
    root = tmp_path / "staged"
    hub.pull(root, "acme", REPO, FIRST)
    mirrored(tmp_path, ("qa", "test"), ("qa", "train"))

    rename = Path.rename

    def fail_from_partial(self, target):
        if self.name.endswith(".partial"):
            raise OSError("quota exceeded")
        return rename(self, target)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "rename", fail_from_partial)
        with pytest.raises(OSError, match="quota exceeded"):
            hub.pull(root, "acme", REPO, FIRST)

    # The earlier copy is back in place, and nothing is left beside it.
    assert [path.name for path in (root / REPO).iterdir()] == [FIRST]
    read_staged(root)
    assert Dataset(REPO, FIRST, "qa", "test").load() == list(splits[("qa", "test")])


def test_a_pull_signs_in_to_its_org_with_read_only_keys(staged, bucket):
    _, root, _ = staged
    hub.pull(root, "acme", REPO, FIRST)
    assert bucket == [("acme", True)]


# A repo part or a path in place of the commit could leave the root, and a branch or tag moves.
@pytest.mark.parametrize(
    ("repo", "revision", "refusal"),
    [
        ("../toy", FIRST, "expected a repo OWNER/NAME"),
        ("cerulean/..", FIRST, "expected a repo OWNER/NAME"),
        ("cerulean/.hidden", FIRST, "expected a repo OWNER/NAME"),
        ("toy", FIRST, "expected a repo OWNER/NAME"),
        (REPO, "main", "needs a commit hash"),
        (REPO, FIRST[:12], "needs a commit hash"),
        (REPO, f"../../../{FIRST}", "needs a commit hash"),
    ],
)
def test_a_pull_refuses_a_repo_or_a_revision_that_could_leave_its_root(
    staged, bucket, repo, revision, refusal
):
    _, root, _ = staged
    with pytest.raises(ValueError, match=refusal):
        hub.pull(root, "acme", repo, revision)
    assert bucket == [] and not root.exists()  # before any sign-in or folder


def test_a_pull_refuses_a_folder_below_the_root_that_others_could_write_to(staged):
    _, root, _ = staged
    root.mkdir(mode=0o1777)
    root.chmod(0o1777)
    (root / "cerulean").mkdir()
    (root / "cerulean").chmod(0o777)  # made by an older pull or by hand, without the sticky bit
    with pytest.raises(ValueError, match="staging folder .*cerulean, so they could replace"):
        hub.pull(root, "acme", REPO, FIRST)

    (root / "cerulean").chmod(0o1777)
    assert hub.pull(root, "acme", REPO, FIRST) == root / REPO / FIRST


def test_a_pull_refuses_a_link_below_the_root(staged, tmp_path):
    _, root, _ = staged
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "cerulean").symlink_to(outside)  # would take the copy out of the root

    with pytest.raises(ValueError, match="cerulean is a link"):
        hub.pull(root, "acme", REPO, FIRST)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("held", [1, 3], ids=["cut short", "grown"])
def test_a_copy_with_other_rows_than_it_records_is_refused_and_a_pull_replaces_it(
    tmp_path, read_staged, held
):
    splits = toy_splits()
    written = lance.dataset(mirrored(tmp_path, ("qa", "test")))
    # A copy that records the two rows of the split, and holds one or three.
    rows = written.to_table()
    root = tmp_path / "staged"
    lance.write_dataset(
        pa.concat_tables([rows, rows]).slice(0, held),
        str(root / REPO / FIRST),
        transaction_properties=hub.recorded(written),
    )

    refusal = f"holds {held} rows, not the 2 it records: run `flock eval hub pull"
    with pytest.raises(ValueError, match=refusal):  # before a run's model loads
        hub.check_staged(root, {(REPO, FIRST): {("qa", "test")}})
    read_staged(root)
    with pytest.raises(ValueError, match=refusal):
        Dataset(REPO, FIRST, "qa", "test").load()

    hub.pull(root, "acme", REPO, FIRST)
    assert Dataset(REPO, FIRST, "qa", "test").load() == list(splits[("qa", "test")])


@pytest.mark.parametrize(
    "pairs",
    [
        '[{"subset": null, "split": "test", "rows": 1, "columns": {}, "digest": "new"}]',
        '[{"subset": null, "split": "test", "rows": "1", "columns": {}, "features": {}}]',
        "not JSON",
    ],
    ids=["other fields", "rows not a number", "not JSON"],
)
def test_a_copy_whose_pairs_this_version_cannot_read_is_refused(tmp_path, pairs):
    uri = str(tmp_path / "hub" / "eval" / REPO)
    written = lance.write_dataset(
        pa.table({"n": [1]}),
        uri,
        transaction_properties={"source_repo": REPO, "source_revision": FIRST, "pairs": pairs},
    )
    lance.dataset(uri).tags.create(FIRST, written.version)
    with pytest.raises(
        ValueError, match="tag .* records pairs in a form this version doesn't read"
    ):
        hub.pull(tmp_path / "staged", "acme", REPO, FIRST)


def test_every_registered_task_reads_exactly_the_pairs_it_reports(monkeypatch):
    read = []

    def record(dataset, split=None):
        read.append((dataset.subset, split or dataset.split))
        return []

    monkeypatch.setattr(Dataset, "load", record)
    for task in TASKS.values():
        read.clear()
        _docs_by_domain.cache_clear()
        with contextlib.suppress(KeyError, ValueError):  # no rows, so no docs or few-shot ids
            task.load()
        assert set(read) == task.reads, task.name


def test_perplexity_rows_are_read_again_when_the_staging_root_changes(staged, monkeypatch):
    _, root, _ = staged
    hub.pull(root, "acme", REPO, FIRST)
    rows = [{"id": "1", "text": "from the Hugging Face Hub"}]
    monkeypatch.setattr(datasets, "load_dataset", lambda *args, **kwargs: rows)
    task = PerplexityTask(name="toy", dataset=Dataset(REPO, FIRST, None, "validation"))
    _docs_by_domain.cache_clear()
    assert [doc.text for doc in task.load()] == ["from the Hugging Face Hub"]

    rows.clear()  # staged rows now, or nothing
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    with pytest.raises(
        KeyError, match="'id'"
    ):  # the staged toy copy has no such rows, but it is read
        task.load()


def test_a_root_others_could_write_to_is_refused_even_with_the_copy_staged(staged):
    _, root, _ = staged
    hub.pull(root, "acme", REPO, FIRST)
    root.chmod(0o777)
    with pytest.raises(ValueError, match="set its sticky bit"):
        hub.pull(root, "acme", REPO, FIRST)


def test_the_staging_root_is_absolute_and_quoted_in_pull_commands(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FLOCK_EVAL_HUB", "eval hub")
    root = tmp_path / "eval hub"
    assert hub.root() == root
    with pytest.raises(FileNotFoundError) as error:
        hub.check_staged(root, {(REPO, FIRST): {("qa", "test")}})
    assert str(error.value).splitlines()[1].endswith(f"--to '{tmp_path}/eval hub'")
    monkeypatch.setenv("FLOCK_EVAL_HUB", "")
    assert hub.root() is None


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """Runs the mirror script on the toy repo's first pin against a stand-in for the Flock API. It answers `info` with the `(anonymous, member, public)` it is told, `before` for the check ahead of any write and `after` from then on, sets a tag on the local copy as the hub would, and records every request."""
    splits = toy_splits()
    copy = str(tmp_path / "hub" / "eval" / REPO)
    monkeypatch.setattr(
        datasets, "load_dataset", lambda repo, subset, revision, split: splits[(subset, split)]
    )
    monkeypatch.setattr(
        credentials, "resolve", lambda: credentials.Credentials("https://flock.test", "t", "env")
    )

    def run(before, after, pairs=(("qa", "test"),), tag=200, trash=200):
        requests = []

        def answer(request: httpx.Request) -> httpx.Response:
            asked = [name for _, name in requests].count("info")
            requests.append((request.method, request.url.path.rsplit("/", 1)[-1]))
            if request.url.path.endswith("/info"):
                # Each check asks as a member, then anonymously: two requests.
                anonymous, member, public = before if asked < 2 else after
                if "Authorization" not in request.headers:
                    return httpx.Response(anonymous)
                return httpx.Response(member, json={"public": public})
            if request.method == "PUT" and tag == 200:
                tags = lance.dataset(copy).tags
                set_tag = tags.update if FIRST in tags.list() else tags.create
                set_tag(FIRST, json.loads(request.content)["version"])
            if request.method == "PUT":
                return httpx.Response(tag, json={})
            if request.url.path.endswith("/delete"):
                return httpx.Response(trash, json={})
            return httpx.Response(200, json={})

        monkeypatch.setattr(hub_mirror, "pins", lambda tasks: {(REPO, FIRST): set(pairs)})
        monkeypatch.setattr(api, "TRANSPORT", httpx.MockTransport(answer))
        result = CliRunner().invoke(hub_mirror.main, ["acme"])
        return result, requests

    return run


MISSING = (404, 404, None)
PRIVATE = (404, 200, False)


def test_the_mirror_writes_only_to_a_path_members_read_or_find_missing(mirror, tmp_path):
    copy = tmp_path / "hub" / "eval" / REPO

    # A member status that is neither 200 nor 404 says nothing about what the path holds.
    result, requests = mirror(before=(404, 503, None), after=PRIVATE)
    assert result.exit_code == 1 and "answers members with 503" in result.output
    assert requests[0] == ("DELETE", "public")  # private first, whatever follows
    assert not copy.exists() and ("PUT", FIRST) not in requests

    # A path that is already there and public gets no more rows, and is not trashed, whether
    # anonymous readers see it yet or only its flag says they may.
    for before in ((200, 200, True), (404, 200, True)):
        result, requests = mirror(before=before, after=PRIVATE)
        assert result.exit_code == 1 and "public True" in result.output
        assert not copy.exists() and ("POST", "delete") not in requests

    # A missing path is written, checked, then tagged: private is an anonymous 404, a member 200.
    result, requests = mirror(before=MISSING, after=PRIVATE)
    assert result.exit_code == 0, result.output
    assert [name for _, name in requests] == ["public", "info", "info", "info", "info", FIRST]
    pairs = hub.check(hub.recorded(lance.dataset(str(copy))), REPO, FIRST, str(copy))
    assert [pair.key for pair in pairs] == [("qa", "test")]


def test_the_mirror_keeps_a_tagged_version_until_a_task_reads_another_split(mirror, tmp_path):
    copy = str(tmp_path / "hub" / "eval" / REPO)
    mirror(before=MISSING, after=PRIVATE)
    first = lance.dataset(copy).tags.list()[FIRST]["version"]

    # The same pairs again: nothing is written and the tag stays.
    result, requests = mirror(before=PRIVATE, after=PRIVATE)
    assert result.exit_code == 0 and ("PUT", FIRST) not in requests
    assert lance.dataset(copy).version == first
    assert json.loads(result.output)["pairs"] == 1

    # A task now reads the few-shot split: a new version holds both, and the tag moves to it.
    result, requests = mirror(before=PRIVATE, after=PRIVATE, pairs=[("qa", "train")])
    assert result.exit_code == 0 and requests[-1] == ("PUT", FIRST)
    second = lance.dataset(copy).tags.list()[FIRST]["version"]
    assert second > first and json.loads(result.output)["version"] == second
    pairs = hub.check(hub.recorded(lance.dataset(copy, version=second)), REPO, FIRST, copy)
    assert [pair.key for pair in pairs] == [("qa", "test"), ("qa", "train")]


@pytest.mark.parametrize(
    ("trash", "outcome"),
    [(200, "; moved to the trash"), (500, "NOT moved to the trash (500 Internal Server Error)")],
)
# Seen by anonymous readers, or hidden from them but flagged public, or unreadable by members.
@pytest.mark.parametrize("after", [(200, 200, True), (404, 200, True), (404, 403, None)])
def test_a_new_path_that_is_not_private_goes_to_the_trash_or_says_it_did_not(
    mirror, after, trash, outcome
):
    result, requests = mirror(before=MISSING, after=after, trash=trash)

    assert result.exit_code == 1
    assert f"answers anonymous readers with {after[0]}" in result.output
    assert outcome in result.output
    # Checked straight after the write: no tag is set on a path others can see.
    assert requests[-1] == ("POST", "delete") and ("PUT", FIRST) not in requests


def test_a_new_path_is_checked_even_when_its_tag_cannot_be_set(mirror):
    result, requests = mirror(before=MISSING, after=PRIVATE, tag=500)

    assert result.exit_code == 1 and f"can't tag {FIRST}" in result.output
    assert [name for _, name in requests][-3:] == ["info", "info", FIRST]  # checked, then tagged


def test_a_path_that_held_versions_is_never_trashed(mirror):
    mirror(before=MISSING, after=PRIVATE)

    # It grows by a split and then fails its check: left for an admin, with its older versions.
    result, requests = mirror(before=PRIVATE, after=(200, 200, True), pairs=[("qa", "train")])
    assert result.exit_code == 1 and "answers anonymous readers with 200" in result.output
    assert "trash" not in result.output
    assert ("POST", "delete") not in requests and ("PUT", FIRST) not in requests
