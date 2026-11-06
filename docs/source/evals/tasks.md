# Tasks

A task is a [`~eval.tasks.MultipleChoiceTask`] or a [`~eval.tasks.PerplexityTask`] registered by name in `TASKS`, and a suite is a [`~eval.tasks.Suite`] of tasks registered in `SUITES`. [Adding a task](add-task) shows how to write one.

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
| `lambada` | [LAMBADA](https://huggingface.co/datasets/EleutherAI/lambada_openai) | `test` | 5153 | none | `acc_greedy`, greedy match of the last word |
| `minerva_math_<subject>` | [MATH](https://huggingface.co/datasets/EleutherAI/hendrycks_math), one of 7 subjects | `test` | 5000 in all | 4, OLMES's fixed Minerva examples | `bpb` of the solution |
| `humaneval` | [HumanEval](https://huggingface.co/datasets/openai/openai_humaneval) | `test` | 164 | 3, from `test`, never the doc itself | `bpb` of the solution |
| `mbpp` | [MBPP](https://huggingface.co/datasets/google-research-datasets/mbpp) | `test` | 500 | 3, from `prompt` | `bpb` of the code |
| `mt_mbpp_<language>` | [Multilingual MBPP](https://huggingface.co/datasets/allenai/multilingual_mbpp), one of 17 languages | `test` | 500 each | 3, from `prompt` | `bpb` of the code |
| `tmmluplus_<subject>` (66) | [TMMLU+](https://huggingface.co/datasets/ikala/tmmluplus) v1.1, in Traditional Chinese | `test` | 19646 in all, 85 to 989 per subject | the subject's dev rows in file order: 5, or 3 or 4 for twelve subjects, and one fewer for the 14 test docs that repeat a dev row's question and answer | `acc_per_char` |
| `tw_mock_exams_<subject>` (10) | Taiwanese senior-high mock exams for 學測 and 指考 (internal, private dataset), in Traditional Chinese, and English for 英文. The set is internal because Taiwan's Copyright Act art. 9 exempts exams held under law (official and school papers) but not publisher practice tests, and quoted passages keep their own copyright. | `test`, single-answer items answerable from the text | 18912 in all, 571 to 3713 per subject | the subject's 5 `dev` rows, its shortest self-contained items | `acc_per_char` |
| `drcd_bpb` | [DRCD](https://huggingface.co/datasets/cerulean-works/drcd), in Traditional Chinese | `test` | 3493 | 1 from `validation`, `1172-3-2` | `bpb` of the first gold answer |

Every English QA task's requests are identical, string for string, to OLMES's for the same OLMo 3 Base Easy task (`<task>:rc::olmes:full`, and `mmlu_<subject>:rc::olmes`). Every task scores its whole split, as the `:full` variants do; OLMES's plain `::olmes` configs for HellaSwag, PIQA and SocialIQA sample 1000 docs. WinoGrande, SocialIQA, PIQA and MMLU have no id column, so a doc's id is its row's position in its split, as in OLMES. HellaSwag's rows are numbered the same way, where OLMES keeps its `ind` column, because `ind` repeats within a split (9609 values over 10042 validation rows). SocialIQA and PIQA read the Hub's parquet conversion of their loading scripts, which `datasets` no longer runs. A few HellaSwag choices start with two spaces: removing a leading `[step]` tag leaves a space before the continuation's own, in OLMES too, so we keep it for parity.

The math and code tasks score only the gold text, after the problem, so they report `bpb` alone. The subjects are `algebra` (1187 docs), `counting_and_probability` (474), `geometry` (479), `intermediate_algebra` (903), `number_theory` (540), `prealgebra` (871) and `precalculus` (546). The languages are `bash`, `c`, `cpp`, `csharp`, `go`, `haskell`, `java`, `javascript`, `matlab`, `php`, `python`, `r`, `ruby`, `rust`, `scala`, `swift` and `typescript`. Each task renders its prompt and gold text exactly as OLMES does, quirks included: HumanEval's examples put a space before the already indented solution, which the scored solution doesn't get.

The other QA tasks' requests are identical to OLMES's too (`<task>:rc::gen2mc:xlarge`, `sciq:rc::olmo3`, `<task>:rc::olmes`, `lab_bench_<subset>`, `<task>:rc::none`, `lambada` and `lambada:bpb`). Where OLMES samples few-shot examples, the sample was drawn once with OLMES's code and is written in the task as ids, so every doc of a task sees the same examples, basic skills apart: its docs see 5 of the 6 drawn, never themselves. SciQ, MedQA and LAMBADA have no id column, so a doc's id is its row's position in its split. Basic skills reads `cerulean-works/basic-skills`, a parquet mirror of `allenai/basic-skills`, whose loading script `datasets` no longer runs.

A few behaviours follow OLMES for parity and are worth knowing:

- **The five gen2mc tasks** are OLMo 3's multiple-choice versions of short-form generative QA datasets. CoQA is zero-shot. The other four's few-shot examples exist only in OLMES's code, so they are written out as rows in `flock.eval.tasks.shots`.
- **Qasper, SciRIFF and LAB-Bench show some docs their own answer.** OLMES draws their examples from the split it scores and keeps the doc itself, so 3 to 5 docs per task are shown their own question and answer.
- **Basic skills shuffles each doc's choices** with a generator seeded by the doc's id, as OLMES does, so the order is fixed per doc. Its primary metric is `acc_per_token`; OLMES's own `acc_per_token` equals its `acc_raw`, so compare with OLMES's numbers on `acc_raw`.
- **LAMBADA is one task for two OLMES tasks.** OLMES's `lambada` reports greedy match and `lambada:bpb` reports `bpb`, on the same requests. Our `lambada` reports both: `acc_greedy` is 1 when every token of the last word is the model's most likely next token. `base_easy:qa_rc` reads `acc_greedy` and `base_easy:qa_bpb` reads `bpb`.

`drcd_bpb` scores Traditional Chinese reading comprehension as OLMo 3 scores SQuAD and DROP: the bits per byte of the gold answer after the passage and the question. It reads `cerulean-works/drcd`, a parquet mirror of DRCD's GitHub release in SQuAD's schema. A request is `文章：<passage>\n問題：<question>\n答案：` and the first of the two annotated answers, with no space before it, after one example from `validation`. There is no title line, since many DRCD titles are Wikipedia page names in Simplified script. OLMES has no DRCD task, so the reference its requests and float32 scores match is lm-evaluation-harness v0.4.3 rendering this format, as for TMMLU+. OLMo 3 shows SQuAD five examples; DRCD shows one, so that every request fits in 2,048 tokens under OLMo 2's and Qwen 2.5's tokenizers.

### Perplexity tasks

A perplexity task scores each doc's text whole and reports `bits_per_byte`, `loss_per_token` and `perplexity` over all of its docs (see [Metrics](metrics#perplexity)). Each [Paloma](https://arxiv.org/abs/2312.10523) domain is one task, scored on its whole `test` split in windows of 2048 tokens.

The first version has 9 of [Paloma](https://huggingface.co/datasets/allenai/paloma)'s 16 sources: those licensed under AI2's ImpACT Low Risk license or CC BY-SA, without the fringe sources. A source with one domain has one task, `paloma_<source>`. A source with several has one task per domain, `paloma_<source>__<domain>`, and `flock/eval/tasks/paloma.py` lists the domains.

| Source | Tasks | Domains | Docs | Licence |
|---|---|---|---|---|
| `c4_en` | `paloma_c4_en` | 1 | 2,124 | ImpACT LR |
| `mc4` | `paloma_mc4` | 1 | 1,202 | ImpACT LR |
| `wikitext_103` | `paloma_wikitext_103` | 1 | 61 | CC BY-SA |
| `ptb` | `paloma_ptb` | 1 | 1 | ImpACT LR |
| `falcon-refinedweb` | `paloma_falcon-refinedweb` | 1 | 1,711 | ImpACT LR |
| `dolma-v1_5` | `paloma_dolma-v1_5__<domain>` | 5 | 4,481 | ImpACT LR |
| `c4_100_domains` | `paloma_c4_100_domains__<domain>` | 99 | 14,131 | ImpACT LR |
| `dolma_100_subreddits` | `paloma_dolma_100_subreddits__<domain>` | 100 | 46,203 | ImpACT LR |
| `dolma_100_programing_languages` | `paloma_dolma_100_programing_languages__<domain>` | 100 | 7,832 | ImpACT LR |

Every task scores its domain's whole `test` split in windows of 2048 tokens, with `bits_per_byte` as its primary metric. `ptb` is one document of 98,844 OLMo 2 tokens. Paloma is gated: accept its terms on the dataset page before the first run.

`fineweb_edu` scores 5,000 documents of [FineWeb-Edu](https://huggingface.co/datasets/HuggingFaceFW/fineweb-edu), drawn with a fixed seed from one file of its newest dump, in windows of 8192 tokens, with `perplexity` as its primary metric. It reads the public dataset `cerulean-works/fineweb-edu-dev` and belongs to no suite. A model needs a context of at least 8192 tokens to run it. [Scoring the FineWeb-Edu dev set](fineweb-edu) defines the number and lists the ids training data must drop.

The `fineweb` suite scores held-out web text in three languages, all in windows of 2048 tokens, which every model the project compares supports. `fineweb_en` reads the same 5,000 FineWeb-Edu documents as `fineweb_edu`. `fineweb_zh_hant` (3,383 documents) and `fineweb_zh_hans` (5,000) read FineWeb-2's held-out Chinese, split by script, from the private dataset `cerulean-works/fineweb-2-zh-dev`. [Scoring held-out web text in three languages](fineweb) defines each member and how to read it.

`tw_text` scores Traditional Chinese text written in Taiwan and published from 2026-04-01 on, in Paloma's shape: one task per domain of each source, `tw_text_<source>__<domain>`, or `tw_text_<source>` for a source with one domain, each over its domain's rows of the `test` split in windows of 2048 tokens with `bits_per_byte` as its primary metric. Its 11 sources are `judicial`, `ly`, `gov_press`, `cna`, `pts`, `ltn`, `cnyes`, `twreporter`, `ptt`, `zhwiki` and `theses`, and `flock/eval/tasks/tw_text.py` lists their domains. `theses` is not held out in time: a thesis carries only its academic year (115, dated from 2026-08-01), so its text may predate the window. `judicial` covers 2026-04-01 to 2026-08-31 only. The dataset, `cerulean-works/tw-text-v0`, is private. [Scoring Taiwan Traditional Chinese text](tw-text) describes the set and how to get access.

## Suites

A suite's score is the unweighted mean of its members' scores, as OLMES's `macro` aggregate. A member task gives the suite's `metric`, or its own primary metric if the suite sets none. A member suite counts as one member, whatever its size, and gives its own score, so a suite that sets `metric` holds only suites that set the same one. A scored suite's members share one scale: all accuracies, or all one other metric such as `bpb` or perplexity `bits_per_byte`, since those don't average. `base_easy` has no score of its own: it groups suites whose scores don't average, so that one name runs them all.

| Name | Members | Metric | OLMES suite |
|---|---|---|---|
| `mmlu` | the 57 `mmlu_<subject>` tasks | each task's primary metric (`acc_per_char`) | `mmlu:rc::olmes` |
| `mmlu:bpb` | the 57 `mmlu_<subject>` tasks | `bpb` | `mmlu:rc:bpb::olmes` |
| `basic_skills` | the 6 `basic_skills_<skill>` tasks | each task's primary metric (`acc_per_token`) | `basic_skills:rc::olmes` |
| `basic_skills:bpb` | the 6 `basic_skills_<skill>` tasks | `bpb` | `basic_skills:rc:bpb::olmes` |
| `base_easy:qa_rc` | `arc_easy`, `arc_challenge`, `csqa`, `hellaswag`, `winogrande`, `socialiqa`, `piqa`, `sciq`, `qasper_yesno`, `sciriff_yesno`, `lab_bench_dbqa`, `lab_bench_protocolqa`, `medmcqa`, `medqa_en`, `coqa`, `drop`, `jeopardy`, `naturalqs`, `squad`, `mmlu`, `basic_skills`, `lambada` | each task's primary metric | `olmo3:base_easy:qa_rc` |
| `base_easy:qa_bpb` | the same tasks, with `mmlu:bpb` and `basic_skills:bpb` in place of `mmlu` and `basic_skills` | `bpb` | `olmo3:base_easy:qa_bpb` |
| `base_easy:math_bpb` | the 7 `minerva_math_<subject>` tasks | `bpb` | `olmo3:base_easy:math_bpb` |
| `mt_mbpp` | the 17 `mt_mbpp_<language>` tasks | `bpb` | `mt_mbpp_v2fix` |
| `base_easy:code_bpb` | `humaneval`, `mbpp`, `mt_mbpp` | `bpb` | `olmo3:base_easy:code_bpb` |
| `base_easy` | `base_easy:qa_rc`, `base_easy:qa_bpb`, `base_easy:math_bpb`, `base_easy:code_bpb` | unscored | none |
| `tmmluplus:stem`, `tmmluplus:social_sciences`, `tmmluplus:humanities`, `tmmluplus:other` | that category's subject tasks | each task's primary metric (`acc_per_char`) | none |
| `tmmluplus` | the four category suites | each category suite's score | none |
| `tmmluplus:taiwan` | `tmmluplus_geography_of_taiwan`, `tmmluplus_ttqav2`, `tmmluplus_taiwanese_hokkien` | each task's primary metric (`acc_per_char`) | none |
| each of the above with `:bpb` | the same tasks, or the `:bpb` category suites for `tmmluplus:bpb` | `bpb` | none |
| `tmmluplus:all` | `tmmluplus`, `tmmluplus:bpb`, `tmmluplus:taiwan`, `tmmluplus:taiwan:bpb` | unscored | none |
| `tw_mock_exams` | the 10 `tw_mock_exams_<subject>` tasks | each task's primary metric (`acc_per_char`) | none |
| `tw_mock_exams:bpb` | the 10 `tw_mock_exams_<subject>` tasks | `bpb` | none |
| `tw_mock_exams:all` | `tw_mock_exams`, `tw_mock_exams:bpb` | unscored | none |
| `zh_tw` | `tmmluplus`, `tw_mock_exams` | each member suite's score | none |
| `zh_tw:bpb` | `tmmluplus:bpb`, `drcd_bpb`, `tw_mock_exams:bpb` | `bpb` | none |
| `paloma:<source>` | the source's tasks | `bits_per_byte` | OLMES's task `paloma_<source>::paloma`: the same number for a source with one domain, and its docs pooled instead of a mean of domains for a source with several |
| `paloma` | the 9 `paloma:<source>` suites | unscored | none |
| `fineweb` | `fineweb_en`, `fineweb_zh_hant`, `fineweb_zh_hans` | `bits_per_byte` | none |
| `tw_text:<source>` | the source's tasks | `bits_per_byte` | none |
| `tw_text` | the 11 `tw_text:<source>` suites | `bits_per_byte` | none |

The Base Easy suites hold only the tasks that exist so far, so their scores change as tasks are added. OLMES groups ARC-Easy and ARC-Challenge into one suite with no metric of its own; here they are two members of `base_easy:qa_rc`. `basic_skills`, and so `base_easy:qa_rc`, averages our `acc_per_token`, which divides by the real token count. OLMES's equals its `acc_raw`, so these two scores differ from OLMES's by that member. In BF16, `lambada`'s `acc_greedy` can also differ by a few near-tie docs, since our argmax is taken over FP32 logits and OLMES's over its BF16 log-probabilities. See [Suites in results](results#suites) for when two suite scores compare.

The `tmmluplus` score is the mean of the four category means, as the dataset card computes its total, so each category counts once however many subjects it has. `tmmluplus:taiwan` holds the three subjects behind the Taiwan columns of the Gemma-3-TAIDE model cards.

The mock exam tasks read `cerulean-works/tw-mock-exams`, a private dataset: the papers are school and publisher mock exams whose rights don't allow a public copy, so only members of the organization can run them, with a Hugging Face token in the environment. The subjects are `english` (英文), `social` (社會), `chinese` (國文), `science` (自然), `history` (歷史), `civics` (公民與社會), `biology` (生物), `geography` (地理), `physics` (物理) and `chemistry` (化學). The items were transcribed from scanned papers, and each task scores only single-answer items a text-only model can answer: no multi-answer, character-sensitive (錯別字, 字音, 字形), listening, cloze, figure or follow-up items. A grouped item shows its passage on the line before the question. 英文 uses MMLU's English format, the others TMMLU+'s Chinese one. No reference implementation scores this data, so there are no reference numbers to compare with.

`zh_tw` and `zh_tw:bpb` give one Traditional Chinese number of each kind, next to the Base Easy suites. No benchmark suite to follow exists: OLMo 3's Base Easy is English only, and TMLU and TMMLU+ each average only their own subjects. Each benchmark is one member through its own suite, so TMMLU+ (66 subjects) and the exam set (10) each count as one member. `drcd_bpb` reports only `bpb`, so it is in `zh_tw:bpb` alone. The exam set's `english` subject is an English exam, so a small part of both scores measures English. Use `zh_tw:bpb` for small checkpoints, whose accuracy is still near chance, and `zh_tw` for larger models. Run both with `--suite zh_tw --suite zh_tw:bpb`; the tasks they share run once. Both read the private exam set, so they need the access described above.

A Paloma source is a suite of its domains, scored as their unweighted mean, as the paper averages a source's domains. OLMES instead reports a source's bits per byte over all of its docs together, so a source with several domains differs from OLMES's number. `paloma` has no score, since Paloma defines none over all sources.

A `tw_text` source is a suite of its domains, scored as their unweighted mean, as a Paloma source is. Unlike `paloma`, `tw_text` has a score: the unweighted mean of its 11 sources, so each source counts once whatever its size.

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
