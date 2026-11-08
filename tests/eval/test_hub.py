import shutil

import datasets
import lance
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
    uri, root, splits = staged
    from_hf = {pair: Dataset(REPO, FIRST, pair[0], pair[1]).load() for pair in splits}
    numbered_hf = Dataset(REPO, FIRST, "qa", "test", row_index=True).load()

    folder = hub.pull(uri, FIRST, root)
    assert folder == root / REPO / FIRST
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    monkeypatch.setattr(datasets, "load_dataset", None)  # the staged path never calls it

    for (subset, split), rows in from_hf.items():
        assert Dataset(REPO, FIRST, subset, split).load() == rows
    assert Dataset(REPO, FIRST, "qa", "test", row_index=True).load() == numbered_hf
    # The scored split's default and a few-shot split read by name.
    assert Dataset(REPO, FIRST, "qa", "test").load("train") == from_hf[("qa", "train")]

    # A second pull of the same pin leaves the staged copy as it is.
    assert hub.pull(uri, FIRST, root) == folder


def test_each_pin_stages_its_own_tagged_version(staged):
    uri, root, _ = staged
    first = hub.pull(uri, FIRST, root)
    second = hub.pull(uri, SECOND, root)

    assert first != second
    for folder, revision in ((first, FIRST), (second, SECOND)):
        table = lance.dataset(str(folder))
        transaction = table.read_transaction(table.version)
        assert transaction is not None and transaction.transaction_properties is not None
        assert transaction.transaction_properties["source_revision"] == revision


def test_a_pull_refuses_a_tag_on_a_version_of_another_revision(staged):
    uri, root, _ = staged
    lance.dataset(uri).tags.create("3" * 40, 1)  # version 1 holds FIRST

    with pytest.raises(ValueError, match="which records revision '1111"):
        hub.pull(uri, "3" * 40, root)
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
    uri, root, _ = staged
    shutil.copytree(hub.pull(uri, FIRST, root), root / REPO / SECOND)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))

    with pytest.raises(ValueError, match=f"holds {REPO}@{FIRST}, not {REPO}@{SECOND}"):
        Dataset(REPO, SECOND, "qa", "test").load()


def test_a_split_the_copy_does_not_hold_is_refused(staged, monkeypatch):
    uri, root, _ = staged
    hub.pull(uri, FIRST, root)
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
    monkeypatch.setattr(hub, "pull", lambda uri, revision, root: pulled.append(uri) or root)
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
    assert pulled == ["acme", f"s3://flock-datasets/eval/{REPO}"]

    def refuse(uri, revision, root):
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
    hub.pull(uri, FIRST, root)

    # A task now reads the few-shot split too: the mirror writes both and moves the tag.
    version = hub.write(
        uri, REPO, FIRST, {pair: splits[pair] for pair in (("qa", "test"), ("qa", "train"))}
    )
    lance.dataset(uri).tags.update(FIRST, version)
    hub.pull(uri, FIRST, root)

    monkeypatch.setattr(Dataset, "load", real_load)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(root))
    assert Dataset(REPO, FIRST, "qa", "train").load() == list(splits[("qa", "train")])
    assert [path.name for path in (root / REPO).iterdir()] == [FIRST]  # nothing left beside it


def test_a_pull_refuses_a_copy_of_another_repo(tmp_path):
    uri = str(tmp_path / "hub" / "eval" / "cerulean" / "other")
    version = hub.write(uri, REPO, FIRST, {("qa", "test"): toy_splits()[("qa", "test")]})
    lance.dataset(uri).tags.create(FIRST, version)
    with pytest.raises(ValueError, match="holds a copy of 'cerulean/toy'"):
        hub.pull(uri, FIRST, tmp_path / "staged")


def test_the_writer_writes_the_rows_a_filtered_dataset_gives(tmp_path, monkeypatch, real_load):
    rows = datasets.Dataset.from_list([{"n": n} for n in range(5)]).filter(lambda row: row["n"] % 2)
    uri = str(tmp_path / "hub" / "eval" / REPO)
    version = hub.write(uri, REPO, FIRST, {(None, "test"): rows, (None, "train"): rows})
    lance.dataset(uri).tags.create(FIRST, version)
    hub.pull(uri, FIRST, tmp_path / "staged")

    monkeypatch.setattr(Dataset, "load", real_load)
    monkeypatch.setenv("FLOCK_EVAL_HUB", str(tmp_path / "staged"))
    assert Dataset(REPO, FIRST, None, "train").load() == [{"n": 1}, {"n": 3}]


def test_a_staged_copy_is_as_readable_as_its_staging_root(tmp_path):
    root = tmp_path / "staged"
    (root / REPO).mkdir(parents=True)
    (root / REPO).chmod(0o755)  # the repo's folder, which every pin of it shares
    uri = str(tmp_path / "hub" / "eval" / REPO)
    version = hub.write(uri, REPO, FIRST, {("qa", "test"): toy_splits()[("qa", "test")]})
    lance.dataset(uri).tags.create(FIRST, version)

    assert hub.pull(uri, FIRST, root).stat().st_mode & 0o777 == 0o755
