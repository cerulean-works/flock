# 046: per-token loss of perplexity tasks

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-23 |
| Builds on | [008](008-paloma.md) (rolling windows), [044](044-row-sources.md) (044.D3 `source` and `domain`), [045](045-token-sidecar.md) (045.D1 the Parquet file, 045.D2 the values) |
| Amends | 045.D1 (a perplexity task can write the file too, in its own shape) |
| Keeps | every task hash, score and metric, every perplexity prediction row, and no text in a perplexity task's files |
| Result | `--perplexity-tokens` writes each perplexity doc's per-token values and the byte where each token ends; `rolling_loglikelihood(..., tokens=True)` and `ScoringModel.byte_ends` |

The decision numbers are provisional: `046.D1` and `046.D2` take the next free global numbers after every stage merged before it.

## 1. Goal

A perplexity row sums a document's loss, so which tokens or which parts of a text carry the loss can't be read back. Paloma (Magnusson et al. 2023, 2312.10523) reports that few vocabulary types account for most of the loss; seeing where a domain's loss sits needs per-token loss and the place of each token in the text. This stage writes them, by flag, without the text.

**Out of scope:** token ids or strings (the text), per-window values, any change to scores or windows.

---

## 2. Design decisions

### 046.D1. Values per token and where each ends in bytes, never the tokens

`rolling_loglikelihood(..., tokens=True)` returns each text's per-token values: the windows predict a text's tokens once each, in order, so the windows' values regrouped per text are the text's. The file holds 045.D2's five values and `byte_end`, where each token ends in the text's UTF-8 bytes. Token ids would spell out the text, which a perplexity task's files never hold (stage 043, Out of scope), so they are not written; with the dataset, `byte_end` places each value in its text.

### 046.D2. Byte ends come from the tokenizer's own offsets, made non-decreasing

A Tokenizers tokenizer gives character offsets, which go to bytes; SentencePiece gives each piece's byte offsets. Byte-level merges can place a token's end inside a character, and byte-fallback pieces of one character get empty spans, so the ends are made non-decreasing and a token can span no bytes. The offsets come from tokenizing again, checked to give the scored token ids.

---

## 3. Plan

1. `tokens` on `rolling_loglikelihood`; `byte_ends`; `write_perplexity_tokens`.
2. `perplexity_tokens` on `EvalConfig`, `score_task`, `score_prepared` and the CLI, off by default.
3. Tests and docs.

---

## 4. How it was actually built

- The file shares the name `predictions/<task>.tokens.parquet` and the reader with stage 045; its rows have `part` `text`.
- Tokenizing again for offsets costs one more tokenizer call per task, only under the flag.

## 5. Known gaps

- Off by default: a full Paloma run's file is large (see the measurements in the eval docs).
- Byte ends of byte-level and byte-fallback pieces are approximate inside a character.
