import dataclasses

import datasets
import pytest
import torch

from flock.eval.metrics import MULTIPLE_CHOICE_METRICS
from flock.eval.requests import Scores
from flock.eval.tasks import (
    TASKS,
    Choice,
    Dataset,
    Fewshot,
    GoldOnlyTask,
    MCDoc,
    Truncation,
    cloze,
)
from flock.eval.tasks.arc import ARC_EASY, arc_doc

from .toy import ROWS, TASK


@pytest.mark.parametrize(
    ("primary_metric", "unconditional_context"),
    [
        pytest.param("acc_per_chars", "Answer:", id="unknown-name"),
        pytest.param("acc_uncond", None, id="uncond-without-context"),
    ],
)
def test_task_rejects_unreported_primary_metric(primary_metric, unconditional_context):
    with pytest.raises(ValueError, match="primary_metric"):
        dataclasses.replace(
            TASK, primary_metric=primary_metric, unconditional_context=unconditional_context
        )


def test_arc_doc_maps_letter_and_digit_keys():
    assert arc_doc(ROWS["train"][0]) == MCDoc(
        "shot-1", cloze("Which gas do plants take in?", ("oxygen", "carbon dioxide")), 1
    )
    assert arc_doc(ROWS["train"][1]).gold == 0


def test_load_keeps_fewshot_order_and_limit():
    docs, shots = TASK.load(limit=1)
    assert [d.id for d in docs] == ["q-1"]
    assert [s.id for s in shots] == ["shot-2", "shot-1"]


@pytest.mark.parametrize("limit", [0, -1])
def test_load_rejects_limit_below_one(limit):
    with pytest.raises(ValueError, match="at least 1"):
        TASK.load(limit=limit)


def test_row_index_numbers_rows_per_split(monkeypatch, real_load):
    splits = {"dev": [{"q": "a"}, {"q": "b"}], "test": [{"q": "c"}]}
    monkeypatch.setattr(
        datasets,
        "load_dataset",
        lambda *args, split, **kwargs: datasets.Dataset.from_list(splits[split]),
    )
    dataset = Dataset("cerulean/toy", "0" * 40, None, "test", row_index=True)

    assert real_load(dataset, "dev") == [{"q": "a", "index": 0}, {"q": "b", "index": 1}]
    assert real_load(dataset) == [{"q": "c", "index": 0}]
    assert real_load(dataclasses.replace(dataset, row_index=False)) == [{"q": "c"}]


def test_row_index_task_needs_an_index_in_written_out_examples():
    numbered = dataclasses.replace(TASK.dataset, row_index=True)
    with pytest.raises(ValueError, match="needs an index in each written-out example"):
        dataclasses.replace(TASK, dataset=numbered, fewshot=Fewshot(rows=({"q": "a"},)))
    # With the index present the task defines fine.
    dataclasses.replace(TASK, dataset=numbered, fewshot=Fewshot(rows=({"q": "a", "index": 0},)))


def test_row_index_rejects_an_index_column(monkeypatch, real_load):
    rows = datasets.Dataset.from_list([{"index": 7}])
    monkeypatch.setattr(datasets, "load_dataset", lambda *args, **kwargs: rows)
    with pytest.raises(ValueError, match="test: the rows already have an index column"):
        real_load(Dataset("cerulean/toy", "0" * 40, None, "test", row_index=True))


def test_load_rejects_unknown_fewshot_id():
    task = dataclasses.replace(TASK, fewshot=Fewshot("train", ("shot-1", "nope")))
    with pytest.raises(KeyError, match="nope"):
        task.load()


def test_context_matches_olmes_format():
    assert TASK.requests(*TASK.load()).requests[0].context == (
        "Question: 臺灣最高的山是哪一座？\nAnswer: 玉山\n\n"
        "Question: Which gas do plants take in?\nAnswer: carbon dioxide\n\n"
        "Question: What do bees make?\nAnswer:"
    )


def test_requests_cover_every_choice_then_the_unconditional_ones():
    built = TASK.requests(*TASK.load())
    assert len(built.requests) == 2 * (3 + 4)
    contexted, uncond = built.requests[:7], built.requests[7:]
    assert [r.continuation for r in contexted] == [
        " honey",
        " milk",
        " silk",
        " 石頭",
        " 蘋果",
        " 椅子",
        " 雲",
    ]
    assert all(r.context.endswith("What do bees make?\nAnswer:") for r in contexted[:3])
    assert [r.context for r in uncond] == ["Answer:"] * 7
    assert [r.continuation for r in uncond] == [r.continuation for r in contexted]


def uniform_scores(n: int, tokens_cut: list[int] | None = None) -> Scores:
    """Scores for `n` requests: log-probability 0, one token each, not greedy, and `tokens_cut` (default none)."""
    cut = torch.tensor(tokens_cut) if tokens_cut is not None else torch.zeros(n, dtype=torch.int64)
    return Scores(
        torch.zeros(n), torch.ones(n, dtype=torch.int64), cut, torch.zeros(n, dtype=torch.bool)
    )


def test_choice_scores_lays_requests_out_per_doc():
    built = TASK.requests(*TASK.load())
    n = len(built.requests)
    # Request i scores -i with i + 1 tokens, and only odd requests are greedy, so each cell shows
    # which request it came from.
    scores = Scores(
        -torch.arange(n, dtype=torch.float32),
        torch.arange(n) + 1,
        torch.zeros_like(torch.arange(n)),
        torch.arange(n) % 2 == 1,
    )
    layout = built.choice_scores(scores)

    # Doc 0 has 3 choices (requests 0 to 2), doc 1 has 4 (requests 3 to 6).
    assert layout.valid.tolist() == [[True, True, True, False], [True, True, True, True]]
    assert layout.logprob[:, :3].tolist() == [[0, -1, -2], [-3, -4, -5]]
    assert layout.logprob[1, 3].item() == -6
    assert layout.num_tokens[0, :3].tolist() == [1, 2, 3]
    assert layout.is_greedy[1].tolist() == [True, False, True, False]
    assert layout.gold.tolist() == [0, 1]

    # The unconditional requests follow the 7 conditional ones, so doc 1's are 7 + 3 to 7 + 6.
    assert layout.logprob_uncond is not None
    assert layout.logprob_uncond[1].tolist() == [-10, -11, -12, -13]


def test_choice_scores_counts_leading_space_and_bytes():
    built = TASK.requests(*TASK.load())
    n = len(built.requests)
    layout = built.choice_scores(uniform_scores(n))

    # " 石頭": a space and two 3-byte characters
    assert layout.num_chars[1, 0].item() == 3
    assert layout.num_bytes[1, 0].item() == 7
    assert layout.num_bytes[0, 0].item() == len(" honey")


def test_without_unconditional_context_there_is_no_pmi():
    task = dataclasses.replace(TASK, unconditional_context=None)
    built = task.requests(*task.load())
    assert len(built.requests) == 7
    n = len(built.requests)
    layout = built.choice_scores(uniform_scores(n))
    assert layout.logprob_uncond is None


def test_choice_scores_rejects_mismatched_request_count():
    built = TASK.requests(*TASK.load())
    broken = dataclasses.replace(built, requests=built.requests[:-1])
    n = len(broken.requests)
    with pytest.raises(ValueError, match="13 requests for 7 choices"):
        broken.choice_scores(uniform_scores(n))


def test_choice_scores_rejects_no_docs():
    built = TASK.requests([], [])
    with pytest.raises(ValueError, match="no docs"):
        built.choice_scores(uniform_scores(0))


def test_truncation_is_zero_when_nothing_is_cut():
    built = TASK.requests(*TASK.load())
    assert built.truncation(uniform_scores(len(built.requests))) == Truncation(0, 0, 0)


def test_truncation_counts_requests_and_their_docs():
    built = TASK.requests(*TASK.load())
    # Doc 1's first choice (request 3) and doc 0's first unconditional request (7 + 0) are cut:
    # two requests in two docs. Mapping the unconditional half to the wrong docs would give 1.
    cut = [0] * len(built.requests)
    cut[3], cut[7 + 0] = 5, 2
    assert built.truncation(uniform_scores(len(cut), cut)) == Truncation(
        requests=2, docs=2, max_tokens_cut=5
    )

    # A request and its own unconditional twin (7 + 3) are one doc.
    cut[7 + 0], cut[7 + 3] = 0, 2
    assert built.truncation(uniform_scores(len(cut), cut)) == Truncation(
        requests=2, docs=1, max_tokens_cut=5
    )


def test_truncation_rejects_no_docs():
    with pytest.raises(ValueError, match="no docs"):
        TASK.requests([], []).truncation(uniform_scores(0))


def test_hash_is_stable():
    docs, shots = TASK.load()
    base = TASK.requests(docs, shots).hash
    assert base == TASK.requests(*TASK.load()).hash
    assert len(base) == 64


@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda docs, shots: TASK.requests(docs[:1], shots), id="docs"),
        pytest.param(lambda docs, shots: TASK.requests(docs, shots[:1]), id="shots"),
        pytest.param(
            lambda docs, shots: TASK.requests(
                [dataclasses.replace(docs[0], gold=2), docs[1]], shots
            ),
            id="gold",
        ),
        pytest.param(
            lambda docs, shots: dataclasses.replace(TASK, description="Science.\n\n").requests(
                docs, shots
            ),
            id="description",
        ),
        pytest.param(
            lambda docs, shots: dataclasses.replace(TASK, unconditional_context=None).requests(
                docs, shots
            ),
            id="uncond",
        ),
        pytest.param(
            lambda docs, shots: dataclasses.replace(
                TASK, dataset=Dataset("cerulean/toy", "1" * 40, None, "test")
            ).requests(docs, shots),
            id="revision",
        ),
        pytest.param(
            lambda docs, shots: dataclasses.replace(TASK, name="toy2").requests(docs, shots),
            id="name",
        ),
        pytest.param(
            lambda docs, shots: dataclasses.replace(TASK, primary_metric="acc_raw").requests(
                docs, shots
            ),
            id="primary-metric",
        ),
        pytest.param(
            lambda docs, shots: dataclasses.replace(TASK, metrics=("acc_per_char",)).requests(
                docs, shots
            ),
            id="metrics",
        ),
    ],
)
def test_hash_tracks_what_is_scored(change):
    docs, shots = TASK.load()
    assert change(docs, shots).hash != TASK.requests(docs, shots).hash


def test_registry_has_arc_easy_with_olmes_shots():
    assert TASKS["arc_easy"] is ARC_EASY
    assert ARC_EASY.fewshot is not None
    assert len(ARC_EASY.fewshot.ids) == 5
    assert ARC_EASY.dataset.split == "test"


def test_cloze_renders_olmes_prompt_and_spaced_answers():
    assert cloze("Why?", ("a", "b"), question_prefix="Goal: ") == (
        Choice("Goal: Why?\nAnswer:", " a"),
        Choice("Goal: Why?\nAnswer:", " b"),
    )


def test_cloze_rejects_one_string_as_answers():
    # A string is a sequence too, and would silently become one choice per character.
    with pytest.raises(TypeError, match=r"pass \(answer,\)"):
        cloze("Why?", "yes")
    assert cloze("Why?", ("yes",)) == (Choice("Question: Why?\nAnswer:", " yes"),)


@pytest.mark.parametrize(
    ("choices", "gold", "match"),
    [
        pytest.param((), 0, "doc d: no choices", id="no-choices"),
        pytest.param(cloze("?", ("a", "b")), 2, "doc d: gold 2", id="gold-past-end"),
        pytest.param(cloze("?", ("a", "b")), -1, "doc d: gold -1", id="gold-negative"),
    ],
)
def test_doc_rejects_bad_choices_or_gold(choices, gold, match):
    with pytest.raises(ValueError, match=match):
        MCDoc("d", choices, gold)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        pytest.param({}, "either ids or rows", id="neither"),
        pytest.param(
            {"split": "train", "ids": ("a",), "rows": ({"id": "b"},)}, "either", id="both"
        ),
        pytest.param({"ids": ("a",)}, "need a split", id="ids-without-split"),
        pytest.param(
            {"split": "train", "rows": ({"id": "b"},)}, "take no split", id="rows-with-split"
        ),
        pytest.param({"split": "train", "ids": ("a", "b", "a")}, r"repeat: \['a'\]", id="repeat"),
        pytest.param({"split": "train", "ids": ("a", "b"), "num_shots": 2}, "below", id="no-spare"),
        pytest.param(
            {"split": "train", "ids": ("a", "b"), "num_shots": 0}, "at least 1", id="zero"
        ),
        pytest.param(
            {"rows": ({"id": "a"}, {"id": "b"}), "num_shots": 1}, "needs ids", id="rows-with-shots"
        ),
    ],
)
def test_fewshot_rejects_bad_definitions(kwargs, match):
    with pytest.raises(ValueError, match=match):
        Fewshot(**kwargs)


def test_task_rejects_num_shots_from_another_split():
    # Ids are unique within a split, so dropping the scored doc's own example by id only works
    # when the examples come from the split the task scores.
    fewshot = Fewshot("train", ("shot-1", "shot-2"), num_shots=1)
    with pytest.raises(ValueError, match="scored split 'test', not 'train'"):
        dataclasses.replace(TASK, fewshot=fewshot)


def test_fewshot_rows_go_through_to_doc():
    task = dataclasses.replace(TASK, fewshot=Fewshot(rows=(ROWS["test"][1],)))
    _, shots = task.load()
    assert shots == [arc_doc(ROWS["test"][1])]


def test_num_shots_skips_the_scored_doc():
    # Examples from the scored split: each doc is shown the first one that isn't itself.
    task = dataclasses.replace(TASK, fewshot=Fewshot("test", ("q-1", "q-2"), num_shots=1))
    built = task.requests(*task.load())
    first, second = built.requests[0].context, built.requests[3].context
    assert first.startswith(
        "Question: 哪一個是水果？\nAnswer: 蘋果\n\nQuestion: What do bees make?"
    )
    assert second.startswith(
        "Question: What do bees make?\nAnswer: honey\n\nQuestion: 哪一個是水果？"
    )


@pytest.mark.parametrize("reorder", [False, True])
def test_a_shot_showing_the_docs_answer_is_not_shown_to_it(reorder):
    docs, shots = TASK.load()
    # Doc q-2 repeated under the few-shot id shot-9, as a dataset may repeat a question, perhaps
    # with its choices in another order. Either way the example would show q-2's answer.
    repeat = dataclasses.replace(docs[1], id="shot-9")
    if reorder:
        repeat = dataclasses.replace(repeat, choices=repeat.choices[::-1], gold=2)
    assert repeat.choices[repeat.gold] == docs[1].choices[docs[1].gold]

    built = TASK.requests(docs, [*shots, repeat])
    # Doc q-1 sees all three examples, and q-2 all but its repeat.
    assert built.requests[0].context.count("Question:") == 4
    assert built.requests[3].context.count("Question:") == 3


def test_a_shot_is_compared_with_the_doc_by_the_text_it_shows():
    docs, _ = TASK.load()
    # A doc whose examples read differently from its scored text, as HumanEval's do. An example
    # is the doc itself only if it shows the same text, so the plain repeat stays.
    doc = dataclasses.replace(docs[0], example="Question: q-1, as an example")
    repeat = dataclasses.replace(docs[0], id="shot-9")

    built = TASK.requests([doc], [repeat, dataclasses.replace(doc, id="shot-8")])
    assert built.requests[0].context.count("Question:") == 2
    assert "as an example" not in built.requests[0].context


def test_shots_from_the_scored_split_are_shown_to_their_own_doc():
    # OLMES shows a curated example from the scored split to every doc, its own included.
    task = dataclasses.replace(TASK, fewshot=Fewshot("test", ("q-1",)))
    docs, shots = task.load()

    built = task.requests(docs, shots)
    assert built.requests[0].context.count("Question:") == 2


def test_shots_from_another_split_may_share_an_id_with_the_doc():
    docs, shots = TASK.load()
    namesake = dataclasses.replace(shots[0], id=docs[0].id)

    built = TASK.requests(docs[:1], [namesake])
    assert built.requests[0].context.count("Question:") == 2


def test_description_and_per_choice_contexts():
    # A WinoGrande-like doc: the choices differ in context and share one continuation.
    doc = MCDoc("w", (Choice("Tom gave it to", " him."), Choice("Ann gave it to", " him.")), 1)
    task = dataclasses.replace(TASK, description="Header\n\n", unconditional_context=None)
    built = task.requests([doc], task.load()[1])

    head = (
        "Header\n\n"
        "Question: 臺灣最高的山是哪一座？\nAnswer: 玉山\n\n"
        "Question: Which gas do plants take in?\nAnswer: carbon dioxide\n\n"
    )
    assert [(r.context, r.continuation) for r in built.requests] == [
        (head + "Tom gave it to", " him."),
        (head + "Ann gave it to", " him."),
    ]


def test_doc_rejects_an_empty_example():
    with pytest.raises(ValueError, match="empty example"):
        MCDoc("s", (Choice("Q1", " a"),), 0, example="")


def test_example_replaces_the_gold_choice_in_the_shots():
    shot = MCDoc("s", (Choice("Q1", " a"),), 0, example="Q1  a")
    doc = MCDoc("d", (Choice("Q2", " b"),), 0)
    # An example from the scored split is shown to its own doc too, as OLMES's curated ones are.
    task = dataclasses.replace(TASK, fewshot=Fewshot("test", ("s",)), unconditional_context=None)

    # The doc's own `example` doesn't change how it is scored, only how it reads as a shot.
    built = task.requests([doc, shot], [shot])
    assert [(r.context, r.continuation) for r in built.requests] == [
        ("Q1  a\n\nQ2", " b"),
        ("Q1  a\n\nQ1", " a"),
    ]


def test_requests_are_immutable():
    built = TASK.requests(*TASK.load())
    assert isinstance(built.docs, tuple) and isinstance(built.requests, tuple)


def test_reported_metrics_follow_pmi_and_the_metrics_field():
    # `acc_greedy` compares no choices, so only a gold-only task reports it.
    assert TASK.reported_metrics == tuple(m for m in MULTIPLE_CHOICE_METRICS if m != "acc_greedy")
    no_pmi = dataclasses.replace(TASK, unconditional_context=None)
    assert "acc_uncond" not in no_pmi.reported_metrics
    bpb_only = dataclasses.replace(TASK, metrics=("bpb",), primary_metric="bpb")
    assert bpb_only.reported_metrics == ("bpb",)


def test_gold_only_tasks_report_bpb_unless_asked_for_greedy_match():
    gold_only = GoldOnlyTask(name="toy:gold", dataset=TASK.dataset, to_doc=arc_doc)
    assert (gold_only.primary_metric, gold_only.reported_metrics) == ("bpb", ("bpb",))
    assert gold_only.unconditional_context is None

    greedy = dataclasses.replace(
        gold_only, primary_metric="acc_greedy", metrics=("bpb", "acc_greedy")
    )
    assert greedy.reported_metrics == ("acc_greedy", "bpb")  # in `multiple_choice`'s order
    with pytest.raises(ValueError, match="metrics must be among"):
        dataclasses.replace(gold_only, metrics=("acc_raw",))


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        pytest.param({"metrics": ("acc_nope",)}, "metrics must be among", id="unknown"),
        pytest.param({"metrics": ("acc_greedy",)}, "metrics must be among", id="greedy"),
        pytest.param(
            {
                "metrics": ("acc_uncond",),
                "primary_metric": "acc_uncond",
                "unconditional_context": None,
            },
            "metrics must be among",
            id="uncond-without-context",
        ),
        pytest.param({"metrics": ("bpb",)}, "primary_metric", id="primary-not-reported"),
        pytest.param({"metrics": ()}, "metrics is empty", id="empty"),
    ],
)
def test_task_rejects_metrics_it_does_not_compute(kwargs, match):
    with pytest.raises(ValueError, match=match):
        dataclasses.replace(TASK, **kwargs)
