import os
import shutil
import stat

import datasets
import lance
import pyarrow as pa
import pytest

from flock.eval import hub
from flock.eval.tasks import Dataset

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
    """A local folder stands in for the org's bucket, so a copy of `repo` lives at `<tmp_path>/hub/eval/<repo>`."""
    monkeypatch.setattr(hub, "BUCKET", str(tmp_path / "hub"))


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


@pytest.fixture
def staged(tmp_path, monkeypatch, real_load):
    """A toy repo written to a local stand-in for the hub at two pins and staged at the first, with `datasets.load_dataset` answering from the same tables."""
    splits = toy_splits()
    uri = str(tmp_path / "hub" / "eval" / REPO)
    for revision in (FIRST, SECOND):
        version = hub.write(uri, REPO, revision, splits)
        lance.dataset(uri).tags.create(revision, version)

    monkeypatch.setattr(
        datasets, "load_dataset", lambda repo, subset, revision, split: splits[(subset, split)]
    )
    monkeypatch.setattr(Dataset, "load", real_load)
    monkeypatch.delenv("FLOCK_EVAL_HUB", raising=False)
    root = tmp_path / "staged"
    return uri, root, splits


def test_a_staged_copy_gives_the_rows_the_hugging_face_hub_gives(staged, monkeypatch):
    _, root, splits = staged
    from_hf = {pair: Dataset(REPO, FIRST, pair[0], pair[1]).load() for pair in splits}
    numbered_hf = Dataset(REPO, FIRST, "qa", "test", row_index=True).load()

    folder = hub.pull("acme", REPO, FIRST, root)
    assert folder == root / REPO / FIRST
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    monkeypatch.setattr(datasets, "load_dataset", None)  # the staged path never calls it

    for (subset, split), rows in from_hf.items():
        assert Dataset(REPO, FIRST, subset, split).load() == rows
    assert Dataset(REPO, FIRST, "qa", "test", row_index=True).load() == numbered_hf
    # The scored split's default and a few-shot split read by name.
    assert Dataset(REPO, FIRST, "qa", "test").load("train") == from_hf[("qa", "train")]

    # A second pull of the same pin leaves the staged copy as it is.
    assert hub.pull("acme", REPO, FIRST, root) == folder


def test_each_pin_stages_its_own_tagged_version(staged):
    _, root, _ = staged
    first = hub.pull("acme", REPO, FIRST, root)
    second = hub.pull("acme", REPO, SECOND, root)

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
        hub.pull("acme", REPO, "3" * 40, root)
    assert not root.exists()


def test_a_staged_read_needs_a_commit_hash(staged, monkeypatch):
    _, root, _ = staged
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    for revision in ("main", "v1.1", FIRST[:12]):
        with pytest.raises(ValueError, match="needs a commit hash"):
            Dataset(REPO, revision, "qa", "test").load()


def test_an_unstaged_pin_names_the_pull_command(staged, monkeypatch):
    _, root, _ = staged
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    with pytest.raises(FileNotFoundError, match=f"flock eval hub pull <org>/{REPO}@{FIRST}"):
        Dataset(REPO, FIRST, "qa", "test").load()


def test_a_staged_copy_of_another_pin_is_refused(staged, monkeypatch):
    _, root, _ = staged
    shutil.copytree(hub.pull("acme", REPO, FIRST, root), root / REPO / SECOND)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))

    with pytest.raises(ValueError, match=f"holds {REPO}@{FIRST}, not {REPO}@{SECOND}"):
        Dataset(REPO, SECOND, "qa", "test").load()


def test_a_split_the_copy_does_not_hold_is_refused(staged, monkeypatch):
    _, root, _ = staged
    hub.pull("acme", REPO, FIRST, root)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))

    with pytest.raises(KeyError, match="no split 'validation' of subset 'qa'"):
        Dataset(REPO, FIRST, "qa", "validation").load()


def test_the_writer_refuses_rows_with_its_bookkeeping_columns(tmp_path):
    for column in ("hf_subset", "hf_split"):
        rows = datasets.Dataset.from_list([{column: "x", "text": "a"}])
        with pytest.raises(ValueError, match=f"the rows have a {{'{column}'}} column"):
            hub.write(str(tmp_path / column), REPO, FIRST, {(None, "test"): rows})


def test_hub_pull_takes_an_org_a_repo_and_a_commit(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from flock.eval.cli import main
    from flock.hub import s3

    pulled = []
    monkeypatch.setattr(s3, "get_process", lambda org, read_only: pulled.append(org))
    monkeypatch.setattr(hub, "pull", lambda org, repo, revision, root: pulled.append(repo) or root)
    runner = CliRunner()

    refs = (
        f"acme/{REPO}@main",
        f"acme/{REPO}@{FIRST[:12]}",
        f"{REPO}@{FIRST}",
        f"acme//toy@{FIRST}",
    )
    for ref in refs:
        result = runner.invoke(main, ["hub", "pull", ref, "--to", str(tmp_path)])
        assert result.exit_code == 2, ref  # a usage error, before any sign-in
    assert pulled == []

    result = runner.invoke(main, ["hub", "pull", f"acme/{REPO}@{FIRST}", "--to", str(tmp_path)])
    assert result.exit_code == 0
    assert pulled == ["acme", REPO]

    def refuse(org, repo, revision, root):
        raise ValueError("tag on another revision")

    monkeypatch.setattr(hub, "pull", refuse)
    result = runner.invoke(main, ["hub", "pull", f"acme/{REPO}@{FIRST}", "--to", str(tmp_path)])
    assert result.exit_code == 1 and "tag on another revision" in result.output


def test_a_pull_replaces_a_copy_staged_from_an_earlier_version_of_the_tag(
    tmp_path, monkeypatch, real_load
):
    splits = toy_splits()
    uri = str(tmp_path / "hub" / "eval" / REPO)
    root = tmp_path / "staged"
    version = hub.write(uri, REPO, FIRST, {("qa", "test"): splits[("qa", "test")]})
    lance.dataset(uri).tags.create(FIRST, version)
    hub.pull("acme", REPO, FIRST, root)

    # A task now reads the few-shot split too: the mirror writes both and moves the tag.
    version = hub.write(
        uri, REPO, FIRST, {pair: splits[pair] for pair in (("qa", "test"), ("qa", "train"))}
    )
    lance.dataset(uri).tags.update(FIRST, version)
    hub.pull("acme", REPO, FIRST, root)

    monkeypatch.setattr(Dataset, "load", real_load)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    assert Dataset(REPO, FIRST, "qa", "train").load() == list(splits[("qa", "train")])
    assert [path.name for path in (root / REPO).iterdir()] == [FIRST]  # nothing left beside it


def test_a_pull_refuses_a_copy_of_another_repo(tmp_path):
    uri = str(tmp_path / "hub" / "eval" / "cerulean" / "other")
    version = hub.write(uri, REPO, FIRST, {("qa", "test"): toy_splits()[("qa", "test")]})
    lance.dataset(uri).tags.create(FIRST, version)
    with pytest.raises(ValueError, match=f"holds {REPO}@{FIRST}, not cerulean/other@{FIRST}"):
        hub.pull("acme", "cerulean/other", FIRST, tmp_path / "staged")


def test_the_writer_writes_the_rows_a_filtered_dataset_gives(tmp_path, monkeypatch, real_load):
    rows = datasets.Dataset.from_list([{"n": n} for n in range(5)]).filter(lambda row: row["n"] % 2)
    uri = str(tmp_path / "hub" / "eval" / REPO)
    version = hub.write(uri, REPO, FIRST, {(None, "test"): rows, (None, "train"): rows})
    lance.dataset(uri).tags.create(FIRST, version)
    hub.pull("acme", REPO, FIRST, tmp_path / "staged")

    monkeypatch.setattr(Dataset, "load", real_load)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(tmp_path / "staged"))
    assert Dataset(REPO, FIRST, None, "train").load() == [{"n": 1}, {"n": 3}]


def test_a_staged_copy_is_as_readable_as_its_staging_root(tmp_path):
    uri = str(tmp_path / "hub" / "eval" / REPO)
    version = hub.write(uri, REPO, FIRST, {("qa", "test"): toy_splits()[("qa", "test")]})
    lance.dataset(uri).tags.create(FIRST, version)

    def modes(root):
        """The modes of the repo's folders, of the copy's folders and of its files."""
        copy = root / REPO / FIRST
        between = {(root / REPO).stat().st_mode, (root / REPO).parent.stat().st_mode}
        folders = {copy.stat().st_mode} | {p.stat().st_mode for p in copy.rglob("*") if p.is_dir()}
        files = {p.stat().st_mode for p in copy.rglob("*") if p.is_file()}
        return [{stat.S_IMODE(mode) for mode in group} for group in (between, folders, files)]

    # A missing root is made readable by its owner only, whatever the umask.
    hub.pull("acme", REPO, FIRST, tmp_path / "made")
    assert modes(tmp_path / "made") == [{0o700}, {0o700}, {0o600}]

    # A group's root, setgid so new files keep its group: the folders between it and a copy take
    # its mode, and the group reads the copy but can't change it, whatever the puller's umask.
    root = tmp_path / "shared"
    root.mkdir()
    root.chmod(0o2770)
    previous = os.umask(0o077)
    try:
        hub.pull("acme", REPO, FIRST, root)
    finally:
        os.umask(previous)
    assert modes(root) == [{0o2770}, {0o2750}, {0o640}]


def test_a_pull_keeps_a_copy_another_pull_staged_meanwhile(staged, monkeypatch):
    _, root, _ = staged
    protect = hub._protect
    others = []

    def another_pull_finishes_first(folder, mode):
        protect(folder, mode)
        monkeypatch.setattr(hub, "_protect", protect)
        other = hub.pull("acme", REPO, FIRST, root)
        others.append((other, (other / "_versions").stat().st_ino))

    monkeypatch.setattr(hub, "_protect", another_pull_finishes_first)
    folder = hub.pull("acme", REPO, FIRST, root)

    # The other pull's copy, which a job may already read, is the one left, with nothing beside it.
    assert others == [(folder, (folder / "_versions").stat().st_ino)]
    assert [path.name for path in (root / REPO).iterdir()] == [FIRST]


def test_a_failed_pull_leaves_nothing_and_a_broken_copy_is_replaced(staged, monkeypatch, real_load):
    _, root, splits = staged
    folder = root / REPO / FIRST
    folder.mkdir(parents=True)
    (folder / "half-copied").write_text("")

    def fail(folder, mode):
        raise OSError("disk full")

    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    monkeypatch.setattr(Dataset, "load", real_load)
    with pytest.raises(FileNotFoundError, match="flock eval hub pull"):
        Dataset(REPO, FIRST, "qa", "train").load()

    with monkeypatch.context() as patched:
        patched.setattr(hub, "_protect", fail)
        with pytest.raises(OSError, match="disk full"):
            hub.pull("acme", REPO, FIRST, root)
    assert [path.name for path in (root / REPO).iterdir()] == [FIRST]

    assert hub.pull("acme", REPO, FIRST, root) == folder
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
        hub.pull("acme", REPO, FIRST, tmp_path / "staged")
    assert not (tmp_path / "staged").exists()


def test_a_copy_of_the_same_rows_from_another_org_is_kept(staged):
    _, root, _ = staged
    folder = hub.pull("acme", REPO, FIRST, root)
    before = (folder / "_versions").stat().st_ino

    # Its source and pairs decide its rows, so a pull through another org keeps it.
    assert hub.pull("other", REPO, FIRST, root) == folder
    assert (folder / "_versions").stat().st_ino == before
    assert hub.recorded(lance.dataset(str(folder)))["hub_org"] == "acme"


def test_a_stale_copy_that_cannot_be_removed_still_gives_way(tmp_path, monkeypatch, real_load):
    splits = toy_splits()
    uri = str(tmp_path / "hub" / "eval" / REPO)
    root = tmp_path / "staged"
    version = hub.write(uri, REPO, FIRST, {("qa", "test"): splits[("qa", "test")]})
    lance.dataset(uri).tags.create(FIRST, version)
    folder = hub.pull("acme", REPO, FIRST, root)
    locked = folder / "locked"
    locked.mkdir()
    (locked / "kept").write_text("")
    locked.chmod(0o500)

    version = hub.write(
        uri, REPO, FIRST, {pair: splits[pair] for pair in (("qa", "test"), ("qa", "train"))}
    )
    lance.dataset(uri).tags.update(FIRST, version)
    try:
        assert hub.pull("acme", REPO, FIRST, root) == folder
        monkeypatch.setattr(Dataset, "load", real_load)
        monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
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
    with pytest.raises(FileNotFoundError, match=f"cerulean/gone@{FIRST}, {REPO}@{FIRST}"):
        hub.check_staged(root, pins)

    hub.pull("acme", REPO, FIRST, root)
    hub.check_staged(root, {(REPO, FIRST): {("qa", "test"), ("qa", "train")}})
    with pytest.raises(FileNotFoundError, match=f"split a task reads: {REPO}@{FIRST}\\."):
        hub.check_staged(root, {(REPO, FIRST): {("qa", "validation")}})


def test_a_run_with_an_unstaged_dataset_fails_before_its_model_loads(tmp_path, monkeypatch):
    from flock.eval.model import ScoringModel
    from flock.eval.run import EvalConfig, evaluate

    def never(*args, **kwargs):
        raise AssertionError("the model loaded")

    monkeypatch.setattr(ScoringModel, "from_pretrained", never)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="allenai/ai2_arc@"):
        evaluate(EvalConfig(model="tiny@0", tasks=("arc_easy",)))
