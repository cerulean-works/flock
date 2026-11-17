# Scoring Taiwan Traditional Chinese text

The `tw_text` and `tw_text_v1` suites score how well a model fits Traditional Chinese as it is written in Taiwan: judgments, legislative records, press releases, news, forum posts, Wikipedia and theses, published from 2026-04-01 on, with theses the one exception. Both are scored the way [Paloma](https://arxiv.org/abs/2312.10523) is: a task per domain, a suite per source, in windows of 2048 tokens. This page says what each version holds, how to get access, how to run the suites and how to read their numbers.

## Two versions, two suites

| Suite | Dataset | Revision | Sources | Domain tasks |
|---|---|---|---|---|
| `tw_text` | `cerulean-works/tw-text-v0` | `1786e787a4ab786d1ed021994007ef3c9590e497` (tag `v0`) | 11 | 24 |
| `tw_text_v1` | `cerulean-works/tw-text-v1` | `b60d46e622d3c9ce7f022831004aab0bef91b32d` (tag `v1`) | the same 11 | 63 |

Both versions are frozen, and each suite pins its own revision. They hold different documents and different domains, so a `tw_text` number and a `tw_text_v1` number are two measurements, never read together. No task or suite name is shared between them, so `compare` lists a task of one version as `only_new` or `only_banked` against a file of the other. `tw_text_v1` doesn't replace `tw_text`: keep running `tw_text` wherever you compare with numbers already banked on it.

`flock/eval/tasks/tw_text.py` pins both revisions and lists every source's domains at each one.

## What the sets hold

Each document is dated by its source between 2026-04-01 and 2026-10-31. Every model the project compares was released before that window, or in Gemma 4's case (released 2026-04-02) states a training cutoff before it, and Cerulean's training data drops it, so no compared model has seen the text of the dated sources. Each document has at least 200 Han characters and passes a check for Simplified-only characters and mainland wording. Text converted from Simplified script isn't in either suite: converted text gets a task name of its own, never a native one.

| Source | Register | Domains in `tw_text` | Domains in `tw_text_v1` |
|---|---|---|---|
| `judicial` | formal: court judgments | 地方 (district courts), 高等 (high courts) | court level by case type: 地方民事, 最高民事, 最高家事, 行政, 最高刑事, 地方刑事, 高等民事, 高等刑事, other |
| `ly` | formal: Legislative Yuan records | 院會紀錄, 委員會紀錄 | 質詢, 院會紀錄, 委員會紀錄 |
| `gov_press` | formal: Executive Yuan and ministry press releases | 文化部, 行政院, 金管會, 農業部 | the same four |
| `cna` | news: Central News Agency | one | news section: 地方, 國際, 政治, 產經, 生活, 社會, 運動, 兩岸, other |
| `pts` | news: Public Television Service | one | news section: 生活, 全球, 社會, 地方, 政治, 文教科技, 產經, other |
| `ltn` | news: Liberty Times | one | news section: 生活, 政治, 社會, 最新消息, 健康, 國際, other |
| `cnyes` | news: Anue (鉅亨網), finance | one | category and article type: announcement, wd_stock, tw_stock, bc, other |
| `twreporter` | news: The Reporter, long-form | one | one |
| `ptt` | forum: posts on eight PTT boards | Stock, movie, Tech_Job, MobileComm, WomenTalk, Baseball, Gossiping, HatePolitics | the same eight boards |
| `zhwiki` | encyclopedic: zh-Wikipedia pages created in the window | one | one |
| `theses` | academic: full texts of master's and doctoral theses, with their English front matter | NYCU, NTNU (school) | field of study: 理學, 醫護, 人文社會, 商管, 工程, 教育, 藝術設計, other |

Two sources differ from the rest in time, in both versions:

- `judicial` covers 2026-04-01 to 2026-08-31 only. The judiciary publishes judgments in monthly bundles, and the bundles for September and October didn't exist when either version was built. A later month joins as a new revision of the dataset, which means a new pin and so a new suite.
- `theses` is not held out in time. A thesis carries only its academic year (115, dated from 2026-08-01), so its text may predate the window, and a compared model may have seen part of it. It is in the mean all the same. Read its number with that in mind.

`zhwiki` holds only pages that passed the script check, which kept 40.3 percent of the pages fetched in the window at `v0` (2,317 of 5,755) and 39.8 percent at `v1` (2,290). That share describes the crawl, not the sample.

### How `v1` differs from `v0`

`v1` re-parses the pages fetched for `v0` with newer extraction code and samples them again. It adds no news, forum, record or wiki page that `v0` didn't fetch. Theses are the exception: they were listed again from a newer release of the thesis repository's index, which adds a school. Each change is a versioned step of the build:

- **Domains from each source's own metadata.** A news page's section, a judgment's court and case type, a thesis's department mapped to one of seven fields of study. `v0` had one domain for most sources.
- **Equal sampling across domains.** Each source gets about 2 million OLMo 2 tokens, split evenly over its domains, as Paloma's stratified sampling does. A domain with less than its share gives the rest to the others. `v0` drew uniformly from each source, so its largest domains filled most of their source.
- **Repaired extraction.** Publisher footers, "continue reading" interstitials, promotions, navigation links and stock widgets are cut from `cna`, `ltn` and `cnyes`, and image placeholders from `ly`. PTT signatures are cut. Similarity-report front matter is dropped from theses. zh-Wikipedia keeps the visible text of common templates, and its tables as one row a line. Cutting boilerplate leaves some short articles under the 200-character floor, so they leave the set.
- **A revised script check.** The 0.5 percent threshold for Simplified-only characters is counted outside quotation marks, so a Taiwanese article quoting mainland social media is no longer dropped, and 信息 and 視頻 no longer count as mainland wording.
- **Withdrawn judgments leave the set.** A judgment the judiciary withdraws is removed from every copy before publishing. None of `v0`'s sampled judgments was on the withdrawal list.
- **Privacy.** A thesis with an e-mail address or phone number in its text is quarantined and never sampled.

The floors are the same in both versions. Inside a source's sample, a domain under 100,000 OLMo 2 tokens, Paloma's per-domain size, merges into `other`, and an `other` still under that size leaves the sample. At `v0` every merged `other` fell under the floor, so no `v0` domain is named `other`, and `tw_text` holds no administrative or supreme court judgments and no interpellation records: their documents left the `v0` sample this way. At `v1` several `other` domains clear it, and each is scored as a domain like any other: for example, `tw_text_v1_ltn__other` holds LTN's 24 smaller sections. The Reporter's podcast descriptions formed a domain under the floor at `v1` (38 documents, 26,940 tokens), so they left the sample.

| Source | `v0` documents | `v1` documents | `v1` OLMo 2 tokens |
|---|---|---|---|
| `judicial` | 660 | 623 | 2,001,167 |
| `ly` | 25 | 49 | 2,025,252 |
| `gov_press` | 1,197 | 1,225 | 2,000,808 |
| `cna` | 1,811 | 1,952 | 2,001,063 |
| `pts` | 2,059 | 2,069 | 2,000,460 |
| `ltn` | 1,935 | 2,020 | 2,049,988 |
| `cnyes` | 946 | 836 | 1,117,628 |
| `twreporter` | 257 | 220 | 1,551,248 |
| `ptt` | 1,748 | 1,717 | 2,000,105 |
| `zhwiki` | 971 | 889 | 2,007,291 |
| `theses` | 34 | 26 | 2,048,523 |
| **Total** | **11,643** | **11,626** | **20,803,533** |

`cnyes` and `twreporter` hold all they kept, under 2 million tokens. `cnyes`'s crawl stopped at a fixed time before reaching that size.

## Access

Both sets are private to the `cerulean-works` organization. Most of their sources are all rights reserved (news outlets, thesis repositories) and are used here for evaluation only, so the data can't be published. Ask an admin of the `cerulean-works` organization on the Hugging Face Hub to add you. Every process that runs the suites needs `HF_TOKEN` set to a token of a member.

## Run the suites

```bash
uv run flock eval run \
  --suite tw_text_v1 \
  --model your-org/your-model@<revision> \
  --batch-tokens 16384 \
  --out results/tw_text_v1
```

Run `--suite tw_text` the same way for `v0`. To score one source, pass its suite instead, such as `--suite tw_text_v1:judicial`. Compare only runs with the same `--dtype` and `--batch-tokens`: in BF16, a score depends slightly on which requests share its batch (see [Batching and precision](scoring#batching-and-precision)).

## Read the results

Each domain is one [`~eval.tasks.PerplexityTask`]. It scores every document whole, in disjoint windows of 2048 tokens, and reports `bits_per_byte`, `loss_per_token` and `perplexity` over all of its documents (see [Metrics](metrics#perplexity)). Task names follow Paloma's: `tw_text_v1_<source>__<domain>`, such as `tw_text_v1_judicial__地方民事`, or `tw_text_v1_<source>` for a source with one domain, and the same without `_v1` for `v0`.

Read the results per source, as for Paloma:

- `tw_text_v1:<source>` is the unweighted mean of its domains' `bits_per_byte`, as `paloma:<source>` is. Each domain counts once, whatever its size, so a small domain (LTN's 國際, 136,693 OLMo 2 tokens) moves its source's number as much as a large one (LTN's `other`, 521,544). Equal sampling narrows these gaps at `v1` but doesn't close them: a domain with less than its share keeps all it has, and the others share the rest.
- `tw_text_v1` is the unweighted mean of the 11 sources, and `tw_text` likewise. Paloma has no score over its sources, but these sets were built for one comparison, so they have one. Each source counts once, so read the sources before the mean.

Compare models on `bits_per_byte`, which divides by UTF-8 bytes rather than tokens and so works across tokenizers. `loss_per_token` and `perplexity` compare only models that share a tokenizer.

## Excluding the sets from training data

Training data must hold out every document of both versions. An exclusion index takes them as whole documents, never as scoring windows:

- **Scored rows.** Every row of all 11 configurations of each pinned revision, split `test`: its configuration, `id`, `url`, `subdomain` and `source`, and the SHA-256 of its UTF-8 `text`, which is the whole document, with the SHA-256 of each configuration's file. `v0`'s rows count as well as `v1`'s, since `v1`'s repairs change the text of documents both versions hold, and both are scored.
- **Identities beyond the sample.** Training drops the whole window, so a document left out of the sample is still held out. The crawl's own records name every document fetched in the window by its URL, judgment id, wiki page id, PTT post id or thesis id, and the index takes those identities too.
- **Withdrawals.** A withdrawn judgment's text leaves the index. Only its id stays, as a tombstone.

The dataset files cover only the first item. The other two come from the crawl's records, which aren't on the Hugging Face Hub. No training data has been checked against these references yet.
