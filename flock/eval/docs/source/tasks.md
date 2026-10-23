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

Every task's requests are identical, string for string, to OLMES's for the same OLMo 3 Base Easy task (`<task>:rc::olmes:full`, and `mmlu_<subject>:rc::olmes`). HellaSwag, WinoGrande, SocialIQA, PIQA and MMLU have no usable id column, so a doc's id is its row's position in its split, as in OLMES (HellaSwag's `ind` repeats within a split). SocialIQA and PIQA read the Hub's parquet conversion of their loading scripts, which `datasets` no longer runs. A few HellaSwag choices start with two spaces, where OLMES's markup cleanup leaves one; we keep them for parity.

## Suites

A suite's score is the unweighted mean of its members' scores, as OLMES's `macro` aggregate. A member task gives the suite's `metric`, or its own primary metric if the suite sets none. A member suite counts as one member, whatever its size, and gives its own score, so a suite that sets `metric` holds only suites that set the same one. A scored suite's members are all accuracies or all `bpb`, since the two don't average. `base_easy` has no score of its own: it groups suites whose scores don't average, so that one name runs them all.

| Name | Members | Metric | OLMES suite |
|---|---|---|---|
| `mmlu` | the 57 `mmlu_<subject>` tasks | `acc_per_char` | `mmlu:rc::olmes` |
| `mmlu:bpb` | the 57 `mmlu_<subject>` tasks | `bpb` | `mmlu:rc:bpb::olmes` |
| `base_easy:qa_rc` | `arc_easy`, `arc_challenge`, `csqa`, `hellaswag`, `winogrande`, `socialiqa`, `piqa`, `mmlu` | each task's primary metric | `olmo3:base_easy:qa_rc` |
| `base_easy:qa_bpb` | the same tasks, and `mmlu:bpb` | `bpb` | `olmo3:base_easy:qa_bpb` |
| `base_easy` | `base_easy:qa_rc`, `base_easy:qa_bpb` | unscored | none |

The Base Easy suites hold only the tasks that exist so far, so their scores change as tasks are added. OLMES groups ARC-Easy and ARC-Challenge into one suite with no metric of its own; here they are two members of `base_easy:qa_rc`. See [Suites in results](results#suites) for when two suite scores compare.

## Classes

[[autodoc]] eval.tasks.MultipleChoiceTask

[[autodoc]] eval.tasks.Dataset

[[autodoc]] eval.tasks.Fewshot

[[autodoc]] eval.tasks.MCDoc

[[autodoc]] eval.tasks.Choice

[[autodoc]] eval.tasks.cloze

[[autodoc]] eval.tasks.MCRequests

[[autodoc]] eval.tasks.Suite
