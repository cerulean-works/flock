# flock-ui

The dashboard for [Flock](../api): a home feed of each
organization's activity beside its GPU budget, and a W&B-style workspace per project where the runs table and the charts share a single run list,
with running runs updating live. It follows
the "Paper" prototype in `mockup/`, restyled to match cerulean.works as described in `design.md`
(Host Grotesk throughout, a pure white canvas, flat cool panels).

```sh
# from this directory: start the API and PostgreSQL
docker compose -f ../api/compose.dev.yaml up -d --build

# here
pnpm install
pnpm dev                 # http://localhost:5173
pnpm build && pnpm preview
pnpm test                # unit tests
```

**First start.** A fresh Flock instance has no accounts, and refuses everything until one exists
(its log says so). Open the dashboard: it shows **Set up Flock**, where the first account is
created. That account is the super admin. The second screen asks them to name and create
their organization; leaving setup and signing in later resumes at that screen. Its **Runs** page (in the sidebar) walks you through creating an API key
and logging a first run with the wandb SDK. After that the page is closed;
everyone else signs in, and joins from an invitation an admin gives them: open an
organization's **Settings** (in the sidebar's organization menu) and create an invite link. Flock sends
no email, so you pass the link on. More organizations are created on the **Super admin** page
(`/admin`, in the sidebar's organization menu).

The SvelteKit server talks to Flock; the browser only talks to SvelteKit. Configure it with:

| Variable               | Default                 | Purpose                                                                                               |
| ---------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------- |
| `FLOCK_API_URL`        | `http://127.0.0.1:8080` | Flock, as this server reaches it                                                                      |
| `FLOCK_API_PUBLIC_URL` | `FLOCK_API_URL`         | Flock as the wandb SDK on your machines reaches it, for the `wandb login --host` line the setup shows |

Set the API's `FLOCK_WEB_URL` to this dashboard's public origin for invitation, password reset
and Google sign-in links. The development Compose file defaults it to `http://localhost:5173`.
For a production web build, supply the same `FLOCK_WEB_URL` at build time:

```sh
FLOCK_WEB_URL=https://flock.example.com pnpm build
```

### Against a hosted Flock

To work on the dashboard with real data, point the dev server at a deployed API instead of a
local one:

```sh
pnpm dev:remote          # http://localhost:5173, against https://flock-api.cerulean.works
FLOCK_REMOTE_API_URL=https://api.flock.example.com pnpm dev:remote   # another deployment
```

It only sets `FLOCK_API_URL` for `vite dev`; plain `pnpm dev` still uses the local API. The
browser still talks only to `localhost`, which keeps the session cookies, so sign in with an
email and password of an account on that deployment. Google sign-in returns to the
deployment's own dashboard (its `FLOCK_WEB_URL`), not to `localhost`. Everything you do
changes that deployment's data.

## Docker

[`../compose.yaml`](../compose.yaml) runs the dashboard, API and PostgreSQL for deployment.
It publishes no host ports; route the dashboard domain to `ui:3000` and the SDK domain to
`api:8080` through your deployment service or reverse proxy. Configure `FLOCK_WEB_URL`,
`FLOCK_API_PUBLIC_URL` and `POSTGRES_PASSWORD` in `app/.env`, then build from `app` with
`docker compose up -d --build`. `FLOCK_VERSION` tags both images.

Without Compose, build with `docker build --build-arg FLOCK_WEB_URL=https://flock.example.com
-t flock-ui .` and run with `FLOCK_API_URL` and `FLOCK_API_PUBLIC_URL`. The image listens on
3000 and sets `BODY_SIZE_LIMIT=4M` for avatar uploads. Changing `FLOCK_WEB_URL` requires a
rebuild; the API addresses are read at runtime. The dashboard always requires a signed-in account.

See [Environment variables](../api/docs/source/deploying.md#environment-variables) for all
variables, their consumers and migration from previous names.

## Node.js security updates

Both build and runtime stages pin Node.js **24.22.1** to include the
[October 21, 2026 security fixes](https://nodejs.org/en/blog/vulnerability/october-2026-security-releases),
including CVE-2026-111497 in Brotli decoding. The Node server makes outbound
requests to the API, so response decompression is relevant. The Python API and
PostgreSQL do not use this Node runtime. A cached `node:24-slim` image can predate
the fixes; the major tag alone does not establish that an installation is patched.

From `app/`, after updating the checkout:

```sh
docker compose build --pull ui
docker compose up -d --no-deps --force-recreate ui
docker compose exec ui node --version
docker compose ps ui
```

Expect `v24.22.1`, then verify sign-in and an API-backed page. Rebuilding and
recreating the UI is required to patch an existing deployment. Production
runtime versions still need verification on the deployment host.

## Styling

Components come from [shadcn-svelte](https://shadcn-svelte.com) (`src/lib/components/ui`), set
up with the "nova" preset (`components.json`), whose padding and rounding are kept as generated
(`--radius: 0.5rem`; cards are flat and square). Every color is Cerulean's instead:
`src/routes/layout.css` maps the palette from `design.md` onto shadcn's semantic tokens
(`background` = white, `card` = Panel, `foreground` = Ink, `muted-foreground` = Slate, `border` = Quiet rule, `ring` = Focus blue,
`primary` = Reading blue for small functional emphasis) and adds the roles shadcn has no slot for
(`faint`, `divider`, `selection`, `link`, `state-*`). Type is Host Grotesk (`font-sans`, linked from
Google Fonts in `app.html`) everywhere; there is no serif. All styling is Tailwind utilities; there are no component `<style>` blocks.

```sh
pnpm dlx shadcn-svelte@latest add <component>   # aliases already point at #lib
```

## What is implemented

- **Sidebar**: every signed-in page has it. It shows the organization's projects (most recently
  updated first, with a live dot for running runs, a filter and a **+** for a new project) and
  an organization switcher at the bottom, which also sets your default organization. On home it
  links to the organization's runs; inside an organization it is **Home** and the projects,
  swiping in from the right. The organization menu at the bottom also opens its settings and
  members, and holds your account: its page and API keys, super admin, and sign out. Pages
  have no header bar of their own. On wide windows the sidebar hides to a narrow rail (home and
  the same menu; remembered in a cookie); on phones a top bar opens it as a drawer.
- **Home** (`/`): the activity of the organization in the sidebar (runs started, finished,
  failed or crashed, and projects created), everything or only your runs, polled every 30
  seconds. On the right is its budget for a UTC month: what's left, spend by project, the five
  most expensive runs, and GPU types with no price. The arrows show earlier months.
- **Runs** (`/{entity}`): an organization's latest runs, everyone's or yours, with project,
  state, owner, GPUs, runtime and start time. New projects are created from here or the
  sidebar; a project is renamed or deleted (typing its name to confirm) from its own header.
- **Changing runs**: select runs in the table (checkboxes, `Esc` clears) to tag, move or delete
  them together, or use a run's `⋯` menu to edit its name, tags, group, job type and notes.
  Active runs can't be moved, and are deleted only when you tick "delete anyway"; a project
  can't be renamed while it has active runs (Flock's rules: live clients address runs by
  path).
- **Accounts**: first-run setup (`/setup`), sign in with email and password or Google
  (`/login`, shown when Flock has Google configured), sign out, and an account page
  (`/account`) with your picture, your organizations and your API keys for the wandb SDK. See
  [Sessions](#sessions).
- **Pictures**: people and organizations show their avatar wherever they appear (feed, runs,
  members, budget, menus, invitations), and their initials without one. Upload a PNG, JPEG, GIF
  or WebP of up to 1 MB on your account page (or take your Gravatar again); org admins set
  their organization's in its settings. Flock serves the images; this server passes
  `/api/v1/avatars/*` through to it, without credentials and cached for a year.
- **Organization settings** (`/orgs/{org}`): everyone sees the members; admins invite people
  by link (as member or admin), withdraw invitations, change roles, create password reset links,
  remove members, change the organization's display name (its ID, shown read-only, never
  changes) or delete it. Members can leave. Its **Budget**
  section sets the monthly budget, the currency and a price per GPU hour for each GPU type
  (admins; members see them). Links land on
  `/invite/{token}` (create an account, or join with the one you're signed in to, by password or
  Google) and `/reset/{token}`.
- **Super admin** (`/admin`): every organization and account; create organizations, make or
  remove super admins, create reset links, delete accounts. Organization names that are the
  dashboard's own pages (`admin`, `login`, `orgs`, …) are refused, since they couldn't be opened.
- **Runs table**: every run in the project, 100 per page (25 to 500 from the pager's menu,
  kept in the URL as `?size=`), with every run attribute, `config.*`
  and `summary.*` column. Rows and columns are both virtualised;
  sticky name column and header, column groups, server-side sort on any column, resizable
  columns, jump to a column group. Running runs are marked **Live**.
- **Charts**: one panel per logged metric in collapsible, paginated sections (grouped by the
  prefix before `/`), each plotting every visible run on the page. System metrics (CPU, GPU,
  memory, ... as sampled by wandb) follow in a **system** section, plotted against runtime with
  clock-friendly ticks. Synced crosshair across
  panels (see Chart tooltips), hover-to-focus the nearest run everywhere, click a line to pin its run, eye
  toggles to hide runs, drag to zoom (y auto-fits the zoomed window), double-click to reset.
  Drag a panel by its header to move it within its section (or use its grip and the arrow
  keys), and drag its bottom edge to resize the section's panels (double-click resets). The
  arrangement is kept per project in this browser, and a run's page uses it too.
- **Run page** (`/{entity}/{project}/runs/{run}`, from a run's name in the table): its
  **Charts** (every logged and system metric, as the project's panels), **Overview** (state,
  times, who logged it, host, command, commit, GPUs and their cost, tags, notes, and
  searchable config and summary tables), **Logs** and **Files** (opened or downloaded through
  this server: plain text and images open, anything else downloads, and nothing in them runs).
- **Logs**: the console output, the last 1,000 lines first (earlier on request), followed live
  while the run runs. Lines are rendered rather than dumped: a run of metric dicts with the same
  keys (as Hugging Face's Trainer prints them) is one table, a lone dict is key/value chips,
  tqdm bars are progress bars, a Python traceback is one block headed by its exception, log
  levels are badged (warnings and errors tinted, the logger's own prefix muted), ANSI colors
  show, stderr's line numbers are tinted, and long lines wrap. Search filters and highlights;
  timestamps show as UTC time, time since the run started, or not at all; "Show lines as
  printed" turns the rendering off. Copy takes the lines shown, download all of them.
- **Live updates**: points logged by running runs, and their system metric samples, appear on
  screen as they arrive, summary
  columns and run states update in the table, the header counts running runs, and runs created
  after the page loaded are offered as a refresh ("2 new runs · Show").
- **Chart tooltips**: hovering one chart shows a tooltip in every chart that follows it, with
  each run's value at the crosshair step, sorted, windowed around the focused run, with raw values
  shown next to smoothed ones. Charts of logged metrics follow each other whatever their x axis
  (`_step`, a step metric, a time): the hovered x is turned into each run's history row, and
  every other chart reads each run at that row's x on its own axis. System metric charts follow
  charts on their own axis. The hovered chart's tooltip floats over the page beside the cursor,
  so it never hides the lines being hovered in a small card; the others stay inside their chart.
- **Full screen**: per panel, deep-linkable (`?panel=train%2Floss`), server-rendered on reload,
  ←/→ to move between panels, Esc or back to close, full run legend.
- **Settings**: smoothing (debiased EMA), show original under smoothing, ignore outliers, panels per
  page; per panel log scale, reset zoom, PNG and CSV export.
- Keyboard: `T` table, `C` charts, `Esc` closes full screen / unpins.
- **Filter** is present but intentionally inert. Search, Ask ARIA, sweeps and reports are
  removed.

## How data gets to the page

Everything a page shows arrives in the server-rendered HTML, and no request ever waits on another.

| Step            | What happens                                                                                                                                                                                                                                                               |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Document        | `+page.ts` makes one request: Flock's workspace snapshot for the page (`GET /api/v1/projects/:entity/:project/workspace`), which carries the column schema, the runs and every metric's curves together. SvelteKit inlines the response into the HTML.                     |
| `<head>`        | Every JS chunk and both stylesheets are preloaded from the document (Host Grotesk comes from Google Fonts behind a preconnect), including SvelteKit's root error node, which its client start awaits but does not preload by default (`hooks.server.ts`).                  |
| Hydration       | The load re-runs against the inlined response, so no data is fetched. The snapshot travels as base64 text for this one request and is decoded natively (`Uint8Array.fromBase64`); SvelteKit's generic binary path is a per-byte JS loop that cost ~300 ms with a cold JIT. |
| After hydration | The live stream opens for the runs on the page; idle-time prefetch of the previous and next page; hover-intent prefetch on sort headers; SvelteKit hover preload on pager links.                                                                                           |
| Navigation      | Paging and sorting fetch exactly one binary snapshot. View switches and full-screen panels only change URL params no load reads, so they fetch nothing.                                                                                                                    |

The snapshot (`FLK2`, decoded in `src/lib/data/snapshot.ts`) is one binary blob: a JSON header (runs,
column schema, metric names, text cells), then Float64 numeric cells, then per-series curves
downsampled to fit a point budget. Evenly stepped series (the usual case) carry no x values; the
client recomputes them from Flock's integer sampling formula. SvelteKit's own preload
cache holds only one entry, so `src/lib/data/cache.ts` keeps an LRU of decoded pages that loads,
preloads and prefetches share.

The project's own record (its description) comes from a layout load (`(app)/[entity]/[project]/+layout.ts`),
so it runs alongside the snapshot rather than after it, and paging or sorting doesn't re-run it.
The sidebar's organizations and projects come from the `(app)` layout's server load, which
re-runs only when the organization in the URL changes. Home and Runs each have a single server
load: the feed's first page (and, on home, the budget), invalidated by polling.

A run's page has one server load (`(app)/[entity]/[project]/runs/[run]/+page.server.ts`): its
curves, then the run itself (so the live stream resumes from its line counts), who logged it
and the organization's currency. `src/lib/data/run.ts` makes it a workspace of one run, so the
project's panels, full screen and live updates work on it unchanged.

The SvelteKit server proxies these flock endpoints (`src/routes/api/...`): the project list,
one project, the snapshot, the live stream, further feed pages for **Show more**, and a run's
logs, file list and files, so Flock's address and key stay server-side.

## Sessions

Flock's sign-ins (`../api/docs/agents/003-organizations-and-auth.md`) give an
access token that lasts 15 minutes and a refresh token that works once. Both are kept by the
SvelteKit server, in httpOnly cookies (`src/lib/server/session.ts`); page scripts never see them.

- `hooks.server.ts` reads the session on every request. When the access token has expired it
  refreshes it before the request goes on. Requests that arrive together with the same refresh
  token (a page and its data) share that one refresh. Requests to Flock then carry the
  token.
- Signed out, pages redirect to `/login?next=<page>`, and this server's API answers 401. The
  sign-in page sends a fresh server's visitor on to `/setup`, and returns to `next` afterwards.
- A session that ends elsewhere (sign-out, password change, removed user) makes Flock
  answer 401: the cookies are dropped, and the page asks you to sign in again.
- Google sign-in runs in Flock. This server proxies `/api/v1/auth/google/*` to it, so
  browsers never need to reach Flock. `/auth/google` exchanges the code Flock sends
  back for a session.

## Changes

Changes are SvelteKit form actions that call Flock from the server
(`change()` in `src/lib/server/flock.ts`): project create, update and delete in
`(app)/[entity]/+page.server.ts`, which the workspace header and the sidebar post to as well, and run edit, tag, move
and delete in `(app)/[entity]/[project]/+page.server.ts`. Budget and prices are saved in `(app)/orgs/[org]/+page.server.ts`, and the switcher posts to home's `workspace` action. Every dialog submits through a `Mutation`
(`src/lib/data/mutation.svelte.ts`):

- Names are checked in the browser with Flock's own rule before anything is sent, and again
  on the server.
- What Flock refuses (409 conflicts such as active runs, 403s for runs you may not change,
  invalid names) stays in the dialog as a message naming the runs in the way, rather than
  becoming an error page.
- A change that lands drops the cached run pages and reloads the page's data before the dialog
  closes, so it closes onto the changed page. Renames and deletes redirect where they must.

## Live updates

`src/lib/data/live.ts` opens a Server-Sent Events stream for the runs on the shown page,
passing each run's history and system line counts from the snapshot
(`?runs=id:lines:system_lines,...`). Flock replays every point logged since the snapshot,
then pushes new points (`history`), new system samples (`system`), state and summary changes
(`run`) and new runs (`created`) as `file_stream` requests are ingested.

Changes made elsewhere arrive on the same stream. Edited names, tags and notes (`run` events
with `attributes`) update the table in place. Runs deleted or moved away (`deleted`) are
offered as a refresh, like new runs, except those this tab is removing itself. A renamed project
(or org) moves the page to its new URL, keeping the view, and a deleted one goes back to the
organization's projects (`project`).

- Points are appended to the page's series in place; series grow by doubling and halve past 4k
  points, so a long live run stays bounded.
- Changes are coalesced per animation frame into version bumps: only panels of metrics that got
  points repaint, and the table re-renders its visible cells once.
- On disconnect the client reconnects with its current cursors (not the original URL), so the
  replay covers exactly the gap. Points carry their line numbers and anything at or below the
  cursor for their stream is dropped, so overlaps never duplicate.
- Cached pages keep their cursors, so a page shown again catches up from where it left off.

## State

- **URL** owns what must survive a reload and render on the server: `page`, `sort`, `order`
  (read by the page load) and `view`, `panel` (read only by components).
- **Server data** (`ProjectMeta`, `RunPage`) is never proxied; the `Workspace` class
  (`src/lib/workspace/workspace.svelte.ts`) reads it through getters. Live updates mutate it and
  bump versions the workspace exposes (`version(metric)`, `tableVersion`, `stateOf(row)`).
- **Workspace UI state** lives in that class, provided via context: hidden runs (by id, so a
  hidden run stays hidden across pages), hover / pin, cursor, smoothing, per-panel zoom and log
  scale (by metric name, since the metric list can differ between pages), collapsed sections
  and section pages, and the live connection status.

## Rendering charts

Axes, ticks and labels are DOM, computed from the data, so they render on the server. Lines are
drawn on two canvases per panel:

- a **base layer** with every run, redrawn only when data, visibility, smoothing, zoom or size
  changes, through a frame-budgeted scheduler (`src/lib/chart/scheduler.ts`): on-screen panels in
  animation frames, everything else in idle time, so the charts are already painted before you
  switch to them;
- an **overlay** with the focused line, crosshair, marker and zoom selection. Focusing a run dims
  the base layer with CSS opacity and draws one line on the overlay, so hover is O(points of one
  run) per panel.

## Performance

`pnpm perf` drives real Chrome (GPU canvas) through the scenarios below on a production build,
against the project at `WORKSPACE` (`/entity/project`) on a running Flock instance. Representative run
on an Apple M4 Pro, 1440×900 @2×, for a project of 155 runs × 1,499 columns × 40 metrics with 4
runs logging live:

| Scenario                                               | Result                                                                                  |
| ------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| Load (7.5 MB HTML incl. data)                          | FCP 144 ms · hydrated 244 ms · 0 API calls before hydration · no late-discovered assets |
| Table wheel scroll, 100 × 1,499                        | 538 frames, 1 over 20 ms, 0 long tasks                                                  |
| Jump to the last column                                | 31 ms                                                                                   |
| Chart hover sweep (6 panels × 100 runs + cursor panel) | 360 frames, 0 dropped                                                                   |
| Run-list hover (focus across all panels)               | 0 dropped frames                                                                        |
| 5 s on the charts while 4 runs stream points           | 299 frames, 0 dropped, 0 long tasks                                                     |
| Toggle one / all runs → all visible charts repainted   | 42 / 49 ms                                                                              |
| Smoothing 0 → 0.6 → repainted                          | 45 ms                                                                                   |
| Next page / sort, prefetched                           | 68 ms / 47 ms                                                                           |
| Sort, not prefetched                                   | 106 ms                                                                                  |

These include ~40 ms of Playwright click overhead. The client bundle is ~142 KB gzipped, most of
it bits-ui (shadcn's headless layer); all of it is modulepreloaded from the document. `THROTTLE=4 pnpm perf` simulates a slower
CPU; `CHANNEL=chromium` uses Playwright's headless shell, which rasterises canvas in software.
There, redrawing 100-run panels causes 250–500 ms long tasks; moving base-layer drawing into an
`OffscreenCanvas` worker is the fix if software rendering matters.
