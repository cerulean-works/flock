"""C-Eval (Huang et al. 2023, 2305.08322) and CMMLU (Li et al. 2023, 2306.09212): native Simplified Chinese exam questions, one task per subject, and the `zh_cn` suites."""

from typing import Any

from flock.eval.tasks.base import SIMPLIFIED_QUESTION_PREFIX, MCDoc
from flock.eval.tasks.chinese_exam import Benchmark, Category, Subject, exam_doc
from flock.eval.tasks.suite import Suite


def ceval_doc(row: dict[str, Any]) -> MCDoc:
    """A C-Eval row as `exam_doc` reads it, with `问题：` (033.D6). A named function rather than a `partial`, so the task snapshot (012) pins it by name."""
    return exam_doc(row, SIMPLIFIED_QUESTION_PREFIX)


def cmmlu_doc(row: dict[str, Any]) -> MCDoc:
    """A CMMLU row as `ceval_doc` reads a C-Eval row. The mirror keeps the loading script's capitalized `Question` and `Answer`."""
    return ceval_doc({**row, "question": row["Question"], "answer": row["Answer"]})


# 033.D8: a non-gold choice of each of these two test rows is a lone symbol that most tokenizers
# merge into `答案：`, so it encodes to no token and no model can score the doc. They are named
# here, as 005.D7's repeats are named in its stage file.
def keep_ceval_operating_system(row: dict[str, Any]) -> bool:
    """Every row but 32, whose choice A is `<`."""
    return row["index"] != 32


def keep_cmmlu_computer_science(row: dict[str, Any]) -> bool:
    """Every row but 108, whose choice C is `%`."""
    return row["index"] != 108


# Categories are `subject_mapping.json`'s third field (`hkust-nlp/ceval` at cba65ae9). The header
# is the paper's (fig. 6, p.16), as lm-evaluation-harness's `ceval` `description`. Test labels
# are public since 2025-07-27, and this revision has all 12,342.
CEVAL = Benchmark(
    name="ceval",
    repo="ceval/ceval-exam",
    revision="617524a00b307ff6f9933702f724131fe12ca7ce",
    fewshot_split="dev",
    header="以下是中国关于{}的单项选择题，请选出其中的正确答案。",
    to_doc=ceval_doc,
    categories=(
        Category(
            "stem",
            (
                Subject("advanced_mathematics", "高等数学"),
                Subject("college_chemistry", "大学化学"),
                Subject("college_physics", "大学物理"),
                Subject("college_programming", "大学编程"),
                Subject("computer_architecture", "计算机组成"),
                Subject("computer_network", "计算机网络"),
                Subject("discrete_mathematics", "离散数学"),
                Subject("electrical_engineer", "注册电气工程师"),
                Subject("high_school_biology", "高中生物"),
                Subject("high_school_chemistry", "高中化学"),
                Subject("high_school_mathematics", "高中数学"),
                Subject("high_school_physics", "高中物理"),
                Subject("metrology_engineer", "注册计量师"),
                Subject("middle_school_biology", "初中生物"),
                Subject("middle_school_chemistry", "初中化学"),
                Subject("middle_school_mathematics", "初中数学"),
                Subject("middle_school_physics", "初中物理"),
                Subject("operating_system", "操作系统", keep=keep_ceval_operating_system),
                Subject("probability_and_statistics", "概率统计"),
                Subject("veterinary_medicine", "兽医学"),
            ),
        ),
        Category(
            "social_science",
            (
                Subject("business_administration", "工商管理"),
                Subject("college_economics", "大学经济学"),
                Subject("education_science", "教育学"),
                Subject("high_school_geography", "高中地理"),
                Subject("high_school_politics", "高中政治"),
                Subject("mao_zedong_thought", "毛泽东思想和中国特色社会主义理论体系概论"),
                Subject("marxism", "马克思主义基本原理"),
                Subject("middle_school_geography", "初中地理"),
                Subject("middle_school_politics", "初中政治"),
                Subject("teacher_qualification", "教师资格"),
            ),
        ),
        Category(
            "humanities",
            (
                Subject("art_studies", "艺术学"),
                Subject("chinese_language_and_literature", "中国语言文学"),
                Subject("high_school_chinese", "高中语文"),
                Subject("high_school_history", "高中历史"),
                Subject("ideological_and_moral_cultivation", "思想道德修养与法律基础"),
                Subject("law", "法学"),
                Subject("legal_professional", "法律职业资格"),
                Subject("logic", "逻辑学"),
                Subject("middle_school_history", "初中历史"),
                Subject("modern_chinese_history", "近代史纲要"),
                Subject("professional_tour_guide", "导游资格"),
            ),
        ),
        Category(
            "other",
            (
                Subject("accountant", "注册会计师"),
                Subject("basic_medicine", "基础医学"),
                Subject("civil_servant", "公务员"),
                Subject("clinical_medicine", "临床医学"),
                Subject("environmental_impact_assessment_engineer", "环境影响评价工程师"),
                Subject("fire_engineer", "注册消防工程师"),
                Subject("physician", "医师资格"),
                Subject("plant_protection", "植物保护"),
                Subject("sports_science", "体育学"),
                Subject("tax_accountant", "税务师"),
                Subject("urban_and_rural_planner", "注册城乡规划师"),
            ),
        ),
    ),
)

# Categories and the China specific tags are `src/categories.py`'s (`haonan-li/CMMLU` at d6e7b716).
# The header is the paper's (p.5), as lm-evaluation-harness's `cmmlu` `description`. The source
# repo is a loading script `datasets` can't run, so the rows come from a private parquet mirror of
# its v1.0.1 zip, cell for cell (033.D5).
CMMLU = Benchmark(
    name="cmmlu",
    repo="cerulean-works/cmmlu",
    revision="46bb6edee95673e17186965063d2fba50ec23e00",
    fewshot_split="dev",
    header="以下是关于{}的单项选择题，请直接给出正确答案的选项。",
    to_doc=cmmlu_doc,
    categories=(
        Category(
            "stem",
            (
                Subject("anatomy", "解剖学"),
                Subject("astronomy", "天文学"),
                Subject("college_actuarial_science", "大学精算学"),
                Subject("college_engineering_hydrology", "大学工程水文学"),
                Subject("college_mathematics", "大学数学"),
                Subject("college_medical_statistics", "大学医学统计"),
                Subject("computer_science", "计算机科学", keep=keep_cmmlu_computer_science),
                Subject("conceptual_physics", "概念物理学"),
                Subject("electrical_engineering", "电气工程"),
                Subject("elementary_mathematics", "初等数学"),
                Subject("genetics", "遗传学"),
                Subject("high_school_biology", "高中生物"),
                Subject("high_school_chemistry", "高中化学"),
                Subject("high_school_mathematics", "高中数学"),
                Subject("high_school_physics", "高中物理学"),
                Subject("machine_learning", "机器学习"),
                Subject("virology", "病毒学"),
            ),
        ),
        Category(
            "social_science",
            (
                Subject("ancient_chinese", "古汉语", china_specific=True),
                Subject("business_ethics", "商业伦理"),
                Subject("chinese_civil_service_exam", "中国公务员考试", china_specific=True),
                Subject("chinese_food_culture", "中国饮食文化", china_specific=True),
                Subject("chinese_foreign_policy", "中国外交政策", china_specific=True),
                Subject("chinese_teacher_qualification", "中国教师资格", china_specific=True),
                Subject("college_education", "大学教育学"),
                Subject("economics", "经济学"),
                Subject("education", "教育学"),
                Subject("elementary_chinese", "小学语文", china_specific=True),
                Subject("ethnology", "民族学", china_specific=True),
                Subject("high_school_geography", "高中地理"),
                Subject("high_school_politics", "高中政治", china_specific=True),
                Subject("journalism", "新闻学"),
                Subject("management", "管理学"),
                Subject("marketing", "市场营销"),
                Subject("modern_chinese", "现代汉语", china_specific=True),
                Subject("professional_accounting", "专业会计"),
                Subject("professional_psychology", "专业心理学"),
                Subject("public_relations", "公共关系"),
                Subject("security_study", "安全研究"),
                Subject("sociology", "社会学"),
            ),
        ),
        Category(
            "humanities",
            (
                Subject("arts", "艺术学"),
                Subject("chinese_history", "中国历史", china_specific=True),
                Subject("chinese_literature", "中国文学", china_specific=True),
                Subject("college_law", "大学法律"),
                Subject("global_facts", "全球事实"),
                Subject("international_law", "国际法学"),
                Subject("jurisprudence", "法理学"),
                Subject("logical", "逻辑学"),
                Subject("marxist_theory", "马克思主义理论"),
                Subject("philosophy", "哲学"),
                Subject("professional_law", "专业法学"),
                Subject("world_history", "世界历史"),
                Subject("world_religions", "世界宗教"),
            ),
        ),
        Category(
            "other",
            (
                Subject("agronomy", "农学"),
                Subject("chinese_driving_rule", "中国驾驶规则", china_specific=True),
                Subject("clinical_knowledge", "临床知识"),
                Subject("college_medicine", "大学医学"),
                Subject("computer_security", "计算机安全"),
                Subject("construction_project_management", "建设工程管理", china_specific=True),
                Subject("elementary_commonsense", "小学常识", china_specific=True),
                Subject("elementary_information_and_technology", "小学信息技术"),
                Subject("food_science", "食品科学"),
                Subject("human_sexuality", "人类性行为"),
                Subject("legal_and_moral_basis", "法律与道德基础"),
                Subject("nutrition", "营养学"),
                Subject("professional_medicine", "专业医学"),
                Subject("sports_science", "体育学"),
                Subject("traditional_chinese_medicine", "中医中药", china_specific=True),
            ),
        ),
    ),
)

CEVAL_ALL = CEVAL.all_suite()
CMMLU_ALL = CMMLU.all_suite()

# One member per benchmark through its own suite, so neither set's subject count decides the mix,
# as `zh_tw` (stage file 017).
ZH_CN = Suite("zh_cn", (CEVAL.suite(), CMMLU.suite()))
ZH_CN_BPB = Suite("zh_cn:bpb", (CEVAL.suite("bpb"), CMMLU.suite("bpb")), metric="bpb")
