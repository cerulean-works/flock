"""Fake Hugging Face repos for the import tests, laid out as `flock.hub.importer.LocalSource` reads them."""

import gzip
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import zstandard

MMLU = "cais-test/mmlu"
DOLMA = "allenai-test/dolma"

SUBJECTS = ["abstract_algebra", "anatomy"]
SPLITS = {"test": 4, "validation": 2, "dev": 1}  # rows per subject

MMLU_README_BODY = "# MMLU (test copy)\n\nMultiple choice questions in 57 subjects.\n"


def _mmlu_rows(subject: str, split: str) -> pa.Table:
    n = SPLITS[split]
    return pa.table(
        {
            "question": [f"{subject} {split} question {i}?" for i in range(n)],
            "subject": [subject] * n,
            "choices": [[f"a{i}", f"b{i}", f"c{i}", f"d{i}"] for i in range(n)],
            "answer": [i % 4 for i in range(n)],
        }
    )


def make_mmlu(root: Path) -> Path:
    """An mmlu-like repo: a config per subject plus `all`, each with test, validation and dev splits in Parquet, and a README whose YAML lists the configs."""
    repo = root / MMLU
    configs = []
    for config in [*SUBJECTS, "all"]:
        data_files = []
        for split in SPLITS:
            path = repo / config / f"{split}-00000-of-00001.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            subjects = SUBJECTS if config == "all" else [config]
            pq.write_table(pa.concat_tables([_mmlu_rows(s, split) for s in subjects]), path)
            data_files.append(f"  - split: {split}\n    path: {config}/{split}-*.parquet")
        configs.append(f"- config_name: {config}\n  data_files:\n" + "\n".join(data_files))
    readme = "---\nlicense: mit\nconfigs:\n" + "\n".join(configs) + "\n---\n" + MMLU_README_BODY
    (repo / "README.md").write_text(readme)
    return repo


def mmlu_rows(config: str, split: str) -> int:
    return SPLITS[split] * (len(SUBJECTS) if config == "all" else 1)


def dolma_docs(shard: int, n: int = 5) -> list[dict]:
    """Dolma documents whose `metadata` changes shape from row to row (and is sometimes missing)."""
    docs = []
    for i in range(n):
        doc: dict = {
            "id": f"doc-{shard}-{i}",
            "text": f"Text of document {i} in shard {shard}. " + "é" * i,
            "source": "test",
            "added": "2026-10-26T00:00:00Z",
            "created": f"2020-01-0{i + 1}",
        }
        shapes = [
            {"url": f"https://example.com/{i}", "lang": {"en": 0.9}},
            {"length": i, "tags": ["a", "b"]},
            {"nested": {"deep": [1, 2, {"x": None}]}},
            None,
        ]
        if i % 5 != 4:
            doc["metadata"] = shapes[i % 4]
        if i % 2:
            doc["attributes"] = {"quality": [[0, 10, 0.5]]}
        docs.append(doc)
    return docs


def make_dolma(root: Path, shards: int = 3) -> Path:
    """A tiny Dolma repo: `.json.gz` and `.jsonl.zst` shards under `data/`, no configs in the README."""
    repo = root / DOLMA
    (repo / "data").mkdir(parents=True, exist_ok=True)
    for shard in range(shards):
        lines = "".join(json.dumps(d) + "\n" for d in dolma_docs(shard)).encode()
        if shard % 2 == 0:
            (repo / "data" / f"part-{shard:04d}.json.gz").write_bytes(gzip.compress(lines))
        else:
            data = zstandard.ZstdCompressor().compress(lines)
            (repo / "data" / f"part-{shard:04d}.jsonl.zst").write_bytes(data)
    (repo / "README.md").write_text("---\nlicense: odc-by\n---\n# Dolma (test copy)\n")
    return repo
