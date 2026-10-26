# 006: one JSON file of a dataset repo

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-24 |
| Builds on | [003](003-base-easy.md) (tasks as data, `Dataset` pinned to a revision) |
| Keeps | D16 (a doc lists its choices as pairs), D17 (few-shot examples), D22 (how parity is measured) |
| Result | `Dataset.data_file`; basic skills' six tasks load; every ARC-Easy request string unchanged, its hash changed once |

The file number and the decision number are provisional. The file takes the next free number when its PR is opened, and `006.D1` takes the next free global number after every stage merged before it.

## 1. Goal

`Dataset.load` reads a repo's configurations through `datasets.load_dataset`. `allenai/basic-skills` has no configurations: it has a loading script, which `datasets` no longer runs, and six `<skill>/validation.json` files the script used to read. The six basic skills tasks of Base Easy (003's task table, and its section 2.2 on loading scripts) need a way to read one of those files at the pinned revision.

**Out of scope:** running loading scripts, and any dataset format other than one JSON file per split.

---

## 2. Design decisions

### 006.D1. `Dataset.data_file: str | None = None`

A `Dataset` may name one JSON file in its repo. `load` then downloads that file at the pinned revision into the Hub cache with `hf_hub_download` and reads it with the `json` builder, so a cached file loads offline, as the configurations do. The file holds only the scored split, so asking `load` for another split raises in `datasets`, and a task whose examples come from another split raises at definition time. A `data_file` takes no `subset`, since the file replaces the configuration.

The field sits on `Dataset`, not on a new class, because everything else about such a dataset (the repo, the revision, the split, the row index) is the same. The alternative, a second loader type, would make the task definitions branch on it.

**The task hash changes once.** The hash covers every field of `Dataset` (D16), so every task's hash changes when the field is added, ARC-Easy's included, with every request string unchanged. This is the same kind of change as D16's and is accepted once here.

---

## 3. Plan

1. `tasks/base.py`: the field, its check against `subset`, the download-and-read branch of `load`, and the few-shot split check.
2. `tasks/basic_skills.py`: six tasks reading `<skill>/validation.json`.
3. Tests: the download and read calls, the subset check, the split check.
4. Docs: `adding-a-task.md` names the field.

---

## 4. How it was actually built

- **Offline first.** The first version read the file through an `hf://` path, which failed with the Hub offline. `hf_hub_download` into the Hub cache loads offline once the file is cached, which the parity jobs need.
- **The split check.** `Dataset.load` could not tell a wrong split from a missing one, so the check moved to the task: a `MultipleChoiceTask` whose dataset is a `data_file` raises when its `Fewshot.split` is another split.
- **Lock.** All 19,002 ARC-Easy request strings are byte-equal before and after the field. The task hash moved from `ad683d6e` to `268be941`, once, as 006.D1 says. Every basic skills request string is identical to OLMES's.
