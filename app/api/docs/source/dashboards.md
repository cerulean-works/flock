# Building a dashboard

Flock has no web interface. A dashboard, such as flock-ui, reads it through the HTTP API. This page shows the pattern the API is designed for: render a project page from one binary snapshot, keep it live with an event stream, and use the query API for everything beyond the page. It also lists the pages your dashboard needs for sign-in, invitations and resets.

The examples are in TypeScript and run in browsers and in Node.js. They read the project `flock/llm-pretrain`.

## Authenticate the dashboard

Every request needs credentials, sent in the `Authorization` header (see [Credentials](api/index#credentials)). A dashboard usually signs people in with `POST /api/v1/auth/login`, sends their access token, and refreshes it with `POST /api/v1/auth/refresh` before it expires after 15 minutes. [Sessions](accounts#sessions) explains the tokens.

If the dashboard calls Flock from the browser on another origin, list that origin in `FLOCK_CORS_ORIGINS`. The alternative is to proxy the API through the dashboard's own server, which also keeps Flock off the public network.

## Pages your dashboard provides

Flock builds links into the dashboard at `FLOCK_WEB_URL`. Your dashboard needs a page at each of these paths:

| Path | Shown when | The page calls |
|---|---|---|
| `/setup` | Flock has no accounts yet. Flock logs this address at startup. | `GET /api/v1/auth/config`, then `POST /api/v1/auth/signup`, then `POST /api/v1/orgs` with the chosen name |
| `/invite/<token>` | Someone opens an invitation link. | `GET /api/v1/invites/{token}`, then `POST /api/v1/invites/{token}/accept` |
| `/reset/<token>` | Someone opens a password reset link. | `GET /api/v1/password-resets/{token}`, then `POST /api/v1/password-resets/{token}` |
| `/auth/google?code=<code>` | A Google sign-in succeeded. | `POST /api/v1/auth/google/exchange` |
| `/login?error=<reason>&message=<text>` | A Google sign-in failed. | nothing: show the message |

`GET /api/v1/auth/config` tells the sign-in page whether signing up is still open and whether to offer Google. For Google, the dashboard also proxies `/api/v1/auth/google/*` to Flock: see [Setting up Sign in with Google](google-sign-in).

## Render a page from a snapshot

`GET /api/v1/projects/{entity}/{project}/workspace` returns everything a project page shows in one response: a page of the runs table with every column, and every metric's curve for each run on the page. The response is binary, so a page of 100 runs with 1,500 columns and dozens of metrics stays a few megabytes, and decodes into typed arrays without parsing.

```ts
import { decodeSnapshot } from "./snapshot";

const flock = "https://flock.example.com";
const res = await fetch(`${flock}/api/v1/projects/flock/llm-pretrain/workspace?limit=100&max_points=500`, {
  headers: { Authorization: `Bearer ${accessToken}` },
});
const { header, cells, curves } = decodeSnapshot(await res.arrayBuffer());

cells.get("config.lr");                   // one value per run on the page
curves.get(`${header.runs[0].id}/loss`);  // { x: Float64Array, y: Float32Array }
```

`decodeSnapshot` is in [Snapshot and live stream](api/dashboard#decoding-a-snapshot), with the full format. A few parameters shape the page:
- `sort`, `offset` and `limit` choose the runs: by default the 100 newest. `sort` takes a column, such as `summary.loss` or `-created_at`.
- `filter` keeps the runs that match a [filter](api/queries#filters), such as `{"config.optimizer": "adamw"}`, and `running=true` the active ones. The header's `total` then counts the matches.
- `max_points` caps the points of each curve (500 by default). The snapshot also shares a total budget of 2,000,000 points between all its curves, so wide pages get fewer points per curve. The header's `points` says which cap applied.
- `system=true` adds the curves of the system metrics.

## Keep it live with the event stream

After rendering, open `GET /api/v1/projects/{entity}/{project}/stream` for the runs on the page. Pass each run's history line count from the snapshot header as a cursor, `runs=<id>:<lines>,...`. The stream first replays every point logged since the snapshot, then sends new ones as they arrive, so the page never misses data between rendering and subscribing.

The stream needs the `Authorization` header. The browser's `EventSource` can't send headers, so read the stream with `fetch` instead:

```ts
export async function watch(
  url: string,
  token: string,
  onEvent: (event: string, data: any) => void,
  signal?: AbortSignal,
) {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` }, signal });
  if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return; // the server ended the stream: reconnect with your latest line counts
    buffer += value;
    let end: number;
    while ((end = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      let event = "message";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      } // lines starting with ":" are keep-alive comments
      if (data) onEvent(event, JSON.parse(data));
    }
  }
}
```

Open it with the cursors from the snapshot:

```ts
const cursors = header.runs.map((run) => `${run.id}:${run.lines}`).join(",");
await watch(`${flock}/api/v1/projects/flock/llm-pretrain/stream?runs=${cursors}`, accessToken, onEvent);
```

Then handle the events:
- **`history`**: append the new points to the run's curves. Each point carries its line number in `rows`. The replay and the live events can overlap, so drop points whose line you already have, and keep the event's `lines` as the run's new cursor.
- **`run`**: update the run's state, and the `summary.*` cells or edited `attributes` it carries.
- **`created`** and **`deleted`**: a run appeared in the project or left it. Fetch a new snapshot, or add or remove the row.
- **`project`**: the project was renamed, its organization was renamed, or it was deleted. Follow the new name or leave the page. The stream ends after this event.

The stream also ends when the dashboard falls too far behind (512 queued events), and when Flock restarts. In both cases, reconnect with your latest line counts, and the replay fills the gap. [Snapshot and live stream](api/dashboard#the-live-stream) lists every event and its fields.

To also show system metrics, fetch the snapshot with `system=true`, and open the stream with `system=true` and cursors of the form `<id>:<lines>:<system_lines>`. System samples then arrive as `system` events.

## Query beyond the page

The snapshot covers one page of runs. The [query endpoints](api/queries) cover the rest:
- **Run tables:** `POST .../runs/query` filters, sorts and pages the runs table, and returns only the columns you ask for. Its filters use the syntax of `wandb.Api().runs(filters=...)`.
- **Column pickers:** `GET .../columns?q=loss` searches the columns, with their types and how many runs have them.
- **Charts across runs:** `POST .../history/query` returns curves of chosen metrics for the runs a filter selects, for example every running run.
- **Run pages:** `GET .../runs/{run}` returns a run's attributes, config, summary and metric names. Its logs and files have their own endpoints, listed in [Projects and runs](api/projects).
