# Scoring Taiwan Traditional Chinese text

The `tw_text` suite scores how well a model fits Traditional Chinese as it is written in Taiwan: judgments, legislative records, press releases, news, forum posts, Wikipedia and theses, published from 2026-04-01 on, with theses the one exception. It is scored the way [Paloma](https://arxiv.org/abs/2312.10523) is: a task per domain, a suite per source, in windows of 2048 tokens. This page says what the set holds, how to get access, how to run it and how to read its numbers.

## What the set is

The documents come from `cerulean-works/tw-text-v0` on the Hugging Face Hub, one configuration per source, split `test`. Each document is dated by its source between 2026-04-01 and 2026-10-31, except `theses`, and `judicial` stops early at 2026-08-31. Every model the project compares was released before that window, or in Gemma 4's case (released 2026-04-02) states a training cutoff before it, and Cerulean's training data drops it, so no compared model has seen the text of the dated sources. Each document has at least 200 Han characters and passes a check for Simplified-only characters and mainland wording. Text converted from Simplified script isn't in the suite: converted text gets a task name of its own, never a native one.

| Source | Register | Domains (`subdomain`) |
|---|---|---|
| `judicial` | formal: court judgments, 2026-04-01 to 2026-08-31 | 地方 (district courts), 高等 (high courts) |
| `ly` | formal: Legislative Yuan records | 院會紀錄, 委員會紀錄 |
| `gov_press` | formal: Executive Yuan and ministry press releases | 文化部 (Ministry of Culture), 行政院 (Executive Yuan), 金管會 (Financial Supervisory Commission), 農業部 (Ministry of Agriculture) |
| `cna` | news: Central News Agency | one |
| `pts` | news: Public Television Service | one |
| `ltn` | news: Liberty Times | one |
| `cnyes` | news: Anue (鉅亨網), finance | one |
| `twreporter` | news: The Reporter, long-form | one |
| `ptt` | forum: posts on eight PTT boards | Stock, movie, Tech_Job, MobileComm, WomenTalk, Baseball, Gossiping, HatePolitics |
| `zhwiki` | encyclopedic: zh-Wikipedia pages created in the window | one |
| `theses` | academic: full texts of master's and doctoral theses, with their English front matter, not held out in time | NYCU, NTNU |

`theses` is not held out in time. A thesis carries only its academic year (115, dated from 2026-08-01), so its text may predate the window, and it is in the `tw_text` mean all the same. Read its number with that in mind. `judicial` stops at 2026-08-31 because the monthly bundles of judgments end there.

`zhwiki` holds only pages that passed the check above. The check kept 40.3 percent of the pages fetched in the window (2,317 of 5,755). That share describes the crawl, not the sample.

Each source is sampled to about 2 million OLMo 2 tokens, or all that was crawled where that is less (`twreporter`, and `cnyes`, whose crawl stopped at a fixed time before reaching that size). Inside that sample, a domain under 100,000 OLMo 2 tokens, Paloma's per-domain size, merges into `other`, and an `other` still under that size leaves the sample. So every domain here holds at least 100,000 tokens. At this revision every merged `other` fell under that size, so none is named `other`. The sample lost 40 judgments (31 of them from the administrative and supreme courts), 1 interpellation record of `ly` and 1 NCKU thesis this way. `flock/eval/tasks/tw_text.py` pins the dataset's revision and lists every source's domains at that revision.

## Access

The set is private to the `cerulean-works` organization. Most of its sources are all rights reserved (news outlets, thesis repositories) and are used here for evaluation only, so the data can't be published. Ask an admin of the `cerulean-works` organization on the Hugging Face Hub to add you. Every process that runs the suite needs `HF_TOKEN` set to a token of a member.

## Run the suite

```bash
uv run flock eval run \
  --suite tw_text \
  --model your-org/your-model@<revision> \
  --dtype float32 \
  --out results/tw_text
```

To score one source, pass its suite instead, such as `--suite tw_text:judicial`.

## Read the results

Each domain is one [`~eval.tasks.PerplexityTask`]. It scores every document whole, in disjoint windows of 2048 tokens, and reports `bits_per_byte`, `loss_per_token` and `perplexity` over all of its documents (see [Metrics](metrics#perplexity)). Task names follow Paloma's: `tw_text_<source>__<domain>`, such as `tw_text_judicial__地方`, and `tw_text_<source>` for a source with one domain.

Read the results per source, as for Paloma:

- `tw_text:<source>` is the unweighted mean of its domains' `bits_per_byte`, as `paloma:<source>` is. Each domain counts once, whatever its size, so a small domain (4 NTNU theses) moves its source's number as much as a large one (30 NYCU theses).
- `tw_text` is the unweighted mean of the 11 sources. Paloma has no score over its sources, but this set was built for one comparison, so it has one. Each source counts once, so read the sources before the mean.

Compare models on `bits_per_byte`, which divides by UTF-8 bytes rather than tokens and so works across tokenizers. `loss_per_token` and `perplexity` compare only models that share a tokenizer.
