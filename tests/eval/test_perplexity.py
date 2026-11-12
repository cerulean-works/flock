from __future__ import annotations

import dataclasses
import json
import math
from typing import TYPE_CHECKING, Never

import pytest
import torch
from transformers import PreTrainedTokenizerBase

from flock.eval import run
from flock.eval.model import ScoringModel
from flock.eval.requests import Scores
from flock.eval.results import Results, perplexity_prediction_rows
from flock.eval.run import EvalConfig, evaluate, score_task
from flock.eval.tasks import SUITES, TASKS, Dataset, PerplexityTask, Suite, TextDoc
from flock.eval.tasks.paloma import PALOMA
from flock.eval.tasks.suite import source_suite
from flock.eval.tasks.tw_text import TW_TEXT

from .toy import DOMAIN_ROWS, PROVENANCE, TASK, TEXT_ROWS, TEXT_TASK

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any

    from transformers import LlamaForCausalLM, PreTrainedTokenizerFast


DOMAINS = dataclasses.replace(TEXT_TASK.dataset, split="domains")
NEWS = dataclasses.replace(TEXT_TASK, name="toy_text__news", dataset=DOMAINS, domain="news")
EXAM = dataclasses.replace(TEXT_TASK, name="toy_text__exam", dataset=DOMAINS, domain="exam")
SOURCE = Suite("toy_text:source", (NEWS, EXAM))


def test_load_keeps_the_domains_rows_in_order() -> None:
    assert [doc.id for doc in TEXT_TASK.load()] == ["t-1", "t-2", "t-3"]
    assert [doc.id for doc in NEWS.load()] == ["t-1", "t-3"]
    assert [doc.id for doc in NEWS.load(limit=1)] == ["t-1"]
    assert NEWS.load()[1].text == TEXT_ROWS[2]["text"]


def test_load_rejects_an_empty_domain_or_a_limit_below_one() -> None:
    with pytest.raises(ValueError, match="no rows in domain 'sports'"):
        dataclasses.replace(NEWS, domain="sports").load()
    with pytest.raises(ValueError, match="no rows in domain 'news'"):
        dataclasses.replace(TEXT_TASK, domain="news").load()
    with pytest.raises(ValueError, match="limit must be at least 1"):
        TEXT_TASK.load(limit=0)


def test_load_refuses_to_pool_a_sources_domains() -> None:
    with pytest.raises(ValueError, match="the rows have domains"):
        dataclasses.replace(NEWS, domain=None).load()


def test_window_below_one_raises() -> None:
    with pytest.raises(ValueError, match="window must be at least 1"):
        dataclasses.replace(TEXT_TASK, window=0)


def test_task_hash_covers_the_definition_and_every_text() -> None:
    docs = TEXT_TASK.load()
    changed_text = [docs[0], dataclasses.replace(docs[1], text="玉山"), docs[2]]
    hashes = {
        TEXT_TASK.task_hash(docs),
        TEXT_TASK.task_hash(changed_text),
        dataclasses.replace(TEXT_TASK, window=16).task_hash(docs),
    }
    assert len(hashes) == 3
    assert TEXT_TASK.task_hash(TEXT_TASK.load()) == TEXT_TASK.task_hash(docs)


def test_bits_per_byte_is_a_ratio_of_sums_over_utf8_bytes(scorer: ScoringModel) -> None:
    texts = [row["text"] for row in TEXT_ROWS]
    scores = scorer.rolling_loglikelihood(texts, TEXT_TASK.window)
    logprob = scores.logprob.tolist()
    num_bytes = [len(text.encode()) for text in texts]
    # Chinese and the emoji take more than one byte each.
    assert num_bytes[1] > len(texts[1])

    result = score_task(scorer, TEXT_TASK)

    expected = -sum(logprob) / (sum(num_bytes) * math.log(2))
    assert result.metrics["bits_per_byte"] == pytest.approx(expected, rel=1e-6)
    mean_of_ratios = sum(-lp / (b * math.log(2)) for lp, b in zip(logprob, num_bytes)) / 3
    assert result.metrics["bits_per_byte"] != pytest.approx(mean_of_ratios, rel=1e-3)
    assert result.primary_metric == "bits_per_byte"
    assert result.num_docs == 3
    assert result.task_hash == TEXT_TASK.task_hash(TEXT_TASK.load())
    assert result.truncation.requests == 0


def test_loss_per_token_averages_over_every_token_of_every_doc(
    scorer: ScoringModel, tokenizer: PreTrainedTokenizerFast
) -> None:
    texts = [row["text"] for row in TEXT_ROWS]
    logprob = scorer.rolling_loglikelihood(texts, TEXT_TASK.window).logprob.tolist()
    # Each token is predicted once, so the count is the doc's own tokens, no prefix and no
    # window overlap.
    num_tokens = [len(tokenizer.encode(text, add_special_tokens=False)) for text in texts]

    result = score_task(scorer, TEXT_TASK)

    expected = -sum(logprob) / sum(num_tokens)
    assert result.metrics["loss_per_token"] == pytest.approx(expected, rel=1e-12)
    assert result.metrics["perplexity"] == math.exp(result.metrics["loss_per_token"])
    assert list(result.metrics) == list(TEXT_TASK.reported_metrics)


def test_perplexity_is_infinite_where_exp_overflows(
    monkeypatch: pytest.MonkeyPatch, scorer: ScoringModel, tmp_path: Path
) -> None:
    # A diverged checkpoint: about 1e4 nats per token, far past `math.exp`'s limit.
    score = scorer.rolling_loglikelihood

    def diverged(texts: list[str], window: int, token_ids: list[list[int]] | None = None) -> Scores:
        scores = score(texts, window, token_ids)
        return dataclasses.replace(scores, logprob=-1e4 * scores.num_tokens.double())

    monkeypatch.setattr(scorer, "rolling_loglikelihood", diverged)
    result = score_task(scorer, TEXT_TASK)

    assert result.metrics["loss_per_token"] == pytest.approx(1e4)
    assert result.metrics["perplexity"] == math.inf

    # JSON has no Infinity token, so the file holds null and a strict parser reads it.
    def refuse(token: str) -> Never:
        raise ValueError(f"non-JSON token {token}")

    path = Results(PROVENANCE, [result]).write(tmp_path)
    written = json.loads(path.read_text(), parse_constant=refuse)["tasks"][0]["metrics"]
    assert written == {**result.metrics, "perplexity": None}


def test_score_task_does_not_depend_on_batching(scorer: ScoringModel) -> None:
    one_batch = score_task(scorer, TEXT_TASK).metrics
    scorer.batch_tokens = 1
    assert score_task(scorer, TEXT_TASK).metrics == pytest.approx(one_batch)


def test_primary_metric_must_be_reported() -> None:
    assert dataclasses.replace(TEXT_TASK, primary_metric="perplexity").primary_metric
    with pytest.raises(ValueError, match="primary_metric must be one of"):
        dataclasses.replace(TEXT_TASK, primary_metric="bpb")


def test_task_hash_leaves_out_the_primary_metric_and_keeps_paloma_hashes() -> None:
    docs = [TextDoc("t-1", "玉山 is 3,952 m tall.")]
    wikitext = TASKS["paloma_wikitext_103"]
    assert isinstance(wikitext, PerplexityTask)
    reread = dataclasses.replace(wikitext, primary_metric="perplexity")
    assert reread.task_hash(docs) == wikitext.task_hash(docs)
    # The hashes `main` gave before the field existed, so no bank or results file goes stale.
    assert wikitext.task_hash(docs) == (
        "889027a97129888e4050e8b3ae822dc53e6df5d65be1bcdf9bad6bd0af7b4846"
    )
    nytimes = TASKS["paloma_c4_100_domains__1_www.nytimes.com"]
    assert isinstance(nytimes, PerplexityTask)
    assert nytimes.task_hash(docs) == (
        "341659acc40baaf6e5a22749f6768cd99c9744c48331b5fd59c2d6f49222280d"
    )


def test_score_task_writes_one_prediction_row_per_doc_and_never_replaces_it(
    scorer: ScoringModel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert isinstance(scorer.tokenizer, PreTrainedTokenizerBase)
    result = score_task(scorer, TEXT_TASK, out=tmp_path)
    assert result.dataset == TEXT_TASK.dataset

    lines = (tmp_path / "predictions" / "toy_text.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert [row["id"] for row in rows] == ["t-1", "t-2", "t-3"]
    for row, source in zip(rows, TEXT_ROWS, strict=True):
        ids = scorer.tokenizer.encode(source["text"], add_special_tokens=False)
        assert row["num_tokens"] == len(ids)
        assert row["num_bytes"] == len(source["text"].encode())
        assert row["windows"] == math.ceil(len(ids) / TEXT_TASK.window)
        assert row["logprob"] < 0
        assert row["is_greedy"] is False  # a random model doesn't predict a whole text

    # A second run into the same directory is refused before anything is scored.
    monkeypatch.setattr(scorer, "rolling_loglikelihood", None)  # scoring would fail on None
    with pytest.raises(FileExistsError, match="Pass a new out directory"):
        score_task(scorer, TEXT_TASK, out=tmp_path)


def test_prediction_rows_need_scores_for_every_doc() -> None:
    docs = [TextDoc("a", "x"), TextDoc("b", "y")]
    scores = Scores(
        logprob=torch.tensor([-1.0], dtype=torch.float64),
        num_tokens=torch.tensor([1]),
        tokens_cut=torch.tensor([0]),
        is_greedy=torch.tensor([False]),
    )

    # Rows are read by doc position, so scores for fewer docs raise instead of dropping rows.
    with pytest.raises(ValueError):
        perplexity_prediction_rows(docs, scores, [1, 1], window=8)


def test_a_window_above_the_models_max_length_raises(
    lm: LlamaForCausalLM, tokenizer: PreTrainedTokenizerFast
) -> None:
    short = ScoringModel(lm, tokenizer, name="tiny@0", max_length=4)
    with pytest.raises(ValueError, match="window must be from 1 to max_length 4, got 8"):
        score_task(short, TEXT_TASK)


def test_a_source_scores_the_mean_of_its_domains(scorer: ScoringModel) -> None:
    news = score_task(scorer, NEWS).metrics
    exam = score_task(scorer, EXAM).metrics
    metrics = {NEWS.name: news, EXAM.name: exam}
    expected = (news["bits_per_byte"] + exam["bits_per_byte"]) / 2
    assert SOURCE.score(metrics) == pytest.approx(expected)


GOLD_ONLY = dataclasses.replace(TASK, name="gold", metrics=("bpb",), primary_metric="bpb")


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        pytest.param({"members": (NEWS, GOLD_ONLY)}, "mix bits_per_byte and bpb", id="bpb"),
        pytest.param({"members": (NEWS, TASK)}, "mix accuracy and bits_per_byte", id="acc"),
        pytest.param({"members": (NEWS,), "scored": False}, "groups only suites", id="unscored"),
    ],
)
def test_suite_rejects_perplexity_tasks_beside_other_scales(
    kwargs: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        Suite("mixed", **kwargs)


def test_evaluate_scores_perplexity_tasks_and_their_source(
    monkeypatch: pytest.MonkeyPatch, scorer: ScoringModel, tmp_path: Path
) -> None:
    group = Suite("toys", (SOURCE,), scored=False)
    monkeypatch.setitem(run.SUITES, group.name, group)
    monkeypatch.setattr(ScoringModel, "from_pretrained", lambda *args, **kwargs: scorer)
    results = evaluate(EvalConfig(model="tiny@0", suites=("toys",), out=tmp_path))

    data = json.loads((tmp_path / "results.json").read_text())
    assert [task["task"] for task in data["tasks"]] == [NEWS.name, EXAM.name]
    source, toys = data["suites"]
    assert source["suite"] == SOURCE.name
    assert source["score"] == pytest.approx(
        SOURCE.score({r.task: r.metrics for r in results.tasks})
    )
    assert toys["score"] is None


def test_paloma_is_registered_as_sources_of_domain_tasks() -> None:
    assert SUITES["paloma"] == PALOMA
    sources = [
        "c4_en",
        "mc4",
        "wikitext_103",
        "ptb",
        "falcon-refinedweb",
        "dolma-v1_5",
        "c4_100_domains",
        "dolma_100_subreddits",
        "dolma_100_programing_languages",
    ]
    assert [suite.name for suite in PALOMA.members] == [f"paloma:{source}" for source in sources]
    assert [suite.metric for suite in PALOMA.suites] == ["bits_per_byte"] * 9 + [None]
    assert len(PALOMA.tasks) == 309
    wikitext = TASKS["paloma_wikitext_103"]
    assert isinstance(wikitext, PerplexityTask)
    assert (wikitext.dataset.subset, wikitext.dataset.split) == ("wikitext_103", "test")
    assert (wikitext.domain, wikitext.window) == (None, 2048)


def test_source_suite_names_a_task_per_domain() -> None:
    dataset = Dataset(repo="allenai/paloma", revision="0", subset="c4_100_domains", split="test")
    suite = source_suite("paloma", dataset, ("00_a.org", "01_b.com"))
    assert suite.name == "paloma:c4_100_domains"
    names = [task.name for task in suite.tasks]
    assert names == ["paloma_c4_100_domains__00_a.org", "paloma_c4_100_domains__01_b.com"]
    domains = [task.domain for task in suite.tasks if isinstance(task, PerplexityTask)]
    assert domains == ["00_a.org", "01_b.com"]


def test_a_source_listed_with_one_domain_is_one_task_over_that_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # tw_text's one-domain sources: every row carries one `subdomain`, the source's name.
    rows = [{**row, "subdomain": "cna"} for row in TEXT_ROWS]
    monkeypatch.setattr(Dataset, "load", lambda self, split=None: rows)
    dataset = Dataset(repo="cerulean-works/tw-text-v0", revision="0", subset="cna", split="test")

    (task,) = source_suite("tw_text", dataset, ("cna",)).tasks
    assert isinstance(task, PerplexityTask)
    assert (task.name, task.domain) == ("tw_text_cna", "cna")
    assert len(task.load()) == 3

    # Listed with no domain, as Paloma's one-domain sources are, the same rows are refused.
    (unlisted,) = source_suite("tw_text", dataset, ()).tasks
    with pytest.raises(ValueError, match="the rows have domains"):
        unlisted.load()


def test_source_suite_needs_a_dataset_configuration() -> None:
    dataset = Dataset(repo="cerulean-works/tw-text-v0", revision="0", subset=None, split="test")
    with pytest.raises(ValueError, match="tw_text: a source is a dataset configuration"):
        source_suite("tw_text", dataset, ())


def test_tw_text_is_scored_over_eleven_native_sources() -> None:
    assert SUITES["tw_text"] == TW_TEXT
    sources = [
        "judicial",
        "ly",
        "gov_press",
        "cna",
        "pts",
        "ltn",
        "cnyes",
        "twreporter",
        "ptt",
        "zhwiki",
        "theses",
    ]
    assert [suite.name for suite in TW_TEXT.members] == [f"tw_text:{source}" for source in sources]
    assert [suite.metric for suite in TW_TEXT.suites] == ["bits_per_byte"] * 12
    assert TW_TEXT.scored
    assert len(TW_TEXT.tasks) == 24
    assert not any("bbc" in task.name for task in TW_TEXT.tasks)  # converted text (026.D5)

    district = TASKS["tw_text_judicial__地方"]
    assert isinstance(district, PerplexityTask)
    assert (district.dataset.repo, district.dataset.subset) == (
        "cerulean-works/tw-text-v0",
        "judicial",
    )
    assert (district.dataset.split, district.domain, district.window) == ("test", "地方", 2048)
    cna = TASKS["tw_text_cna"]
    assert isinstance(cna, PerplexityTask)
    assert cna.domain == "cna"
    board = TASKS["tw_text_ptt__Tech_Job"]
    assert isinstance(board, PerplexityTask)
    assert (board.dataset.subset, board.domain) == ("ptt", "Tech_Job")


def test_fineweb_edu_is_the_labs_task_in_no_suite() -> None:
    task = TASKS["fineweb_edu"]
    assert isinstance(task, PerplexityTask)
    assert (task.window, task.primary_metric, task.domain) == (8192, "perplexity", None)
    assert (task.dataset.repo, task.dataset.split) == (
        "cerulean-works/fineweb-edu-dev",
        "validation",
    )
    assert all(task not in suite.tasks for suite in SUITES.values())


def test_the_fineweb_suite_scores_three_languages_at_one_window_by_bits_per_byte() -> None:
    suite = SUITES["fineweb"]
    names = [task.name for task in suite.tasks]
    assert names == ["fineweb_en", "fineweb_zh_hant", "fineweb_zh_hans"]
    assert suite.metric == "bits_per_byte"
    for task in suite.tasks:
        assert TASKS[task.name] is task
        assert isinstance(task, PerplexityTask)
        assert (task.window, task.primary_metric, task.domain) == (2048, "perplexity", None)

    # English reads the FineWeb-Edu dev set, so training drops one list of ids for both tasks.
    english, traditional, simplified = suite.tasks
    assert english.dataset == TASKS["fineweb_edu"].dataset
    for task, script in ((traditional, "zh_hant"), (simplified, "zh_hans")):
        dataset = task.dataset
        assert (dataset.repo, dataset.subset, dataset.split) == (
            "cerulean-works/fineweb-2-zh-dev",
            script,
            "test",
        )
    # One instrument: both scripts come from one revision of the dataset.
    assert traditional.dataset.revision == simplified.dataset.revision

    # The suite averages bits per byte, never perplexity, which depends on the tokenizer.
    metrics = {
        "fineweb_en": {"bits_per_byte": 0.6, "perplexity": 9.0},
        "fineweb_zh_hant": {"bits_per_byte": 0.9, "perplexity": 30.0},
        "fineweb_zh_hans": {"bits_per_byte": 0.6, "perplexity": 20.0},
    }
    assert suite.score(metrics) == pytest.approx(0.7)


def test_toy_texts_span_several_windows(tokenizer: PreTrainedTokenizerFast) -> None:
    lengths = [len(tokenizer.encode(row["text"], add_special_tokens=False)) for row in TEXT_ROWS]
    assert min(lengths) > TEXT_TASK.window


def test_evaluate_checks_every_window_against_the_model_first(
    monkeypatch: pytest.MonkeyPatch, lm: LlamaForCausalLM, tokenizer: PreTrainedTokenizerFast
) -> None:
    short = ScoringModel(lm, tokenizer, name="tiny@0", max_length=4)
    monkeypatch.setattr(ScoringModel, "from_pretrained", lambda *args, **kwargs: short)
    monkeypatch.setitem(run.TASKS, TEXT_TASK.name, TEXT_TASK)
    monkeypatch.setattr(run, "score_task", lambda *args: pytest.fail("scored before the check"))
    with pytest.raises(ValueError, match="window 8 is above tiny@0's max_length 4"):
        evaluate(EvalConfig(model="tiny@0", tasks=(TEXT_TASK.name,)))


@pytest.mark.parametrize("subdomain", [None, ""])
def test_a_null_or_empty_subdomain_counts_as_one_domain(
    monkeypatch: pytest.MonkeyPatch, subdomain: None | str
) -> None:
    rows = [{**row, "subdomain": subdomain} for row in TEXT_ROWS]
    monkeypatch.setattr(Dataset, "load", lambda self, split=None: rows)
    assert len(TEXT_TASK.load()) == 3


def test_the_domains_of_a_source_load_its_split_once(monkeypatch: pytest.MonkeyPatch) -> None:
    loads = []

    def load(self: Dataset, split: str | None = None) -> list[dict[str, Any]]:
        loads.append(self)
        return DOMAIN_ROWS

    monkeypatch.setattr(Dataset, "load", load)
    assert [len(task.load()) for task in SOURCE.tasks] == [2, 1]
    assert loads == [DOMAINS]


def test_the_cache_reloads_when_another_split_comes_between(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads = []

    def load(self: Dataset, split: str | None = None) -> list[dict[str, Any]]:
        loads.append(self.split)
        return DOMAIN_ROWS if self.split == "domains" else TEXT_ROWS

    monkeypatch.setattr(Dataset, "load", load)
    assert [doc.id for doc in NEWS.load()] == ["t-1", "t-3"]
    assert [doc.id for doc in TEXT_TASK.load()] == ["t-1", "t-2", "t-3"]
    assert [doc.id for doc in EXAM.load()] == ["t-2"]
    assert loads == ["domains", "text", "domains"]


def test_an_empty_split_has_no_rows_to_score(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Dataset, "load", lambda self, split=None: [])
    with pytest.raises(ValueError, match="the split has no rows"):
        TEXT_TASK.load()


def test_a_4096_token_model_cannot_run_fineweb_edu(
    monkeypatch: pytest.MonkeyPatch, lm: LlamaForCausalLM, tokenizer: PreTrainedTokenizerFast
) -> None:
    # OLMo 2 1B's context: the error names the task and both lengths before anything loads.
    short = ScoringModel(lm, tokenizer, name="tiny@0", max_length=4096)
    monkeypatch.setattr(ScoringModel, "from_pretrained", lambda *args, **kwargs: short)
    monkeypatch.setattr(run, "score_task", lambda *args: pytest.fail("scored before the check"))
    with pytest.raises(ValueError, match="fineweb_edu: window 8192 is above tiny@0's max_length"):
        evaluate(EvalConfig(model="tiny@0", tasks=("fineweb_edu",)))
