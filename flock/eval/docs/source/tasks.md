# Tasks

A task is a [`~eval.tasks.MultipleChoiceTask`] registered by name in `TASKS`, and a suite is a [`~eval.tasks.Suite`] of tasks registered in `SUITES`. [Adding a task](adding-a-task) shows how to write one.

## Available tasks

| Name | Benchmark | Split | Docs | Few-shot | Primary metric |
|---|---|---|---|---|---|
| `arc_easy` | [ARC-Easy](https://huggingface.co/datasets/allenai/ai2_arc) | `test`, all of it, as OLMo 3's Base Easy scores it | 2376 | 5, the curated OLMES examples | `acc_per_char` |
| `arc_challenge` | [ARC-Challenge](https://huggingface.co/datasets/allenai/ai2_arc) | `test` | 1172 | 5, the curated OLMES examples | `acc_uncond` |
| `csqa` | [CommonsenseQA](https://huggingface.co/datasets/tau/commonsense_qa) | `validation` | 1221 | 5, the curated OLMES examples | `acc_uncond` |
| `hellaswag` | [HellaSwag](https://huggingface.co/datasets/allenai/hellaswag) | `validation` | 10042 | 5, the curated OLMES examples | `acc_per_char` |
| `winogrande` | [WinoGrande](https://huggingface.co/datasets/allenai/winogrande) (xl) | `validation` | 1267 | 5, the curated OLMES examples | `acc_raw`, no PMI |
| `socialiqa` | [SocialIQA](https://huggingface.co/datasets/allenai/social_i_qa) | `validation` | 1954 | 5, the curated OLMES examples | `acc_per_char` |
| `piqa` | [PIQA](https://huggingface.co/datasets/ybisk/piqa) | `validation` | 1838 | 5, the curated OLMES examples | `acc_per_char` |
| `mmlu_<subject>` (57) | [MMLU](https://huggingface.co/datasets/cais/mmlu) | `test` | 14042 in all | the subject's first 5 `dev` rows | `acc_per_char` |
| `minerva_math_<subject>` | [MATH](https://huggingface.co/datasets/EleutherAI/hendrycks_math), one of 7 subjects | `test` | 5000 in all | 4, OLMES's fixed Minerva examples | `bpb` of the solution |
| `humaneval` | [HumanEval](https://huggingface.co/datasets/openai/openai_humaneval) | `test` | 164 | 3, from `test`, never the doc itself | `bpb` of the solution |
| `mbpp` | [MBPP](https://huggingface.co/datasets/google-research-datasets/mbpp) | `test` | 500 | 3, from `prompt` | `bpb` of the code |
| `mt_mbpp_<language>` | [Multilingual MBPP](https://huggingface.co/datasets/allenai/multilingual_mbpp), one of 17 languages | `test` | 500 each | 3, from `prompt` | `bpb` of the code |

Every QA task's requests are identical, string for string, to OLMES's for the same OLMo 3 Base Easy task (`<task>:rc::olmes:full`, and `mmlu_<subject>:rc::olmes`). Every task scores its whole split, as the `:full` variants do; OLMES's plain `::olmes` configs for HellaSwag, PIQA and SocialIQA sample 1000 docs. WinoGrande, SocialIQA, PIQA and MMLU have no id column, so a doc's id is its row's position in its split, as in OLMES. HellaSwag's rows are numbered the same way, where OLMES keeps its `ind` column, because `ind` repeats within a split (9609 values over 10042 validation rows). SocialIQA and PIQA read the Hub's parquet conversion of their loading scripts, which `datasets` no longer runs. A few HellaSwag choices start with two spaces: removing a leading `[step]` tag leaves a space before the continuation's own, in OLMES too, so we keep it for parity.

The math and code tasks score only the gold text, after the problem, so they report `bpb` alone. The subjects are `algebra` (1187 docs), `counting_and_probability` (474), `geometry` (479), `intermediate_algebra` (903), `number_theory` (540), `prealgebra` (871) and `precalculus` (546). The languages are `bash`, `c`, `cpp`, `csharp`, `go`, `haskell`, `java`, `javascript`, `matlab`, `php`, `python`, `r`, `ruby`, `rust`, `scala`, `swift` and `typescript`. Each task renders its prompt and gold text exactly as OLMES does, quirks included: HumanEval's examples put a space before the already indented solution, which the scored solution doesn't get.

## Suites

A suite's score is the unweighted mean of its members' scores, as OLMES's `macro` aggregate. A member task gives the suite's `metric`, or its own primary metric if the suite sets none. A member suite counts as one member, whatever its size, and gives its own score, so a suite that sets `metric` holds only suites that set the same one. A scored suite's members share one scale: all accuracies, or all one other metric such as `bpb`, since those don't average. `base_easy` has no score of its own: it groups suites whose scores don't average, so that one name runs them all.

| Name | Members | Metric | OLMES suite |
|---|---|---|---|
| `mmlu` | the 57 `mmlu_<subject>` tasks | each task's primary metric (`acc_per_char`) | `mmlu:rc::olmes` |
| `mmlu:bpb` | the 57 `mmlu_<subject>` tasks | `bpb` | `mmlu:rc:bpb::olmes` |
| `base_easy:qa_rc` | `arc_easy`, `arc_challenge`, `csqa`, `hellaswag`, `winogrande`, `socialiqa`, `piqa`, `mmlu` | each task's primary metric | `olmo3:base_easy:qa_rc` |
| `base_easy:qa_bpb` | the same tasks, and `mmlu:bpb` | `bpb` | `olmo3:base_easy:qa_bpb` |
| `base_easy:math_bpb` | the 7 `minerva_math_<subject>` tasks | `bpb` | `olmo3:base_easy:math_bpb` |
| `mt_mbpp` | the 17 `mt_mbpp_<language>` tasks | `bpb` | `mt_mbpp_v2fix` |
| `base_easy:code_bpb` | `humaneval`, `mbpp`, `mt_mbpp` | `bpb` | `olmo3:base_easy:code_bpb` |
| `base_easy` | `base_easy:qa_rc`, `base_easy:qa_bpb`, `base_easy:math_bpb`, `base_easy:code_bpb` | unscored | none |

The Base Easy suites hold only the tasks that exist so far, so their scores change as tasks are added. OLMES groups ARC-Easy and ARC-Challenge into one suite with no metric of its own; here they are two members of `base_easy:qa_rc`. See [Suites in results](results#suites) for when two suite scores compare.

## Classes

[[autodoc]] eval.tasks.MultipleChoiceTask

[[autodoc]] eval.tasks.GoldOnlyTask

[[autodoc]] eval.tasks.Dataset

[[autodoc]] eval.tasks.Fewshot

[[autodoc]] eval.tasks.MCDoc

[[autodoc]] eval.tasks.Choice

[[autodoc]] eval.tasks.cloze

[[autodoc]] eval.tasks.MCRequests

[[autodoc]] eval.tasks.Suite
