# Forks and blends

How Flock builds forks and blends on Lance, the rules they follow, and their Python, CLI and HTTP interfaces. For step-by-step use, see [Fork a dataset](fork-datasets) and [Blend datasets](blend-datasets).

Neither copies rows. Both write a new Lance manifest at their own path whose data files point back into their sources' folders, using Lance's base paths. Every manifest is written by the Flock server, which holds the upstream S3 credentials. Python and the CLI call the server's API.

## Forks[[forks]]

A fork is a Lance shallow clone (`LanceDataset.shallow_clone`) of the source at one version. Lance records the source's folder as base path `0` of the fork, and every data file of the forked version keeps reading from there.

| Property | Value |
|---|---|
| First version | The source version's number. Forking version 12 makes a fork at version 12. |
| Writes | Deletes, updates, appends and compaction write to the fork's own `data/` and `_deletions/`. |
| Source | Unchanged. Pinned at the forked version, see [Pins](#pins). |
| Destination | Must not exist, and must differ from the source. |

## Blends[[blends]]

A blend is built in two commits by `flock.hub.datasets._blend.build`:

1. **Version 1** is an `Overwrite` that creates the dataset. Its `initial_bases` name every source's folder, and every base a source already reads (a fork's original, for example), each once. Its fragments are the source fragments the blend takes rows from, with their data files pointing at those bases. Its `transaction_properties` hold the request as JSON under `flock.lineage`.
2. **Version 2** is a `Delete` that writes a deletion file for each fragment: the rows not taken, and the rows the source had already deleted. A fragment with no rows taken is left out of version 1. A fragment that ends up empty is removed. Version 2 is tagged `blend`.

Lance 14 refuses deletion files in the `Overwrite` that creates a dataset ("Overwrite fragments must be newly written … Use Delete"), and detached commits can't create a dataset, so the deletions need their own version.

Fragments are ordered by a seeded shuffle, so a scan in order alternates between sources instead of reading all of one, then all of the next.

### Sampling[[sampling]]

Each live row of a source gets a 64-bit key, and the source gives the rows with the smallest keys:

```text
salt = fnv1a64("{path}@{version}")
key  = splitmix64(offset XOR (seed XOR salt XOR (fragment_id << 32)))
```

`splitmix64` is SplitMix64's finalizer, implemented in Flock so a blend comes out the same whatever NumPy does in later releases. The keys don't depend on the other sources or the amounts, which gives these properties:

| Property | Why |
|---|---|
| Same sources, versions, amounts and seed give the same rows | Keys depend only on the seed, the source's path and version, and the row's position. |
| A source's rows don't change when another source's amount does | Each source is sampled on its own. |
| A smaller amount takes a subset of a larger one, for the same seed | Both take the smallest keys. |
| Rows are spread evenly over the source | Keys are uniformly distributed. |

Finding the `k` smallest keys takes two passes and bounded memory, so it works on billions of rows. The first pass counts keys by their top 16 bits. The second keeps every row in the buckets below the one holding the `k`-th key, and sorts only that bucket's rows. Neither pass reads a column: offsets come from the fragment's row count and deletion file.

### Amounts[[amounts]]

| Mode | Rules |
|---|---|
| Row counts | Every source takes at least 1 row. |
| Fractions of `total` | Every source has a fraction, and `total` is at least 1. The fractions may add up to within 0.001 of 1 (0.1 points), and are scaled to add up to it. Each source gets `floor(fraction × total)` rows. The rows left go one each to the largest remainders, ties in source order. `[0.66, 0.34]` of 101 is `[67, 34]`, and thirds of 100 are `[34, 33, 33]`. |

A source asked for more rows than it has is refused unless `replace` is set. With `replace`, the source is listed `k // live` times in full, and the remaining `k % live` rows are sampled as above. A fragment listed more than once shares one fill file.

### Column maps[[column-maps]]

A column map is a list of output columns, each `{"name", "type", "nullable", "from"}`. `from` maps every source path to one of:

| Spec | Meaning | Written |
|---|---|---|
| `{"column": "content"}` | The source's column, of the output's type. | Nothing: the blend reads the source's file under the output's field id. |
| `{"column": "doc_id", "cast": true}` | The source's column, cast to the output's type. | A fill file. |
| `{"value": "stack-v2"}` | The same value in every row. | A fill file. |
| `{"null": true}` | Null in every row. The output must be nullable. | Nothing. |

A source column already given to one output, and taken again by another, is written into a fill file as a copy, since one field id can only be one column. The preview reports it as a `copy` write.

`type` is an Arrow type name, such as `int64`, `large_string` or `list<item: string>`. Nested types are matched by the name of a source column's type.

Casts are allowed only when no value can change:

| From | To |
|---|---|
| `int8`, `int16`, `int32` | any wider signed integer, `float` (from `int8` and `int16` only), `double` |
| `int64` | nothing |
| `uint8`, `uint16`, `uint32`, `uint64` | any wider unsigned or signed integer, `float` (from `uint8` and `uint16` only), `double` (not from `uint64`) |
| `halffloat` | `float`, `double` |
| `float` | `double` |
| `string` | `large_string` |
| `binary` | `large_binary` |

The **automatic map** has the first source's columns, in its order. Each other source gives the column of the same name: as it is when the types match, cast when the cast is safe, and null when it has none (which makes the output nullable). A same-named column of a type that can't be cast stays in the map as a problem to fix. Leaving `columns` out, or `null`, uses the automatic map. An empty list is a map with no columns, and is refused.

A map is refused, with every problem named, when:

- it has no columns, a column has no name, or two columns share a name;
- a type isn't a known Arrow type;
- a source has no spec for a column, or its spec is none of the four above;
- a source lacks the named column, or its type can't become the output's without losing values;
- a value isn't of the output's type, or the output is a nested type;
- a non-nullable output gets null from a source, or takes a source column that can hold nulls;
- a source gives only nulls, since it would add rows with no values.

Problems that concern one column start with its name, which is how the **Columns** step places them.

### Field ids and fill files[[field-ids]]

Lance identifies columns by field id. The blend's schema numbers its fields depth first from 0, as Lance does for a new dataset. Each listed data file has its source field ids rewritten to the output's. Fields of that file no output takes become `-2`, which Lance skips, and a file with no column in the blend is left out.

A fill file holds a fragment's casts, values and copies, one value per physical row of the source fragment, in the blend's `data/` as `flock-fill-{uuid}.lance`. It's written with `LanceFileWriter` and listed as one more data file of that fragment. Rows the source had deleted are null in it, and stay deleted.

## Pins[[pins]]

Each fork and blend pins every source version it reads:

- a tag `flock-ref-{ulid}` on the source, on that version, so `cleanup_old_versions` keeps its files;
- a row in `dataset_links` with the same ULID, the dependent's path, and the source's path and version.

The ULID is the link's own id, so each pin is a separate tag, and removing one link removes only its tag. A fork or blend of a dataset that reads others also pins what that dataset reads (`kind = inherited`), since its manifest lists those files too. The Datasets tab hides `flock-ref-` tags.

`dataset_origins` records each fork and blend: its kind and, for a blend, the lineage below. Both tables follow renames and the trash:

| Event | Pins and origin |
|---|---|
| The fork or blend is renamed or moved | They follow it. |
| It goes to the trash | They stay, so it can be restored. Its path is free again. |
| It's restored | They come back, at the path it's restored to. |
| It's deleted for good | The tags are removed and the rows deleted. |
| It's made independent | The tags are removed and the rows deleted. The origin stays, with `"independent": true`. |

A dataset with a pin can't be renamed, moved or deleted, and neither can a folder that holds one. The request answers 409, naming up to two of the datasets that read it and counting those in the trash.

## Making a dataset independent[[independent]]

`make-independent` rewrites the fork's or blend's live rows at its latest version into a new version in its own `data/`, with `transaction_properties` `{"flock.independent": "true"}`. It then removes every tag, including `blend`, removes every earlier version with `cleanup_old_versions`, and releases its pins. Its sources are unchanged.

It runs on the server, which refuses datasets whose data files total more than `FLOCK_HF_IMPORT_MAX_BYTES` (50 GiB by default). A dataset that isn't a fork or blend, or is already independent, is refused.

## Jobs[[jobs]]

Blends and independence copies run as jobs on the API server, recorded in `dataset_jobs`.

| Field | Meaning |
|---|---|
| `kind` | `blend` or `independent` |
| `state` | `running`, `finished`, `failed`, `cancelled` or `interrupted` (the server stopped while it ran) |
| `phase` | What it's doing, such as `sampling` |
| `rows_done`, `rows_total`, `progress` | How far it is. A blend counts the sources' live rows once for sampling, and the blend's rows once for building. |
| `result` | When finished: `{"path", "version", "rows"}` |
| `error` | Why it failed |

A blend that fails, is cancelled or is interrupted has everything it wrote removed, with its origin and pins.

## Lineage[[lineage]]

A blend's origin, returned as `origin` by `GET info`, holds what it was made from. It is enough to make the same blend again:

```json
{
  "kind": "blend",
  "seed": 42,
  "spec": {"mode": "fraction", "total": 300000},
  "replace": false,
  "columns": [{"name": "text", "type": "string", "nullable": true, "from": {"...": {}}}],
  "sources": [
    {"path": "web/fineweb-edu/train", "version": 12, "rows": 198000, "live": 9672000, "fraction": 0.66}
  ],
  "version": 2
}
```

`spec.mode` is `rows` when the amounts were row counts, and then `fraction` is absent. A fork's origin is `{"kind": "fork", "source": {"path", "version"}}`. Both also have `created_by` and `created_at`, and `"independent": true` once made independent.

## HTTP API[[http-api]]

Paths are relative to `/api/v1/orgs/{org}/datasets`. Members and admins can use every route. See [Datasets](../app/api/datasets) for the others.

| Endpoint | Meaning |
|---|---|
| `POST fork` | Forks `{"path", "version", "to"}`. `version` is a number or tag, and defaults to the latest. Answers 201 with `{"path", "version", "source": {"path", "version"}}`. 409 with `"taken": true` when something exists at `to`. |
| `POST blend/preview` | Checks a blend and writes nothing. The body is as for `POST blend`, with `seed` and `to` optional. Answers `sources` (each with `path`, `version`, `live`, `rows`, `share` and `schema`), `total`, `columns`, `automatic`, `unused` (each source's columns no output takes), `writes` (`{"column", "source", "kind"}` with `kind` one of `cast`, `copy` and `value`), `problems` (all of them) and `column_problems` (the column map's alone). |
| `POST blend` | Starts a blend. The body has `sources` (2 to 16, each `{"path", "version", "rows"}` or `{"path", "version", "fraction"}`), `total` (for fractions), `seed` (required, 0 to 2⁶³−1), `columns`, `replace` and `to`. Answers 202 with the job. 422 naming every problem, 409 when something exists at `to`. |
| `POST independent` | Starts making `{"path"}` independent. Answers 202 with the job. 409 over the size limit. |
| `GET jobs` | The organization's jobs of the last week, newest first: `{"jobs": [...]}`. |
| `GET jobs/{id}` | One job. |
| `DELETE jobs/{id}` | Cancels a running blend. Its starter or an org admin. |

`GET info` also answers `origin` (above, or `null`) and `dependents` (`{"count", "trashed", "paths"}`), and `GET at` labels forks and blends in a folder's listing.

A source given twice, sources mixing `rows` and `fraction`, or more than 16 sources answer 422. A source that doesn't exist, or a version it doesn't have, answers 404.

## Python[[python]]

[[autodoc]] hub.datasets.fork

[[autodoc]] hub.datasets.blend

[[autodoc]] hub.datasets.blend_preview

[[autodoc]] hub.datasets.make_independent

[[autodoc]] hub.datasets.value

[[autodoc]] hub.datasets.null

`DEFAULT_SEED` is `42`, the seed `blend` uses when none is given, and the one the dialog fills in.

## flock datasets fork, blend and make-independent[[commands]]

| Command | Does |
|---|---|
| `flock datasets fork SRC DEST` | Forks `SRC` to `DEST`. |
| `flock datasets blend DEST --source PATH[@VERSION]=AMOUNT ...` | Blends the sources into `DEST`. |
| `flock datasets make-independent PATH` | Makes the fork or blend `PATH` independent. |

| Flag | Commands | Default | Meaning |
|---|---|---|---|
| `--version` | `fork` | the latest | A version number or tag. |
| `--source` | `blend` | required | A source and its amount: rows (`100000`) or a fraction of `--total` (`66%` or `0.66`). Repeat for each source. |
| `--total` | `blend` | | The blend's rows, when the amounts are fractions. |
| `--seed` | `blend` | `42` | Picks the rows. |
| `--columns` | `blend` | the automatic map | A JSON file holding a column map, in either form `datasets.blend` takes. |
| `--replace` | `blend` | off | Take rows more than once from sources with too few. |
| `--preview` | `blend` | off | Print each source's rows, the column map and the problems, and write nothing. Exits with `1` when there are problems. |
| `--org` | all three | your default organization | The organization. |

`blend` and `make-independent` wait for the job and print its progress to stderr. The commands exit with code `1` and the server's message when it refuses.
