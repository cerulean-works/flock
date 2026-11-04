"""Taiwan Traditional Chinese text published from 2026-04-01 on, one source per register, scored as Paloma scores its sources."""

from flock.eval.tasks.base import Dataset
from flock.eval.tasks.suite import Suite, source_suite

# Each source's domains, the distinct `subdomain` values of its `test` rows at the pinned
# revision, most documents first (stage file 026, 026.D6). A source of one register lists its own name, the one
# value its rows carry. `bbc_zh_trad` is converted from Simplified script, so it isn't here
# (026.D5).
SOURCES: dict[str, tuple[str, ...]] = {
    "judicial": ("地方", "高等", "行政", "最高"),
    "ly": ("委員會紀錄", "院會紀錄", "質詢"),
    "gov_press": ("文化部", "行政院", "金管會", "農業部"),
    "cna": ("cna",),
    "pts": ("pts",),
    "ltn": ("ltn",),
    "cnyes": ("cnyes",),
    "twreporter": ("twreporter",),
    "zhwiki": ("zhwiki",),
    "theses": ("NYCU", "NTNU", "NCU", "NCKU", "NCHU"),
}

# Every task keeps `PerplexityTask`'s window of 2048 tokens, Paloma's, which every compared model
# has (026.D4). Unlike `paloma`, the suite has a score: the unweighted mean of its sources (026.D3).
TW_TEXT = Suite(
    "tw_text",
    tuple(
        source_suite(
            "tw_text",
            Dataset(
                repo="cerulean-works/tw-text-v0",
                revision="24e735dfd6b5b9b730703c9da7c9237bdf4229a9",
                subset=source,
                split="test",
            ),
            domains,
        )
        for source, domains in SOURCES.items()
    ),
    metric="bits_per_byte",
)
