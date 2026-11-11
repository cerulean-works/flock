"""TMMLU+ (Tam et al. 2024, 2403.01858): 66 subjects of Traditional Chinese exam questions, one task each."""

from typing import Any

from flock.eval.tasks.base import CHINESE_QUESTION_PREFIX, MCDoc
from flock.eval.tasks.chinese_exam import Benchmark, Category, Subject, exam_doc
from flock.eval.tasks.suite import Suite


def tmmluplus_doc(row: dict[str, Any]) -> MCDoc:
    """A TMMLU+ row as `exam_doc` reads it, with `問題：`. A named function rather than a `partial`, so the task snapshot (012) pins it by name."""
    return exam_doc(row, CHINESE_QUESTION_PREFIX)


# The 66 subjects by category: lm-evaluation-harness's `subject.tsv` gives each a field, and the
# field-to-category table in its `_generate_configs.py` (copied from `iKala/ievals`) gives the
# category. Together they reproduce the card's v1.1 counts. `linear_algebra` has CSV files but no
# config, so it can't be loaded by name and isn't scored.
TMMLUPLUS_BENCHMARK = Benchmark(
    name="tmmluplus",
    repo="ikala/tmmluplus",
    # Tag v1.1, the corrected release the card describes.
    revision="94d86f1d013d59f389b710a902af30b7a0aa8c8f",
    # The `train` split is the `*_dev.csv` files, shown whole in file order, as OLMES shows MMLU's
    # first 5 dev rows.
    fewshot_split="train",
    # The TMMLU+ paper's header (figs. 8 and 9).
    header="以下是關於{}考試單選題，請選出正確的答案。",
    to_doc=tmmluplus_doc,
    categories=(
        Category(
            "stem",
            (
                Subject("advance_chemistry", "化學"),
                Subject("basic_medical_science", "基礎醫學"),
                Subject("computer_science", "資訊工程"),
                Subject("engineering_math", "工程數學"),
                Subject("junior_chemistry", "國中理化"),
                Subject("junior_math_exam", "國中會考基測數學科", 4),
                Subject("junior_science_exam", "國中會考基測自然科"),
                Subject("organic_chemistry", "有機化學", 3),
                Subject("pharmacy", "藥劑學", 4),
                Subject("physics", "物理"),
                Subject("secondary_physics", "高中物理"),
                Subject("statistics_and_machine_learning", "統計與機器學習"),
                Subject("tve_mathematics", "統測數學", 4),
                Subject("tve_natural_sciences", "統測自然科"),
            ),
        ),
        Category(
            "social_sciences",
            (
                Subject("chinese_language_and_literature", "國文"),
                Subject("clinical_psychology", "臨床心理學"),
                Subject("economics", "經濟學"),
                Subject("education", "教育常識", 4),
                Subject("education_(profession_level)", "教育專業"),
                Subject("educational_psychology", "教育心理"),
                Subject("geography_of_taiwan", "台灣地理"),
                Subject("human_behavior", "人類行為與社會"),
                Subject("junior_chinese_exam", "國中會考基測國文"),
                Subject("macroeconomics", "總經"),
                Subject("national_protection", "軍事"),
                Subject("occupational_therapy_for_psychological_disorders", "心理障礙職能治療學"),
                Subject("physical_education", "體育"),
                Subject("politic_science", "政治"),
                Subject("taiwanese_hokkien", "閩南語"),
                Subject("three_principles_of_people", "三民主義"),
                Subject("ttqav2", "台灣在地用語"),
                Subject("tve_chinese_language", "統測國文"),
            ),
        ),
        Category(
            "humanities",
            (
                Subject("administrative_law", "行政法"),
                Subject("anti_money_laundering", "洗錢防制"),
                Subject("general_principles_of_law", "法學大意"),
                Subject("introduction_to_law", "法律概論"),
                Subject("jce_humanities", "指考人文科目"),
                Subject("taxation", "稅務"),
                Subject("trust_practice", "信託實務", 4),
            ),
        ),
        Category(
            "other",
            (
                Subject("accounting", "會計學"),
                Subject("agriculture", "農業", 4),
                Subject("auditing", "審計學"),
                Subject("business_management", "企業管理"),
                Subject("culinary_skills", "餐旅"),
                Subject("dentistry", "牙醫學"),
                Subject("finance_banking", "金融與法規"),
                Subject("financial_analysis", "財務分析"),
                Subject("fire_science", "火災學"),
                Subject("insurance_studies", "保險學", 4),
                Subject("junior_social_studies", "國中會考基測社會科", 4),
                Subject("logic_reasoning", "邏輯思維"),
                Subject("management_accounting", "管理會計"),
                Subject("marketing_management", "行銷管理"),
                Subject("mechanical", "機械與機電概論"),
                Subject("music", "音樂科"),
                Subject("nautical_science", "航海"),
                Subject("official_document_management", "機關文書", 4),
                Subject("optometry", "視光學"),
                Subject("pharmacology", "藥理學", 4),
                Subject("real_estate", "房地產"),
                Subject("technical", "技術工相關"),
                Subject("trade", "貿易", 4),
                Subject("traditional_chinese_medicine_clinical_medicine", "中醫臨床醫學"),
                Subject("tve_design", "統測＿設計"),
                Subject("veterinary_pathology", "獸醫病理學"),
                Subject("veterinary_pharmacology", "獸醫藥理學"),
            ),
        ),
    ),
    # The card's total is the mean of the four category means, "matching the ievals methodology",
    # so a category counts once however many subjects it has.
    averages_categories=True,
)

TMMLUPLUS = TMMLUPLUS_BENCHMARK.suite
TMMLUPLUS_BPB = TMMLUPLUS_BENCHMARK.bpb_suite

# The subjects behind the Gemma-3-TAIDE cards' Taiwan columns (台灣地理, 台灣用語, 閩南語). The
# cards don't name the second one's subject: `ttqav2` is the only one about Taiwanese terms.
TAIWAN_TASKS = (
    TMMLUPLUS_BENCHMARK.tasks["geography_of_taiwan"],
    TMMLUPLUS_BENCHMARK.tasks["ttqav2"],
    TMMLUPLUS_BENCHMARK.tasks["taiwanese_hokkien"],
)
TAIWAN = Suite("tmmluplus:taiwan", TAIWAN_TASKS)
TAIWAN_BPB = Suite("tmmluplus:taiwan:bpb", TAIWAN_TASKS, metric="bpb")

# An accuracy and a `bpb` don't average, so the group that runs them all has no score.
TMMLUPLUS_ALL = TMMLUPLUS_BENCHMARK.all_suite(TAIWAN, TAIWAN_BPB)
