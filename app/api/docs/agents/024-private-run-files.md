# 024: run files under `private/` stay with the organization's members

| | |
| --- | --- |
| Status | built |
| Date | 2026-11-21 |
| Builds on | [015](015-public-projects.md) (D110 to D113), [021](021-public-access-checks.md) (D152); eval's stage 041 (`flock/eval/docs/agents/041-result-files.md`: a private dataset's predictions on the run) |
| Amends | D112 (the run files routes answer outsiders except for `private/`), D113 (one more thing outsiders never see) |
| Keeps | D110, D111, D114 to D118, D151 to D157 |

## 1. Goal

`flock eval` uploads each task's predictions to the eval run, and some tasks read private datasets (exam sets, crawled text). Eval checks that such a run goes to a private project before it uploads (041.D4), but that is a snapshot: once the files are on the run, making the project public, or moving the run into a public project, serves them to anyone through the run files routes. The server keeps no mark of which files are private, and the wandb protocol's `CreateRunFiles` carries names only, so a client can't attach one.

This stage gives run files one rule that holds whatever happens to the project later: a file in a top-level `private/` folder is listed and served to the org's members only.

**Out of scope:**
- refusing to publish a project, or to move runs into a public one, while it holds such files;
- a count of members-only files in the publish dialog (D116);
- marking files from their content, such as reading a run's `results.json`;
- moving or renaming files already uploaded, and a route that deletes one run file.

---

## 2. Design decisions

The numbering continues from 023 (D171 to D180).

### D181. A run file whose first path component is `private` is members-only, whatever the project's visibility

`GET .../runs/{run}/files` leaves such files out for a viewer whose access is `public` (no name, size or time, since a name like `private/predictions/<task>.jsonl` says which private dataset a run read). `GET .../runs/{run}/files/{name}` answers such a viewer the same `file not found` 404 as a missing file, before storage is opened. Members, org admins and super admins read them as any other file, as `accounts.access` already answers (D111).

- **On the validated path components.** The check reads the first part of `store.file_parts(name)`, as D152 reads the metadata file's, so `./private/x`, `private//x` and an encoded `%2e/private/x` are refused too. The component is compared without case, since a local volume may be case-insensitive and serve `PRIVATE/x` from the same folder. `private-notes.txt` and `privatex/y` are ordinary files.
- **Moves and visibility changes.** The rule reads the name, not when or how the project became public, so a run moved into a public project, or a project published after the upload, keeps its `private/` files members-only. Moves stay within an organization, so the same members read them.
- **A name, not a column.** No precedent for file-level visibility was found in a wandb-compatible tracker: W&B has project visibility only. The nearest are this app's own read-path rules, D113 (fields outsiders never see on a public project) and D115/D156 (privacy flags on exact dataset paths, private winning on a move). A name needs no migration, no backfill, and no knowledge of any client's file format, and it holds however a correctly named file arrived. A column set from a run's `results.json` would tie the tracker to `flock.eval`'s schema and need a rule for uploads that race the results file. A refusal to publish or move would need the same mark to know what to refuse, would not cover a file uploaded to a project that is already public, and would span three routes that lock different rows.

---

## 3. Plan

1. The check in `list_run_files` and `get_run_file`.
2. A scenario test in `tests/app/test_public.py`: members and the super admin list and read the files; outsiders and anonymous visitors don't see them listed and get the missing-file 404 for every spelling of the path; a run moved into a public project keeps them members-only.
3. The run files section of the Projects API page and the publishing guide.

---

## 4. How it was actually built

- `PRIVATE_FOLDER = "private"` sits beside `PRIVATE_METADATA` in `runs.py`, shared by the two routes.
- `get_run_file` computes the path parts and the viewer's access once, refuses first, then runs D113's metadata filter on the same values.
- The new test failed with the routes as they were (the outsider's listing held the private file), and passes with the check.

## 5. Known gaps

- A file named without the prefix is not protected. Eval keeps its own check before the run (041.D4), so a private dataset's predictions still go only to a private project.
- Files uploaded before this release under another name are not moved. Eval's only such uploads were two check runs in a private project, deleted by hand.
- A server older than this release serves `private/` files like any other. The server must be deployed before clients upload under the prefix.
- The rule covers the run files routes. A route added later that reads a run's files on the server, such as one that diffs two eval runs, must apply it too before it answers an outsider.
- The case-insensitive match covers case only. A local volume that also ignores some Unicode characters in names (HFS+ on macOS) could serve the folder under a name the check reads as public. The deployed backend is S3, whose keys compare exactly, and the local backend runs on Linux.
- Previously downloaded copies can't be recalled, as for every other permission change (D157).
