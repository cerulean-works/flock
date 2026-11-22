# 043: item text in the predictions, and `flock eval items`

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-22 |
| Builds on | [041](041-result-files.md) (041.D2 `correct`, 041.D5 `read_results`, 041.D6 `diff` and its refusals), [042](042-private-predictions.md) (042.D1 private predictions under `private/`) |
| Amends | 041.D2 (a row also carries the doc's text and `pick`), 041.D5 (the reader drops the header line of schema 11 files) |
| Keeps | every task hash and suite hash, every request and score, every metric in `results.json`, `diff` and its JSON, which files a run uploads and under which names |
| Result | predictions schema 11: a header with the task's prompt, and per row the doc's `question`, `choices` (or per-choice `contexts`), `shots` when a doc saw fewer examples, and `pick`; `flock eval items` prints one run's or two runs' docs with text, probabilities and shares; `items` is the torch-free reader both use |

The decision numbers are provisional: `043.D1` to `043.D5` take the next free global numbers after every stage merged before it.

## 1. Goal

A diff says how many docs two models disagree on, and which ids. It can't say why, because a row holds ids and numbers only. To read failures, a person or an agent needs the question, the choices, what each model picked and how likely it found each choice. This stage writes the text into the predictions rows and adds a reader, a command and its JSON, so the dashboard can show two runs side by side from the same code.

**Out of scope:** the dashboard page, comparing more than two runs, text for perplexity tasks (their docs are whole texts), rescoring older runs, any change to a task hash or a metric, significance tests.

---

## 2. Design decisions

### 043.D1. The text lives in the predictions rows, as scored

Each multiple-choice row gains `question`, the context its choices share, and `choices`, each continuation exactly as scored, leading space included, so `num_chars` counts the same string and `prompt + question + choice` rebuilds each request. A doc whose choices have their own contexts (Winogrande puts each option into the sentence) has `question` null and `contexts`, one per choice. Display code strips the space. This follows lm-evaluation-harness, whose `--log_samples` keeps each sample's `doc` and request `arguments` beside its responses (`lm_eval/evaluator.py`, v0.4.3), rather than OLMES, which keeps requests in a separate, opt-in `requests.jsonl` (`--save-raw-requests`, `oe_eval/run_eval.py`). A separate file per task, or a dataset load at view time, was considered and not taken: one file per task keeps the upload names and the `private/` rule of 042.D1 as they are, and the text read is the text scored, whatever the dataset's revision does later.

Rows before schema 11 have no text and get it only by scoring again.

### 043.D2. The few-shot prompt is a header line, written once per task

A multiple-choice file opens with `{"prompt": {"description": ..., "shots": [{"id": ..., "text": ...}, ...]}}`. A doc whose examples differ from all of them in order (a task drawing them from the scored split drops the doc itself, as `basic_skills` does for every doc) has `shots`, the indices it was shown. Written into every row, the prompt would add 193 MB to a Base Easy run; written once, 164 KB (measured, see section 4). `WrittenResults.predictions` drops the header, so `diff` and the app read rows as before. A perplexity file has no header.

### 043.D3. `pick` is written by the scorer

A row whose primary metric ranks choices (every accuracy but `acc_greedy`) gains `pick`, the choice that metric picks, computed on the device from the same scores the metric averages. A reader recomputing the pick in Python could break a near tie the other way than the FP32 scorer did. `metrics.ranking_scores` now names each accuracy's ranking score once, and `multiple_choice` and the pick both use it, so the metric values are unchanged bit for bit.

### 043.D4. Each run's answer carries `prob` and `share`

`prob` is the softmax of the choices' log-probabilities, a probability renormalized over the choices (OLMES's `acc_raw` ranks by the same log-probabilities). `share` is the softmax over the choices of the score the primary metric ranks them by: per token, character or byte (in bits) for the length-normalized accuracies, the log-probability minus the unconditional one for `acc_uncond`. Neither OLMES nor OLMo 3 defines a per-choice quantity under a length-normalized metric, so this one is named a share and never a probability. Measured on an ARC-Easy doc, the shares under `acc_per_char` sit within 0.014 of 0.25 while `prob` puts 0.62 on one choice, which is why both are kept: `prob` for how likely the model found a choice, `share` and `pick` for what the metric made of it.

### 043.D5. `items` reads one run or two, under `diff`'s rules

`items(runs, task, keep, limit, seed)` returns `Item`s, each with a list of `Answer`s, one per run, so a later view of more runs needs no new format. One run keeps its `wrong`, `right` or `all` docs; two runs keep one of `diff`'s cells. Two runs go through `diff`'s checks, now shared as `check_instrument` and `outcomes`, plus equal task hashes, equal ids in order and equal text per doc. A seeded sample keeps doc order. A run of an older schema is refused with a message that says to score it again. The module imports no torch, so the app's API can call it.

---

## 3. Plan

1. Rows and header: `MCRequests` records the prompt and the examples each doc saw; `prediction_rows` and `write_predictions` write them; `score_prepared` writes `pick`. Schema 11.
2. The reader drops the header; `diff`'s checks become two shared functions; `items` and `flock eval items`.
3. Tests: the text rebuilds every scored request; cells match `diff`; picks explain outcomes; refusals; a tracker run prints what its folder prints; a private task's text reaches no file outside `private/`.
4. Docs: results, commands, comparison (with reading failures with an agent), the Python API.
5. End to end on two models, with every task hash and metric equal to a banked run.

---

## 4. How it was actually built

- Growth, measured before writing any code by loading every Base Easy task and the private chemistry exam task without a model: the predictions of one run grow from 39.2 MB to about 114 MB with UTF-8 text (75.3 MB added with escaped text instead). CoQA, SQuAD, DROP and HellaSwag account for 44 MB, since their passages repeat for every question. The chemistry task grows from 306 KB to 631 KB, its text taking 325 KB as UTF-8 and 527 KB escaped, so rows are written with `ensure_ascii=False` and read as UTF-8 from a folder and from the tracker.
- Only Winogrande, in Base Easy, has per-choice contexts, and only the six `basic_skills` tasks show a doc fewer examples.
- `MultipleChoiceTask._shots_for` returns indices into the examples rather than the examples, so the requests and the rows come from one list.
- A training loop that scores between steps writes no predictions unless it passes an output folder, so it pays nothing for the text.

## 5. Known gaps

- The header is not checked against the task hash: a header from another file would read without complaint. Rows are, through the ids and the outcomes.
- Passages that repeat across a task's questions are stored once per question.
