# 024: Gemma 4 loads and scores as OLMES scores Gemma

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-04 |
| Builds on | [001](001-eval-core.md) (`ScoringModel`, tokenization), [013](013-score-compare.md) (`compare`), [014](014-run-provenance.md) (the results file), [022](022-prepared-tasks.md) (prepared tasks), [023](023-deterministic-scoring.md) (schema 7), [025](025-core-style.md) (the inlined `encode_requests`) |
| Keeps | Every task hash, every score of a run without `add_bos`, the `max_length` of every text-only model |
| Result | `ScoringModel` reads `max_position_embeddings` from the text config, and `add_bos` (`--add-bos`) starts every non-empty context with BOS, recorded in the provenance (schema 8) and refused by `compare` when it differs |

## 1. Goal

`AutoModelForCausalLM` loads `google/gemma-4-E2B@d29ff6b4` (transformers 5.20.1) as `Gemma4ForConditionalGeneration`, whose `Gemma4Config` holds a text, a vision and an audio config. The top level has no `max_position_embeddings`, so `ScoringModel.__init__` refused the model ("config has no max_position_embeddings; pass max_length"). The text config has 131072.

Once it loads, a second gap shows: flock adds no special tokens, as OLMES does by default, but OLMES makes one exception. Every OLMES model config that sets `add_bos_token` is a Gemma (`oe_eval/configs/models.py`, 9 configs at `5a51f50`), and Gemma is trained with BOS before every text. Scored without it, Gemma's numbers would not be OLMES's.

**Out of scope:** a loader that drops the vision and audio parts (section 4), any other multimodal family, BF16.

---

## 2. Design decisions

### 024.D1. `max_length` is read from the text config

`ScoringModel.__init__` reads `max_position_embeddings` from `model.config.get_text_config(decoder=True)`, transformers' own accessor for the text part of a composite config. For a text-only config it returns the config itself, so OLMo 2 (4096), Llama 3.2 (131072) and Qwen3.5 (262144) record the same `max_length` as before. `decoder=True` picks the config of the model that produces the logits, which is the one whose positions flock fills. A Flock model, wrapped in `FlockCausalLM`, has a plain namespace for a config with no sub-configs, and keeps reading it directly.

The other choice was to pass `max_length` from `from_pretrained` for multimodal models only. It would leave a `ScoringModel` built by hand failing on the same model, and add a second path for one family.

### 024.D2. BOS before every context is an explicit setting, `add_bos`, off by default

With `add_bos`, every non-empty context starts with the tokenizer's BOS token and the continuation is unchanged. This is what OLMES's `tok_encode` does with `add_bos_token` (`oe_eval/models/eleuther_huggingface.py:169-170`, `[bos_token_id] + encoding`): it prepends BOS to the context and to the joined text alike, so lm-evaluation-harness's `_encode_pair` leaves the continuation as it was. A tokenizer without a BOS token refuses the setting.

It is a setting, not read from the tokenizer. transformers 5.20.1 does not expose Gemma's `add_bos_token: true` from `tokenizer_config.json` (the attribute reads `False`), and its default `add_special_tokens=True` adds BOS for Llama 3.2 too, which OLMES scores without one. A rule read from the tokenizer would move Llama's numbers away from OLMES's. A switch per model family in code would hide the setting from the command line. So the caller sets it, as OLMES's model config does, and the docs say to set it for Gemma.

Two OLMES side effects are not copied. With an empty context, OLMES encodes the continuation with BOS in front and scores that BOS as part of it. flock keeps the empty context as one BOS token and the continuation as it is. A rolling text in OLMES starts from EOS and predicts BOS as its first token. flock's first window already starts from BOS when the tokenizer has one (008), as the Paloma paper writes "<BOS>document", so `add_bos` does not touch the rolling path. Later windows start from the previous window's last token, not from BOS (Known gaps).

### 024.D3. `add_bos` is in the provenance, and `compare` refuses a difference

BOS changes every context, so it is part of the instrument, like `max_length`. `Provenance` gains `add_bos: bool`, `results.json` goes to schema 8, and `compare` refuses two files whose `add_bos` differs. A file older than the field reads as `False`, because no code before it added BOS, so the banks stay comparable with today's runs without BOS. A task prepared by `prepare_task` records `add_bos` too, and `score_prepared` refuses a model with another value: the token ids differ while the tokenizer object is the same, which the identity check alone would miss. A perplexity task is not checked, since its windows don't change.

---

## 3. How it was built

1. `model.py`: the text-config read in `ScoringModel.__init__`, `add_bos` on `ScoringModel` and `from_pretrained`, and BOS in `encode_requests`.
2. `provenance.py`, `results.py`, `run.py`, `cli.py`, `compare.py`: the field, schema 8, `EvalConfig.add_bos`, `--add-bos`, the refusal, and the prepared-task check.
3. Tests (CPU): a tiny `Gemma4ForConditionalGeneration` with vision and audio towers, built in the test. Its top-level config has no `max_position_embeddings`, its vision config has 131072, and its text config 24. `max_length` is 24, requests are cut to it, scores match the model's own unbatched forward pass, and forward hooks show neither tower runs. With `add_bos`, contexts gain BOS and nothing else changes, scores match the unbatched reference, and rolling scores do not move. `compare` refuses another `add_bos` and reads a file without it as `False`.
4. Docs: `scoring.md` (both changes), `results.md`, `comparison.md` and `commands.md` (the field, the refusal and the flag).

Before the full Gemma runs, OLMo 2 1B's smoke run (20 docs per task, FP32) was repeated after the text-config change and again after `add_bos`, and diffed with the run before both: the same `max_length`, task hashes and doc counts, and every metric equal. A tiny copy of Gemma 4 E2B's real config, built with random weights, took `ScoringModel`'s forward arguments on a left-padded batch and returned finite logits of the vocabulary's width.

The first version had only the text-config change. Review found that Gemma would then load but be scored without BOS, which is how 024.D2 and D3 came in.

This branch was then merged with `main` after the core refactor (025), so BOS now sits on the inlined form of `encode_requests` that 025 left. Both smoke runs were repeated at the merged code, OLMo 2 1B without BOS and Gemma 4 E2B with it, and every metric of all 495 and 496 tasks was equal.

---

## 4. Known gaps

- **The towers load and sit idle.** In FP32 they take 1.9 GB of Gemma 4 E2B's 20.4 GB of weights. That fits an H200 with room for the default batches, so a text-only loader would only save memory, and it would be a second loading path for one family. If a larger multimodal model does not fit, that loader is the change to make.
- **`attention` reads the top-level config.** It records `model.config._attn_implementation`. A model loaded with a different attention implementation per sub-config would record the top level's, not the text model's. `from_pretrained` never asks for that, so every model it loads has one value.
- **`get_text_config` raises on an ambiguous config.** A config with two text sub-configs among `decoder`, `generator` and `text_config` makes transformers raise. No model scored today has one.
- **Left truncation can cut BOS.** BOS is the context's first token, so a request longer than `max_length` loses it with the rest of the cut, and the provenance still says `add_bos`. OLMES cuts the same way (`oe_eval/models/eleuther_huggingface.py:383`, `(context_enc + continuation_enc)[-(self.max_length + 1) :]`, with BOS in `context_enc`). The truncation count shows the cut. Gemma 4 E2B's runs cut nothing.
- **Later rolling windows have no BOS.** Only a document's first window starts from BOS. Each later window starts from the previous window's last token, with or without `add_bos`, as in OLMES (`oe_eval/models/eleuther_huggingface.py:202-205`). A model that leans on BOS pays for that on long documents: scored once with BOS before every later window, Gemma 4 E2B's `paloma_wikitext_103` fell from 0.9025 to 0.6749 bits per byte, while Llama 3.2 1B's moved from 0.7207 to 0.7211. That scoring would be another task under another name, so it is not built.
- **Nothing sets `add_bos` for you.** A Gemma run without `--add-bos` scores without BOS and records `add_bos: false`, so it can be told apart but not prevented.
