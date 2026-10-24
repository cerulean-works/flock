# Tasks

A task is a [`~eval.tasks.MultipleChoiceTask`] registered by name in `TASKS`, and a suite is a [`~eval.tasks.Suite`] of tasks registered in `SUITES`. [Adding a task](adding-a-task) shows how to write one.

## Available tasks

| Name | Benchmark | Split | Docs | Few-shot | Primary metric |
|---|---|---|---|---|---|
| `arc_easy` | [ARC-Easy](https://huggingface.co/datasets/allenai/ai2_arc) | `test`, all of it, as OLMo 3's Base Easy scores it | 2376 | 5, the curated OLMES examples | `acc_per_char` |
| `coqa` | [CoQA](https://huggingface.co/datasets/allenai/coqa-gen2mc), as multiple choice | `validation` | 7728 | none | `acc_per_char` |
| `drop` | [DROP](https://huggingface.co/datasets/allenai/drop-gen2mc), as multiple choice | `validation` | 8380 | 5, written by OLMES | `acc_per_char` |
| `jeopardy` | [Jeopardy](https://huggingface.co/datasets/allenai/jeopardy-gen2mc), as multiple choice | `test` | 2099 | 5, written by OLMES | `acc_per_char` |
| `naturalqs` | [Natural Questions](https://huggingface.co/datasets/allenai/nq-gen2mc), as multiple choice | `validation` | 3293 | 5, written by OLMES | `acc_per_char` |
| `squad` | [SQuAD](https://huggingface.co/datasets/allenai/squad-gen2mc), as multiple choice | `validation` | 10205 | 5, written by OLMES | `acc_per_char` |
| `sciq` | [SciQ](https://huggingface.co/datasets/allenai/sciq) | `validation` | 1000 | 5 from `train` | `acc_per_char` |
| `qasper_yesno` | [Qasper yes/no](https://huggingface.co/datasets/allenai/qasper-yesno) | `train` | 319 | 5 from `train`, the scored split | `acc_raw` |
| `sciriff_yesno` | [SciRIFF yes/no](https://huggingface.co/datasets/allenai/sciriff-yesno) | `train` | 1582 | 5 from `train`, the scored split | `acc_raw` |
| `basic_skills_<skill>` (6) | [Basic skills](https://huggingface.co/datasets/allenai/basic-skills) | `validation` | 5967 in all | 5 of 6 from `validation`, never the doc itself | `acc_per_token` |
| `lab_bench_dbqa` | [LAB-Bench](https://huggingface.co/datasets/futurehouse/lab-bench) DbQA | `train` | 520 | 3 from `train`, the scored split | `acc_per_char` |
| `lab_bench_protocolqa` | [LAB-Bench](https://huggingface.co/datasets/futurehouse/lab-bench) ProtocolQA | `train` | 108 | 3 from `train`, the scored split | `acc_per_char` |
| `medmcqa` | [MedMCQA](https://huggingface.co/datasets/openlifescienceai/medmcqa) | `validation` | 4183 | 5 from `train` | `acc_per_char` |
| `medqa_en` | [MedQA](https://huggingface.co/datasets/davidheineman/medqa-en) (English) | `test` | 1273 | 5 from `train` | `acc_per_char`, no PMI |
| `lambada` | [LAMBADA](https://huggingface.co/datasets/EleutherAI/lambada_openai) | `test` | 5153 | none | `bpb` of the last word |

Every task's requests are identical, string for string, to OLMES's for the same OLMo 3 Base Easy task (`<task>:rc::gen2mc:xlarge`, `sciq:rc::olmo3`, `<task>:rc::olmes`, `lab_bench_<subset>`, `<task>:rc::none` and `lambada:bpb`). Where OLMES samples few-shot examples, the sample was drawn once with OLMES's code and is written in the task as ids, so every doc of a task sees the same examples. SciQ, MedQA and LAMBADA have no id column, so a doc's id is its row's position in its split.

A few behaviours follow OLMES for parity and are worth knowing:

- **The five gen2mc tasks** are OLMo 3's multiple-choice versions of short-form generative QA datasets. CoQA is zero-shot. The other four's few-shot examples exist only in OLMES's code, so they are written out in `flock/eval/tasks/gen2mc.py` as rows.
- **Qasper, SciRIFF and LAB-Bench show some docs their own answer.** OLMES draws their examples from the split it scores and keeps the doc itself, so 3 to 5 docs per task are shown their own question and answer.
- **Basic skills shuffles each doc's choices** with a generator seeded by the doc's id, as OLMES does, so the order is fixed per doc. Its primary metric is `acc_per_token`; OLMES's own `acc_per_token` equals its `acc_raw`, so compare with OLMES's numbers on `acc_raw`.
- **LAMBADA reports only `bpb` for now.** Its Base Easy accuracy is greedy match of the last word, which the scorer doesn't compute yet, so it is only in `base_easy:qa_bpb`.

## Suites

A suite's score is the unweighted mean of its members' scores, as OLMES's `macro` aggregate. A member task gives the suite's `metric`, or its own primary metric if the suite sets none. A member suite counts as one member, whatever its size, and gives its own score, so a suite that sets `metric` holds only suites that set the same one. A scored suite's members are all accuracies or all `bpb`, since the two don't average. `base_easy` has no score of its own: it groups suites whose scores don't average, so that one name runs them all.

| Name | Members | Metric | OLMES suite |
|---|---|---|---|
| `basic_skills` | the 6 `basic_skills_<skill>` tasks | each task's primary metric (`acc_per_token`) | `basic_skills:rc::olmes` |
| `basic_skills:bpb` | the 6 `basic_skills_<skill>` tasks | `bpb` | `basic_skills:rc:bpb::olmes` |
| `base_easy:qa_rc` | `arc_easy`, `coqa`, `drop`, `jeopardy`, `naturalqs`, `squad`, `sciq`, `qasper_yesno`, `sciriff_yesno`, `lab_bench_dbqa`, `lab_bench_protocolqa`, `medmcqa`, `medqa_en`, `basic_skills` | each task's primary metric | `olmo3:base_easy:qa_rc` |
| `base_easy:qa_bpb` | the same tasks, `basic_skills:bpb` and `lambada` | `bpb` | `olmo3:base_easy:qa_bpb` |
| `base_easy` | `base_easy:qa_rc`, `base_easy:qa_bpb` | unscored | none |

The Base Easy suites hold only the tasks that exist so far, so their scores change as tasks are added. `basic_skills`, and so `base_easy:qa_rc`, averages our `acc_per_token`, which divides by the real token count. OLMES's equals its `acc_raw`, so these two scores differ from OLMES's by that member. See [Suites in results](results#suites) for when two suite scores compare.

## Classes

[[autodoc]] eval.tasks.MultipleChoiceTask

[[autodoc]] eval.tasks.Dataset

[[autodoc]] eval.tasks.Fewshot

[[autodoc]] eval.tasks.MCDoc

[[autodoc]] eval.tasks.Choice

[[autodoc]] eval.tasks.cloze

[[autodoc]] eval.tasks.MCRequests

[[autodoc]] eval.tasks.Suite
