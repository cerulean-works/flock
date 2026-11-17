"""Taiwan Traditional Chinese text published from 2026-04-01 on (theses are the exception), one source per register, scored as Paloma scores its sources, at the frozen v0 and at v1."""

from flock.eval.tasks.base import Dataset
from flock.eval.tasks.suite import Suite, source_suite

# Each source's domains, the distinct `subdomain` values of its `test` rows at the pinned
# revision, most documents first (stage file 026, 026.D6), as
# `flock/eval/scripts/source_census.py tw_text` prints them and checks them against the split. A
# domain under 100,000 OLMo 2 tokens in the sample is merged into `other`, and an `other` still
# under that size leaves the sample, as every one did at `v0` (029.D2). A source with one value
# lists it. `bbc_zh_trad` is converted from Simplified script, so it isn't here (026.D5).
SOURCES: dict[str, tuple[str, ...]] = {
    "judicial": ("地方", "高等"),
    "ly": ("院會紀錄", "委員會紀錄"),
    "gov_press": ("文化部", "行政院", "金管會", "農業部"),
    "cna": ("cna",),
    "pts": ("pts",),
    "ltn": ("ltn",),
    "cnyes": ("cnyes",),
    "twreporter": ("twreporter",),
    "ptt": (
        "Stock",
        "movie",
        "Tech_Job",
        "MobileComm",
        "WomenTalk",
        "Baseball",
        "Gossiping",
        "HatePolitics",
    ),
    "zhwiki": ("zhwiki",),
    "theses": ("NYCU", "NTNU"),
}

# The domains of `tw-text-v1` at its pinned revision, listed and checked as `SOURCES` are. v1 draws
# its domains from each source's own metadata (a news section, a court and case type, a field of
# study) and samples them equally, so an `other` that clears the floor is a domain of its own
# (stage file 038, 038.D2).
SOURCES_V1: dict[str, tuple[str, ...]] = {
    "judicial": (
        "地方民事",
        "最高民事",
        "最高家事",
        "行政",
        "最高刑事",
        "other",
        "地方刑事",
        "高等民事",
        "高等刑事",
    ),
    "ly": ("質詢", "院會紀錄", "委員會紀錄"),
    "gov_press": ("文化部", "行政院", "金管會", "農業部"),
    "cna": ("地方", "other", "國際", "政治", "產經", "生活", "社會", "運動", "兩岸"),
    "pts": ("生活", "全球", "社會", "地方", "政治", "文教科技", "other", "產經"),
    "ltn": ("other", "生活", "政治", "社會", "最新消息", "健康", "國際"),
    "cnyes": ("announcement", "other", "wd_stock", "tw_stock", "bc"),
    "twreporter": ("twreporter",),
    "ptt": (
        "WomenTalk",
        "MobileComm",
        "Gossiping",
        "Tech_Job",
        "Baseball",
        "movie",
        "Stock",
        "HatePolitics",
    ),
    "zhwiki": ("zhwiki",),
    "theses": ("理學", "醫護", "人文社會", "商管", "工程", "教育", "藝術設計", "other"),
}


def tw_text_suite(
    name: str, repo: str, revision: str, sources: dict[str, tuple[str, ...]]
) -> Suite:
    """
    Returns a revision of the Taiwan text set as a suite of its sources, each a `source_suite` of its domains in the `test` split.

    Every task keeps `PerplexityTask`'s window of 2048 tokens, Paloma's, which every compared model has (026.D4). Unlike `paloma`, the suite has a score: the unweighted mean of its sources (026.D3). Each revision is its own suite under its own name, so a number never mixes two revisions' documents (038.D1).
    """
    return Suite(
        name,
        tuple(
            source_suite(
                name,
                Dataset(repo=repo, revision=revision, subset=source, split="test"),
                domains,
            )
            for source, domains in sources.items()
        ),
        metric="bits_per_byte",
    )


TW_TEXT = tw_text_suite(
    "tw_text", "cerulean-works/tw-text-v0", "1786e787a4ab786d1ed021994007ef3c9590e497", SOURCES
)
TW_TEXT_V1 = tw_text_suite(
    "tw_text_v1",
    "cerulean-works/tw-text-v1",
    "b60d46e622d3c9ce7f022831004aab0bef91b32d",
    SOURCES_V1,
)
