# Queries and filters

Endpoints that read a project's run table and curves, and the filter syntax they share. Paths are relative to `/api/v1/projects/{entity}/{project}`. Every endpoint that selects runs has a `GET` form with query parameters, and a `POST .../query` form that takes the same fields as a JSON body. The `POST` form is easier when filters get long.

## Columns[[columns]]

The run table has one row per run and one column per value a run can have:
- the [run attributes](projects#run-attributes), such as `name`, `display_name`, `state`, `tags` and `owner`
- `config.*`: the config, flattened with dots, such as `config.lr` and `config.model.layers`
- `summary.*`: the summary, flattened the same way, such as `summary.loss` and `summary.val_loss.min`

Values with a `_type` key, such as histograms in the summary, stay whole instead of being flattened. A column exists while at least one run has a value for it.

Column names also accept two aliases: `summary_metrics.loss` means `summary.loss`, and `run.state` means `state`.

### `GET /columns`[[get-columns]]

Lists the columns, sorted by name:

```json
{"columns": [{"name": "config.lr", "type": "number", "count": 412}, {"name": "config.optimizer", "type": "string", "count": 412}]}
```

| Parameter | Meaning |
|---|---|
| `q` | Keep columns whose name contains this text, in any case. |
| `prefix` | Keep columns whose name starts with this, such as `summary.`. |

`count` is how many runs have a value. `type` is `number`, `string`, `bool`, `list` or `object`, or `mixed` when the column holds numbers and other values.

## The run table[[the-run-table]]

`GET /runs` and `POST /runs/query` filter, sort and page the run table.

| Field | Default | Meaning |
|---|---|---|
| `filter` | none | A [filter](#filters). In `GET`, a JSON string. |
| `sort` | newest first | [Sort keys](#sorting). |
| `columns` | `display_name`, `state`, `created_at`, `updated_at`, `tags`, `group` | Column names, or patterns with `*`, `?` and `[...]`, such as `config.*` or `summary.*loss*`. In `GET`, comma-separated. |
| `limit` | `100` | Runs per page, up to 100,000. |
| `offset` | `0` | Runs to skip. |
| `all_ids` | `false` | Also return the ids of every matching run, not only this page's. |
| `running` | `false` | Only the active runs. |

For example, the five runs with the lowest validation loss among those trained with Adam or AdamW:

```bash
curl -s -H "Authorization: Bearer $TOKEN" $FLOCK/api/v1/projects/flock/llm-pretrain/runs/query --json '{
  "filter": {"$and": [{"config.optimizer": {"$in": ["adam", "adamw"]}}, {"state": "finished"}]},
  "sort": "summary.val_loss.min",
  "columns": ["display_name", "config.lr", "summary.val_loss.min"],
  "limit": 5
}'
```

The response is column-oriented: `data` holds one list per column, in the order of `ids`.

```json
{
  "total": 37,
  "offset": 0,
  "limit": 5,
  "ids": ["9fc2v9ee", "k3m9x2pq", "e97s97iv", "p0w8z1rt", "m2n4b6v8"],
  "columns": ["display_name", "config.lr", "summary.val_loss.min"],
  "data": {
    "display_name": ["gpt-small-lr3e-4", "gpt-small-lr1e-4", "gpt-small-lr6e-4", "gpt-small-lr3e-5", "gpt-small-lr1e-3"],
    "config.lr": [0.0003, 0.0001, 0.0006, 0.00003, 0.001],
    "summary.val_loss.min": [1.92, 1.97, 2.01, 2.12, 2.3]
  }
}
```

| Field | Meaning |
|---|---|
| `total` | How many runs match. |
| `offset`, `limit` | The page that was returned. |
| `ids` | The page's run ids, in order. |
| `columns` | The columns returned: your names, with patterns expanded. |
| `data` | One list of values per column. A run without a value has `null`. A column no run has is all `null`. |
| `all_ids` | With `all_ids: true`, the ids of every matching run, in order. |

### Sorting[[sorting]]

`sort` is a list of keys, applied in order:
- In a string, comma-separated: `"-summary.loss,created_at"`. A leading `-` sorts descending, and `+` or nothing ascending.
- In a JSON body, also as objects: `[{"key": "config.optimizer"}, {"key": "name", "desc": true}]`.

Runs without a value always sort last, in both directions. In a column with mixed values, an ascending sort puts numbers and booleans first, then strings, then other values, and a descending sort reverses that order.

Without `sort`, runs come newest first. A sort key that names a column no run has is ignored, so sorting only by such a column returns the runs oldest first.

## Filters[[filters]]

Filters use the Mongo-style syntax of `wandb.Api().runs(filters=...)`. A filter is a JSON object that maps column names to conditions, and all of them must hold:

```json
{"config.optimizer": "adamw", "summary.loss": {"$lt": 2.5}, "tags": "baseline"}
```

A plain value means equality. An object of operators applies each of them.

### Comparison operators[[comparison-operators]]

| Operator | Matches runs whose value |
|---|---|
| `$eq` | equals the operand. `{"col": {"$eq": 1}}` is the same as `{"col": 1}`. |
| `$ne` | doesn't equal the operand, including runs without a value. |
| `$gt`, `$gte`, `$lt`, `$lte` | compares with a number, or with a string in alphabetical order. Other operands answer 400. |
| `$in` | equals one of the listed values. |
| `$nin` | equals none of them, including runs without a value. |
| `$exists` | is present (`true`) or missing (`false`). |
| `$regex` | is a string that the regular expression matches anywhere. PostgreSQL runs it, so a pattern must mean the same in Python's `re` and in PostgreSQL. Those that don't, such as `\b`, named groups and flags other than a leading `(?i)`, answer 400. |
| `$contains` | is a string that contains the operand, or a list that contains it. |
| `$not` | doesn't match the operator object, such as `{"$not": {"$in": ["adam", "sgd"]}}`. |

### Logical operators[[logical-operators]]

| Operator | Takes | Matches runs that |
|---|---|---|
| `$and` | a list of filters | match all of them |
| `$or` | a list of filters | match at least one |
| `$nor` | a list of filters | match none |
| `$not` | a filter | don't match it |

```json
{"$or": [{"summary.acc": {"$gt": 0.8}}, {"$and": [{"tags": "baseline"}, {"$not": {"config.optimizer": "sgd"}}]}]}
```

### How values compare[[how-values-compare]]

- **Missing values** never satisfy a comparison, and do satisfy `$ne` and `$nin`. `{"col": null}` matches the runs without a value, and so does `{"col": {"$exists": false}}`.
- **Lists**, such as `tags`, match `$eq` and `$in` when they contain the value: `{"tags": "baseline"}` matches every run tagged `baseline`. A list operand matches the whole list: `{"tags": []}` matches runs without tags.
- **Numbers** compare as numbers, whether they were logged as integers or floats. `NaN` never compares.
- **Booleans** match only booleans: `{"config.amp": true}`.
- **Strings** never match a numeric comparison, and numbers never match a string one.
- **Unknown columns** behave as if no run had a value.

A filter that isn't an object, an unknown operator or an invalid regular expression answers 400.

## Curves[[curves]]

`GET /history` and `POST /history/query` return curves of chosen metrics for many runs. By default they return the newest 50 runs.

| Field | Default | Meaning |
|---|---|---|
| `keys` | required | Metrics to return, such as `loss,val_loss`. In `GET`, comma-separated. |
| `runs` | none | Run ids. When given, `filter`, `sort`, `running` and `limit` are ignored, and unknown ids are skipped. In `GET`, comma-separated. |
| `filter` | none | A [filter](#filters) selecting the runs. |
| `sort` | newest first | The order of the runs, before `limit` applies. |
| `running` | `false` | Only the active runs. |
| `limit` | `50` | Maximum number of runs, up to 100,000. |
| `stream` | `history` | `history` for logged metrics, or `system` for [system metrics](../logging-behavior#system-metrics). |
| `x` | `_step`, or `_runtime` for `system` | The x-axis: `_step`, `_runtime`, `_timestamp`, or any logged metric, such as `epoch`. |
| `max_points` | `0` | Downsample each curve to at most this many points. `0` keeps them all. |

For example, the GPU utilization of every active run:

```bash
curl -s -H "Authorization: Bearer $TOKEN" "$FLOCK/api/v1/projects/flock/llm-pretrain/history?stream=system&keys=system.gpu.0.gpu&running=true"
```

```json
{
  "stream": "system",
  "x": "_runtime",
  "keys": ["system.gpu.0.gpu"],
  "runs": [
    {"id": "9fc2v9ee", "display_name": "gpt-small-lr3e-4", "state": "running", "series": {"system.gpu.0.gpu": {"x": [15.2, 30.2, 45.2], "y": [97.0, 98.0, 98.0]}}}
  ]
}
```

Each run's `series` maps a metric to its points, `{"x": [...], "y": [...]}`. A run that never logged a metric has no entry for it. Leaving out `keys` answers 422, and an empty list of keys answers 400.

### Curves of one run[[curves-of-one-run]]

`GET /runs/{run}/history` returns curves of one run:

```json
{"id": "9fc2v9ee", "stream": "history", "x": "_step", "series": {"loss": {"x": [0, 1, 2], "y": [5.5, 5.45, 5.4]}}}
```

It takes `keys`, `stream`, `x` and `max_points` as above. Without `keys`, it returns every metric of the stream, including wandb's bookkeeping keys such as `_step`, `_runtime` and `_timestamp`.

### How curves are built[[how-curves-are-built]]

- **Points without an x value are left out.** With `x=epoch`, only the steps that logged `epoch` have points.
- **Non-finite values** (`NaN` and infinity) come back as `null`.
- **Booleans** come back as 0 and 1. Strings and media values don't make curves.
- **`_step`** values come back as integers.
- **Downsampling** with `max_points` keeps evenly spaced points, including the first and the last.
