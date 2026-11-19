# 040: score a local model folder by the digest of its files

| | |
| --- | --- |
| Status | in review |
| Date | 2026-11-19 |
| Builds on | [014](014-run-provenance.md) (provenance), [018](018-bank-schema6.md) (`model_digest` in results), [021](021-compare-changed-suites.md) (`compare` refuses another `model` or `model_digest`), the hub's [014-model-registry](../../../../app/api/docs/agents/014-model-registry.md) (D98: a version's digest) |
| Keeps | every score, request, task hash and suite; Hugging Face and Flock model ids load and are recorded as before; the results schema stays 8 |
| Result | a `local:folder@digest` model id, checked against the folder's files before anything loads and recorded as `local:digest` with the digest in `model_digest`; `flock eval digest` prints the id; a local folder named as `repo@revision` is refused |

## 1. Goal

A training run's export is a folder in the Hugging Face format, and it should be scored and recorded as exactly as a Hub commit or a Flock version. Until now a folder could only be passed as `repo@revision`. A full 40-hex revision is taken as pinned without asking anyone, so any 40 hex characters passed, the folder's files were never checked, and the results recorded the path with `model_digest: null`. Nothing in the file said which weights were scored, and changing the folder's files after a run left its id the same.

**Out of scope:** how Hugging Face and Flock models are pinned; rescoring results written under the old form.

## 2. Design decisions

### 040.D1. A local folder is named by a digest of every file in it

The id is `local:folder@digest`, where the digest is the sha256 of one `{path}\t{size}\t{sha256}\n` line per file under the folder, sorted by path as strings. This is the form the Flock registry uses for a version's digest (D98), so one rule names model files across Flock. It covers every file, not only the weights: the config and tokenizer change scores as much as a weight does. `flock eval digest folder` prints the id, and a mismatch prints the folder's actual digest.

*Prior work:* the registry's digest (D98); a Hub commit hash, which also names every file of a repo. No evaluation harness we follow pins a local folder: lm-evaluation-harness and OLMES record the path as given.

### 040.D2. The digest is checked before anything loads, and a mismatch refuses to score

`ModelRef.resolve` hashes the folder and compares it with the digest in the id, as it resolves a Flock tag to its number before anything loads. A folder whose files changed since its id was printed, or an id copied from another folder, fails in seconds with both digests in the message. The files are read once more to load, so a file rewritten between the check and the load is not caught; exports are written once and not rewritten.

### 040.D3. The results record `local:digest`, without the path

`provenance.model` is `local:{digest}` and `model_digest` is the digest. The path says where the files were, not what they are: the same export copied to scratch or another node is the same model, and `compare` should compare it. Two different folders give two different ids, so `compare` refuses them as different models. The path stays in `provenance.command`.

### 040.D4. A folder named as `repo@revision` is refused

A Hugging Face id whose repo is an existing folder is refused, with the `local:` id to use in the message. Transformers loads a folder of that name before asking the Hub, so the old form scored unchecked local files under a Hub-looking id. Only a `local:` model reads its files from a folder now.

## 3. Plan

1. Add the `local` kind to `ModelRef`, with parsing, the digest and the check in `resolve`.
2. Load a local model with `AutoModelForCausalLM` and `load_tokenizer`, passing the digest to `ScoringModel`.
3. Add `flock eval digest`, the docs, and tests on a tiny saved model.

## 4. How it was actually built

- **One model path.** A local model loads through the same `AutoModelForCausalLM` and `load_tokenizer` calls as a Hugging Face model; `_repo_file` reads a local model's files from its folder by its kind, no longer by whether a folder of the repo's name exists.
- **Tests.** On a tiny Llama saved with its tokenizer: the digest equals D98's formula over the same files, including the string order of `a-b` before `a/c`; a folder resolves to the same id twice and from a copy; one flipped bit in `model.safetensors` is refused; the old form is refused with the right id; `from_pretrained` loads the saved weights under `local:{digest}`; and `evaluate`, run twice from the id `flock eval digest` printed, records the same `model` and the digest in `model_digest`.

## 5. Known gaps

- Hashing reads every file once more before loading. A 190M export (1.07 GB) took under 30 seconds on a login node, imports included.
- Results written before this stage with a folder as `repo@revision` keep that id and a `null` digest. They compare with neither a rerun under the new id nor each other's folders unless the paths match.
