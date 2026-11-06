# 028: A repo with only `tokenizer.model` is tokenized by that SentencePiece model

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-05 |
| Builds on | [001](001-eval-core.md) (`ScoringModel`, tokenization), [022](022-prepared-tasks.md) (prepared tasks hold one tokenizer object), [024](024-text-config-length.md) (`add_bos`) |
| Keeps | Every task hash, the schema, and `AutoTokenizer` for every repo that ships `tokenizer.json` |
| Result | `load_tokenizer` gives a repo with `tokenizer.model` and no `tokenizer.json` a `SentencePieceTokenizer`, whose ids equal raw `SentencePieceProcessor.encode` on all 34,804 strings TAIDE-LX-7B tokenizes at `--limit 20` |

## 1. Goal

`taide/TAIDE-LX-7B` ships `tokenizer.model` and no `tokenizer.json`, so `flock eval` can't load it: with neither `sentencepiece` nor `tiktoken` installed, transformers 5.20.1 raises `ValueError: tiktoken is required to read a tiktoken file`. Adding `sentencepiece` alone would make it load, with the wrong tokens. This stage loads such a repo with its SentencePiece model and makes the conversion unreachable.

**Out of scope:** scoring TAIDE; any other model's tokenizer path; changing how `add_bos` works; a provenance or schema field for the tokenizer kind; the chat TAIDE models.

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

The choice asks for `tokenizer.json` first, and for `tokenizer.model` only if the repo has no `tokenizer.json`, with `hf_hub_download`, not `HfApi.list_repo_files`. A repo with `tokenizer.json` therefore fetches nothing else to choose, and a pinned model still loads offline from the cache as `from_pretrained` documents, which a file-list call would break for every model.

"The repo has no such file" means a 404 from the Hub, or the `.no_exist` record a 404 leaves in the cache. A file that merely isn't in the snapshot doesn't count, so a download interrupted after `tokenizer.model` landed can't make a repo with both files look SentencePiece-only. A failed request online raises `LocalEntryNotFoundError` too, because the next request may succeed and choose differently. Offline, with `tokenizer.model` cached and neither `tokenizer.json` nor its record, the load raises the same error rather than guess. A load through `AutoTokenizer` writes the `tokenizer.json` record, so a cache flock or transformers has loaded from answers offline. A cache filled only by `hf download` doesn't, and needs one online load. A repo with no `tokenizer.model` on disk isn't affected: `AutoTokenizer` can only read what is there, so it loads offline as before.

A folder that exists at the repo's path is read directly, as `AutoTokenizer` and `AutoModelForCausalLM` read it, so a local model never reaches the Hub.

A `tokenizer.model` in another format, such as tiktoken's, fails to load in `SentencePieceProcessor`. That is an error, not another tokenization.

### 028.D3. Literal special-token text is text

A document that contains `<unk>` holds those five characters. Raw SentencePiece encodes them as ordinary pieces, as the tokenizers of OLMo 2 1B, Llama 3.2 1B and Qwen3.5 0.8B already do with that string. The slow tokenizer of the card and the legacy backend split them out, each in its own way. No reference harness sets a precedent: OLMES would load a Llama 2 family model through the transformers 4 fast tokenizer, which would also split them out. This is the suite's own choice, justified by the suite scoring documents as text. It differs from the model's card on 4 of the 34,804 strings, so a table carrying TAIDE's `ptb` cell should mark it.

### 028.D4. TAIDE's `add_bos_token: true` is not applied

No special tokens are added to tokenized text unless `add_bos` is set (024.D2), as for every model. `add_bos` works on this path, with the SentencePiece model's BOS, but TAIDE runs without it: no reference config for this family is cited that would set it. The repo's `tokenizer_config.json` isn't read on this path. An empty context and a rolling text's first window still start from the tokenizer's BOS, id 1, as for any tokenizer with one.

### 028.D5. A continuation that resegments the context is refused on this path

`encode_requests` takes the continuation's ids as the joined text's ids after as many as the context has alone, which assumes the joined ids start with the context's. SentencePiece segments a whole string at once, so added text can break that: a tiny unigram model gave `黃色` as `▁黃`, `色` and `黃色的` as `▁黃色的`. The slice then gives an empty continuation, or, with longer text, silently drops characters and scores the wrong tokens.

On this path that raises `ValueError` from `encode_requests`. `evaluate` encodes every multiple-choice task's requests before it scores any task, so a resegmented request can't lose the tasks before it, or leave predictions a rerun would refuse to overwrite. A continuation with no tokens still raises during scoring, as for every tokenizer. That tokenizes these tasks twice, which costs less than holding every task's tokens at once. Encoding the two parts separately was rejected, because it departs from the suite's rule that the two are tokenized together. Other tokenizers keep lm-evaluation-harness's behavior with no check, so their scores can't move.

TAIDE's own model never hits this on the current suite: 0 mismatches over all 972,271 requests of all 186 tasks in the registry's suites, with no `--limit`. That includes continuations with no leading whitespace, about half of the 25,886 requests at `--limit 20`, such as `答案：` + `五氯酚`. A future task or another model can raise it, which stops the run rather than scoring it wrongly.

### 028.D6. A conversion under another file name is refused, not loaded raw

With `sentencepiece` installed, a repo with no `tokenizer.json` and a SentencePiece model under another name would load through `AutoTokenizer` as a conversion, where before it failed. `load_tokenizer` raises `ValueError` for these. They don't get `SentencePieceTokenizer`, because their tokenizer classes can remap the model's ids, as XLM-R's adds an offset, so raw ids may not be the ones the weights expect.

The test reads the loaded class's `vocab_files_names`, for a file ending in `.model` or `.spm`, not a list of file names. A first version listed names and missed `sentencepiece.model` (`RemBertTokenizer`) and `spm_char.model` (`SpeechT5Tokenizer`). The class also catches a model file its class doesn't name, if the class declares a `.model` at all: `T5Tokenizer` beside `sp.model` builds a tokenizer that reads no vocabulary. `GPT2Tokenizer`, built from `vocab.json` and `merges.txt`, passes. So does any class that declares no such file (Known gaps).

### 028.D7. A Flock model can't get the conversion either

`BaseModel.get_hf_tokenizer` raises `ValueError` if its tokenizer's class is built on a SentencePiece model and no `tokenizer.json` is on disk, by the test in 028.D6. It is the one place training, `from_pretrained` and a checkpoint's scoring all pass through. A Flock model doesn't get `SentencePieceTokenizer` instead, because training needs a Tokenizers tokenizer, and a model scored with a different tokenizer from the one it trained with would be silently wrong.

The check reads only the cache or the local folder, which the load has just filled, so it adds no Hub call. It lives in `flock.training`, which can't import `flock.eval`, so the suffixes are written in both.

### 028.D8. A context that tokenizes to no ids starts from the prefix token, for every tokenizer

A non-empty context can tokenize to nothing, such as a lone zero-width space. With no context tokens, the first continuation token has no logit of its own to be scored from, so such a context now starts as an empty context does. Before this stage it raised when alone in a batch, and beside a longer request it silently scored that token from a padding position's logit, so this does change such a score, on a tiny model from -6.80 to -6.97. This is the suite's own choice, and no reference covers the case. 0 of TAIDE's 972,271 requests hit it. It isn't counted for other models, where a context would have to hold only characters their tokenizer drops.

---

## 3. Plan

1. `tokenizer.py`: `SentencePieceTokenizer`, and the `Tokenizer` type that `ScoringModel` and the prepared tasks accept.
2. `load_tokenizer` in `model.py`, called by `from_pretrained`. Flock models keep `get_hf_tokenizer`, which refuses a conversion.
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

- **`sentencepiece`'s version isn't in the provenance.** `tokenizers` is, and the lockfile pins this one at the recorded commit. A field for it would be a schema change.
- **The refusals read a tokenizer's class, and only some repos are caught.** With a SentencePiece model and no `tokenizer.json`, a config naming a class that declares no `.model` or `.spm` file, such as `GemmaTokenizer` or `GPT2Tokenizer`, passes `load_tokenizer` and `get_hf_tokenizer` and gets whatever that class builds. A repo with `tokenizer.model` whose config names a class that remaps ids, such as `CamembertTokenizer`, gets raw ids, because `tokenizer_config.json` isn't read. Both need a repo whose config disagrees with its files or its family, and no model the suite scores has one.
- **BOS and EOS come only from the SentencePiece file.** `tokenizer_config.json` isn't read (028.D4), so a repo that names a `bos_token` there and not in the model starts empty contexts and rolling texts from EOS, or raises if it has neither. TAIDE's file has both.
