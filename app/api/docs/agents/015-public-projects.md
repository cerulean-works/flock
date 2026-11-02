# 015: public projects and datasets, readable by anyone with the link

| | |
| --- | --- |
| Status | applied |
| Date | 2026-11-01 |
| Builds on | [003](003-organizations-and-auth.md), [011](011-auth-split.md), [013](013-datasets-tab.md) |
| Amends | D19 ("anyone else" gets a 404 for everything in an org), D24 (some read routes need no credential), D86–D88 (who can open a dataset page) |
| Keeps | D20–D23 (credentials, invites, the wandb protocol), D70–D72, D85, D87, D89–D96, and invite-only signup |
| Result | 940 tests in `tests` pass on moto (11 new: 8 in `tests/app/test_public.py`, 2 in `test_datasets.py`, 1 in `test_migrations.py`), and 541 in `app/web` (32 new); see [section 7](#7-how-it-was-actually-built) |

## 1. Goal

Everything in Flock is private to an org's members. Someone outside the org, signed in or not,
gets a 404. That makes it impossible to share a training run or an eval dataset with a
collaborator, a paper's readers or the public without inviting them into the org.

This stage adds read-only public access, the way a public Hugging Face repo works:

1. **Public projects** (D110). An org admin makes a project public. Anyone with its URL can then
   see its runs, charts, run pages, files and logs, without signing in.
2. **Public datasets** (D115). An org admin makes a single dataset public. Anyone with its URL
   can then open its dataset page (README, rows, schema and versions). The rest of the org's S3
   storage stays private.
3. **A public view of the org** (D114). An org's page lists its public projects and datasets to
   outsiders, and nothing else.
4. **The same view for everyone outside the org** (D111). A signed-in user who isn't a member sees
   exactly what an anonymous visitor sees.

Nothing changes for members: they see and do what they did before.

**Out of scope:**
- open signup. Accounts still come only from invites (D21);
- writing to public projects by outsiders: no forks, no logging, no comments;
- anonymous reads through the wandb SDK (`wandb.Api()`) or `flock.cli`. The REST API answers
  anonymous reads, since the web app uses it, but no client wraps it;
- anonymous access to S3, through the proxy or the Storage tab, including public datasets'
  objects;
- an explore page or any listing of public projects across orgs. Visitors find a project by its
  URL or from its org's page;
- per-project roles (a member who can only see some projects).

---

## 2. Design decisions

The numbering continues from 014 (the model registry, D97–D109).

### D110. Projects have a visibility

Migration `0016_public_projects.sql` adds
`projects.visibility text NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'public'))`.
Every existing project stays private.

- **In memory:** `Project.public: bool`, loaded with the project, as D2 keeps the rest. Checking
  it costs nothing per request.
- **Who changes it:** org admins (and super admins), with `PATCH /projects/{entity}/{project}
  {visibility}`, the route that renames projects. Members get 403, as they do for renames.
- **Event log:** a `visibility` event `{from, to, by}` on the project, so an org can see who
  published what and when.
- **Live streams:** going private closes every stream an outsider holds on the project (they get
  a `project` event `{"visibility": "private"}` first, as a rename sends `entity_renamed_to`).
  Members' streams are untouched.
- **Renames and moves.** Renaming a project keeps its visibility, and its old URL stops working
  as it does today. Moving runs from a private project into a public one publishes them: the
  move dialog says so when the target is public (D116).

### D111. Who is reading: a viewer, not always a user

Today every REST route depends on `rest_user`, which answers 401 without a credential. Read
routes for projects and datasets switch to a new dependency, `viewer`:

- **No credential at all:** the viewer is `None` (anonymous).
- **A valid browser session or CLI token:** the viewer is that user, as `rest_user` gives now.
- **A credential that's present but invalid or expired:** still 401, never a silent downgrade to
  anonymous. Otherwise an expired session would show a member the public view of their own
  project, with no hint to sign in again, and the web app's refresh flow would never start.
- **A wandb key:** still 401 with `WANDB_KEY_REFUSED` (D72), even on public projects. A wandb key
  only reaches the wandb protocol.

What a viewer can do with a project comes from one function, `accounts.access(viewer, project)`:

| Viewer | Private project | Public project |
| --- | --- | --- |
| super admin | `admin` | `admin` |
| org admin | `admin` | `admin` |
| member | `member` | `member` |
| signed-in non-member | none (404) | `public` |
| anonymous | none (404) | `public` |

`public` means read-only. A private project still returns the same 404 as an unknown one, so
its existence isn't revealed (D19 keeps holding for private things).

### D112. Which routes are readable, and which stay members-only

`ProjectDep` becomes `ReadableProjectDep` (any access) and `MemberProjectDep` (`member` or
`admin`). Each route picks one:

| Readable with `public` access | Members only (unchanged) |
| --- | --- |
| `GET projects/{e}/{p}` | `PATCH`, `DELETE projects/{e}/{p}` |
| `GET …/columns`, `GET …/runs`, `POST …/runs/query` | `PATCH`, `DELETE …/runs/{run}` |
| `GET …/history`, `POST …/history/query` | `POST …/runs/update`, `…/runs/delete`, `…/runs/move` |
| `GET …/runs/{run}`, `…/runs/{run}/history`, `…/runs/{run}/logs` | `POST projects` (create) |
| `GET …/runs/{run}/files`, `…/runs/{run}/files/{name}` | every wandb protocol route (D23) |
| `GET …/workspace`, `GET …/stream` | |

`POST …/runs/query` and `POST …/history/query` are reads that use POST for their bodies, so
they're readable.

The routers keep `rest_user` as a router-level dependency for everything else. The readable
routes move to a second router per module (`public` in `runs.py`, `dashboard.py`,
`projects.py`), the pattern `auth.py` and `avatars.py` already use.

### D113. What outsiders never see, even on a public project

The response shapes don't change. Some fields are emptied when access is `public`:

- **GPU cost.** `cost` in a run's details (`budgets.run_cost`) is `null`. Org budget, billing,
  warnings and the deleted-projects ledger stay members-only (they're org routes, D114).
- **Owners' emails.** `accounts.briefs(pks)` gains a `public` form, `{id, name, avatar_url}`
  without `email`. It's used for run owners in run details and the runs table. The `owner`
  column's filter still works by user pk.
- **Org members.** No route lists them to outsiders (D114).

Everything else the run logged is shown as members see it: config, summary, metrics, system
metrics, files and logs. Flock can't tell which config values or log lines are secret, so the
publish dialog says so (D116).

### D114. The org, as outsiders see it

- **`GET orgs/{org}`** answers outsiders with `{name, display_name, avatar_url}` when the org
  has at least one public project or public dataset, and 404 otherwise. Members get the full
  response, as today.
- **`GET projects?entity={org}`** lists the org's public projects to outsiders. Without
  `entity`, it lists only the viewer's own orgs' projects, as today, so there's no way to list
  public projects across orgs.
- **`GET orgs/{org}/datasets/public`** lists the org's public datasets: `{path, made_public_at}`,
  with each one's latest version read as the folder listing does (D87).
- **Members-only, unchanged:** members, invites, imports, billing, budget, budget warnings,
  deleted projects, the feed, the Storage routes (D63–D69) and the dataset folder browser
  (`datasets/at`).

### D115. Public datasets

Datasets aren't rows anywhere (D85), so the flag needs its own table. Migration 0016 adds:

```sql
public_datasets(org_pk REFERENCES orgs ON DELETE CASCADE, path text,
                made_by REFERENCES users ON DELETE SET NULL, created_at,
                PRIMARY KEY (org_pk, path))
```

Like visibility, it's loaded into memory at startup: a set of `(org_pk, path)`.

- **Who:** org admins, with `PUT` and `DELETE orgs/{org}/datasets/public?path=`. `PUT` checks
  that `path` is a dataset (D87), and gives 404 otherwise. Both write a `dataset_visibility`
  event.
- **The exact path only.** A public dataset doesn't make its folder or its siblings public. A
  dataset nested inside a public dataset's folder isn't looked for (D87), so it can't become
  public by accident.
- **Readable routes** for outsiders, when `(org, path)` is public: `info`, `rows`, `cell`,
  `versions` and `readme`, at any version or tag. Anything else (`at`, delete, rename, tags, the
  trash) stays members-only. An outsider asking for a private path gets 404, the same answer as
  for a missing one.
- **Renames carry the flag** (D95): the row's `path` is updated in the transaction that commits
  the rename, so a public dataset stays public at its new path.
- **Deletes drop the flag** (D93). A dataset restored from the trash comes back private, so an
  admin decides again whether to publish it.
- **S3 is unaffected.** The proxy (D54) never serves anonymous requests, and the bucket's
  objects stay reachable only with an S3 key. The dataset page reads rows through the API, so
  outsiders get rendered pages of rows, not the files.

### D116. The web app

- **Signed-out visitors aren't redirected** from project, run and dataset pages. `hooks.server.ts`
  lets `/{entity}`, `/{entity}/{project}/…` and `/datasets/{org}/…` through without a session.
  `flock()` sends no token when there's none. Everything else (home, account, admin, org
  settings, storage) still redirects to sign in.
- **A 404 while signed out goes to sign in,** with `next` set to the page, because the visitor
  may be a member whose project is private. A 404 while signed in shows the 404 page, as today.
- **The app shell for outsiders.** The layout loads `/me` and `/orgs` only when signed in. An
  outsider sees:
  - the org's name and picture at the top of the sidebar, with no org switcher;
  - the org's public projects in the sidebar, and no Datasets, Storage or Settings items;
  - a "Sign in" button where the account menu goes;
  - no run actions (edit, move, delete, bulk select), no budget cards, and no GPU cost on run
    pages.

  A signed-in non-member gets the same, plus their own account menu and org switcher.
- **The org page** (`/{entity}`) shows outsiders two lists: public projects, and public datasets
  linking to their dataset pages.
- **A "Public" badge** next to the project's name in its header, and on public datasets'
  headers, for members and outsiders alike.
- **Visibility settings,** for admins:
  - in the project's settings, a "Visibility" section with Private and Public;
  - on a dataset page, a "Make public" or "Make private" item in its menu.

  Making something public opens a `ConfirmDialog`: "Anyone with the link will be able to see
  every run in this project, including configs, files and logs. Check that none of them hold
  secrets." (for a dataset: "…see every version of this dataset."). Making it private needs no
  confirmation.
- **The move dialog** warns when the target project is public: "The runs you move will be
  public."

### D117. Load from outsiders

Public pages bring traffic that isn't bounded by an org's size. This stage takes no new
measures for it, because the reads are the ones members already make:

- reads are served from memory and the database, within 001's latency budgets;
- a dataset's row pages are already capped (D90), and the Lance cache (D94) serves repeats;
- each `stream` is one SSE connection on the event bus.

Rate limiting and a cap on anonymous streams are known gaps (section 4).

---

## 3. Plan

One PR, `feat/public-projects`, from main.

### Step 1. Migration

`0016_public_projects.sql`: `projects.visibility` and the `public_datasets` table (D110, D115).
Load both into memory in `store.py` and `core/datasets.py`.

### Step 2. Access

- `viewer` in `deps.py`, and `accounts.access(viewer, project)` (D111).
- `ReadableProjectDep`, `MemberProjectDep` and `ReadableRunDep`; the public routers in
  `projects.py`, `runs.py` and `dashboard.py` (D112).
- The public `brief`, and `cost: null` for outsiders (D113).
- `GET orgs/{org}` and `GET projects?entity=` for outsiders (D114).

### Step 3. Visibility

- `visibility` in `PATCH projects/{e}/{p}` and in `project_info`, the event, and closing
  outsiders' streams on going private (D110).
- `public_datasets`: the PUT, DELETE and list routes, the readable dataset routes, and carrying
  the flag through renames and dropping it on deletes (D115).

### Step 4. Web

- `hooks.server.ts`, `flock()` without a token, and the sign-in redirect on a signed-out 404.
- The outsiders' shell in `(app)/+layout.server.ts` and the sidebar.
- The org page's public lists, the Public badges, the Visibility section, the dataset menu items,
  the confirm dialogs, and the move dialog's warning (D116).
- Hiding run actions, budget cards and cost for `public` access. The layout passes the access
  level down as `shell.access` instead of `shell.admin` alone.

### Step 5. Tests

- `tests/app/test_public.py`:
  - every route in D112's left column, for anonymous and signed-in non-member viewers, on a public
    project (200) and a private one (404, the same body as an unknown project);
  - every route in the right column refused to outsiders on a public project;
  - an invalid or expired token on a public project giving 401, not the public view, and a
    wandb key giving 401;
  - `cost` null and owners without email for outsiders, present for members;
  - only admins changing visibility, the event, and an outsider's stream closed on going private
    while a member's stays open;
  - `GET orgs/{org}` for outsiders with and without public things, and `GET projects` without
    `entity` never listing other orgs' public projects;
  - runs moved into a public project becoming readable.
- `tests/app/test_datasets.py` additions: making a dataset public and private, a non-dataset path
  refused, the readable routes at a version and a tag, a sibling and a parent folder staying 404,
  `at` refused, the flag following a rename, dropped on delete, and absent after a restore.
- `tests/app/test_migrations.py`: 0016 on a database with projects, leaving them private.
- Web specs: the shell signed out and as a non-member, the sign-in redirect on a 404, the org
  page's lists, the badges, the Visibility section and its dialog, the dataset menu items, and
  the move dialog's warning.
- **Manual:** make a project with a live run public, open it in a private window and watch the
  charts update, check the run page, files and logs, and that no cost or email shows. Make it
  private and see the private window's stream end. Publish a dataset, rename it, and open its
  new URL while signed out.

### Step 6. Docs and status

- A "Sharing" page in the user docs: making projects and datasets public, what outsiders see,
  and the secrets warning.
- The API docs: which routes accept anonymous reads.
- Mark this file `applied`, with a "How it was actually built" section.

---

## 4. Known gaps after this stage

- **No rate limiting** of anonymous reads, and no cap on anonymous streams (D117).
- **No secret scrubbing.** Configs, files and logs are shown as logged (D113).
- **No anonymous SDK access.** `wandb.Api()` and `flock.cli` can't read public projects without
  a key (section 1).
- **Public datasets can't be downloaded** as files, since S3 stays private (D115). Rows can only
  be read a page at a time.
- **No discovery** beyond an org's page: no explore page and no search.
- **No link previews** (Open Graph tags) for shared URLs.

## 5. Settled before review

1. **Read-only public access, like a public Hugging Face repo.** Signup stays invite-only.
2. **Visibility is per project**, not per org (D110).
3. **Outsiders see runs, metrics, charts, files and logs** of a public project (D112), but **not
   GPU spend or owners' emails** (D113).
4. **Datasets are made public one at a time**, and **S3 stays private** (D115).
5. **Only org admins** change visibility, and **there's no explore page** (D110, D114).
6. **Signed-in non-members see what anonymous visitors see** (D111).

## 6. Open questions for review

1. **A signed-out 404 goes to sign in** (D116). A visitor can't tell a private project from a
   missing one, but they're asked to sign in for both. Hugging Face shows a 404 with a sign-in
   link instead.
2. **Owners' names stay visible** on public runs, only emails are hidden (D113). Should names go
   too, leaving only avatars?
3. **Live streams for outsiders** (D112). Dropping `stream` for outsiders would make public pages
   static until reloaded, but bound the load.

## 7. How it was actually built

The whole feature is one PR on `feat/public-projects`. The subsections below follow its parts:
the API, the web app and the checks. Each records the places where the code differs from the
decisions above, or adds to them. The open questions in section 6 were built as proposed: a
signed-out 404 goes to sign in, owners' names stay visible, and outsiders get live streams.

### 7.1. The API

- **The migration (D110, D115).** `0016_public_projects.sql` adds `projects.visibility` and
  `public_datasets` as planned, and a third table, `visibility_events`, for the event log. Each
  row has `org_pk`, then `project_pk` for a project or `path` for a dataset, with `kind`
  (`visibility` or `dataset_visibility`), `from_visibility`, `to_visibility`, `user_pk` and
  `at`. Projects had no event log that this could join. A row is written only when the
  visibility actually changes, so publishing twice logs one event.
- **Visibility in memory (D110).** `Project.visibility` is read with the project's other columns
  (`PROJECT_COLUMNS`), and `Project.public` is a property on it. `update_project` re-reads the
  locked row's visibility before writing, since another admin may have changed it since the
  request read the project.
- **Public datasets aren't cached (D115).** `Datasets.is_public` is one primary-key lookup per
  request instead of a set loaded at startup. That needs no invalidation across replicas (012),
  and dataset reads already go to S3, which costs far more. Members skip it, except in `info`,
  which reports `public`; an outsider's `info` doesn't ask twice.
- **Deletes outside the dataset routes drop the flag too (D115).** A dataset deleted through the
  Storage tab or the S3 proxy would otherwise leave its row, and a dataset written at that path
  later would start public. After any delete from flock-datasets, `drop_deleted_public` drops
  the flag of each public dataset the keys were in whose `_versions/` is now empty. Deletes
  made straight against the upstream S3, with the global key, aren't seen.
- **The viewer (D111).** `viewer` in `deps.py` returns None only when the request has no
  `x-api-key` and no `Authorization` with a value. Anything else goes through `rest_user`, so a
  bad token is 401 and a wandb key is `WANDB_KEY_REFUSED`. `accounts.access` returns `admin`,
  `member`, `public` or None, as in the D111 table.
- **Routers (D112).** `projects.py`, `runs.py` and `dashboard.py` have a `public` router as
  planned. So do `orgs.py` (for `GET orgs/{org}`) and `datasets.py`. `dashboard.py` has only the
  public router, since both of its routes are readable. `app.py` includes each public router
  before its module's `router`.
- **Additions to D112's left column:**
  - `GET projects/{e}/{p}` answers with the caller's `access` (`admin`, `member` or `public`).
    `PATCH` answers `admin` too. The web app reads the field to decide what to show.
  - `GET projects/{e}/{p}/owners` is new: the users who logged the project's runs, as briefs.
    Outsiders can't list the org's members, which the runs table and its owner filter used to
    name owners, so they get these instead, without emails. Members get them with emails.
  - `GET …/runs/{run}` gains `owner_user`, the owner's brief (public form for outsiders), so the
    run page doesn't need the members list either.
- **Members only, beyond D112's right column:** `GET …/runs/{run}/models` (the model registry
  is the org's, 014). The run page skips it for outsiders.
- **Streams (D110).** `Subscriber` has an `outsider` flag, set when the stream was opened with
  `public` access. Going private publishes a `project` event `{"visibility": "private"}` to
  everyone, then a close message with `outsiders: true`. Every replica's `Hub` then ends only
  the outsiders' streams. A member's stream gets the event and stays open, and the web app
  updates the badge from it.
- **The org (D114).** `GET orgs/{org}` answers outsiders with `role: null` beside the three
  fields, so the web app can tell the views apart with one call. `Store.has_public` checks for a
  live public project or any public dataset. `GET orgs/{org}/datasets/public` answers members
  too, and each entry carries `version` and `updated_at` as well as `path` and
  `made_public_at`. Without an S3 upstream it answers an empty list. Paths whose dataset is gone
  from the bucket (deleted outside Flock) are left out.
- **Datasets (D115).** `info` answers `public`, for the badge and the menu. `PUT public` checks
  for a dataset with the same `latest` lookup that folder listings use, under the dataset's
  lock. `DELETE public` on a path that isn't public changes nothing, and logs nothing. A rename
  moves the row inside the rename's lock. A delete drops the row in the transaction that moves
  the dataset to the trash. A restore deletes it again at the target path, in case an admin
  published a new dataset at that path meanwhile.

### 7.2. The web app

- **Signed-out access (D116).** `hooks.server.ts` names the pages that need a session
  (`SIGNED_IN_PAGES`: account, admin, orgs, storage, models, device, logout). Everything else
  at the top level, plus the `/api/projects` and `/api/datasets` endpoints, passes without
  one. Home (`/`) still redirects. `/datasets/{org}/…` passes, but the Datasets tab's folder
  browser is members-only in the API, so only dataset pages render for outsiders.
- **Signed-out 404s (D116)** go to sign in from two places. `flock()` redirects on a 404 from
  the API when there's no session, through a new `notFound()` next to `signedOut()`. The hook
  redirects when no route matched at all. `flockAnswer` returns the response without either
  redirect, for loads where a 404 only means "nothing public" (the org's outside view, its
  public datasets). Pages behind a universal load (the project page) redirect with 307 rather
  than 303. Both land on sign in with `next`.
- **The shell (D116).** `(app)/+layout.server.ts` loads `/me` and `/orgs` only when signed in.
  It passes `shell.access` (`admin`, `member` or `public`) instead of `shell.admin`, plus
  `shell.orgAvatar` for an outsider's sidebar header. `shell.me` is null when signed out, and
  the sidebar shows a Sign in button in place of the account menu.
- **Owners.** The project layout loads the org's members and, for `public` access, the
  project's owners (7.1). `People` and the filters accept owners without emails: a user
  filter's value reads back as the owner's name when there's no email.
- **The org page (D114)** renders `home/PublicOrg.svelte` (public projects, then public
  datasets) when the viewer is signed out or the org answers `role: null`. There's no feed.
- **Visibility.** The project settings (`ProjectDialog`) have a Visibility section for admins.
  Saving with Public chosen opens the `ConfirmDialog` with D116's text, which submits the
  form. `ConfirmDialog` gained a `done` callback so the settings dialog closes with it. The dataset page's menu has Make public…
  (confirmed) and Make private, and `PublicBadge` shows on both headers. `MoveRunsDialog`
  warns when the target is public.
- **Live events.** `ProjectEvent` gained `visibility`. On `private`, the project page toasts
  that it's no longer public and reloads its data. A member's page then shows it without the
  badge. An outsider's reload gets 404, which sends them to sign in (or to the 404 page when
  signed in).

### 7.3. Checks

- `tests/app/test_public.py` covers every item in step 5's list. The streams test opens an
  anonymous and a member stream, makes the project private, and checks that both get the event,
  only the anonymous one ends, the member's still gets history, and a reconnect is 404.
- The dataset tests cover publishing, a non-dataset path, a sibling and the parent folder
  staying 404, `at` refused, reads at a version and a tag, a rename carrying the flag, a delete
  dropping it, and a restore coming back private.
- Web specs: `hooks.spec.ts`, `flock.spec.ts`, `layout.spec.ts`, `sidebar.svelte.spec.ts`,
  `projects.svelte.spec.ts`, `project.svelte.spec.ts`, with additions to `runs.svelte.spec.ts`
  and `datasets.svelte.spec.ts`. `WorkspaceHarness.svelte` mounts a component inside the
  workspace with given page data, so specs can set `shell.access`.
- **Manual, against a local API and web server on PostgreSQL 14** (no Docker, so no S3). With
  no cookies: an org with a public and a private project showed only the public one on its
  page. The public project and its run page rendered, with the owner's name but no email or
  cost. The private project, an unknown org and `/account` redirected to sign in with `next`.
  An anonymous `stream` received history, then the `project` event, then ended when the
  project was made private. A member's stream on the same project kept receiving history.
  Public datasets weren't checked by hand, since that needs an S3 upstream. The moto-backed
  tests above cover them.
