import gzip
import json
import math
from pathlib import Path
from types import SimpleNamespace

import datasets
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from flock.eval.scripts import source_census
from flock.eval.tasks import Dataset, Suite
from flock.eval.tasks.suite import source_suite

from .toy import DOMAIN_ROWS, TEXT_ROWS

# A dataset card in the shape of Paloma's and tw-text's: one configuration per source, each
# split a pattern, or a list of them, over the repo's files. `forum` is a source the toy suite
# leaves out.
README = """---
configs:
- config_name: news
  data_files:
  - split: test
    path: data/news/test.parquet
- config_name: wiki
  data_files:
  - split: val
    path: "wiki/val/*"
  - split: test
    path: "wiki/test/*"
- config_name: cna
  data_files:
  - split: test
    path: [data/cna/a.parquet, data/cna/b.parquet]
- config_name: forum
  data_files:
  - split: test
    path: data/forum/test.parquet
---
"""


def write_jsonl_gz(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        f.writelines(json.dumps(row) + "\n" for row in rows)


def write_parquet(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)


# The toy suite's table when it lists every domain: `news` has two, `wiki` has rows without a
# `subdomain`, and every row of `cna` carries its source's name, as in `tw_text`.
SOURCES = {"news": ("news", "exam"), "wiki": (), "cna": ("cna",)}


def toy_suite(sources: dict[str, tuple[str, ...]]) -> Suite:
    members = []
    for source, domains in sources.items():
        dataset = Dataset(repo="toy/sources", revision="0" * 40, subset=source, split="test")
        members.append(source_suite("toy", dataset, domains))
    return Suite("toy", tuple(members), metric="bits_per_byte")


@pytest.fixture
def repo(tmp_path, monkeypatch, tokenizer) -> Path:
    """A local copy of the toy dataset's files, which the census downloads and lists from."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text(README)
    write_parquet(root / "data/news/test.parquet", DOMAIN_ROWS)
    write_jsonl_gz(root / "wiki/test/test-0.jsonl.gz", TEXT_ROWS[:2])
    write_jsonl_gz(root / "wiki/test/test-1.jsonl.gz", TEXT_ROWS[2:])
    write_jsonl_gz(root / "wiki/val/val-0.jsonl.gz", TEXT_ROWS)
    # `datasets` skips a hidden file, so a domain in it is not the split's.
    write_jsonl_gz(root / "wiki/test/.stray.jsonl.gz", DOMAIN_ROWS)
    write_parquet(root / "data/cna/a.parquet", [{**TEXT_ROWS[0], "subdomain": "cna"}])
    write_parquet(root / "data/cna/b.parquet", [{**TEXT_ROWS[1], "subdomain": "cna"}])
    write_parquet(root / "data/forum/test.parquet", DOMAIN_ROWS)

    # `datasets` resolves the local copy's card as it would the hub's, and the census sees the
    # files under the hub's URLs.
    real_names = datasets.get_dataset_config_names
    real_builder = datasets.load_dataset_builder

    def builder(repo_id, name, revision):
        data_files = {}
        resolved = real_builder(str(root), name).config.data_files
        assert resolved is not None  # A card with configurations always resolves its files.
        for split, files in resolved.items():
            urls = [
                f"hf://datasets/{repo_id}@{revision}/{Path(f).relative_to(root)}" for f in files
            ]
            data_files[split] = urls
        return SimpleNamespace(config=SimpleNamespace(data_files=data_files))

    monkeypatch.setattr(datasets, "get_dataset_config_names", lambda *a, **k: real_names(str(root)))
    monkeypatch.setattr(datasets, "load_dataset_builder", builder)
    monkeypatch.setattr(
        source_census, "hf_hub_download", lambda repo_id, filename, **kwargs: str(root / filename)
    )
    monkeypatch.setattr(
        source_census.AutoTokenizer, "from_pretrained", lambda *args, **kwargs: tokenizer
    )
    return root


def test_count_source_reads_jsonl_gz_and_parquet_alike(tmp_path, tokenizer):
    write_jsonl_gz(tmp_path / "test-0.jsonl.gz", DOMAIN_ROWS)
    write_jsonl_gz(tmp_path / "test-1.jsonl.gz", [])
    write_parquet(tmp_path / "test.parquet", DOMAIN_ROWS)

    from_jsonl = source_census.count_source(
        tokenizer, 4, [tmp_path / "test-0.jsonl.gz", tmp_path / "test-1.jsonl.gz"]
    )
    from_parquet = source_census.count_source(tokenizer, 4, [tmp_path / "test.parquet"])

    del from_jsonl["empty_files"], from_parquet["empty_files"]
    assert from_jsonl == from_parquet
    assert source_census.count_source(
        tokenizer, 4, [tmp_path / "test-1.jsonl.gz", tmp_path / "test.parquet"]
    )["empty_files"] == ["test-1.jsonl.gz"]


def test_count_source_counts_bytes_tokens_and_windows_per_domain(tmp_path, tokenizer):
    write_jsonl_gz(tmp_path / "test.jsonl.gz", DOMAIN_ROWS)

    counts = source_census.count_source(tokenizer, 4, [tmp_path / "test.jsonl.gz"])

    lengths = [
        len(tokenizer(row["text"], add_special_tokens=False)["input_ids"]) for row in DOMAIN_ROWS
    ]
    sizes = [len(row["text"].encode()) for row in DOMAIN_ROWS]
    assert counts["docs"] == 3
    assert counts["bytes"] == sum(sizes)  # UTF-8 bytes, not characters
    assert counts["bytes"] > sum(len(row["text"]) for row in DOMAIN_ROWS)
    assert counts["tokens"] == sum(lengths)
    assert counts["windows"] == sum(math.ceil(length / 4) for length in lengths)
    assert counts["over_4"] == sum(length > 4 for length in lengths)
    # Most documents first: news has two, exam one.
    assert counts["domain_docs"] == {"news": 2, "exam": 1}
    assert counts["domain_bytes"] == {"news": sizes[0] + sizes[2], "exam": sizes[1]}
    assert counts["domain_tokens"] == {"news": lengths[0] + lengths[2], "exam": lengths[1]}


def test_count_source_ranks_palomas_numbered_domains_before_counting_documents(tmp_path, tokenizer):
    rows = [
        {"id": "1", "text": "a", "subdomain": "10_b"},
        {"id": "2", "text": "a", "subdomain": "2_a"},
        {"id": "3", "text": "a", "subdomain": "10_b"},
    ]
    write_jsonl_gz(tmp_path / "test.jsonl.gz", rows)

    counts = source_census.count_source(tokenizer, 4, [tmp_path / "test.jsonl.gz"])

    assert list(counts["domain_docs"]) == ["2_a", "10_b"]


def test_count_source_counts_rows_without_a_subdomain_under_the_empty_domain(tmp_path, tokenizer):
    rows = [*DOMAIN_ROWS, {**TEXT_ROWS[0], "id": "t-4"}, {**TEXT_ROWS[1], "subdomain": None}]
    write_jsonl_gz(tmp_path / "test.jsonl.gz", rows)

    counts = source_census.count_source(tokenizer, 4, [tmp_path / "test.jsonl.gz"])

    assert counts["domain_docs"] == {"news": 2, "": 2, "exam": 1}


def test_count_source_rejects_another_format(tmp_path, tokenizer):
    (tmp_path / "test.csv").write_text("id,text\n")
    with pytest.raises(ValueError, match="reads .jsonl.gz and .parquet files only"):
        source_census.count_source(tokenizer, 4, [tmp_path / "test.csv"])


def test_the_census_passes_when_the_suite_lists_every_domain_and_reports_left_out_sources(
    repo, tmp_path, monkeypatch, capsys
):
    monkeypatch.setitem(source_census.SUITES, "toy", toy_suite(SOURCES))
    out = tmp_path / "census.json"

    source_census.main("toy", out)

    census = json.loads(out.read_text())
    assert census["differences"] == {}
    assert census["unlisted_sources"] == ["forum"]
    # The scored split only: wiki's `val` file and its hidden file aren't counted.
    assert census["sources"]["wiki"]["docs"] == 3
    assert census["sources"]["news"]["domain_docs"] == {"news": 2, "exam": 1}
    assert census["sources"]["cna"]["domain_docs"] == {"cna": 2}  # both files of the list
    printed = capsys.readouterr().out
    assert "'news': ('news', 'exam'),\n" in printed
    assert "'wiki': (),\n" in printed
    assert "'cna': ('cna',),\n" in printed
    assert "Sources toy leaves out: forum" in printed


def test_the_census_fails_when_a_domain_is_not_listed_or_not_in_the_split(
    repo, tmp_path, monkeypatch
):
    out = tmp_path / "census.json"

    # `exam` is in the split and no task scores it.
    monkeypatch.setitem(source_census.SUITES, "toy", toy_suite({**SOURCES, "news": ("news",)}))
    with pytest.raises(SystemExit) as exit_:
        source_census.main("toy", out)
    assert exit_.value.code == 1
    differences = json.loads(out.read_text())["differences"]
    assert differences == {"news": {"not_listed": ["exam"], "not_in_split": []}}

    # `sports` is listed and the split has no rows for it.
    sports = {**SOURCES, "news": ("news", "exam", "sports")}
    monkeypatch.setitem(source_census.SUITES, "toy", toy_suite(sports))
    with pytest.raises(SystemExit):
        source_census.main("toy", out)
    differences = json.loads(out.read_text())["differences"]
    assert differences == {"news": {"not_listed": [], "not_in_split": ["sports"]}}

    # `cna` listed with no value would read rows without a `subdomain`, and its rows all have one.
    monkeypatch.setitem(source_census.SUITES, "toy", toy_suite({**SOURCES, "cna": ()}))
    with pytest.raises(SystemExit):
        source_census.main("toy", out)
    differences = json.loads(out.read_text())["differences"]
    assert differences == {
        "cna": {"not_listed": ["cna"], "not_in_split": ["(rows without a subdomain)"]}
    }

    # A listed source with no configuration in the card has none of its domains.
    monkeypatch.setitem(source_census.SUITES, "toy", toy_suite({**SOURCES, "blog": ("blog",)}))
    with pytest.raises(SystemExit):
        source_census.main("toy", out)
    differences = json.loads(out.read_text())["differences"]
    assert differences == {"blog": {"not_listed": [], "not_in_split": ["blog"]}}
