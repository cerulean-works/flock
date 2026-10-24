# Tasks

A task is a [`~eval.tasks.MultipleChoiceTask`] registered by name in `TASKS`, and a suite is a [`~eval.tasks.Suite`] of tasks registered in `SUITES`. [Adding a task](adding-a-task) shows how to write one.

## Available tasks

| Name | Benchmark | Split | Docs | Few-shot | Primary metric |
|---|---|---|---|---|---|
| `arc_easy` | [ARC-Easy](https://huggingface.co/datasets/allenai/ai2_arc) | `test`, all of it, as OLMo 3's Base Easy scores it | 2376 | 5, the curated OLMES examples | `acc_per_char` |
| `minerva_math_<subject>` | [MATH](https://huggingface.co/datasets/EleutherAI/hendrycks_math), one of 7 subjects | `test` | 5000 in all | 4, OLMES's fixed Minerva examples | `bpb` of the solution |
| `humaneval` | [HumanEval](https://huggingface.co/datasets/openai/openai_humaneval) | `test` | 164 | 3, from `test`, never the doc itself | `bpb` of the solution |
| `mbpp` | [MBPP](https://huggingface.co/datasets/google-research-datasets/mbpp) | `test` | 500 | 3, from `prompt` | `bpb` of the code |
| `mt_mbpp_<language>` | [Multilingual MBPP](https://huggingface.co/datasets/allenai/multilingual_mbpp), one of 17 languages | `test` | 500 each | 3, from `prompt` | `bpb` of the code |

The math and code tasks score only the gold text, after the problem, so they report `bpb` alone. The subjects are `algebra` (1187 docs), `counting_and_probability` (474), `geometry` (479), `intermediate_algebra` (903), `number_theory` (540), `prealgebra` (871) and `precalculus` (546). The languages are `bash`, `c`, `cpp`, `csharp`, `go`, `haskell`, `java`, `javascript`, `matlab`, `php`, `python`, `r`, `ruby`, `rust`, `scala`, `swift` and `typescript`. Each task renders its prompt and gold text exactly as OLMES does, quirks included: HumanEval's examples put a space before the already indented solution, which the scored solution doesn't get.

## Suites

A suite's score is the unweighted mean of its members' scores, as OLMES's `macro` aggregate. A member task gives the suite's `metric`, or its own primary metric if the suite sets none. A member suite counts as one member, whatever its size. A scored suite's members are all accuracies or all `bpb`, since the two don't average. `base_easy` has no score of its own: it groups suites whose scores don't average, so that one name runs them all.

| Name | Members | Metric | OLMES suite |
|---|---|---|---|
| `base_easy:qa_rc` | `arc_easy` | each task's primary metric | `olmo3:base_easy:qa_rc` |
| `base_easy:qa_bpb` | `arc_easy` | `bpb` | `olmo3:base_easy:qa_bpb` |
| `base_easy:math_bpb` | the 7 `minerva_math_<subject>` tasks | `bpb` | `olmo3:base_easy:math_bpb` |
| `mt_mbpp` | the 17 `mt_mbpp_<language>` tasks | `bpb` | `mt_mbpp_v2fix` |
| `base_easy:code_bpb` | `humaneval`, `mbpp`, `mt_mbpp` | `bpb` | `olmo3:base_easy:code_bpb` |
| `base_easy` | `base_easy:qa_rc`, `base_easy:qa_bpb`, `base_easy:math_bpb`, `base_easy:code_bpb` | unscored | none |

The Base Easy suites hold only the tasks that exist so far, so their scores change as tasks are added. See [Suites in results](results#suites) for when two suite scores compare.

## Classes

[[autodoc]] eval.tasks.MultipleChoiceTask

[[autodoc]] eval.tasks.Dataset

[[autodoc]] eval.tasks.Fewshot

[[autodoc]] eval.tasks.MCDoc

[[autodoc]] eval.tasks.Choice

[[autodoc]] eval.tasks.cloze

[[autodoc]] eval.tasks.MCRequests

[[autodoc]] eval.tasks.Suite
