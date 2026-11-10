"""Taiwan Traditional Chinese text published from 2026-04-01 on (theses are the exception), one source per register, scored as Paloma scores its sources."""

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

# Every task keeps `PerplexityTask`'s window of 2048 tokens, Paloma's, which every compared model
# has (026.D4). Unlike `paloma`, the suite has a score: the unweighted mean of its sources (026.D3).
TW_TEXT = Suite(
    "tw_text",
    tuple(
        source_suite(
            "tw_text",
            Dataset(
                repo="cerulean-works/tw-text-v0",
                revision="1786e787a4ab786d1ed021994007ef3c9590e497",
                subset=source,
                split="test",
            ),
            domains,
        )
        for source, domains in SOURCES.items()
    ),
    metric="bits_per_byte",
)
