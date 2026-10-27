# 006: basic skills from a parquet mirror

| | |
| --- | --- |
| Status | in review |
| Date | 2026-10-26 |
| Builds on | [003](003-base-easy.md) (tasks as data, `Dataset` pinned to a revision) |
| Keeps | D16 (a doc lists its choices as pairs), D17 (few-shot examples), D22 (how parity is measured) |
| Result | basic skills' six tasks load from `cerulean-works/basic-skills` with no change to `Dataset`; every basic skills request string identical to OLMES's; every other task's requests and hash unchanged |

The file number and the decision number are provisional. The file takes the next free number when its PR is opened, and `006.D1` takes the next free global number after every stage merged before it.

## 1. Goal

`allenai/basic-skills` has no configurations: it has a loading script, which `datasets` no longer runs, and six `<skill>/validation.json` files the script used to read. The six basic skills tasks of Base Easy (003's task table, and its section 2.2 on loading scripts) need those rows at a pinned revision.

**Out of scope:** running loading scripts, and reading raw files of other dataset repos.

---

## 2. Design decisions

### 006.D1. A dataset with a loading script is mirrored as parquet, not read by a special loader

`cerulean-works/basic-skills` holds the six skills as configurations with one `validation` split each, written by `datasets` from `allenai/basic-skills` at `faf63e9` (ODC-BY, as the source). A task then reads it like any other dataset, with `subset` set to the skill.

The first version added `Dataset.data_file`, one JSON file of the repo read in place of its configurations. Review preferred a dataset with proper Hub formatting to a second loading path in `Dataset`: the field needed its own check against `subset`, a split check on the task, and its own download branch, and every task's hash changed to make room for it. A mirror costs one upload per dataset and keeps `Dataset` as it was.

---

## 3. Plan

1. Mirror the six files: each read with the `json` builder, as the loading script did, and pushed as its own configuration.
2. `tasks/basic_skills.py`: six tasks reading the mirror at revision `849126c520dd18b3d07ada2b4716ebde7c3ef50e` (public, ODC-BY), built from `allenai/basic-skills@faf63e97`.
3. Check the mirror's rows equal the source files', row for row.

---

## 4. How it was actually built

- **The `data_file` detour.** The field was built, tested and reviewed first (see section 2). Removing it returns every task's hash to its value before the field.
- **Rows.** For every skill, the mirror's rows at the pinned revision equal the source file's rows read with the `json` builder, and every basic skills request string is identical to OLMES's.
