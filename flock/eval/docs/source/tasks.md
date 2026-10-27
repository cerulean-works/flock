# Tasks

A task is a [`~eval.tasks.MultipleChoiceTask`] or a [`~eval.tasks.PerplexityTask`] registered by name in `TASKS`, and a suite is a [`~eval.tasks.Suite`] of tasks registered in `SUITES`. [Adding a task](adding-a-task) shows how to write one.

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
| `coqa` | [CoQA](https://huggingface.co/datasets/allenai/coqa-gen2mc), as multiple choice | `validation` | 7728 | none | `acc_per_char` |
| `drop` | [DROP](https://huggingface.co/datasets/allenai/drop-gen2mc), as multiple choice | `validation` | 8380 | 5, written by OLMES | `acc_per_char` |
| `jeopardy` | [Jeopardy](https://huggingface.co/datasets/allenai/jeopardy-gen2mc), as multiple choice | `test` | 2099 | 5, written by OLMES | `acc_per_char` |
| `naturalqs` | [Natural Questions](https://huggingface.co/datasets/allenai/nq-gen2mc), as multiple choice | `validation` | 3293 | 5, written by OLMES | `acc_per_char` |
| `squad` | [SQuAD](https://huggingface.co/datasets/allenai/squad-gen2mc), as multiple choice | `validation` | 10205 | 5, written by OLMES | `acc_per_char` |
| `sciq` | [SciQ](https://huggingface.co/datasets/allenai/sciq) | `validation` | 1000 | 5 from `train` | `acc_per_char` |
| `qasper_yesno` | [Qasper yes/no](https://huggingface.co/datasets/allenai/qasper-yesno) | `train` | 319 | 5 from `train`, the scored split | `acc_raw` |
| `sciriff_yesno` | [SciRIFF yes/no](https://huggingface.co/datasets/allenai/sciriff-yesno) | `train` | 1582 | 5 from `train`, the scored split | `acc_raw` |
| `basic_skills_<skill>` (6) | [Basic skills](https://huggingface.co/datasets/cerulean-works/basic-skills) | `validation` | 5967 in all | 5 of 6 from `validation`, never the doc itself | `acc_per_token` |
| `lab_bench_dbqa` | [LAB-Bench](https://huggingface.co/datasets/futurehouse/lab-bench) DbQA | `train` | 520 | 3 from `train`, the scored split | `acc_per_char` |
| `lab_bench_protocolqa` | [LAB-Bench](https://huggingface.co/datasets/futurehouse/lab-bench) ProtocolQA | `train` | 108 | 3 from `train`, the scored split | `acc_per_char` |
| `medmcqa` | [MedMCQA](https://huggingface.co/datasets/openlifescienceai/medmcqa) | `validation` | 4183 | 5 from `train` | `acc_per_char` |
| `medqa_en` | [MedQA](https://huggingface.co/datasets/davidheineman/medqa-en) (English) | `test` | 1273 | 5 from `train` | `acc_per_char`, no PMI |
| `lambada` | [LAMBADA](https://huggingface.co/datasets/EleutherAI/lambada_openai) | `test` | 5153 | none | `bpb` of the last word |
| `minerva_math_<subject>` | [MATH](https://huggingface.co/datasets/EleutherAI/hendrycks_math), one of 7 subjects | `test` | 5000 in all | 4, OLMES's fixed Minerva examples | `bpb` of the solution |
| `humaneval` | [HumanEval](https://huggingface.co/datasets/openai/openai_humaneval) | `test` | 164 | 3, from `test`, never the doc itself | `bpb` of the solution |
| `mbpp` | [MBPP](https://huggingface.co/datasets/google-research-datasets/mbpp) | `test` | 500 | 3, from `prompt` | `bpb` of the code |
| `mt_mbpp_<language>` | [Multilingual MBPP](https://huggingface.co/datasets/allenai/multilingual_mbpp), one of 17 languages | `test` | 500 each | 3, from `prompt` | `bpb` of the code |

Every QA task's requests are identical, string for string, to OLMES's for the same OLMo 3 Base Easy task (`<task>:rc::olmes:full`, and `mmlu_<subject>:rc::olmes`). Every task scores its whole split, as the `:full` variants do; OLMES's plain `::olmes` configs for HellaSwag, PIQA and SocialIQA sample 1000 docs. WinoGrande, SocialIQA, PIQA and MMLU have no id column, so a doc's id is its row's position in its split, as in OLMES. HellaSwag's rows are numbered the same way, where OLMES keeps its `ind` column, because `ind` repeats within a split (9609 values over 10042 validation rows). SocialIQA and PIQA read the Hub's parquet conversion of their loading scripts, which `datasets` no longer runs. A few HellaSwag choices start with two spaces: removing a leading `[step]` tag leaves a space before the continuation's own, in OLMES too, so we keep it for parity.

The math and code tasks score only the gold text, after the problem, so they report `bpb` alone. The subjects are `algebra` (1187 docs), `counting_and_probability` (474), `geometry` (479), `intermediate_algebra` (903), `number_theory` (540), `prealgebra` (871) and `precalculus` (546). The languages are `bash`, `c`, `cpp`, `csharp`, `go`, `haskell`, `java`, `javascript`, `matlab`, `php`, `python`, `r`, `ruby`, `rust`, `scala`, `swift` and `typescript`. Each task renders its prompt and gold text exactly as OLMES does, quirks included: HumanEval's examples put a space before the already indented solution, which the scored solution doesn't get.

The other QA tasks' requests are identical to OLMES's too (`<task>:rc::gen2mc:xlarge`, `sciq:rc::olmo3`, `<task>:rc::olmes`, `lab_bench_<subset>`, `<task>:rc::none` and `lambada:bpb`). Where OLMES samples few-shot examples, the sample was drawn once with OLMES's code and is written in the task as ids, so every doc of a task sees the same examples, basic skills apart: its docs see 5 of the 6 drawn, never themselves. SciQ, MedQA and LAMBADA have no id column, so a doc's id is its row's position in its split. Basic skills reads `cerulean-works/basic-skills`, a parquet mirror of `allenai/basic-skills`, whose loading script `datasets` no longer runs.

A few behaviours follow OLMES for parity and are worth knowing:

- **The five gen2mc tasks** are OLMo 3's multiple-choice versions of short-form generative QA datasets. CoQA is zero-shot. The other four's few-shot examples exist only in OLMES's code, so they are written out as rows in `flock.eval.tasks.shots`.
- **Qasper, SciRIFF and LAB-Bench show some docs their own answer.** OLMES draws their examples from the split it scores and keeps the doc itself, so 3 to 5 docs per task are shown their own question and answer.
- **Basic skills shuffles each doc's choices** with a generator seeded by the doc's id, as OLMES does, so the order is fixed per doc. Its primary metric is `acc_per_token`; OLMES's own `acc_per_token` equals its `acc_raw`, so compare with OLMES's numbers on `acc_raw`.
- **LAMBADA reports only `bpb` for now.** Its Base Easy accuracy is greedy match of the last word, which the scorer doesn't compute yet, so it is only in `base_easy:qa_bpb`.

### Perplexity tasks

A perplexity task scores each doc's text whole and reports `bits_per_byte` over all of its docs (see [Metrics](metrics#perplexity)). Each [Paloma](https://arxiv.org/abs/2312.10523) domain is one task, scored on its whole `test` split in windows of 2048 tokens.

| Name | Source | Split | Docs | Window | Primary metric |
|---|---|---|---|---|---|
| `paloma_wikitext_103` | [Paloma](https://huggingface.co/datasets/allenai/paloma)'s `wikitext_103` | `test`, all of it | 61 | 2048 | `bits_per_byte` |

Paloma is gated: accept its terms on the dataset page before the first run.

## Suites

A suite's score is the unweighted mean of its members' scores, as OLMES's `macro` aggregate. A member task gives the suite's `metric`, or its own primary metric if the suite sets none. A member suite counts as one member, whatever its size, and gives its own score, so a suite that sets `metric` holds only suites that set the same one. A scored suite's members share one scale: all accuracies, or all one other metric such as `bpb` or perplexity `bits_per_byte`, since those don't average. `base_easy` has no score of its own: it groups suites whose scores don't average, so that one name runs them all.

| Name | Members | Metric | OLMES suite |
|---|---|---|---|
| `mmlu` | the 57 `mmlu_<subject>` tasks | each task's primary metric (`acc_per_char`) | `mmlu:rc::olmes` |
| `mmlu:bpb` | the 57 `mmlu_<subject>` tasks | `bpb` | `mmlu:rc:bpb::olmes` |
| `basic_skills` | the 6 `basic_skills_<skill>` tasks | each task's primary metric (`acc_per_token`) | `basic_skills:rc::olmes` |
| `basic_skills:bpb` | the 6 `basic_skills_<skill>` tasks | `bpb` | `basic_skills:rc:bpb::olmes` |
| `base_easy:qa_rc` | `arc_easy`, `arc_challenge`, `csqa`, `hellaswag`, `winogrande`, `socialiqa`, `piqa`, `sciq`, `qasper_yesno`, `sciriff_yesno`, `lab_bench_dbqa`, `lab_bench_protocolqa`, `medmcqa`, `medqa_en`, `coqa`, `drop`, `jeopardy`, `naturalqs`, `squad`, `mmlu`, `basic_skills` | each task's primary metric | `olmo3:base_easy:qa_rc` |
| `base_easy:qa_bpb` | the same tasks, `mmlu:bpb`, `basic_skills:bpb` and `lambada` | `bpb` | `olmo3:base_easy:qa_bpb` |
| `base_easy:math_bpb` | the 7 `minerva_math_<subject>` tasks | `bpb` | `olmo3:base_easy:math_bpb` |
| `mt_mbpp` | the 17 `mt_mbpp_<language>` tasks | `bpb` | `mt_mbpp_v2fix` |
| `base_easy:code_bpb` | `humaneval`, `mbpp`, `mt_mbpp` | `bpb` | `olmo3:base_easy:code_bpb` |
| `base_easy` | `base_easy:qa_rc`, `base_easy:qa_bpb`, `base_easy:math_bpb`, `base_easy:code_bpb` | unscored | none |
| `paloma:wikitext_103` | `paloma_wikitext_103` | `bits_per_byte` | none; OLMES's task `paloma_wikitext_103::paloma` |
| `paloma` | `paloma:wikitext_103` | unscored | none |

A Paloma source is a suite of its domains, scored as their unweighted mean, as the paper averages a source's domains. OLMES instead reports a source's bits per byte over all of its docs together, so a source with several domains differs from OLMES's number. `paloma` has no score, since Paloma defines none over all sources.

The Base Easy suites hold only the tasks that exist so far, so their scores change as tasks are added. OLMES groups ARC-Easy and ARC-Challenge into one suite with no metric of its own; here they are two members of `base_easy:qa_rc`. `basic_skills`, and so `base_easy:qa_rc`, averages our `acc_per_token`, which divides by the real token count. OLMES's equals its `acc_raw`, so these two scores differ from OLMES's by that member. See [Suites in results](results#suites) for when two suite scores compare.

## Classes

[[autodoc]] eval.tasks.MultipleChoiceTask

[[autodoc]] eval.tasks.GoldOnlyTask

[[autodoc]] eval.tasks.Dataset

[[autodoc]] eval.tasks.Fewshot

[[autodoc]] eval.tasks.MCDoc

[[autodoc]] eval.tasks.Choice

[[autodoc]] eval.tasks.cloze

[[autodoc]] eval.tasks.MCRequests

[[autodoc]] eval.tasks.PerplexityTask

[[autodoc]] eval.tasks.TextDoc

[[autodoc]] eval.tasks.Suite
