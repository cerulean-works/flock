"""Held-out web text in English (FineWeb-Edu, 2406.17557) and Chinese (FineWeb-2, 2506.20920) at one window."""

from flock.eval.tasks.base import Dataset
from flock.eval.tasks.fineweb_edu import FINEWEB_EDU
from flock.eval.tasks.perplexity import PerplexityTask
from flock.eval.tasks.suite import Suite

# Windows of 2048 tokens, which every compared model has (stage file 027, 027.D4). English is the
# FineWeb-Edu dev set: `fineweb_edu` keeps 8192 (027.D1).
FINEWEB_EN = PerplexityTask(
    name="fineweb_en", dataset=FINEWEB_EDU.dataset, window=2048, primary_metric="perplexity"
)
# FineWeb-2's `cmn_Hani` `test` split, which FineWeb-2 says should not be trained on, split by
# script with `hanzidentifier` as Mimir does (arXiv 2605.25263): all 3,383 Traditional documents, and 5,000
# Simplified ones drawn with a fixed seed (027.D2, 027.D3).
FINEWEB_ZH_HANT = PerplexityTask(
    name="fineweb_zh_hant",
    dataset=Dataset(
        repo="cerulean-works/fineweb-2-zh-dev",
        revision="6d54ae9ec544b822699267d615597649452d9eea",
        subset="zh_hant",
        split="test",
    ),
    window=2048,
    primary_metric="perplexity",
)
FINEWEB_ZH_HANS = PerplexityTask(
    name="fineweb_zh_hans",
    dataset=Dataset(
        repo="cerulean-works/fineweb-2-zh-dev",
        revision="6d54ae9ec544b822699267d615597649452d9eea",
        subset="zh_hans",
        split="test",
    ),
    window=2048,
    primary_metric="perplexity",
)
# The one metric whose unit doesn't depend on the tokenizer, each member counting once (027.D5).
FINEWEB = Suite("fineweb", (FINEWEB_EN, FINEWEB_ZH_HANT, FINEWEB_ZH_HANS), metric="bits_per_byte")
