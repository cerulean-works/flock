"""Taiwan Traditional Chinese text published from 2026-04-01 on, one source per register, scored as Paloma scores its sources."""

from flock.eval.tasks.base import Dataset
from flock.eval.tasks.suite import Suite, source_suite

# Each source's domains, the distinct `subdomain` values of its `test` rows at the pinned
# revision, most documents first (stage file 026, 026.D6). Domains under 100,000 OLMo 2 tokens are
# merged into `other` in the dataset, so a source with one value lists it: its name, or `other`
# when the whole source is that small. `bbc_zh_trad` is converted from Simplified script, so it
# isn't here (026.D5).
SOURCES: dict[str, tuple[str, ...]] = {
    "judicial": ("地方", "other"),
    "ly": ("委員會紀錄", "院會紀錄", "質詢"),
    "gov_press": ("other", "文化部"),
    "cna": ("cna",),
    "pts": ("pts",),
    "ltn": ("ltn",),
    "cnyes": ("other",),
    "twreporter": ("twreporter",),
    "zhwiki": ("other",),
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
                revision="8741fe41c17dca60e773b642b755043a78a021a8",
                subset=source,
                split="test",
            ),
            domains,
        )
        for source, domains in SOURCES.items()
    ),
    metric="bits_per_byte",
)
