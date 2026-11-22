# Results files

A run with an output directory writes `results.json` and one predictions file per task:

```text
<out>/
  results.json
  predictions/<task>.jsonl
  predictions/<task>.tokens.parquet
```

The `.tokens.parquet` files hold [per-token values](#per-token-values), for multiple-choice tasks unless you pass `--no-tokens`.

## `results.json`

```text
{
  "provenance": { ... },
  "tasks": [
    {
      "task": "arc_easy",
      "task_hash": "<sha256>",
      "dataset": {"repo": "allenai/ai2_arc", "revision": "210d026f...", "subset": "ARC-Easy", "split": "test", "row_index": false, "private": false},
      "primary_metric": "acc_per_char",
      "metrics": {"acc_raw": ..., "acc_per_token": ..., "acc_per_char": ..., "acc_per_byte": ..., "acc_uncond": ..., "bpb": ...},
      "num_docs": 2376,
      "limit": null,
      "seconds": ...,
      "truncation": {"requests": 0, "docs": 0, "max_tokens_cut": 0},
      "docs_per_second": ...
    },
    ...
  ],
  "suites": [
    {"suite": "mmlu", "suite_hash": "<sha256>", "members": ["mmlu_abstract_algebra", ...], "metric": null, "score": ...},
    {"suite": "base_easy:qa_rc", "suite_hash": "<sha256>", "members": ["arc_easy", ..., "basic_skills", "lambada"], "metric": null, "score": ...},
    {"suite": "mmlu:bpb", "suite_hash": "<sha256>", "members": ["mmlu_abstract_algebra", ...], "metric": "bpb", "score": ...},
    {"suite": "base_easy:qa_bpb", "suite_hash": "<sha256>", "members": ["arc_easy", ..., "basic_skills:bpb", "lambada"], "metric": "bpb", "score": ...},
    ...,
    {
      "suite": "base_easy",
      "suite_hash": "<sha256>",
      "members": ["base_easy:qa_rc", "base_easy:qa_bpb", "base_easy:math_bpb", "base_easy:code_bpb"],
      "metric": null,
      "score": null
    }
  ],
  "schema_version": 12
}
```

`flock eval run` prints only a summary of this file (each suite's score and the primary metric of each task outside a scored suite, or every metric with `--verbose`), so read the file for the rest. The file is a [`~eval.results.Results`]. Each entry of `tasks` is a [`~eval.results.TaskResult`] plus its `docs_per_second`, which is `null` if the clock measured no time. A metric or suite score that isn't finite, such as a diverged checkpoint's `perplexity`, is written as `null`, since JSON has no infinity (RFC 8259, sec. 6). Each entry of `suites` is a [`~eval.results.SuiteResult`], and `provenance` is described below. The class docstrings at the end of this page define every field.

`dataset.private` says whether the dataset's repository is private, such as the Taiwanese exam sets. A run never uploads a private dataset's predictions to a public tracker project, and it uploads them under `private/`, which only your organization's members read (see [Tracker runs](commands#tracker-runs)). In the run's folder they stay in `predictions/`. Task hashes leave it out, since it changes no request. Files written before schema version 9 have no `private`.

### The task hash

`task_hash` is the sha256 of the task's definition (every field but `to_doc`, so its name and primary metric too), every scored context and continuation, and every gold index. Changing the dataset revision, the few-shot examples, the task's name, description, metrics or primary metric, or a choice's context or continuation or the gold index that `to_doc` returns changes it. Doc ids aren't hashed. Two results with the same hash scored the same requests under the same task definition. `dataset` repeats the pinned dataset next to the hash, so you can see which rows were scored without the task's code.

A perplexity task's hash covers its definition (name, dataset, domain and window) and every doc's id and text.

### Suites

`suites` holds the suites the run was given and every suite they hold, each after its members, and is empty for a run of tasks only. Each entry lists its members by name, and every member has its own entry in `tasks` or `suites`, so you can recompute the score: the mean of each member task's `metric` (its `primary_metric` when `metric` is `null`) and each member suite's `score`. A suite that only groups others has a `null` score. A suite's members can grow between versions of `flock eval`, so compare two suite scores only when their `suite_hash`es are equal. `suite_hash` is the sha256 of the JSON object `{"suite": <name>, "metric": <metric>, "scored": <bool>, "members": [[<name>, <hash>], ...]}`, written with Python's `json.dumps(..., sort_keys=True)`, where each member gives its `task_hash` or `suite_hash`. Equal suite hashes mean the same members, all the way down, scored the same requests, so a `limit` shows up too. This is a check of our own: OLMES and lm-evaluation-harness record no hash for a group of tasks.

### Truncation

A context too long for the model loses tokens from the left, and the continuation is kept whole (see [How scoring works](scoring)). `truncation` says how often that happened in the task: how many requests were cut, how many docs had at least one cut request, and the most tokens cut from one request. Metrics on cut contexts can differ from metrics on whole ones, so check `docs` before comparing a task across models: a different `max_length` or tokenizer cuts different requests. `max_tokens_cut` counts the model's own tokens. All three are 0 when nothing was cut.

## Provenance

`provenance` records what produced the run, so a number can be traced to the exact model, code and settings: [`~eval.provenance.Provenance`] at the end of this page defines each field. It is collected once the model has loaded and before any task is scored, so `created_at` is when scoring started, as lm-evaluation-harness records its start date. `command` is the command line that started the run, the program's name followed by its arguments (for example `flock eval run --model ... --task arc_easy --out ...`), and `null` for a Python API call that passes none. Older files record the CLI's former name instead: `flock-eval run ...` from the standalone script, and `flock-eval eval run ...` from `flock eval` before the provenance caught up. `flock_commit` is `"unknown"` when the installed package isn't a flock checkout, and ends in `-dirty` when tracked files changed or new files appeared under `flock/`.

`model` is `repo@commit` for a Hugging Face model, `flock:org/path@{number}` or `flock:project:path@{number}` for a version stored in Flock, and `local:{digest}` for a local folder. `model_digest` is that version's or folder's digest, the sha256 of its file list, which names its exact files. It's `null` for a Hugging Face model, whose commit already names the files. Files written before `model_digest` was added, including some at schema version 6, have no `model_digest`, and read as `null`. For a Flock model, `model_org`, `model_project` and `model_path` name it whatever the default organization of whoever reads the file: `flock:pretrain:sweeps/lr-3e-4@12` records `"acme"`, `"pretrain"` and `"sweeps/lr-3e-4"`. `model_project` is `null` for an organization's model. All three are `null` for a Hugging Face model, a local folder, and in files written before schema version 10.

`attention` is the model's attention implementation, and `sdpa_backend` the SDPA kernel scoring was pinned to: `"EFFICIENT_ATTENTION"` on CUDA with `sdpa` attention, `null` on the CPU or another non-CUDA device, where PyTorch still picks the kernel, or with other attention (see [Batching and precision](scoring#batching-and-precision)). Files written before schema version 7 have no `sdpa_backend`: PyTorch chose the kernel for each batch, so their BF16 scores don't repeat exactly.

`add_bos` says whether every non-empty context started with the tokenizer's BOS token (see [Tokenizing a request](scoring#tokenizing-a-request)). Files written before schema version 8 have no `add_bos`: none of them added BOS.

`tokenizer` says where the tokenizer was loaded from: the Hugging Face repo or folder, or the `tokenizer.model` file of a repo tokenized by SentencePiece. `tokenizer_hash` is the sha256 of the tokenizer's whole definition: its `tokenizer.json` as the Tokenizers library writes it, or the SentencePiece model's bytes. Token ids in a run's files mean nothing without the tokenizer that made them, so two runs whose `tokenizer_hash` differs can't be compared token by token. A tokenizer written in Python, with no Tokenizers backend, has no `tokenizer_hash`. Files written before schema version 12 have neither, and read as `null`.

## Predictions

A multiple-choice task's `predictions/<task>.jsonl` opens with one header line, the text every doc's context starts with:

```json
{"prompt": {"description": "", "shots": [{"id": "MCAS_2007_8_5189", "text": "Question: Lichens are symbiotic organisms made of green algae and fungi. What do the green algae supply to the fungi in this symbiotic relationship?\nAnswer: food"}, ...]}}
```

`description` is the task's text before its examples, and `shots` lists each few-shot example as another doc is shown it. A doc's scored context is `description`, then each example it was shown followed by a blank line, then its own `question`. The header is written once per task rather than in every row, since repeating it would triple the size of a Base Easy run's files.

Each later line is one doc, with its text and enough to recompute every metric of the task and its truncation counts:

```json
{"id": "Mercury_7017010", "gold": 0, "correct": false, "pick": 3, "question": "Question: Which tools are needed to measure the effects of friction on a block of wood?\nAnswer:", "choices": [" meter stick, spring scale", " balance, stopwatch", " balance, meter stick", " meter stick, stopwatch"], "logprob": [-11.73, -7.96, -9.27, -9.11], "num_tokens": [5, 3, 4, 4], "num_chars": [26, 19, 21, 23], ...}
```

| Field | Meaning |
|---|---|
| `id` | The doc's id in the dataset. |
| `gold` | The index of the correct choice. |
| `correct` | Whether the doc counts as right under the task's primary metric, for a task whose primary metric is an accuracy. It is the value that metric averages, so the rows' count of `true` over the doc count is the metric in `results.json`. Tasks read first by `bpb` have no `correct`, and neither do files written before schema version 9. |
| `pick` | The index of the choice the primary metric picks, ties going to the first. Only a primary metric that ranks choices has it, so `acc_greedy` and `bpb` tasks don't. `correct` is `pick == gold`. |
| `question` | The doc's own context, which its choices share, as scored. `null` if each choice has its own context. |
| `contexts` | Each choice's own context, only when `question` is `null`, as in Winogrande, which puts each option into the sentence. |
| `choices` | Each choice's continuation exactly as scored, leading space included, so `num_chars` counts it. |
| `shots` | The indices in the header's `shots` of the examples this doc was shown, only when it saw other than all of them in order. A task that draws its examples from the scored split, such as `basic_skills`, never shows a doc itself. |
| `logprob` | Each choice's summed log-probability. |
| `num_tokens` | Each choice's token count. |
| `num_chars` | Each choice's character count, leading space included. |
| `num_bytes` | Each choice's UTF-8 byte count, leading space included. |
| `is_greedy` | Whether each choice's continuation is the model's greedy output: every token is its position's most likely next token. |
| `tokens_cut` | The tokens cut from the left of each choice's context, 0 if it fit. |
| `logprob_uncond` | Each choice's log-probability after the unconditional context, for tasks that score pointwise mutual information (PMI). |
| `tokens_cut_uncond` | `tokens_cut` for the unconditional requests, with `logprob_uncond`. |
| `split` | The dataset split the doc's row is in. |
| `source_index` | The row's position in that split, counting rows a task's `keep` drops, so you can find the row in the dataset whatever its id. |
| `native_id` | The dataset's own id for the row, where the task names its column and it differs from `id`, as OLMES records `native_id`. HellaSwag's is `ind`. |
| `subject` | The row's subject or category, where the task names its column, such as MMLU's subject. |
| `meta` | Other values of the row the task names, each a string, number, boolean or `null`, such as a MATH problem's `level`. |
| `doc_hash` | lm-evaluation-harness's `doc_hash` of the row: the sha256 of `json.dumps(row, indent=2, ensure_ascii=False)`, the row as the dataset gives it. A sample that lm-evaluation-harness logged with `--log_samples` for the same row, in a task that keeps rows as they are, has the same `doc_hash`, so the two join. |
| `prompt_hash` | lm-evaluation-harness's `prompt_hash`: the sha256 of the doc's first scored context, few-shot examples included. Equal across runs of one task hash. |

[Metrics](metrics) gives each metric's formula over these fields.

Read rows by their keys. A later schema may add fields to a row, or keys to `meta`, and a reader should skip what it doesn't know, as `flock eval` does. Files written before schema version 12 have none of the last seven fields. A task names the columns `native_id`, `subject` and `meta` come from in its `row_fields` (see [Task definitions](task-definitions#what-a-row-records)).

Text is written as UTF-8, not escaped, so a Chinese task reads as written. Files written before schema version 11 have no header and no `pick`, `question`, `contexts`, `choices` or `shots`. Task hashes don't change: they already cover every scored request, and the text is output, not part of the task's definition. A private dataset's text stays in its predictions file, so it follows that file's privacy rule.

A perplexity task's file has no header, and no text, since its docs are whole texts. Its lines have other fields, enough to recompute each of its metrics over any set of docs:

| Field | Meaning |
|---|---|
| `id` | The doc's id in the dataset. |
| `logprob` | The text's summed log-probability over all of its windows. |
| `num_tokens` | The text's token count. |
| `num_bytes` | The text's UTF-8 byte count. |
| `windows` | How many windows the text was scored in. |
| `is_greedy` | Whether every token of the text is its position's most likely next token. |
| `source` | The dataset's subset, which for Paloma is the source, such as `c4_100_domains`. `null` for a dataset with none. |
| `domain` | The task's domain within its source, the rows' `subdomain`. `null` for a source with one domain. |

`source` and `domain` let you pool the rows of many tasks and split them again by either. Files written before schema version 12 have neither.

## Per-token values

A multiple-choice task's `predictions/<task>.tokens.parquet` holds what the model made of each token, for analyses that summed log-probabilities can't support: where along a choice two checkpoints diverge, which tokens surprise a model, and contamination checks such as Min-K% (Shi et al. 2023) and Min-K%++ (Zhang et al. 2024). The predictions file stays as it is, so readers of rows don't change. The file is [Parquet](https://parquet.apache.org/), compressed with zstd, as LightEval writes its per-sample details. Each row is one token sequence of one doc:

| Column | Meaning |
|---|---|
| `id` | The doc's id, as in its prediction row. |
| `choice` | The choice the sequence belongs to: for a `context` or `greedy` row, the first choice with that context. |
| `part` | `continuation`: a choice's scored tokens, one row per choice. `greedy`: the first 32 tokens greedy decoding writes after the doc's context, one row per distinct context, with `--greedy-tokens`. `context`: the context's own tokens, with `--context-tokens`. |
| `token_ids` | The tokens, from the tokenizer the provenance names and hashes. |
| `logprob` | Each token's natural-log probability. A continuation's add up to its `logprob` in the prediction row, to FP32 rounding. |
| `rank` | 1 plus the number of tokens the model found more likely, so the model's own choice has rank 1. |
| `entropy` | The entropy, in nats, of the distribution each token was drawn against. Its negative is the mean log-probability under that distribution, Min-K%++'s mu. |
| `top1_logprob` | The log-probability of the most likely token at that position. |
| `logprob_std` | The standard deviation of the log-probability under that distribution, Min-K%++'s sigma. |

A `greedy` row has no values. A Flock model has no `greedy` rows: it keeps no key-value cache, and decoding without one would read each context 32 times over. A `context` row covers every token of the context but its first, which nothing predicts, after the context loses tokens from the left to leave room for 32 decoded ones.

Context values and greedy tokens come from a pass of their own over each doc's distinct contexts, once per context, not once per choice. Each is off by default, for its cost: on OLMo 2 1B over all of MMLU, HellaSwag and ARC-Easy on one H200, the choices' values alone write 35.5 MB beside 35.1 MB of predictions, and 32 greedy tokens, decoded one step at a time, took scoring from 355 s to 539 s. Scoring itself runs as it does without the file, so every score and every prediction row is the same, bit for bit, with or without it.

A private dataset's per-token file is readable by its owner only, and goes under `private/` on a tracker run, as its predictions do: token ids are the dataset's text. A training loop that scores between steps writes none unless it asks (`score_prepared(..., tokens=True)`).

Read a task's rows, or only some docs', with [`~eval.results.WrittenResults.tokens`]:

```python
run = read_results("results/olmo2-1b")
rows = run.tokens("arc_easy", ids=["Mercury_7017010"])  # None if the run has no file for it
```

## Reading a run back

[`~eval.results.read_results`] reads a written run from its folder, or from a tracker run that uploaded its files (see [Tracker runs](commands#tracker-runs)) as `run:<entity>/<project>/<id>`. It reads schema versions 8 to 12, and reads a task's predictions only when you ask for them, without the header line:

```python
from flock.eval.results import read_results

run = read_results("results/olmo2-1b")
print(run.provenance.model, [task.task for task in run.tasks][:3])
rows = run.predictions("arc_easy")  # None if the run has no predictions file for it
```

A tracker run is read as the user signed in with `flock login`, on that server. [`flock eval diff`](commands#flock-eval-diff) compares two runs read this way, and [`flock eval items`](commands#flock-eval-items) prints their docs with the text.

## Classes

[[autodoc]] eval.results.Results

[[autodoc]] eval.results.TaskResult

[[autodoc]] eval.results.SuiteResult

[[autodoc]] eval.tasks.Truncation

[[autodoc]] eval.provenance.Provenance

[[autodoc]] eval.results.read_results

[[autodoc]] eval.results.WrittenResults

[[autodoc]] eval.requests.TokenValues

[[autodoc]] eval.requests.ContextScores
