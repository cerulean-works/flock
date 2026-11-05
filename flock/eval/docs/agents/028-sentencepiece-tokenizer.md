# 028: A repo with only `tokenizer.model` is tokenized by that SentencePiece model

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-05 |
| Builds on | [001](001-eval-core.md) (`ScoringModel`, tokenization), [022](022-prepared-tasks.md) (prepared tasks hold one tokenizer object) |
| Keeps | Every task hash, the schema, and `AutoTokenizer` for every repo that ships `tokenizer.json` |
| Result | `load_tokenizer` gives a repo with `tokenizer.model` and no `tokenizer.json` a `SentencePieceTokenizer`, whose ids equal raw `SentencePieceProcessor.encode` on all 34,804 strings TAIDE-LX-7B tokenizes at `--limit 20` |

## 1. Goal

`taide/TAIDE-LX-7B` ships `tokenizer.model` and no `tokenizer.json`, so `flock eval` can't load it: with neither `sentencepiece` nor `tiktoken` installed, transformers 5.20.1 raises `ValueError: tiktoken is required to read a tiktoken file`. Adding `sentencepiece` alone would make it load, with the wrong tokens. This stage loads such a repo with its SentencePiece model and makes the conversion unreachable.

**Out of scope:** scoring TAIDE; any other model's tokenizer path; `add_bos`; a provenance or schema field for the tokenizer kind; the chat TAIDE models.

---

## 2. Design decisions

### 028.D1. Raw SentencePiece, because transformers 5 offers no tokenizer that equals the model's

The TAIDE card asks for the slow tokenizer. transformers 5 has none: `AutoTokenizer` ignores `use_fast` (`tokenization_auto.py:749-750`) and resolves the repo to a Tokenizers BPE built from the SentencePiece pieces. On the 34,804 distinct strings TAIDE tokenizes at `--limit 20` for `base_easy`, `zh_tw`, `zh_tw:bpb`, `paloma` and `fineweb_edu`:

| Tokenizer | Strings differing from raw `SentencePieceProcessor.encode` | Why |
|---|---|---|
| transformers 5 conversion (what adding `sentencepiece` gives) | 10,512 | Another segmentation, for example 「黃色 的」 becoming 「黃 色的」 |
| transformers 4.37.2 `use_fast=False` (the card's setting) | 4 | The 4 contain literal `<s>`, `</s>` or `<unk>`, which it splits out as special tokens |
| transformers 5 `SentencePieceBackend`, `legacy=True` | 4 | The same 4, split out differently: it agrees with the slow tokenizer on none of them |
| transformers 5 `SentencePieceBackend`, `legacy=False` (the repo's `legacy` value) | 34,804 | The v5 base class turns the dummy prefix off (`tokenization_utils_sentencepiece.py:78-83`) and nothing adds it back. The repo's value isn't the cause: 4.37.2 `LlamaTokenizer` with the same `legacy: false` keeps the leading `▁` |

The conversion's effect on any score is unmeasured. It is refused because a different tokenization is a different instrument. `SentencePieceBackend` with `legacy=True` would give the right ids on all but 4 strings, but only because that mode happens to call `sp_model.encode` directly. `SentencePieceTokenizer` calls `encode` itself, with the model file's own settings and no options, and offers only what `ScoringModel` reads: the call with `add_special_tokens=False`, `bos_token_id` and `eos_token_id`. Asking it for special tokens raises.

### 028.D2. The repo's files choose the path, so one model id always means one tokenizer

A repo with `tokenizer.model` and no `tokenizer.json` at the pinned revision gets `SentencePieceTokenizer`. Every other repo takes the `AutoTokenizer` line as before, including a repo with both files, as Gemma's has. No flag and no model name is involved, so two runs of one model id can't differ in tokenizer, and the rule needs no schema field. `sentencepiece` in the lockfile can't switch a repo to the conversion, because `AutoTokenizer` is not called for one.

The choice reads `snapshot_download` with `allow_patterns` for the two files, not `HfApi.list_repo_files`. Online that is the revision's file list. Offline it is the cached snapshot, so a pinned model still loads from the cache as `from_pretrained` documents, which a file-list call would break for every model. The same call returns the path to `tokenizer.model`. A repo with both files now also downloads `tokenizer.model`, which nothing reads.

A `tokenizer.model` in another format, such as tiktoken's, fails to load in `SentencePieceProcessor`. That is an error, not another tokenization.

### 028.D3. Literal special-token text is text

A document that contains `<unk>` holds those five characters. Raw SentencePiece encodes them as ordinary pieces, as the tokenizers of OLMo 2 1B, Llama 3.2 1B and Qwen3.5 0.8B already do with that string. The slow tokenizer of the card and the legacy backend split them out, each in its own way. No reference harness sets a precedent: OLMES would load a Llama 2 family model through the transformers 4 fast tokenizer, which would also split them out. This is the suite's own choice, justified by the suite scoring documents as text. It differs from the model's card on 4 of the 34,804 strings, so a table carrying TAIDE's `ptb` cell should mark it.

### 028.D4. TAIDE's `add_bos_token: true` is not applied

No special tokens are added to tokenized text, as for every model, and no reference config for this family is cited that would set one. The repo's `tokenizer_config.json` isn't read on this path. An empty context and a rolling text's first window still start from the tokenizer's BOS, id 1, as for any tokenizer with one.

---

## 3. Plan

1. `tokenizer.py`: `SentencePieceTokenizer`, and the `Tokenizer` type that `ScoringModel` and the prepared tasks accept.
2. `load_tokenizer` in `model.py`, called by `from_pretrained`. Flock models keep `get_hf_tokenizer`.
3. `sentencepiece` in `flock-eval`'s dependencies.
4. Tests in `tests/eval/test_tokenizer.py` with a tiny SentencePiece model trained in the test.
5. A paragraph in the scoring docs.

---

## 4. How it was actually built

- **The loader is its own function,** not inlined in `from_pretrained`, so the tokenizer can be checked without loading weights.
- **The dependency adds 30 lock lines.** It was added with uv 0.13.7, because uv 0.12.17 (CI's) rewrites the lockfile from revision 5 to revision 3 and churns its markers. CI only reads the lock (`--frozen`), so its version is unchanged.
- **The docs workflows mock `sentencepiece`.** They build without flock's dependencies, and autodoc's import of `flock.eval.tokenizer` failed without it, so `mock_deps` gains it in `flock-docs.yml` and `flock-docs-pr.yml`.
- **Checks.**
  - All 34,804 strings give the same ids as raw `encode`, the 4 with special-token text included.
  - OLMo 2 1B, Llama 3.2 1B, Qwen3.5 0.8B, Qwen3.5 4B and Gemma 4 E2B give the same ids on those strings with `sentencepiece` installed and through `load_tokenizer` as with neither.
  - With `HF_HUB_OFFLINE=1`, `load_tokenizer` picks the same class from the cache for TAIDE-LX-7B, OLMo 2 1B, Llama 3.2 1B and Gemma 4 E2B.

### Known gaps

- **Flock models keep `AutoTokenizer`.** `BaseModel.get_hf_tokenizer` in `flock.training` loads its `hf_repo` that way, so with `sentencepiece` installed a Flock model naming a SentencePiece-only repo would get the conversion. No model does today.
- **An incomplete offline cache can choose wrongly.** Offline, a snapshot holding `tokenizer.model` but not the repo's `tokenizer.json` looks like a SentencePiece-only repo. It takes a partial download of a repo that was never loaded by flock, since flock's own load fetches both files.
- **`sentencepiece`'s version isn't in the provenance.** `tokenizers` is, and the lockfile pins this one at the recorded commit. A field for it would be a schema change.
