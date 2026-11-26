# 026: run groups, and comparing the best of each

| | |
| --- | --- |
| Status | proposed |
| Date | 2026-11-23 |
| Builds on | [012](012-postgres-only.md) (D77: `run_values` and the filter compiler), [015](015-public-projects.md) (D110 to D116: what outsiders see), [018](018-workspace-panels.md) (D129 to D137: panels, live updates), [019](019-bulk-select.md) (bulk selection) |
| Amends | nothing |
| Keeps | the Runs page as it is: its table, charts, filters and URL |

## 1. Goal

A project with 70 runs is hard to read. A learning-rate sweep, two ablations and a pile of
ungrouped runs share one table. Each run carries ~50 config keys, of which only one or two
explain a result. Today you answer "which batch size won?" by filtering and sorting by hand.
Then you read past `save_dir`, `wandb_id` and 40 keys that are the same in every run.

This stage adds **groups**: saved sets of runs, in their own area beside Runs. A group:

- is a list of runs, chosen with filters, by hand, or both; runs that match its filter
  later are offered, never added for you;
- shows only the columns that vary inside it, plus its metric;
- names its best run by one metric and direction, with seeds averaged when it has them;
- can be compared against the other groups and against ungrouped runs on one page.

Flock suggests groups from W&B groups and config differences. An assistant can
set filters and pick runs from plain-language requests. It runs on OpenAI's Decisions API
([D224](#d224)), which returns typed answers, never text, so every change it makes is an
ordinary filter or hand pick that you can see and undo.
When you create groups, a second model proposes them for the whole project, and you refine
them in a chat ([D229](#d229)). Flock recomputes everything it shows about each proposal from
the runs.

Mockups: the "Groups v3" page of the design canvas
(<https://claude.ai/artifact/Bj31Nm42Rin7SAFhXh1LNP>; private, ask for access).

**Out of scope:**
- changes to the Runs page, including a group-by mode in its table;
- W&B sweeps: Flock doesn't support them yet, so they are neither launched nor used to
  suggest groups;
- parameter importance and parallel coordinates;
- groups spanning projects;
- "best" over a metric's history (minimum over all steps): best reads the summary value;
- a heatmap panel for two-axis sweeps (Split by and Color by cover it for now, [D222](#d222));
- free-text answers from the assistant, such as an explanation of a result.

---

## 2. Design decisions

The numbering continues from 024 (D181).

### D215. Groups are their own area beside Runs

The project's sidebar panel gains **Groups** under **Runs**. Its children are always
expanded, in this order:

1. each saved group, with its color and run count;
2. **Ungrouped runs**, the runs in no saved group;
3. **New group**;
4. suggested groups (dashed, with a **Suggested** label; [D219](#d219)).

Clicking **Groups** opens Compare ([D221](#d221)). Clicking a group opens that group
([D222](#d222)).

The Runs page doesn't change. It stays the place to see everything that was logged, in the
order it was logged.

- **Why not a mode of the Runs page.** An earlier mockup put groups inside the Runs table
  as collapsible sections. It mixed two jobs, browsing everything logged and reading one
  experiment, on one page with one column set. Each group needs its own columns
  ([D218](#d218)), and the Runs table has one header.

### D216. A group is a list of runs; new matches are offered, never added for you

A group stores:

- **its runs**, by id. This list is the group. Nothing changes it except you.
- **its filter**: the Mongo-style filter it was built with, the same JSON that
  `POST .../runs/query` takes and `query.py` compiles (D77). It may be empty.
- **when it last loaded runs**: set at creation and at each load.
- **dropped runs**: runs you removed. They are never offered again.

When the group is created, its runs are the ones the filter matched then, plus the runs
ticked by hand, minus the ones unticked.

**Loading new runs.** Later, runs that match the filter and are neither in the group nor
dropped are *new matches*. They are runs logged since the last load, and older runs that
match only now (a run that was `running` when the group was made and has since finished).
The group's header shows **N new matches** when there are any. It opens the edit page
([D223](#d223)) with those runs marked and unticked. You tick the ones to add, or **Add
all**. The load time then moves to now.

- **Why not join automatically.** A group is a record of an experiment as you meant it. If
  a group changed whenever a matching run was logged, a stray debug run in the same W&B
  group would move its best and its averages without anyone noticing. Offering new matches
  keeps the filter useful without letting it edit the group.
- **Adding and dropping by hand** works at any time, from the edit page or from **Add to
  group** ([D220](#d220)). A run added by hand needn't match the filter.
- Reading a group's runs is the runs query with `{"name": {"$in": <its runs>}}` as the
  filter, so the existing query path serves it unchanged.
- A run can belong to several groups.
- A deleted run leaves its groups. A run moved to another project leaves them too, since a
  group belongs to one project. Membership rows cascade on delete. A move removes them in the
  same transaction.
- A group with no filter never has new matches. It changes only by hand.

### D217. "Best" is one summary column and a direction

Each project has a default **best by** setting: a `summary.*` column and `lowest` or
`highest`. A group can override it. The first time a project needs one, the default is the
first of these it has: `summary.val/loss`, `summary.eval/loss`, `summary.loss` (lowest).
Otherwise the setting stays unset until someone picks one, and Compare asks for it.

- The value is the run's summary as logged: the last value, unless the client set
  `define_metric(summary=...)`.
- NaN and infinities are never best. A run without the column is never best.
- **Seeds.** When a group has a repeat key ([D218](#d218)), runs that share every other
  varying value form one *configuration*. A configuration's value is the mean over its runs,
  shown as `mean ± std`. Best is then the best configuration, not the luckiest seed.

### D218. Columns are sorted into four kinds, from the group's own runs

For a set of runs, each `config.*` key falls into one kind. The kind is computed in one SQL
pass over `run_values`: distinct values, runs with a value, and the largest number of runs
sharing one value.

| Kind | Rule | Shown |
| --- | --- | --- |
| **Varies** | 2 or more distinct values, and some value is shared by 2 or more runs. Also numbers that are distinct in every run (a random search samples `lr` per run). | as a column |
| **Repeat** | named `seed`, `*_seed`, `random_state` or `rng`, or set to repeat by hand | as `± std` ([D217](#d217)) |
| **Same everywhere** | 1 distinct value, present in every run | hidden behind **+N hidden** |
| **Different every run** | strings or lists distinct in every run (paths, ids, timestamps) | hidden behind **+N hidden** |

- A key missing from some runs and the same in the rest counts as **Varies**, with the
  missing value shown as `—`.
- You can drag a key between kinds. The override is saved on the group.
- `summary.*` columns other than the best-by column are hidden by default. You add them
  with **Columns**.
- **Why rules and not the model.** The rules are cheap, explainable and the same every
  time. No product reviewed (W&B, Neptune, ClearML, DVC) hides paths or seeds automatically.
  DVC's `--only-changed`, which hides constant columns, is the closest. The one case rules
  get wrong is a string axis that happens to be unique in a small group, such as 3 runs
  with 3 `data_mix` values. A group with fewer than 4 runs treats unique strings as
  **Varies**.

### D219. Groups are suggested from W&B groups and config differences

Suggestions come from these sources, in order. A run is placed by the first source that
claims it:

1. **W&B group:** runs sharing a `group` attribute, set with `wandb.init(group=...)`.
2. **Config differences:** the runs left are clustered greedily. Two runs fall in one
   cluster when their configs differ in at most 2 keys, ignoring **Different every run**
   and **Repeat** keys. A cluster needs at least 3 runs.

A W&B group suggestion's filter is `group is <name>`. A config-difference cluster has no
filter: its runs are hand picks. Saving a suggestion creates the group with the
suggestion's runs.

- **No sweeps.** Flock doesn't support W&B sweeps yet. Runs imported from W&B can carry a
  `sweep` attribute, but it isn't used here. When sweeps are supported, a sweep can become
  the first source. Dismissing one remembers its signature, the sorted run ids, so it isn't
offered again unless its runs change.

Suggestions are computed when Groups is opened, not stored. Each one is recomputed when the
project's run count or newest update changes.

### D220. Ungrouped runs are listed, ranked, and added to groups by hand

A run in no saved group is ungrouped. Compare lists ungrouped runs in their own section, ranked
by the same metric as the groups, so a strong ungrouped run isn't missed. It shows the best 3
and a **Show N more** link.

Each ungrouped row has an **Add to group** button. It opens a searchable list of every group
with checkboxes, plus **New group with this run…**. Nothing is pre-checked or recommended.
An earlier mockup suggested the nearest group by config distance. It was dropped: the
suggestion was right only when the user already knew where the run belonged.

### D221. Groups opens on Compare: the best of each group, side by side

Compare is one table with a row per group, best first:

- the group's best run, or best configuration when it has repeats;
- its value, the change from the best group's, `± std` across seeds, and its run count;
- a **Suggested** section, roomier and ended by a bolder divider as in a group's table
  ([D222](#d222)): the settings and metrics that matter for comparing the groups. They're
  the union of each group's primary columns (`GET .../groups/{group}/primary`, [D224](#d224)),
  ranked by how many groups pick them, settings then metrics, at most 4 of each. Settings are
  the config keys that differ between the rows; a metric cell is the best run's value. The
  page shows without them, and while they load or if they fail the table has no section;
- the other config keys that differ between the rows' best runs. Cells that differ from the
  best group's are highlighted. The keys that don't differ are hidden while **Differences
  only** is on, the default.

Every group's best-by setting must match the page's. A group with a different metric shows
its row greyed, with the metric named.

- **Why no reference.** An earlier version pinned a reference run at the top (the oldest
  finished run, else one picked) and measured every row against it, in the table and as a
  line in both charts. It was dropped: the groups are what's compared, and the reference
  was rarely the run anyone meant. The change is from the best group, which the ranking
  already names.

Compare has no assistant or chat bar. An earlier mockup put one at the bottom as a
shortcut into creating a group. It was dropped: the assistant lives only on the create and
edit page ([D223](#d223)), where its changes are visible in the table.

### D222. A group shows its runs with the Runs page's view

A group's page is the Runs page's view (`RunView`: the table, the charts, full-screen
panels, smoothing, the panel editor, per-run visibility, search, filters, paging) pointed at
the group's members, which it reads from `GET .../groups/{group}/workspace`, the same
snapshot the Runs page reads. There is one implementation of the view; the group adds a
coloring hook (`Workspace.style`, #lib/chart/grouping.ts) and a primary section in the table.

- **Views:** **Table**, **Visualize** (the table on the left, the charts on the right; a
  group's default) and **Charts**. The Runs page has all three too.
- **Header:** the name with a **⋯** menu (**Edit group…**, which opens the editor of
  [D223](#d223) in a dialog, and **Review N new matches…**), the filter in words, the run
  count, **N new matches** as a quiet link when there are any ([D216](#d216)), **Best by**
  and the live status.
- **Table:** a **Primary** section first, roomier, with a light scroll snap where it ends:
  the columns `GET .../groups/{group}/primary` names, else the **Varies** columns, then the
  best-by column. The rest of the table follows at its usual density.
- **Charts:** one panel per metric, as on the Runs page. The toolbar adds:
  - **Group by**: one **Varies** key. Each value gets a hue of its own (blue, orange, green,
    violet, …), and every run of it is drawn in it.
  - **Color by**: another key. Within a group, runs are shaded faint to strong by its value's
    rank among all of the key's values (numbers ascending), so a trend inside a group reads
    at a glance. Without it, a group's runs share its hue.
  - **Repeats**, only when some runs repeat a configuration with another seed: **Average
    (± std)**, one mean line with a shaded band, or **Each run**.

  Panel legends name the groups (or the Color by values) when there are 8 or fewer. By
  default a group is grouped by the Varies key with the fewest values (2 to 8) and colored
  by the one with the most; with one varying key, it is colored by it.
- Group by, Color by and Repeats are saved on the group (`view`: `split`, `color`, `seeds`);
  the panel layout and hidden runs per group in the browser, starting from the project's.

### D223. Creating and editing a group show the whole table

**New group** opens a page with every run in the project:

- **Filter:** the workspace's filter popover, with the applied filters shown as chips.
- **Checkboxes** tick runs in or out. The rows have three looks:
  - matched by the filters: blue;
  - added by hand: green;
  - matched but unticked by hand: struck through.

  The checkbox carries in or out; the look only says why, so it needs no column of its own.
- A footer counts each kind. A toggle shows **All runs** or **In group** only.
- The assistant panel sits on the right ([D224](#d224)). Its left edge drags to resize it
  (280–640px, kept in a cookie like the sidebar's width). An empty thread offers three
  requests to start from, as clickable cards, drawn from the project where it says enough:
  a state its runs are in, a varying config number and a middle value, a run to add.
- **Create group · N runs** saves the ticked runs as the group's list, and the filter with
  them. The name is editable in the title and starts from a template ([D225](#d225)).

**Edit group** and **N new matches** open the same editor for an existing group, in a dialog
over the group's page, so saving lands back on the page it was opened from. The
`.../groups/{id}/edit` URL still renders the editor as a page, for links and reloads:

- its runs are ticked;
- new matches ([D216](#d216)) are unticked, amber, and labeled **new match**, with **Add
  all** in the footer;
- dropped runs are struck through, labeled **dropped**, and can be ticked back in.

Changing the filter here changes only which runs are offered as new matches. It never
removes a run already in the group. **Save** records the ticked runs and moves the load time
to now.

### D224. The assistant picks from candidates Flock lists, through the Decisions API

The assistant uses OpenAI's Decisions API (`POST /v1/decisions`, model `gpt-6-luna`). It
answers typed questions about one input:

- a `predicate` (a probability);
- a `choice` (one of 2 to 255 values, with a probability for each);
- a `score`.

A request can ask up to 400 questions about the same input. OpenAI describes it as about
10× faster than the Responses API. It can't return free text or JSON, and it can't ask
the user anything back.

So Flock lists the candidates and the model picks. A request runs in two calls, since a
later answer depends on an earlier one:

1. **What to do.** The input is the request, the current filters and the column list.
   - A `choice` question picks the action: `filter`, `add runs`, `leave out runs`, `set best
     by`, or `none`.
   - For each candidate column, a `predicate` asks "does the request constrain this
     column?". The candidates are the 50 columns with most runs, plus any column whose name
     shares a word with the request, up to 380 questions.
2. **Values.** For each column picked (probability ≥ 0.5), a `choice` picks the operator
   and a `choice` picks the value:
   - text, state and boolean columns: up to 255 observed values;
   - numeric columns: their observed distinct values, or quantiles when there are more
     than 255.

   `add runs` and `leave out runs` choose among run names, narrowed to 255 by substring
   match first.

Each reply becomes an action card in the panel: **Set filters** with chips, **Added by
hand** with run names, or **Set best by**. Each card has **Undo**. Manual edits are logged
in the same thread.

- **No clarifying questions.** The top choice is applied. When it wins with less than 0.8,
  its chip shows the probability. Clicking the chip lists the other readings by
  probability. If nothing reaches 0.5, the card says nothing was changed.
- **Sent to OpenAI:** the request, column names, observed values of the candidate columns,
  and run names. Run histories, files and logs are never sent.
- **Off by default.** The assistant needs `FLOCK_OPENAI_API_KEY` set, and an org admin to
  turn it on for the org (org setting `assistant_enabled`). Otherwise the panel isn't
  shown and everything else works. Outsiders on a public project never see it.
- The call is made by the API, never the browser, so the key stays on the server.
- **Primary columns.** A group's table shows a few columns first, wider: one request asks,
  per varying config key and summary metric of its runs, "is it one of the 3 to 6 columns
  someone comparing these runs would look at first?". Those at ≥ 0.5 are kept, most likely
  first, at most 6, always with the best by column; they're kept per replica until the
  group's runs, best by column or kinds change. With the assistant off, or when OpenAI
  fails, they're the varying keys and the best by column.

### D225. Group names come from templates

The Decisions API can't write a name. A new group is named from its **Varies** keys and
filters:

- `lr × batch_size` for two keys;
- `qk_norm on / off` for one boolean;
- `data_mix: web-v2, web-v3` for one key with up to 3 values.

`, finished` is added when the filter fixes `state`. A suggestion from a W&B group starts
with the group's name. The name is editable on the create page and in the group's header.

### D226. Storage

An expand migration (`0026_run_groups.sql`) adds:

```sql
CREATE TABLE run_groups (
    pk bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_pk bigint NOT NULL REFERENCES projects ON DELETE CASCADE,
    name text NOT NULL,
    color text NOT NULL,
    filter jsonb NOT NULL DEFAULT '{}',
    loaded_at timestamptz NOT NULL DEFAULT now(),   -- D216: the last time runs were loaded
    best_key text,              -- null: the project's default
    best_order text CHECK (best_order IN ('asc', 'desc')),
    repeat_key text,
    columns jsonb NOT NULL DEFAULT '{}',   -- D218 overrides: {"config.x": "varies", ...}
    view jsonb NOT NULL DEFAULT '{}',      -- D222 split/color/seeds and panel layout
    created_by bigint REFERENCES users ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (project_pk, name)
);
CREATE TABLE run_group_runs (
    group_pk bigint NOT NULL REFERENCES run_groups ON DELETE CASCADE,
    run_pk bigint NOT NULL REFERENCES runs ON DELETE CASCADE,
    status text NOT NULL CHECK (status IN ('member', 'dropped')),
    added_by text CHECK (added_by IN ('filter', 'hand')),   -- null for dropped runs
    changed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (group_pk, run_pk)
);
CREATE INDEX run_group_runs_run ON run_group_runs (run_pk);
CREATE TABLE run_group_dismissals (
    project_pk bigint NOT NULL REFERENCES projects ON DELETE CASCADE,
    signature bytea NOT NULL,   -- sha256 of the sorted run pks
    PRIMARY KEY (project_pk, signature)
);
ALTER TABLE projects ADD COLUMN best_key text, ADD COLUMN best_order text;
ALTER TABLE orgs ADD COLUMN assistant_enabled boolean NOT NULL DEFAULT false;
```

- `run_group_runs` stores run pks, so a renamed run stays in its groups. The `$in` filter
  of [D216](#d216) is built with the runs' current ids.
- `added_by` is kept so the edit page can show how a run joined. It doesn't change what the
  group means.
- The index on `run_pk` serves the ungrouped query ([D220](#d220)), "runs in no group".
- **Permissions** follow runs': org members create and edit groups, and outsiders on a
  public project read them (D110). A group's filter and name are shown to outsiders like
  run names are.

### D227. API

| Route | Does |
| --- | --- |
| `GET .../groups` | groups with counts, best run and value; the ungrouped count |
| `POST .../groups` | creates a group from a name, filter, its runs and settings |
| `PATCH .../groups/{group}` | changes the name, filter or settings, adds runs, drops runs, and can move the load time to now |
| `GET .../groups/{group}/new` | the group's new matches ([D216](#d216)) |
| `DELETE .../groups/{group}` | deletes the group (its runs stay) |
| `POST .../groups/{group}/runs/query` | the runs query (`RunsQuery`) over the group's runs |
| `GET .../groups/{group}/workspace` | the workspace snapshot of the group's runs, for its page's table and charts ([D222](#d222)) |
| `GET .../groups/{group}/columns` | each config key's kind and distinct count ([D218](#d218)) |
| `GET .../groups/{group}/primary` | the columns its table shows first, from the model or the analysis ([D224](#d224)) |
| `GET .../groups/compare` | Compare's rows and differing keys ([D221](#d221)) |
| `GET .../groups/suggestions` | suggested groups ([D219](#d219)) |
| `POST .../groups/suggestions/dismiss` | dismisses one |
| `POST .../groups/assist` | one assistant turn: the request and the draft in, an action out ([D224](#d224)) |
| `POST .../groups/propose` | one turn of the proposals chat: the conversation and the proposals on screen in, the model's reply and checked proposals out ([D229](#d229)) |

`...` is `/projects/{entity}/{project}`. Every read route is on the `public` router, and
every write route needs membership. `groups/assist` answers `404` when the assistant is
off. `groups/{group}/primary` never fails because of OpenAI: it falls back to the analysis.
Only members' visits ask the model; the group's name and filter, and its columns' names and
a few values, are sent.

### D228. Live updates change values and counts, never a group's runs

The project's live events (D129) already announce new runs, removed runs and new metrics.
On those events:

- group pages and Compare refresh their values, such as a running member's loss;
- **N new matches** in a group's header, and the count beside the group in the sidebar,
  are recounted.

A new matching run is never added to a group by an event ([D216](#d216)).

### D229. Proposals: a chat with gpt-6.2-sol

**New group** gains an **Auto** tab, and opens on it. When it opens, a model proposes how to
group the project's runs, as cards. You refine them in words ("split the lr sweep by
optimizer", "leave out crashed runs", "merge those two"), and accepting a card creates its
group. The manual editor of [D223](#d223) stays as the other tab.

It uses OpenAI's Responses API with a strict JSON schema, model `gpt-6.2-sol`
(`FLOCK_OPENAI_PROPOSAL_MODEL`), at medium reasoning effort. One turn is one request:

- **Sent:** the project's live runs, one line each: id, name, state, W&B group, creation
  time, which saved groups hold it, the config keys that vary across the project, and the
  best-by value. Before them: each varying key with its values and their counts (a range for
  numbers with more than 12), the seed keys, the summary metrics' names, the W&B groups, the
  saved groups (name, filter, size), and the suggestions of [D219](#d219) as hints. Then the
  conversation, with the proposals on screen just before the latest request. Run histories,
  files and logs are never sent.
- **Big projects:** past ~40,000 characters of runs (about 300 runs of 20 keys), keys with
  more than 20 values leave the table, then runs with equal lines merge into one line with a
  count, then the last lines are cut. The model is told the table is summarized and to use
  filters.
- **Returned:** a message of 1 to 3 sentences, and every proposal that should be on screen,
  each with an id, a name, a sentence on what it compares, a filter or a hand list, the
  order of its axes, and a metric only when the user asked for one.
- **Asked for:** groups that each compare one controlled change (a sweep of one or two
  settings, a toggle, an ablation; a random search counts as one), seeds together, one-off
  runs left out rather than forced in, no saved group again, filters rather than hand lists
  where a clean one exists, names in the style of [D225](#d225).

Everything shown about a proposal is computed by Flock from the runs it ends up with, never
taken from the model:

- a filter's runs are what it matches now; a refused filter falls back to the hand list;
  unknown ids are dropped (display names are resolved);
- a proposal of fewer than 2 runs, or with the same runs as another proposal or a saved
  group, is dropped;
- axes are its **Varies** keys ([D218](#d218)), the model's order first; with 1 or 2 axes
  of at most 24 values, a grid shows each cell's runs and best, so an unfinished sweep shows
  its holes;
- best, seeds and the repeat key come from [D217](#d217) and [D218](#d218).

Accepting a proposal is `POST .../groups` with the body the proposal carries (`create`): its
runs, and its filter, so runs logged later are offered as new matches ([D216](#d216)).

- **Why a second model, and why text.** The Decisions API ([D224](#d224)) picks among listed
  candidates; it can't write a name, explain a group in a sentence, or answer "merge those
  two" about cards it can't see. Proposals are few, and they're read before anything is
  saved, so a slower model that writes is worth it here. A turn takes 5 to 15 s on the seeded
  projects (90 s timeout). `low` effort halves it, but its names drift from the templates.
- **Why stateless.** The conversation lives in the page. Nothing to store, expire or protect,
  and a reload starts over, which is what an onboarding screen should do.
- **Why the server recomputes.** A card that shows the model's claims would be wrong in the
  ways that matter: a filter that matches other runs than it says, a sweep that isn't one.
  The model chooses; Flock measures.
- **Off like the assistant:** it needs the key and the org's `assistant_enabled`, and answers
  403 otherwise.

### D230. Each org picks its proposal model and tier

An org admin sets, under **Run groups assistant** on the org's page, the switch of
[D224](#d224), the model that proposes groups ([D229](#d229)) and OpenAI's service tier for
it. `orgs.assistant_model` (migration `0027_assistant_settings.sql`) is an OpenAI model ID, or
null for the server's `FLOCK_OPENAI_PROPOSAL_MODEL`. `orgs.assistant_tier` is `standard`,
`fast` or `ultrafast`. Standard sends no tier. The other two go in the request body's
`service_tier` and in the `OpenAI-Service-Tier` header, which must match it.

Only proposals use the tier: they are the slow requests (4 to 16 s a turn), while the
Decisions API's answers are already quick. Flock doesn't check that a model offers a tier;
OpenAI refuses the request, and the Auto tab shows the error. `GET /orgs/{org}` also returns
`assistant_default_model` and `assistant_available` (whether the server has a key), so the
page can say what an empty model means and why the assistant stays off.

---

## 3. Plan

1. Migration `0026_run_groups.sql` ([D226](#d226)).
2. `core/groups.py`:
   - a group's runs, and its new matches ([D216](#d216));
   - the column kinds query ([D218](#d218));
   - picking a configuration's and a group's best ([D217](#d217));
   - suggestions ([D219](#d219)).

   Tests in `tests/app/test_groups.py`:
   - creating from a filter, by hand, and both;
   - a matching run logged later is offered, not added;
   - a run that matches only after it finishes is offered;
   - a dropped run is never offered again;
   - changing the filter never removes a member;
   - deletes and moves;
   - every kind rule, including a random search and a 3-run group;
   - seeds, and NaN is never best;
   - both suggestion sources, and a run's `sweep` attribute is ignored.
3. API routes ([D227](#d227)) and their access tests in `test_public.py`.
4. Web:
   1. the sidebar's **Groups** section ([D215](#d215));
   2. Compare, with the ungrouped section and **Add to group** ([D220](#d220), [D221](#d221));
   3. the group page: table, then charts with Split by and Color by, then seed bands in
      `LinePlot` ([D222](#d222));
   4. the create and edit page ([D223](#d223)).
5. The assistant:
   1. `settings.openai_api_key` and the org setting;
   2. the Decisions client with the two-call flow ([D224](#d224));
   3. the panel's action cards and Undo.

   Tests mock the API and check which candidates and questions are sent, the thresholds,
   and that nothing is sent when the assistant is off.
6. Docs: a guide page `docs/source/app/groups.md`, covering groups, compare, the assistant
   and what it sends to OpenAI. Also new routes in the Projects API reference.
7. Proposals ([D229](#d229)): `core/proposals.py` (the prompt, the Responses client, the
   checks), `POST .../groups/propose`, the Auto tab. Tests in `tests/app/test_proposals.py`
   with a mock Responses API, and the route in `tests/app/test_groups.py`.

---

## 4. Known gaps

- **Best reads the summary.** A run that peaked early and finished worse is ranked by its
  final value, unless its client set `define_metric(summary="min")`. A history-reduced best
  (min, max or last over steps) needs the history store and is left for later.
- **The assistant's numeric values are observed values or quantiles**, not any number. "lr
  below 5e-4" picks the nearest observed value as the bound. The chip shows it, and it can be
  edited.
- **Projects with more than ~380 columns:** the assistant sees a subset ([D224](#d224)). A
  column outside it can't be named in a request, only set with **Filter**.
- **Not verified with OpenAI:** whether a shared input is billed once or once per question,
  the cache-read price, and rate limits. These decide the cost of a request with hundreds of
  questions. Measure them before turning the assistant on for every org.
- **Two-axis sweeps** read as a grid of panels, not a heatmap ([D222](#d222)). A heatmap
  panel is the obvious next panel type.
- **Runs page entry point:** adding the selected runs to a group from the Runs page's
  selection bar would be natural. It is left out because the Runs page doesn't change in this
  stage ([D215](#d215)).
- **New matches come only from the filter.** A related run that doesn't match it, such as a
  rerun with a new W&B group name, isn't offered. It has to be added by hand, or the filter
  widened on the edit page.
- **A member that stops matching stays.** A run in a `state is finished` group that is
  later resumed and running stays in the group until it is dropped by hand. This is
  deliberate ([D216](#d216)).
- **No sweep suggestions** until Flock supports W&B sweeps ([D219](#d219)).
- **A proposal's runs can go stale before it's accepted.** It's created with the runs it
  showed and its filter. A run logged in between matches the filter but isn't listed, so the
  create route drops it ([D216](#d216)) and it's never offered. Rare on an onboarding screen;
  the fix is a create flag that leaves unlisted matches as new matches.
- **The model's words aren't checked.** Its name and sentence can claim an axis the runs
  don't vary on. The axes, values, grid and best shown beside them are right.
