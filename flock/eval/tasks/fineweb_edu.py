"""FineWeb-Edu (Penedo et al. 2024, 2406.17557): the cycle 3 lab's dev set, scored as perplexity."""

from flock.eval.tasks.base import Dataset
from flock.eval.tasks.perplexity import PerplexityTask

# 5,000 documents drawn with a fixed seed from one file of FineWeb-Edu's newest dump, whose ids
# training data drops (stage file 019, 019.D3). Windows of 8192 tokens, the lab's context
# (019.D4), and perplexity first, the number the lab ranks (019.D1).
FINEWEB_EDU = PerplexityTask(
    name="fineweb_edu",
    dataset=Dataset(
        repo="cerulean-works/fineweb-edu-dev",
        revision="0000000000000000000000000000000000000000",
        subset=None,
        split="validation",
    ),
    window=8192,
    primary_metric="perplexity",
)
