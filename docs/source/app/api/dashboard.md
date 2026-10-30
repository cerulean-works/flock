# Snapshot and live stream

The two endpoints a dashboard page is built on: a binary snapshot of the page, and a stream of Server-Sent Events that keeps it current. Paths are relative to `/api/v1/projects/{entity}/{project}`. For how to use them together, see [Building a dashboard](../build-dashboard-client).

## The workspace snapshot[[the-workspace-snapshot]]

`GET /workspace` returns a page of the run table with every column, and every metric's curve for each run on the page, as one binary response (`application/octet-stream`). Each run also carries what a dashboard needs to plot its curves against another x-axis: its metric definitions and its x-axis mappings.

| Parameter | Default | Meaning |
|---|---|---|
| `sort` | `-created_at` | [Sort keys](queries#sorting), comma-separated. |
| `filter` | none | A [filter](queries#filters) as a JSON string. Only the runs that match are on the pages. |
| `running` | `false` | Only the active runs. |
| `offset` | `0` | Runs to skip. |
| `limit` | `100` | Runs on the page, up to 500. |
| `max_points` | `500` | Maximum points per curve, from 32 to 10,000. |
| `system` | `false` | Also include the curves of the system metrics. |

### Layout[[layout]]

All numbers are little-endian. The sections follow each other without gaps:

```text
b"FLK2"                  4 bytes, the format's magic number
u32                      length of the header, in bytes
header                   JSON, then zero bytes up to a multiple of 8 bytes from the start
f64[numeric × runs]      numeric table cells, column after column (NaN = no value)
f64[runs × series × 6]   six numbers describing each curve
f64[x_len]               x values of the curves that aren't evenly spaced
f32[y_len]               y values of every curve, then 4 zero bytes if y_len is odd
f64[runs × axes × 2]     two numbers describing each x-axis mapping
f64[axes_len]            the mappings' knots
```

Every section starts at a multiple of 8 bytes, so you can read each one as a typed array without copying. The last two sections came later than the others: a header without `axes` has neither, and older clients can ignore them.

### Header[[header]]

| Field | Meaning |
|---|---|
| `entity`, `project` | The project. |
| `now` | Flock's clock when it built the snapshot, as a Unix timestamp. |
| `total` | How many runs match `filter` and `running`: all of the project's without them. |
| `runs_total` | How many runs the project has, matching or not. Compare it with the stream's `hello` to count runs created since. |
| `active` | How many of them are active. |
| `offset` | The `offset` of this page. |
| `runs` | The runs on the page, in order. See below. |
| `columns` | The table's columns, as `{"key", "kind"}` objects. See below. |
| `metrics` | The names of the metrics the runs on the page logged, sorted. Metrics that only the project's other runs logged aren't listed. Every run on the page has a curve slot for each. |
| `system_metrics` | The names of the system metrics the runs on the page logged, sorted, or an empty list without `system=true`. |
| `numeric` | Indices into `columns` of the columns whose cells are in the numeric section, in that section's order. |
| `text` | The other columns' cells, as an object that maps a column's index (as a string) to a list with one value per run. |
| `points` | The maximum number of points per curve that applied. See [Point budget](#point-budget). |
| `x_len`, `y_len` | The lengths of the x and y sections, in values. |
| `axes` | The keys of the x-axis mappings against `_step`: `_runtime`, `_timestamp`, then every step metric the page's runs define, sorted. See [X-axis mappings](#x-axis-mappings). |
| `system_axes` | The keys of the mappings against `_runtime`: `["_timestamp"]` with `system=true`, else an empty list. |
| `axes_len` | The length of the knots section, in values. |

Each entry of `runs` has:

| Field | Meaning |
|---|---|
| `id` | The run id. |
| `name` | The display name, or the run id if it has none. |
| `state` | The run's state. |
| `ordinal` | The run's position in the project's creation order, from 0 for the oldest. Deleting or moving runs renumbers it. |
| `lines` | The number of history lines: the cursor to pass to the [stream](#the-live-stream). |
| `system_lines` | The number of system samples, the cursor for system metrics. |
| `metric_defs` | The run's metric definitions. See [Metric definitions](#metric-definitions). |

`columns` lists the run attributes `state`, `name`, `created_at`, `updated_at`, `owner`, `tags`, `group`, `job_type`, `notes`, `host`, `program`, `commit`, `sweep` and `exitcode`, leaving out those no run has. Then come the `config.*` columns, then the `summary.*` columns, each sorted by name. `display_name` isn't a column, since it's each run's `name` in `runs`. Neither are `description` and `heartbeat_at`: read them from the [run table](queries#the-run-table).

A column's `kind` tells you how to display it:

| `kind` | Values |
|---|---|
| `state` | The `state` column. |
| `time` | Unix timestamps: `created_at`, `updated_at` and `summary._timestamp`. |
| `duration` | Seconds: `summary._runtime`. |
| `user` | User ids: `owner`, who logged the run. `GET /api/v1/orgs/{org}/members` names them. |
| `num` | Numbers. |
| `str` | Strings. |
| `bool` | Booleans. |
| `json` | Lists, objects, or mixed values. |

The kind doesn't decide where a column's cells are. Read them from the numeric section if the column's index is in `numeric`, and from `text` otherwise.

### Curves[[curves]]

Each run has one curve slot per name in `metrics`, against `_step`, followed by one per name in `system_metrics`, against `_runtime`. Slot `m` of run `r` is described by the six numbers at position `(r × (len(metrics) + len(system_metrics)) + m) × 6` of the series section:

| Number | Meaning |
|---|---|
| y offset | Where the curve's values start in the y section. |
| `n` | How many points the snapshot holds. `0` means the run never logged this metric. |
| `N` | How many points the run logged. |
| `x0`, `dx` | The first x value and the step between x values, when they're evenly spaced. |
| x offset | Where the curve's x values start in the x section, or `-1` when they're evenly spaced. |

A curve with more than `n` points is sampled at the raw indices:

```text
k_i = (i·(N−1) + (n−1)//2) // (n−1)    for i = 0 … n−1
```

The formula uses integer division only, so a client computes the same indices as Flock. When the x values are evenly spaced, which is the usual case for `_step`, they aren't sent: point `i` has `x = x0 + dx·k_i`. Otherwise the x section holds them explicitly, starting at the x offset.

### Metric definitions[[metric-definitions]]

A run's `metric_defs` are what its `run.define_metric` calls said about plotting, in the SDK's order. Each is an object with:

| Field | Meaning |
|---|---|
| `key` or `glob` | The metric's name, or a glob: a name ending in `*` that matches every key starting with what comes before the `*`. |
| `step` | The metric's step metric, its default x-axis. Missing when it has none. |
| `hidden` | `true` when the metric should get no chart of its own. Missing otherwise. |

To find a metric's definition, look for one with its `key` first, then for the glob with the longest prefix it starts with. wandb's chart for a metric plots it against its definition's `step`, or against `_step` without one. Hugging Face's `Trainer`, for example, gives every run these definitions:

```json
[{"key": "train/global_step"}, {"glob": "*", "step": "train/global_step"}]
```

### X-axis mappings[[x-axis-mappings]]

Curves are always sent against `_step`, and system metrics against `_runtime`. To plot one against something else, such as its step metric or the wall-clock time, interpolate the run's mapping of that key at the curve's x values. A mapping is the key's curve against the same x, kept to its knots: Flock leaves out every point that is in line with both its neighbors, because interpolating over it gives the same value. A step metric logged at a fixed stride shrinks to its two ends.

Each run has one mapping per name in `axes`, against `_step`, followed by one per name in `system_axes`, against `_runtime`. Mapping `a` of run `r` is described by the two numbers at position `(r × (len(axes) + len(system_axes)) + a) × 2` of its section:

| Number | Meaning |
|---|---|
| offset | Where the mapping's knots start in the knots section. |
| `n` | How many knots it has. `0` means the run never logged the key. |

The knots section holds the mapping's `n` x values, then its `n` values, both as `f64`, so wall-clock times keep their precision. Mappings of `_runtime` and `_timestamp` are sampled like curves when they have more knots than the point cap. Mappings of step metrics keep up to 2,048 knots, or the cap if it is higher.

The stream sends the points of `_runtime` and `_timestamp` too, so mappings can grow with the curves. See [Events](#events).

### Point budget[[point-budget]]

A snapshot aims at 2,000,000 points in total, shared between its curves and its mappings. The cap per curve, in the header's `points`, is the smaller of `max_points` and 2,000,000 divided by the number of curves and mappings that have data, but never fewer than 32. Curves with fewer points than the cap are sent whole.

### Decoding a snapshot[[decoding-a-snapshot]]

This TypeScript function decodes a snapshot into its header, its table cells by column, its curves by run and metric, and its x-axis mappings by run and key:

```ts
type Curve = { x: Float64Array; y: Float32Array };

export function decodeSnapshot(buffer: ArrayBuffer) {
  const view = new DataView(buffer);
  const magic = new TextDecoder().decode(new Uint8Array(buffer, 0, 4));
  if (magic !== "FLK2") throw new Error(`not a snapshot: ${magic}`);
  const headerLength = view.getUint32(4, true);
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 8, headerLength)));
  let pos = 8 + headerLength + ((8 - ((8 + headerLength) % 8)) % 8);

  const nRuns: number = header.runs.length;
  const metrics: string[] = [...header.metrics, ...header.system_metrics];
  // Numeric table cells: one block of nRuns values per column listed in header.numeric.
  const numeric = new Float64Array(buffer, pos, header.numeric.length * nRuns);
  pos += numeric.byteLength;
  // Six values per (run, series): y offset, n, raw count N, x0, dx, x offset.
  const meta = new Float64Array(buffer, pos, nRuns * metrics.length * 6);
  pos += meta.byteLength;
  const xs = new Float64Array(buffer, pos, header.x_len);
  pos += xs.byteLength;
  const ys = new Float32Array(buffer, pos, header.y_len);
  pos = Math.ceil((pos + ys.byteLength) / 8) * 8;
  // Two values per (run, mapping): offset, n. Snapshots without `axes` have none.
  const axes: string[] = [...(header.axes ?? []), ...(header.system_axes ?? [])];
  const axisMeta = new Float64Array(buffer, pos, header.axes ? nRuns * axes.length * 2 : 0);
  pos += axisMeta.byteLength;
  const knots = new Float64Array(buffer, pos, header.axes_len ?? 0);

  const cells = new Map<string, unknown[]>();
  header.numeric.forEach((column: number, i: number) => {
    const values = Array.from(numeric.subarray(i * nRuns, (i + 1) * nRuns));
    cells.set(header.columns[column].key, values.map((v) => (Number.isNaN(v) ? null : v)));
  });
  for (const [column, values] of Object.entries(header.text)) {
    cells.set(header.columns[Number(column)].key, values as unknown[]);
  }

  const curves = new Map<string, Curve>(); // keyed by `${runId}/${metric}`
  header.runs.forEach((run: { id: string }, r: number) => {
    metrics.forEach((metric, m) => {
      const s = (r * metrics.length + m) * 6;
      const [yOffset, n, raw, x0, dx, xOffset] = meta.subarray(s, s + 6);
      if (n === 0) return;
      let x: Float64Array;
      if (xOffset >= 0) {
        x = xs.subarray(xOffset, xOffset + n);
      } else {
        // Evenly spaced: x is x0 + dx * k_i, with k_i = (i(N-1) + (n-1)//2) // (n-1).
        x = new Float64Array(n);
        for (let i = 0; i < n; i++) {
          const k = n > 1 ? Math.floor((i * (raw - 1) + Math.floor((n - 1) / 2)) / (n - 1)) : 0;
          x[i] = x0 + dx * k;
        }
      }
      curves.set(`${run.id}/${metric}`, { x, y: ys.subarray(yOffset, yOffset + n) });
    });
  });

  // Keyed by `${runId}/${key}`, and `${runId}/system:${key}` for those against _runtime.
  const mappings = new Map<string, { x: Float64Array; y: Float64Array }>();
  header.runs.forEach((run: { id: string }, r: number) => {
    axes.forEach((key, a) => {
      const o = (r * axes.length + a) * 2;
      const [offset, n] = [axisMeta[o], axisMeta[o + 1]];
      const stream = a < header.axes.length ? "" : "system:";
      mappings.set(`${run.id}/${stream}${key}`, {
        x: knots.subarray(offset, offset + n),
        y: knots.subarray(offset + n, offset + 2 * n),
      });
    });
  });
  return { header, cells, curves, mappings };
}
```

## The live stream[[the-live-stream]]

`GET /stream` sends Server-Sent Events (`text/event-stream`) about the runs a dashboard shows.

| Parameter | Default | Meaning |
|---|---|---|
| `runs` | none | The runs to watch, comma-separated, each as `<id>`, `<id>:<lines>` or `<id>:<lines>:<system_lines>`. |
| `system` | `false` | Also send system metric samples. |
| `ping_seconds` | `15` | Seconds of silence after which Flock sends a keep-alive comment, from 1 to 300. |

`lines` and `system_lines` are cursors: the line counts the snapshot reported. For each run with a cursor, the stream first replays the points logged after it. A run without a cursor is watched without a replay, and unknown run ids are ignored.

Each event is an `event:` line naming it and a `data:` line of JSON. Keep-alive comments are lines that start with `:`.

### Events[[events]]

| Event | Sent to | Data |
|---|---|---|
| `hello` | the new subscriber, first | `active` and `total` runs in the project, and `runs`: for each watched run, its `state` and `lines`, and its `system_lines` with `system=true`. |
| `history` | subscribers watching the run | New points of logged metrics: `run`, `lines` (the run's new history line count) and `series`. |
| `system` | subscribers watching the run, with `system=true` | New system samples, in the same shape as `history`, with `_runtime` as x. |
| `run` | subscribers watching the run | `run`, `state`, `lines` and `updated_at`, plus `summary` with the `summary.*` columns that changed (`null` for removed ones), `attributes` with the attributes an edit changed, and `metric_defs` with the run's [metric definitions](#metric-definitions) when they changed. |
| `created` | every subscriber of the project | A run was created, or moved in: `run`, `display_name` and `state`. |
| `deleted` | every subscriber of the project | Runs were deleted: `runs`, a list of run ids. Runs that moved out also carry `moved_to`, the target project's name. |
| `project` | every subscriber of the project | `{"renamed_to": "<name>"}` when the project was renamed, or `{"deleted": true}` when it or its organization was deleted. The stream ends after it. |

`history` and `system` events carry each metric's new points as three aligned lists:

```text
event: history
data: {"run":"9fc2v9ee","lines":203,"series":{"loss":{"rows":[200,201,202],"x":[200,201,202],"y":[1.83,1.82,1.82]}}}
```

`rows` are the points' line numbers. The replay and the first live events can overlap, so drop points whose line you already have. Of wandb's own keys, only `_runtime` and `_timestamp` are sent, for the [x-axis mappings](#x-axis-mappings): `history` events carry both, and `system` events carry `_timestamp`. They aren't metrics.

A `run` event comes when a run's state or summary changes, when its client updates its metadata, and when someone edits its attributes. Crash detection sends one too.

### When the stream ends[[when-the-stream-ends]]

The stream stays open until one of these happens:
- the client disconnects
- a `project` event ends it
- the subscriber falls 512 events behind, because it reads too slowly
- Flock shuts down

In the last two cases, reconnect with your latest cursors, and the replay fills the gap. The stream checks permissions when it opens: removing a member from the organization doesn't end a stream they already have open.
