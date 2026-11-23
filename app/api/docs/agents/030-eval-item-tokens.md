# 030: an opened doc's tokens, tinted per run

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-23 |
| Builds on | [029](029-eval-pair-map.md), [028](028-eval-item-links.md) (D199 the doc route); eval's per-token sidecar (`flock/eval/docs/agents/045-token-sidecar.md`) |
| Amends | D199 (an opened doc also fetches its tokens), D185 (`PairFiles` reads bytes too) |

## 1. Goal

Results schema 12 writes, beside each task's predictions, a Parquet sidecar with per-token values over each choice's continuation: log-probability, rank, entropy and more. An opened doc showed only each choice's summed numbers, so a reader couldn't see which word a run stumbled on, or whether two runs failed on the same token.

1. **Each run's tokens under each choice,** as that run's tokenizer cut them, tinted by how unlikely the run found each one.
2. **Other measures** a click away: rank, entropy, and the two runs against each other.

**Out of scope:** the greedy tokens and the context's values (written only by flags, and no such runs are shared yet), perplexity tasks, any change to how eval reads, scores or writes.

---

## 2. Design decisions

### D206. `GET .../item/tokens` answers each run's continuation values for one doc

- `GET .../runs/{run}/item/tokens?doc=&task=&keep=[&with=]` takes the doc route's query (D199) and answers `{"runs": [{"choices", "note"}]}`, one entry per run in order. `choices` has one entry per choice: its tokens' `text`, `logprob`, `rank` and `entropy`, read with eval's `WrittenResults.tokens` for that doc only.
- Only the `continuation` rows: the context and greedy rows come by flags and aren't shown here. `top1_logprob` and `logprob_std` aren't sent: the four shown are what a reader asked of a token.
- **A run without values says why** (`choices` null, a `note`): a run written before schema 12, or without the sidecar. The other run's tokens still show.
- **Access** is the doc's: the sidecar of a private task is read from `private/` for the org's members only. Its token ids are the exam's text, so to anyone else the route is eval's refusal and holds none.
- `PairFiles` gains `read_bytes` (the sidecar is Parquet), and `eval_readers` hands each run's text and bytes readers in `WrittenResults.read`'s order.
- **Cost**, measured on two whole-bank runs: HellaSwag's 22 MB sidecar reads in 0.1 to 0.6 s per run, after the doc's own read (0.46 s); an ARC-Easy doc's tokens take 0.02 s once both tokenizers are fetched.

### D207. The API names each token with the run's tokenizer, and checks it gives the choice back

The sidecar holds token ids, not text (text would double its size and, for a private task, its exposure). The API decodes the opened doc's ids itself.

- **The tokenizer** is the one the results file names (`provenance.tokenizer`), its `tokenizer.json` fetched from the Hub with `huggingface_hub` at the model's revision when the tokenizer is the model's own repo, and kept for the server's life. `tokenizers` joins the API's dependencies; torch and transformers stay out.
- **A token's text is the difference between decoding its prefix and the one before**, so a byte-level token that ends inside a character (common in Chinese) has an empty piece and the character goes to the token that completes it.
- **Checked by giving the choice back**: the pieces, joined and stripped, must equal the choice as scored. `provenance.tokenizer_hash` can't serve as the check: it hashes the tokenizer as transformers serializes it, which differs from the raw `tokenizer.json` for OLMo 2 and Qwen 3.5 (it matches for Llama 3.2 and Gemma 4). Giving the text back checks what the reader sees.
- **A tokenizer that can't be fetched or doesn't give the choice back** (a local path such as TAIDE's SentencePiece model, a gated repo without a token, no network) leaves the values without text, with a `note` saying why; the tokens show as dots with their numbers.

### D208. Four tints, fixed scales; A against B only where both cut the choice alike

- **Surprisal** (minus the log-probability) by default, then **rank**, **entropy** and **A vs B**, one switch above the choices, kept from doc to doc.
- One hue, the reading blue, for the first three, as strong as the value over a fixed scale: 8 nats of surprisal, rank 1,024 (log scale) and 6 nats of entropy are full. Fixed, not per doc, so two docs read alike; hovering a token gives its three numbers.
- **A vs B** tints each token by its run's log-probability minus the other's, green where this run was surer and red where less, full at 3 nats, only where both runs cut the choice into the same tokens. Elsewhere tokens don't line up, so nothing is tinted.
- Tokens show whitespace as cut, with a newline as ↵.

---

## 3. Plan

1. The route, `PairFiles.read_bytes`, `token_texts` and their tests.
2. The relay, the types, the fetch and the switch in `EvalItems`, the tokens in `ItemChoiceRow`, a spec.
3. Docs: the dashboard guide and the API reference.

## 4. How it was actually built

- `token_texts` lives in `app/api/src/core/evals.py` beside `PairFiles`; the Hub fetch is a cached `_tokenizer`, so tests replace it with a stand-in that decodes bytes as a byte-level tokenizer does.
- `ItemChoiceRow`, made its own component in 027 for this, shows each run's tokens in that run's column under its numbers. `tint` returns a colour token and a strength, drawn with `color-mix` as the list's margins are.
- `EvalItems` fetches the tokens beside the doc; a failed fetch leaves the doc without tokens.

## 5. Known gaps

- Each opened doc reads both runs' whole sidecars for the task (22 MB each for HellaSwag), as the doc route reads the predictions. Reading only the doc's row group needs a range read on the store.
- A run whose tokenizer the server can't fetch shows values without text.
