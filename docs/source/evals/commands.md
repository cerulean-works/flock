# Command line

`flock eval` is installed with the workspace. Run it with `uv run flock eval` from the repository root.

## `flock eval run`

Scores one model on tasks and suites, writes the results, and prints a summary: one line per suite with its score, its metric (`primary` for each member's primary metric) and member count, each suite before its members, starting from the suites no other suite holds in the order you gave them, then one line per task with its primary metric and doc count. A task inside a scored suite prints only with `--verbose`, so `--suite base_easy` shows the suite lines alone. `--verbose` prints every metric of every task and each suite's members instead. `results.json` holds all of it either way.

```bash
uv run flock eval run \
  --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
  --task arc_easy \
  --out results/olmo2-1b
```

| Flag | Default | Meaning |
|---|---|---|
| `--model` | required | The model as `repo@revision`. A branch or tag is resolved to its commit through the Hub, and the results record the commit. A full commit hash needs no Hub access, so it works offline. A model stored in Flock is `flock:org/path`, `flock:org/path@{number}`, `flock:org/path@{tag}` or `flock:org/path@primary`, where the path may hold folders, such as `flock:acme/eval/olmo2-1b@12`. A project's model is `flock:project:path[@version]`, in your default organization, such as `flock:pretrain:sweeps/lr-3e-4`. No version means the primary. It's resolved to its number through Flock, and the results record it with that number, the version's `model_digest` and the model's organization, project and path. See [Score a model stored in Flock](run-evaluation#score-a-model-stored-in-flock). |
| `--task` | none | A task name from [Tasks](tasks#available-tasks). Repeat it for several tasks with one model load. |
| `--suite` | none | A suite name from [Suites](tasks#suites). It runs the suite's tasks and scores the suite and every suite it holds. Repeat it for several suites. A task in several suites, or also given with `--task`, runs once. |
| `--out` | required | The directory for `results.json` and `predictions/`. It must be new or empty, so one directory holds one run. |
| `--limit` | all docs | Score only each task's first N docs, for quick checks. Results with a limit aren't comparable to full runs. |
| `--batch-tokens` | `32768` | The most padded tokens in one forward pass. Lower it if the GPU runs out of memory. Compare only runs with the same value. See [Batching and precision](scoring#batching-and-precision). |
| `--dtype` | `bfloat16` | The weights' dtype, `bfloat16` or `float32`. Log-softmax runs in float32 either way. Compare only runs with the same value. |
| `--add-bos` | off | Start every non-empty context with the tokenizer's BOS token. Set it for Gemma models, as OLMES does. Compare only runs with the same value. See [Tokenizing a request](scoring#tokenizing-a-request). |
| `--device` | `cuda` if available, else `cpu` | The torch device. |
| `--wandb-project` | off | Also log the run to this project through the `wandb` SDK, with `results.json` and the predictions as files of the run. [Connect the SDK](../cli/sign-in#wandb-sdk) first so it goes to Flock. See [Tracker runs](#tracker-runs). |
| `--verbose` | off | Print every metric of each task, primary first, with its speed and the start of its task hash, then each suite with its members. |

Give at least one `--task` or `--suite`. The command runs [`~eval.run.evaluate`], and every flag maps to a field of [`~eval.run.EvalConfig`].

To run all of OLMo 3's Base Easy that exists so far:

```bash
uv run flock eval run \
  --model allenai/OLMo-2-0425-1B@a1847dff35000b4271fa70afc5db10fd29fedbdf \
  --suite base_easy \
  --out results/olmo2-1b-base-easy
```

## `flock eval compare`

Compares a new `results.json` with a banked one and exits non-zero if any score moved past its tolerance or the runs used different settings. See [Compare with the regression bank](compare-regression) for the procedure and [Comparison rules](comparison) for refusals and tolerances.

```bash
uv run flock eval compare tests/eval/regression/olmo-2-0425-1b-float32.json results/regression/results.json
```

## `flock eval diff`

Compares two runs doc by doc: for each task both scored the same way, it counts the docs both runs got right, both got wrong, and only one of them got right, and lists the ids of the docs only one got right. The two runs may score different models.

```bash
uv run flock eval diff results/olmo2-1b run:acme/model-evals/k3v9x2pa
```

| Argument or flag | Default | Meaning |
|---|---|---|
| `A`, `B` | required | Each run: a folder holding `results.json` and `predictions/`, or a tracker run as `run:<entity>/<project>/<id>`, read as the user signed in with `flock login`. |
| `--json` | off | Print the diff as JSON instead of a table. See [Doc-by-doc diff](comparison#doc-by-doc-diff) for its fields. |

It exits with code 2 if the runs can't be compared: a missing or damaged results file, a run it can't read, or runs scored with another `dtype`, `batch_tokens`, `attention` or `sdpa_backend`. [Doc-by-doc diff](comparison#doc-by-doc-diff) lists which tasks it compares.

## `flock eval items`

Prints a task's docs with their question, choices and each run's answer: from one run, its wrong (or right, or all) docs; from two runs, one cell of their [doc-by-doc diff](comparison#doc-by-doc-diff). Use it to read what a model gets wrong, not only how often.

```bash
uv run flock eval items results/olmo2-1b --task arc_easy --limit 5
uv run flock eval items results/olmo2-1b run:acme/model-evals/k3v9x2pa --task arc_easy --cell only_b --json
```

| Argument or flag | Default | Meaning |
|---|---|---|
| `RUNS` | required | One run or two, each a folder or `run:<entity>/<project>/<id>`, as for `flock eval diff`. |
| `--task` | required | The task. Its primary metric must be an accuracy. |
| `--only` | `wrong` | One run: print its `wrong`, `right` or `all` docs. |
| `--cell` | required with two runs | Two runs: print the docs of `only_a`, `only_b`, `both_wrong` or `both_right`. |
| `--limit` | all | Print at most this many docs, the first ones in doc order. |
| `--seed` | none | With `--limit`, print a sample drawn with this seed instead, still in doc order. |
| `--json` | off | Print one JSON object per doc instead of text. See [Reading items](comparison#reading-items) for its fields. |

The text form prints, per doc, the task, id and metric, each run's model, verdict and pick, the question, then one line per choice: each run's probability (`p`) and share (`s`), `*` on the gold choice and `<` on each run's pick. Two runs must be readable by `flock eval diff`, with the task under one hash in both. Runs written before schema version 11 hold no text, so the command refuses them: score the model again. It exits with code 2 on any refusal.

## `flock eval log`

Logs a `results.json` written earlier to a tracker project, as written, without scoring anything. Use it for results scored without `--wandb-project`.

```bash
uv run flock eval log results/olmo2-1b/results.json --project model-evals
```

| Argument or flag | Default | Meaning |
|---|---|---|
| `RESULTS` | required | The `results.json` to log. It must have `schema_version` 6 to 11. It goes on the run as `results.json`, whatever its name, and from schema version 9 on so do the predictions in the `predictions/` folder beside it. From schema version 9 on, the file must be named `results.json`, so the predictions beside it are its own. |
| `--project` | required | The tracker project. |

It needs both sign-ins: `flock login` for the duplicate check, and the wandb SDK's key for the same server to log the run. [Sign in](../cli/sign-in) sets up both. The run goes to the server you signed in to with `flock login` and to your default organization, online, whatever the SDK's own settings and `WANDB_MODE` say. A `WANDB_API_KEY` set for another server fails with HTTP 401.

The command refuses a file that's already logged. It looks in the project for a run whose `config.model` and `config.created_at` equal the file's, and exits with code 1 if it finds one. A run that failed or crashed doesn't count, since it may hold no scores. The check covers only the project in your default organization on the server you signed in to. A run logged live into another organization or server, or by a teammate into theirs, isn't found.

The run differs from a live one in a few ways:

- **Fields the file lacks stay absent.** An older file has no `add_bos` or `sdpa_backend`, so its run has neither. A missing `sdpa_backend` means the kernel is unknown, which is different from `None` (no pin applied). See [provenance](results#provenance).
- **The run's own stamps are the logging machine's.** The tracker records the host, time and git commit of the machine that ran `flock eval log`. The scoring job's facts are `config.created_at` and `config.flock_commit`.
- **Infinite and NaN scores are missing.** `results.json` writes them as `null`, so the run has no value for that metric or suite, where a live run logs the number.
- **Two runs started in the same second look like one.** `created_at` has one-second resolution, so a second file of the same model stamped in the same second is refused as already logged.
- **The organization is yours.** Until a shared project for comparisons is agreed, a run lands in your default organization.

## `flock eval hub pull`

Stages one pinned dataset from Flock for jobs that can't reach the network. See [Run where there is no network](run-evaluation#run-where-there-is-no-network).

```bash
uv run flock eval hub pull acme/allenai/ai2_arc@210d026faf9955653af8916fad021475a3f00453 --to /shared/eval-hub
```

| Argument or option | Default | Description |
|---|---|---|
| `ORG/OWNER/NAME@COMMIT` | required | The organization that holds the copy, the Hugging Face repo and its pinned commit, as the task's definition names them. The copy is the version of `eval/OWNER/NAME` tagged with the commit. |
| `--to` | `$FLOCK_EVAL_HUB` | The staging folder. The copy is written to `<folder>/OWNER/NAME/COMMIT`. A copy already there with the same splits is kept, and one from an earlier version of the tag is replaced, so don't pull a newer version while a job reads that folder. Whoever can read the folder can read the copies in it, only you and the owners of the folders above a copy can change it, and a missing folder is made readable by you only. A folder others can write to needs the sticky bit, and so does every folder below it that others can write to; a folder below it that is a link is refused. Two pulls of the same dataset on one machine take turns. A shared filesystem may not pass that turn between machines (NCHC's `/work` doesn't), so pull from one machine. |

| Environment variable | Effect |
|---|---|
| `FLOCK_EVAL_HUB` | `flock eval run` reads every dataset from the copies staged in this folder, and fails before the model loads if one is missing instead of downloading it. |

## Tracker runs

`flock eval run --wandb-project` and `flock eval log` log one run with job type `eval`:

| Run field | Holds |
|---|---|
| config | The [provenance](results#provenance), plus `task_hashes` (each task's hash by name), `suite_members` (each suite's members by name) and `suite_hashes` (each suite's hash by name). |
| summary | `<task>/<metric>` for each metric, `<task>/docs_per_second` unless the clock measured no time, and `suites/<suite>` for each scored suite's score. |
| files | `results.json`, and `predictions/<task>.jsonl` for each task that has one, or `private/predictions/<task>.jsonl` for a task on a private dataset. A file older than schema version 9 sends only `results.json`: its predictions have no `correct`. |

`flock eval run` writes `results.json` whether or not it logs. It uploads files only when `--out` is set.

A run has its project's visibility, so anyone can read the predictions of a run in a public project. A task on a private dataset (`dataset.private` in [`results.json`](results#resultsjson)) therefore sends its predictions under `private/`, which Flock shows to your organization's members only, even after the project is made public ([Run files](../app/api/projects#run-files)). They go only to a project that is private, or that the run creates, which starts private. To check, the command reads the project from the server you signed in to with `flock login`, and the run goes there, to your default organization unless one is named. It refuses before creating the run if the project is public, if you aren't signed in to Flock, or if Flock refuses the query, and uploads nothing if the run ends up in another project, as a sweep agent can arrange. A live run keeps `WANDB_MODE`, but an offline run with private predictions is refused, since a later sync can send it to any project: log its folder afterwards with `flock eval log`. Log such runs to a private project, such as `model-evals` in your organization.

> [!WARNING]
> A Flock server older than this release serves `private/` files like any other run file. On such a server, the private project is the only protection: don't make it public, and don't move its runs into a public project.

`read_results`, `flock eval diff` and `flock eval items` read a private task's predictions from `private/predictions/<task>.jsonl` on a run, and from `predictions/` in a folder.

When the config's `model` is a version stored in Flock (`flock:org/path@{number}`, or `flock:project:path@{number}` for a project's model) and its `model_digest` is that version's digest, Flock lists the run among the version's eval runs, in the run's own organization. A project's model is found by the config's `model_org`, `model_project` and `model_path`. See [Score a model stored in Flock](run-evaluation#score-a-model-stored-in-flock). The run's page shows its scores in a **Results** tab, and two runs can be compared doc by doc in the dashboard as `flock eval diff` compares them (see [Eval results in the dashboard](../app/explore-runs#eval-results)).
