# 040: score a local model folder by the digest of its files

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-19 |
| Builds on | [014](014-run-provenance.md) (provenance), [018](018-bank-schema6.md) (`model_digest` in results), [021](021-compare-changed-suites.md) (`compare` refuses another `model` or `model_digest`), the hub's [014-model-registry](../../../../app/api/docs/agents/014-model-registry.md) (D98: a version's digest) |
| Keeps | every score, request, task hash and suite; Hugging Face and Flock model ids load and are recorded as before (their remote code, which none uses, is now refused; a Flock model's tokenizer loads as before); uses the existing results schema |
| Result | a `local:folder@digest` model id, checked against the folder's files before anything loads and recorded as `local:digest` with the digest in `model_digest`; `flock eval digest` prints the id; a local folder named as `repo@revision` is refused |

## 1. Goal

A training run's export is a folder in the Hugging Face format, and it should be scored and recorded as exactly as a Hub commit or a Flock version. Until now a folder could only be passed as `repo@revision`. A full 40-hex revision is taken as pinned without asking anyone, so any 40 hex characters passed, the folder's files were never checked, and the results recorded the path with `model_digest: null`. Nothing in the file said which weights were scored, and changing the folder's files after a run left its id the same.

**Out of scope:** how Hugging Face and Flock models are pinned; rescoring results written under the old form.

## 2. Design decisions

### 040.D1. A local folder is named by a digest of every file in it

The id is `local:folder@digest`, where the digest is the sha256 of one `{path}\t{size}\t{sha256}\n` line per file under the folder, sorted by path as strings. A folder is refused when the loader could read something the digest never saw: it holds a link, a folder that can't be read or a file that isn't regular (a pipe would also hang the read), its weights index or tokenizer config names a file outside it (`../` or an absolute path, which transformers joins to the folder and opens), or it is an adapter, whose base model loads from wherever its config says. A Hugging Face or local model's tokenizer and weights load with `trust_remote_code=False`, since code that a config's `auto_map` fetches from another repo is pinned by neither a revision nor a digest; no model the suite scores needs it. A file name with a control character is refused too, as the registry refuses it, so two file lists can't write the same lines. `save_pretrained` writes none of these. This is the form the Flock registry uses for a version's digest (D98), so one rule names model files across Flock. It covers every file, not only the weights: the config and tokenizer change scores as much as a weight does. `flock eval digest folder` prints the id, and a mismatch prints the folder's actual digest.

*Prior work:* the registry's digest (D98); a Hub commit hash, which also names every file of a repo. No evaluation harness we follow pins a local folder: lm-evaluation-harness and OLMES record the path as given.

### 040.D2. The digest is checked before anything loads, and a mismatch refuses to score

`ModelRef.resolve` hashes the folder and compares it with the digest in the id, as it resolves a Flock tag to its number before anything loads. A folder whose files changed since its id was printed, or an id copied from another folder, fails in seconds with both digests in the message. A folder given as a link is resolved to its target first, so a `latest` link moved after the check can't switch the folder that loads. Once the tokenizer and weights have loaded, the folder is hashed again and the run is refused if it changed, so files rewritten during the load are never scored under the old digest.

### 040.D3. The results record `local:digest`, without the path

`provenance.model` is `local:{digest}` and `model_digest` is the digest. The path says where the files were, not what they are: the same export copied to scratch or another node is the same model, and `compare` should compare it. Two different folders give two different ids, so `compare` refuses them as different models. The path stays in `provenance.command`.

### 040.D4. A folder named as `repo@revision` is refused

A Hugging Face id whose repo is an existing folder is refused, with the `local:` id to use in the message. Transformers loads a folder of that name before asking the Hub, so the old form scored unchecked local files under a Hub-looking id. Only a `local:` model reads its files from a folder now.

## 3. Plan

1. Add the `local` kind to `ModelRef`, with parsing, the digest and the check in `resolve`.
2. Load a local model with `AutoModelForCausalLM` and `load_tokenizer`, passing the digest to `ScoringModel`.
3. Add `flock eval digest`, the docs, and tests on a tiny saved model.

## 4. How it was actually built

- **Review.** Independent review rounds found that the first version loaded the folder without checking it again; missed files reached through links, unreadable folders, an index or tokenizer config naming files outside the folder, or an adapter's base model; refused a tokenizer config listing versions it never saved, and read it in the locale's encoding; accepted pipes; could run code another repo supplies; and broke the local cases of the tokenizer tests, which built a Hugging Face ref for a folder. Each is fixed and tested. What the digest still does not guard is under Known gaps.
- **One model path.** A local model loads through the same `AutoModelForCausalLM` and `load_tokenizer` calls as a Hugging Face model; `_repo_file` reads a local model's files from its folder by its kind, no longer by whether a folder of the repo's name exists.
- **Tests.** On a tiny Llama saved with its tokenizer: the digest equals D98's formula over the same files, including the string order of `a-b` before `a/c`; a folder resolves to the same id twice and from a copy; one flipped bit in `model.safetensors` is refused; the old form is refused with the right id; `from_pretrained` loads the saved weights under `local:{digest}`; and `evaluate`, run twice from the id `flock eval digest` printed, records the same `model` and the digest in `model_digest`.

## 5. Known gaps

- Hashing reads every file twice more, before and after loading. One hash of a 190M export (1.07 GB) took under 30 seconds on a login node, imports included; the second mostly reads from the page cache.
- Links inside an export are refused. When scoring, the export folder itself may be a link: it is resolved to its target before anything is hashed or loaded. `flock eval digest` hashes the path as given, so retarget no link while it runs.
- The digest guards an export against accidental change, not against a folder built to mislead it. Transformers has more ways for a hand-written config to name a file outside the folder than the two checked here: a differently named weights index chosen by `transformers_weights`, or a tokenizer's `gguf_file`. `save_pretrained` writes neither, and neither is checked.
- The folder is read three times: hashed, loaded, hashed again. A file swapped out during the load and restored before the second hash would be scored unnoticed, and so would a file rewritten in place after the second hash while scoring on the CPU in the weights' own dtype, where transformers can keep reading a memory-mapped shard. So an export should be published by renaming a finished folder and never rewritten, or scored only once it is finished; the suite scores in BF16 on a GPU, which copies the weights. Loading from a private copy verified against the digest would close both, at the cost of copying every export.
- Results written before this stage with a folder as `repo@revision` keep that id and a `null` digest. They compare with neither a rerun under the new id nor each other's folders unless the paths match.
