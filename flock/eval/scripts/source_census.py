"""Counts the docs, domains, bytes, tokens and windows of every source of a `source_suite` benchmark in its scored split, and checks that the suite lists each source's domains.

Run from the repository root, with read access to the dataset (Paloma's terms accepted on the Hugging Face Hub, an `HF_TOKEN` for a private one):

    uv run python flock/eval/scripts/source_census.py paloma paloma-census.json
    uv run python flock/eval/scripts/source_census.py tw_text tw-text-census.json

It exits with status 1 if a source the suite lists has a domain in the split that the suite doesn't list, which no task would score and nothing at run time reports, or lists a domain the split doesn't have. A source the suite leaves out is reported but allowed, as Paloma leaves out 7 of its 16.

It takes each source's files as `datasets` resolves them, the files the tasks load, and reads their raw rows itself, so the counts don't depend on how a `datasets` version parses them. It loads only a tokenizer, never model weights.
"""

import gzip
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import datasets
import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download
from transformers import AutoTokenizer, PreTrainedTokenizerBase

from flock.eval.tasks import SUITES, PerplexityTask


def count_source(
    tokenizer: PreTrainedTokenizerBase, window: int, files: list[Path]
) -> dict[str, Any]:
    """
    Counts one source's split from its `.jsonl.gz` or `.parquet` files, overall and per domain, with windows of `window` tokens.

    Raises:
        `ValueError`: if a file is in another format.
    """
    # Every total is written, at 0 for a source whose files are all empty.
    totals: Counter[str] = Counter(
        {key: 0 for key in ("docs", "bytes", "tokens", "empty_texts", f"over_{window}", "windows")}
    )
    domain_docs: Counter[str] = Counter()
    domain_bytes: Counter[str] = Counter()
    domain_tokens: Counter[str] = Counter()
    doc_ids = []
    empty_files = []
    for file in files:
        if file.name.endswith(".jsonl.gz"):
            with gzip.open(file, "rt", encoding="utf-8") as f:
                rows = [json.loads(line) for line in f]
        elif file.suffix == ".parquet":
            # Only the columns a task reads: other columns can be large, and a source with one
            # domain may have no `subdomain`.
            names = pq.read_schema(file).names
            columns = [column for column in ("id", "text", "subdomain") if column in names]
            rows = pq.read_table(file, columns=columns).to_pylist()
        else:
            raise ValueError(f"{file.name}: the census reads .jsonl.gz and .parquet files only")
        if not rows:
            empty_files.append(file.name)
            continue

        # No special tokens, as `ScoringModel` tokenizes.
        encoded = tokenizer([row["text"] for row in rows], add_special_tokens=False)
        lengths = [len(token_ids) for token_ids in encoded["input_ids"]]
        for row, length in zip(rows, lengths, strict=True):
            # A source with one domain may have no `subdomain` field. Its domain is "", and a
            # null `subdomain` counts as none, as `PerplexityTask.load` reads it. Rows without one
            # in a source with domains count under "" too, so the check reports them unlisted.
            domain = row.get("subdomain") or ""
            size = len(row["text"].encode())
            doc_ids.append(row["id"])
            domain_docs[domain] += 1
            domain_bytes[domain] += size
            domain_tokens[domain] += length
            totals["docs"] += 1
            totals["bytes"] += size
            totals["tokens"] += length
            totals["empty_texts"] += length == 0
            totals[f"over_{window}"] += length > window
            # The last window holds what is left. An empty text has none: it can't be scored,
            # and `empty_texts` counts it.
            totals["windows"] += math.ceil(length / window)

    # Paloma's ranked domains in rank order, `1_www.nytimes.com` before `10_...`, and the others
    # most documents first, as `tw_text` lists them (026.D6), then by name.
    rank = {}
    for domain in domain_docs:
        prefix = domain.split("_")[0]
        ranked_prefix = prefix.isascii() and prefix.isdigit()
        rank[domain] = (int(prefix) if ranked_prefix else 0, -domain_docs[domain], domain)
    ranked = sorted(domain_docs, key=rank.__getitem__)

    return {
        **totals,
        "domains": len(domain_docs),
        "repeated_ids": len(doc_ids) - len(set(doc_ids)),
        "empty_files": empty_files,
        "domain_docs": {domain: domain_docs[domain] for domain in ranked},
        "domain_bytes": {domain: domain_bytes[domain] for domain in ranked},
        "domain_tokens": {domain: domain_tokens[domain] for domain in ranked},
    }


def main(suite_name: str, out: Path) -> None:
    """Writes the census of `suite_name`'s scored split to `out` as JSON, prints each source's domains, and exits with status 1 if a source the suite lists has other domains than the split."""
    listed: dict[str, set[str]] = {}
    pins = set()
    for task in SUITES[suite_name].tasks:
        if not isinstance(task, PerplexityTask) or task.dataset.subset is None:
            raise TypeError(f"{task.name} is not a task of a source_suite")
        # A one-domain source lists no domain when its rows have no `subdomain`: the "" the
        # census counts them under.
        listed.setdefault(task.dataset.subset, set()).add(task.domain or "")
        pins.add((task.dataset.repo, task.dataset.revision, task.dataset.split, task.window))
    # One census reads one split, so every source must score the same revision with the same
    # window for the counts to be what the tasks score.
    if len(pins) != 1:
        raise ValueError(f"{suite_name}: the tasks score more than one split or window: {pins}")
    repo, revision, split, window = pins.pop()

    tokenizer_repo = "allenai/OLMo-2-0425-1B"
    tokenizer_revision = "a1847dff35000b4271fa70afc5db10fd29fedbdf"
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_repo, revision=tokenizer_revision)
    assert tokenizer is not None  # The stubs allow None; loading raises instead.

    # A source is a configuration of the dataset, and its split is the files `datasets` resolves
    # for it, as `Dataset.load` reads them: `hf://datasets/<repo>@<revision>/<file>` each.
    sources = sorted(datasets.get_dataset_config_names(repo, revision=revision))
    prefix = f"hf://datasets/{repo}@{revision}/"
    census = {}
    for source in sources:
        try:
            builder = datasets.load_dataset_builder(repo, source, revision=revision)
        except FileNotFoundError:
            # A pattern of a source the suite leaves out may match no file; it is reported as
            # left out. A listed source's raises, as its tasks' loads would.
            if source in listed:
                raise
            continue
        files = []
        for url in (builder.config.data_files or {}).get(split, []):
            if not url.startswith(prefix):
                raise ValueError(f"{url}: datasets resolved a file outside {repo}@{revision}")
            file = url.removeprefix(prefix)
            files.append(Path(hf_hub_download(repo, file, repo_type="dataset", revision=revision)))
        if files:
            census[source] = count_source(tokenizer, window, files)

    # A domain only the split has is never scored, and nothing at run time says so. One only the
    # suite has raises when its task loads. Rows without a `subdomain` count under "", which a
    # table can't list in a source with domains, so the differences name them in words.
    differences = {}
    for source, domains in listed.items():
        found = set(census[source]["domain_docs"]) if source in census else set()
        if found != domains:
            differences[source] = {
                "not_listed": [
                    domain or "(rows without a subdomain)" for domain in sorted(found - domains)
                ],
                "not_in_split": [
                    domain or "(rows without a subdomain)" for domain in sorted(domains - found)
                ],
            }
    unlisted = [source for source in sources if source not in listed]

    header = {
        "suite": suite_name,
        "repo": repo,
        "revision": revision,
        "split": split,
        "tokenizer": f"{tokenizer_repo}@{tokenizer_revision}",
        "unlisted_sources": unlisted,
        "differences": differences,
    }
    census_json = json.dumps({**header, "sources": census}, indent=1, ensure_ascii=False)
    out.write_text(census_json + "\n", encoding="utf-8")

    # Printed after the file is written, so a terminal that can't show a domain loses no counts.
    # The listed sources come first, in the suite's order, ready to paste over its table. The
    # sources it leaves out follow, and the line after them names them.
    for source in [*listed, *unlisted]:
        if source in census:
            domains = [domain for domain in census[source]["domain_docs"] if domain]
            print(f"{source!r}: {tuple(domains)!r},")
    if unlisted:
        print(f"Sources {suite_name} leaves out: {', '.join(unlisted)}")
    for source, difference in differences.items():
        print(f"{source}: {difference}")
    if differences:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
