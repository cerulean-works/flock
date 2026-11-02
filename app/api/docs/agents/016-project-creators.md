# 016: a project's creator decides whether it's public

| | |
| --- | --- |
| Status | applied |
| Date | 2026-11-02 |
| Builds on | [015](015-public-projects.md) |
| Amends | D110 (who changes a project's visibility) |
| Keeps | D111 to D117, and admin-only renames, descriptions and deletes |
| Result | The 44 tests in `test_projects.py`, `test_public.py` and `test_migrations.py` pass (2 new: 1 in `tests/app/test_public.py`, 1 in `test_migrations.py`), and 543 in `app/web` (1 new); see [section 5](#5-how-it-was-actually-built) |

## 1. Goal

Since 015, only org admins can make a project public or private (D110). A member who logs a
project's runs, and knows best whether they can be shared, has to ask an admin each time.

This stage lets the member who created a project change its visibility. Everything else about
the project stays with admins: renaming it, changing its description and deleting it.

Flock had no notion of a project's creator. Only runs have an owner, and a project's "owners"
(`GET …/owners`) are everyone who logged a run in it. Letting any of them publish would let one
teammate publish everyone's runs, so this stage records a single creator per project.

**Out of scope:**
- transferring a project to another creator, or more than one creator;
- per-project roles beyond this one right;
- dataset visibility, which stays with admins (D115).

---

## 2. Design decisions

The numbering continues from 015 (D110 to D117).

### D118. A project has a creator, who may change its visibility

Migration `0017_project_creators.sql` adds
`projects.created_by bigint REFERENCES users ON DELETE SET NULL`.

- **Who the creator is.** The user whose request created the project: `POST /projects`, the
  first run logged to it through the wandb protocol (`wandb.init` creates projects), a run move
  that creates its target, or a W&B import (the admin who started it).
- **Existing projects.** Nothing recorded who created them, and most were created by a run's
  upload. The migration credits each to the owner of its earliest run (by `created_at`, then
  `pk`). Projects without an owned run get no creator, so only admins change their visibility.
- **Who changes visibility.** `PATCH /projects/{entity}/{project}` now takes any member's
  project (`MemberProjectDep`). An admin may send every field, as before. A member gets 403 unless
  they're the creator and the body has no field but `visibility`. Outsiders still get 404.
- **A deleted account** leaves the project without a creator, as it leaves runs without an owner.
- **The API shows it.** Project info gains `created_by`, the creator's user ID or null, for the
  web app to decide whether to offer the control. User IDs are already public through
  `GET …/owners` (D113), so outsiders learn nothing new.
- **The web app.** The project dialog shows **Visibility** to admins and to the creator. For a
  creator who isn't an admin, **Name** and **Description** are disabled with a note that only
  admins can change them, and the form sends neither.

---

## 3. Plan

1. Migration and `Project.created_by`, read with `PROJECT_COLUMNS`.
2. Pass the creator to `create_project`, `ensure_project` and `import_project`.
3. Split the permission in `update_project`.
4. Web: `ProjectInfo.created_by`, the dialog, and the `update` action.
5. Tests and docs (`permissions.md`, `share-publicly.md`, `api/projects.md`).

## 4. Known gaps after this stage

- The backfill guesses. A project an admin created in the dashboard before this stage, whose
  first run a member logged, is credited to that member.
- The project menu still offers **Edit project…** to members who can change nothing, as before.

## 5. How it was actually built

Built as planned, on `feat/project-creator-visibility`.

- **Creation paths.** `_insert_project` writes `created_by`, and `create_project`,
  `ensure_project` and `import_project` take a keyword-only `creator`. `upsert_run` passes the
  run's owner, so the wandb protocol records whoever logged the first run.
- **The permission** is in the route rather than a new dependency, since only this route mixes
  the two rules. `PATCH` answers `access` as `admin` or `member` instead of always `admin`.
- **The web form.** The `update` action sends `description` only when the form has it. A
  disabled field isn't submitted, so a creator's form sends just `visibility` and the current
  name (which isn't sent on, as it's unchanged).
- **Checks.** All four CI gates pass. The Python tests ran against PostgreSQL 18.4 under
  Apptainer through `FLOCK_TEST_DATABASE_URL`, as the login node has no Docker. The web tests
  ran with Node 22 and pnpm 12.4.1 (the Dockerfile's).
