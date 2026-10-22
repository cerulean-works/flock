import dataclasses

import pytest
import torch

from flock.eval.requests import Scores
from flock.eval.tasks import TASKS, Dataset, Fewshot, MCDoc, Truncation
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
        "shot-1", "Which gas do plants take in?", ("oxygen", "carbon dioxide"), 1
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


def test_load_rejects_unknown_fewshot_id():
    task = dataclasses.replace(TASK, fewshot=Fewshot("train", ("shot-1", "nope")))
    with pytest.raises(KeyError, match="nope"):
        task.load()


def test_context_matches_olmes_format():
    docs, shots = TASK.load()
    assert TASK.context(docs[0], shots) == (
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
    """Scores for `n` requests: log-probability 0, one token each, and `tokens_cut` (default none)."""
    cut = torch.tensor(tokens_cut) if tokens_cut is not None else torch.zeros(n, dtype=torch.int64)
    return Scores(torch.zeros(n), torch.ones(n, dtype=torch.int64), cut)


def test_choice_scores_lays_requests_out_per_doc():
    built = TASK.requests(*TASK.load())
    n = len(built.requests)
    # Request i scores -i with i + 1 tokens, so each cell shows which request it came from.
    scores = Scores(
        -torch.arange(n, dtype=torch.float32),
        torch.arange(n) + 1,
        torch.zeros_like(torch.arange(n)),
    )
    layout = built.choice_scores(scores)

    # Doc 0 has 3 choices (requests 0 to 2), doc 1 has 4 (requests 3 to 6).
    assert layout.valid.tolist() == [[True, True, True, False], [True, True, True, True]]
    assert layout.logprob[:, :3].tolist() == [[0, -1, -2], [-3, -4, -5]]
    assert layout.logprob[1, 3].item() == -6
    assert layout.num_tokens[0, :3].tolist() == [1, 2, 3]
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
    # Doc 1's first choice (request 3) and its unconditional twin (7 + 5) are cut, and nothing
    # in doc 0: two requests, one doc.
    cut = [0] * len(built.requests)
    cut[3], cut[7 + 5] = 5, 2

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
            lambda docs, shots: dataclasses.replace(TASK, answer_prefix="A:").requests(docs, shots),
            id="prompt",
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
