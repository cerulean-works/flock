from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

from flock.eval import run
from flock.eval.cli import main
from flock.eval.items import items
from flock.eval.model import ScoringModel
from flock.eval.results import read_results
from flock.eval.run import EvalConfig, evaluate, score_task
from flock.eval.tasks import Dataset, PerplexityTask, RowFields
from flock.eval.tokenizer import SentencePieceTokenizer

from .test_tokenizer import train
from .toy import ROWS, TASK, not_about_bees

if TYPE_CHECKING:
    from pathlib import Path

    from transformers import LlamaForCausalLM, PreTrainedTokenizerFast


def lm_eval_doc_hash(row: dict[str, Any]) -> str:
    """lm-evaluation-harness's `doc_hash` as `lm_eval/evaluator.py` computes it at v0.4.3."""
    return hashlib.sha256(json.dumps(row, indent=2, ensure_ascii=False).encode()).hexdigest()


@pytest.fixture
def offline_model(monkeypatch: pytest.MonkeyPatch, scorer: ScoringModel) -> None:
    monkeypatch.setitem(run.TASKS, TASK.name, TASK)
    monkeypatch.setattr(ScoringModel, "from_pretrained", lambda *args, **kwargs: scorer)
    monkeypatch.setenv("FLOCK_COMMIT", "abc123")


def test_load_records_each_docs_split_position_before_keep_and_lm_eval_doc_hash() -> None:
    [doc], _ = dataclasses.replace(TASK, keep=not_about_bees).load()

    assert doc.id == "q-2"
    assert doc.source is not None
    assert doc.source.split == "test"
    assert doc.source.index == 1  # its position in the split, though `keep` dropped q-1
    assert doc.source.doc_hash == lm_eval_doc_hash(ROWS["test"][1])
    assert (doc.source.native_id, doc.source.subject, doc.source.meta) == (None, None, {})


def test_row_fields_copy_named_columns_and_leave_the_task_hash_alone() -> None:
    fields = RowFields(native_id="answerKey", subject="question", meta=("answerKey", "id"))
    task = dataclasses.replace(TASK, row_fields=fields)
    docs, shots = task.load()

    source = docs[1].source
    assert source is not None
    assert source.native_id == "2"
    assert source.subject == "哪一個是水果？"
    assert source.meta == {"answerKey": "2", "id": "q-2"}
    assert task.requests(docs, shots).hash == TASK.requests(*TASK.load()).hash
    # A native id equal to the doc's own id says nothing new, so it isn't recorded.
    [same, _], _ = dataclasses.replace(TASK, row_fields=RowFields(native_id="id")).load()
    assert same.source is not None and same.source.native_id is None


def test_meta_takes_only_scalars_and_a_named_column_must_exist() -> None:
    with pytest.raises(ValueError, match="toy: doc q-1: meta columns must hold scalars"):
        dataclasses.replace(TASK, row_fields=RowFields(meta=("choices",))).load()
    with pytest.raises(KeyError, match="level"):
        dataclasses.replace(TASK, row_fields=RowFields(meta=("level",))).load()


def test_the_doc_hash_of_a_row_index_dataset_leaves_out_the_index_it_adds(
    rows_by_split: dict[str, list[dict[str, Any]]],
) -> None:
    rows = [{**row, "index": i} for i, row in enumerate(ROWS["test"])]
    rows_by_split.update(test=rows, train=ROWS["train"])
    task = dataclasses.replace(TASK, dataset=dataclasses.replace(TASK.dataset, row_index=True))

    docs, _ = task.load()
    assert [doc.source.doc_hash for doc in docs if doc.source] == [
        lm_eval_doc_hash(row) for row in ROWS["test"]
    ]


def test_prediction_rows_carry_the_source_and_lm_eval_prompt_hash(
    scorer: ScoringModel, tmp_path: Path
) -> None:
    fields = RowFields(subject="question", meta=("answerKey",))
    task = dataclasses.replace(TASK, row_fields=fields)
    score_task(scorer, task, out=tmp_path)
    lines = (tmp_path / "predictions" / "toy.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines[1:]]

    docs, shots = task.load()
    requests = task.requests(docs, shots).requests
    # Each doc's first request: q-1 has three choices.
    first_contexts = [requests[0].context, requests[3].context]
    for row, raw, context in zip(rows, ROWS["test"], first_contexts, strict=True):
        assert row["split"] == "test"
        assert row["doc_hash"] == lm_eval_doc_hash(raw)
        assert row["prompt_hash"] == hashlib.sha256(context.encode()).hexdigest()
        assert row["subject"] == raw["question"]
        assert row["meta"] == {"answerKey": raw["answerKey"]}
        assert "native_id" not in row
    assert [row["source_index"] for row in rows] == [0, 1]


def test_perplexity_rows_name_their_source_and_domain(scorer: ScoringModel, tmp_path: Path) -> None:
    task = PerplexityTask(
        name="toy_news",
        dataset=Dataset("cerulean/toy", "0" * 40, "toy_source", "domains"),
        domain="news",
        window=8,
    )
    score_task(scorer, task, out=tmp_path)

    lines = (tmp_path / "predictions" / "toy_news.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert [(row["source"], row["domain"]) for row in rows] == [("toy_source", "news")] * 2
    assert "text" not in rows[0]  # no document text (a perplexity row never holds it)


def test_provenance_names_the_tokenizer_and_hashes_its_definition(
    offline_model: None, tokenizer: PreTrainedTokenizerFast, tmp_path: Path
) -> None:
    evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path))

    provenance = read_results(str(tmp_path)).provenance
    assert provenance.tokenizer == tokenizer.name_or_path
    definition = tokenizer.backend_tokenizer.to_str().encode()
    assert provenance.tokenizer_hash == hashlib.sha256(definition).hexdigest()


def test_a_sentencepiece_tokenizer_is_hashed_by_its_model_bytes(
    lm: LlamaForCausalLM, tmp_path: Path
) -> None:
    model_file = train(tmp_path / "tokenizer.model")
    scorer = ScoringModel(lm, SentencePieceTokenizer(model_file), name="tiny@0")

    assert scorer.tokenizer_name == str(model_file)
    assert scorer.tokenizer_hash == hashlib.sha256(model_file.read_bytes()).hexdigest()


def test_items_and_the_cli_show_where_a_doc_comes_from(
    offline_model: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    task = dataclasses.replace(TASK, row_fields=RowFields(subject="question"))
    monkeypatch.setitem(run.TASKS, TASK.name, task)
    evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=tmp_path / "run"))

    [first, _] = items([read_results(str(tmp_path / "run"))], "toy", "all")
    assert first.source == {
        "split": "test",
        "source_index": 0,
        "subject": "What do bees make?",
        "doc_hash": lm_eval_doc_hash(ROWS["test"][0]),
        "prompt_hash": first.source["prompt_hash"],
    }
    assert first.to_dict()["source"] == first.source

    args = ["items", str(tmp_path / "run"), "--task", "toy", "--only", "all", "--limit", "1"]
    output = CliRunner().invoke(main, args)
    assert output.exit_code == 0, output.output
    assert "split test  source_index 0  subject What do bees make?" in output.output


def test_a_schema_11_run_still_reads_and_its_items_have_no_source(
    offline_model: None, tmp_path: Path
) -> None:
    folder = tmp_path / "run"
    evaluate(EvalConfig(model="tiny@0", tasks=("toy",), out=folder))
    # The same run as schema 11 wrote it: no source fields, hashes or tokenizer.
    data = json.loads((folder / "results.json").read_text())
    data["schema_version"] = 11
    del data["provenance"]["tokenizer"], data["provenance"]["tokenizer_hash"]
    (folder / "results.json").write_text(json.dumps(data))
    path = folder / "predictions" / "toy.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    dropped = ("split", "source_index", "doc_hash", "prompt_hash")
    old = [rows[0]] + [{k: v for k, v in row.items() if k not in dropped} for row in rows[1:]]
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in old), "utf-8")

    written = read_results(str(folder))
    assert written.provenance.tokenizer is None
    assert all(item.source == {} for item in items([written], "toy", "all"))


def test_a_tokenizer_without_a_tokenizers_backend_is_hashed_by_its_vocabulary(
    lm: LlamaForCausalLM, tokenizer: PreTrainedTokenizerFast
) -> None:
    class PythonTokenizer:
        """Stands in for a transformers tokenizer written in Python: a vocabulary, no backend."""

        name_or_path = "python-tok"
        bos_token_id = None

        def get_vocab(self) -> dict[str, int]:
            return {"b": 1, "a": 0}

    scorer = ScoringModel(lm, tokenizer, name="tiny@0")
    scorer.tokenizer = PythonTokenizer()  # ty: ignore[invalid-assignment]
    vocab = json.dumps([["a", 0], ["b", 1]]).encode()
    assert scorer.tokenizer_hash == hashlib.sha256(vocab).hexdigest()
