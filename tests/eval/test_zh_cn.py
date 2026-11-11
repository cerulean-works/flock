import datasets
import pytest

from flock.eval.run import score_task
from flock.eval.tasks import SUITES, TASKS, Choice, Dataset, MultipleChoiceTask
from flock.eval.tasks.zh_cn import CEVAL, CMMLU, ZH_CN, ZH_CN_BPB, ceval_doc, cmmlu_doc


def ceval_row(position: int, question: str, choices: list[str], answer: str) -> dict:
    """A row in C-Eval's columns, with the `id` the dataset numbers from 0."""
    return {
        "id": position,
        "question": question,
        **dict(zip("ABCD", choices, strict=True)),
        "answer": answer,
        "explanation": "",
    }


def cmmlu_row(question: str, choices: list[str], answer: str) -> dict:
    """A row in the columns of CMMLU's loading script, which the mirror keeps."""
    return {"Question": question, **dict(zip("ABCD", choices, strict=True)), "Answer": answer}


DEV = [
    ("中国最长的河流是哪一条？", ["黄河", "长江", "珠江", "淮河"], "B"),
    ("一年有几个月？", ["10", "11", "12", "13"], "C"),
    ("哪一个是水果？", ["石头", "椅子", "云", "苹果"], "D"),
    ("水的化学式是什么？", ["H2O", "CO2", "O2", "NaCl"], "A"),
    ("太阳从哪个方向升起？", ["西", "东", "南", "北"], "B"),
]
TEST = [
    (" 水在摄氏几度结冰？ ", [" 0 度", "50 度", "100 度", "200 度 "], "A"),
    ("哪一种动物会飞？", ["鱼", "老鹰", "牛", "蛇"], "B"),
    # Dev row 2's question and gold answer, with the choices reordered.
    ("哪一个是水果？", ["苹果", "石头", "椅子", "云"], "A"),
]
ROWS = {
    # TMMLU+ reads C-Eval's columns, and its dev rows are its `train` split. The three benchmarks
    # share one task builder, so these rows serve the test that covers all three.
    ("ikala/tmmluplus", "train"): [ceval_row(i, *row) for i, row in enumerate(DEV)],
    ("ikala/tmmluplus", "test"): [ceval_row(i, *row) for i, row in enumerate(TEST)],
    ("ceval/ceval-exam", "dev"): [ceval_row(i, *row) for i, row in enumerate(DEV)],
    ("ceval/ceval-exam", "test"): [ceval_row(i, *row) for i, row in enumerate(TEST)],
    ("cerulean-works/cmmlu", "dev"): [cmmlu_row(*row) for row in DEV],
    ("cerulean-works/cmmlu", "test"): [cmmlu_row(*row) for row in TEST],
}


@pytest.fixture
def zh_cn_rows(monkeypatch, real_load):
    """Every TMMLU+, C-Eval and CMMLU task loads `ROWS` through the real `Dataset.load`, which numbers the rows."""
    monkeypatch.setattr(Dataset, "load", real_load)
    monkeypatch.setattr(
        datasets,
        "load_dataset",
        lambda repo, *args, split, **kwargs: datasets.Dataset.from_list(ROWS[repo, split]),
    )


def test_requests_are_each_benchmarks_header_then_five_dev_rows_then_the_question(zh_cn_rows):
    examples = (
        "问题：中国最长的河流是哪一条？\n答案：长江\n\n"
        "问题：一年有几个月？\n答案：12\n\n"
        "问题：哪一个是水果？\n答案：苹果\n\n"
        "问题：水的化学式是什么？\n答案：H2O\n\n"
        "问题：太阳从哪个方向升起？\n答案：东\n\n"
    )
    headers = {
        "ceval_logic": "以下是中国关于逻辑学的单项选择题，请选出其中的正确答案。\n\n",
        "cmmlu_logical": "以下是关于逻辑学的单项选择题，请直接给出正确答案的选项。\n\n",
    }
    for name, header in headers.items():
        task = TASKS[name]
        assert isinstance(task, MultipleChoiceTask)
        built = task.requests(*task.load())

        # The question and choices are stripped, and nothing separates `答案：` from the answer.
        question = "问题：水在摄氏几度结冰？\n答案："
        assert built.requests[3].context == header + examples + question
        assert [request.continuation for request in built.requests[:4]] == [
            "0 度",
            "50 度",
            "100 度",
            "200 度",
        ]
        assert built.docs[0].gold == 0
        # The unconditional context for PMI is the answer prefix alone.
        assert built.requests[12].context == "答案："
        assert built.requests[12].continuation == "0 度"


def test_a_test_row_repeating_a_dev_row_sees_the_other_four(zh_cn_rows):
    task = TASKS["cmmlu_logical"]
    assert isinstance(task, MultipleChoiceTask)
    docs, shots = task.load()
    assert [shot.id for shot in shots] == ["0", "1", "2", "3", "4"]

    context = task.requests(docs, shots).requests[8].context  # The repeat's first choice.
    assert context.count("问题：") == 5
    assert "答案：苹果\n\n" not in context


def test_dropped_rows_are_left_out_of_the_test_split_only(zh_cn_rows, monkeypatch):
    # `operating_system` drops row 32 (033.D8), so it needs 34 rows here.
    rows = [ceval_row(i, f"第{i}题？", ["甲", "乙", "丙", "丁"], "A") for i in range(34)]
    monkeypatch.setitem(ROWS, ("ceval/ceval-exam", "test"), rows)
    task = TASKS["ceval_operating_system"]
    assert isinstance(task, MultipleChoiceTask)

    docs, shots = task.load()

    assert [doc.id for doc in docs] == [str(i) for i in range(34) if i != 32]
    assert len(shots) == 5
    # Only the two subjects of 033.D8 leave a row out.
    with_keep = []
    for benchmark in (CEVAL, CMMLU):
        for category in benchmark.categories:
            for subject in category.subjects:
                if subject.keep is not None:
                    with_keep.append(f"{benchmark.name}_{subject.name}")
    assert with_keep == ["ceval_operating_system", "cmmlu_computer_science"]


# One test for the three benchmarks, since `Benchmark.tasks` builds all their tasks.
@pytest.mark.parametrize("name", ["tmmluplus_organic_chemistry", "ceval_logic", "cmmlu_logical"])
def test_scores_do_not_depend_on_batching(scorer, zh_cn_rows, name):
    task = TASKS[name]
    assert isinstance(task, MultipleChoiceTask)
    one_batch = score_task(scorer, task)

    scorer.batch_tokens = 1
    assert score_task(scorer, task).metrics == pytest.approx(one_batch.metrics, abs=1e-5)


def test_doc_reads_each_benchmarks_columns():
    ceval = ceval_doc({**ceval_row(0, *TEST[1]), "index": 7})
    cmmlu = cmmlu_doc({**cmmlu_row(*TEST[1]), "index": 7})

    assert ceval == cmmlu
    assert ceval.id == "7"
    assert ceval.choices[1] == Choice("问题：哪一种动物会飞？\n答案：", "老鹰")


def test_subject_tables_match_the_benchmarks_files():
    for benchmark, sizes in (
        (CEVAL, {"stem": 20, "social_science": 10, "humanities": 11, "other": 11}),
        (CMMLU, {"stem": 17, "social_science": 22, "humanities": 13, "other": 15}),
    ):
        categories = benchmark.categories
        subjects = [subject for category in categories for subject in category.subjects]
        assert {category.name: len(category.subjects) for category in categories} == sizes
        assert len({subject.name for subject in subjects}) == len(subjects)
        assert len(SUITES[benchmark.name].tasks) == len(subjects)

    china_specific = SUITES["cmmlu:china_specific"].tasks
    # CMMLU's `src/categories.py` tags 15, though its paper says 16.
    assert len(china_specific) == 15
    assert set(china_specific) <= set(SUITES["cmmlu"].tasks)
    assert not any(s.china_specific for category in CEVAL.categories for s in category.subjects)


def test_each_benchmark_averages_its_subjects_and_zh_cn_weighs_both_equally():
    metrics = {task.name: {"acc_per_char": 0.0, "bpb": 1.0} for task in ZH_CN.tasks}
    # One of C-Eval's 11 humanities subjects scores 1.
    metrics["ceval_logic"] = {"acc_per_char": 1.0, "bpb": 3.0}

    assert SUITES["ceval:humanities"].score(metrics) == pytest.approx(1 / 11)
    # A subject counts once in 52, not a category once in 4 as TMMLU+ averages (033.D3).
    assert SUITES["ceval"].score(metrics) == pytest.approx(1 / 52)
    assert SUITES["cmmlu"].score(metrics) == 0
    assert ZH_CN.score(metrics) == pytest.approx(1 / 52 / 2)
    assert ZH_CN_BPB.score(metrics) == pytest.approx((1 + 2 / 52 + 1) / 2)


def test_all_groups_run_every_subject_once():
    assert SUITES["ceval:all"].tasks == SUITES["ceval"].tasks
    assert SUITES["cmmlu:all"].tasks == SUITES["cmmlu"].tasks
    assert ZH_CN.tasks == ZH_CN_BPB.tasks
    assert len(ZH_CN.tasks) == 119
