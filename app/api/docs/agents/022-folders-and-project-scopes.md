# 022: model folders, and datasets and models that belong to a project

| | |
| --- | --- |
| Status | implemented, pending review |
| Date | 2026-11-21 |
| Builds on | [002](002-projects-and-crud.md), [013](013-datasets-tab.md), [014](014-model-registry.md), [019](019-bulk-select.md), [021](021-public-access-checks.md) |
| Amends | D86 and D108 (routes), D98 (model names have folders, and models have a scope), D99 and D134 (where a new model's folder goes), D104 to D106 (refs), D109 and D93 (trash entries know their project), D131 and D134 (models can move, and anything can move between scopes) |
| Keeps | D87 to D92, D94 to D97, D100 to D103, D107, D110 to D118, D130, D132, D133, D135, D151 to D157 |

## 1. Goal

Today every dataset and model belongs to the org. Datasets live in `flock-datasets` and already
nest in folders of any depth (D87). Models live in the registry as `org/name`, with one-segment
names (014, open question 1), so a team with fifty experiment models gets one long flat list.

This stage keeps the org's datasets and models as they are and adds two things:

1. **Folders for models.** A model's name becomes a path, `acme/eval/olmo2-1b`, browsed like
   the Datasets tab (D158).
2. **A scope.** Every dataset and model is either **global** (the org's, today's behavior) or
   belongs to **one project** (D159). Global is for data that is expensive to store or used
   by many projects, such as a pretraining corpus or a released checkpoint. A project's own
   datasets and models are for trying things: an ablation's checkpoints or a filtered eval
   split live next to the runs that made them, don't crowd the org's lists, and go away with
   the project.

Both scopes nest in folders, and the same code serves both. Moving something from a project to
global ("promoting" it) is a move (D166).

Saving and loading in a project is the same call with a `project` argument:

```python
from flock.hub import models

models.save_to_hub(
    model, "sweeps/lr-3e-4", project="pretrain", run=run, step=12000, tags=["checkpoint-12000"]
)
model = models.load_from_hub("sweeps/lr-3e-4@checkpoint-12000", project="pretrain")
model = models.load_from_hub("pretrain:sweeps/lr-3e-4@checkpoint-12000")  # the same, shorter
model = models.load_from_hub("acme/eval/olmo2-1b")  # a global model, in the eval folder
```

and a project's dataset is found by name, not by its S3 path:

```python
import lance
from flock.hub import datasets

where = datasets.location("eval/mmlu-filtered", project="pretrain")
lance.write_dataset(table, where.uri, storage_options=where.storage_options)
```

**Out of scope:**
- permissions per project. Every member of the org can still read and write every project's
  datasets and models, as they can its runs (D165);
- public access to a project's datasets and models (D165);
- storage usage or quotas per project;
- copying a global dataset into a project (projects read global datasets in place);
- empty model folders (D158);
- moving datasets or models between orgs.

---

## 2. Design decisions

The numbering continues from 021.

### D158. Model names are paths

- **A model's name is a path:** segments joined by `/`, each following D98's rule (lowercase
  ASCII letters, digits, `.`, `-` and `_`, starting with a letter or digit, at most 100
  characters), at most 255 characters in all. `olmo2-1b` and `eval/olmo2-1b` are both names.
  Every existing name is a one-segment path, so nothing is renamed.
- **Folders are implicit.** A folder exists while some live model is under it, as an S3 prefix
  exists while an object is under it. There is no folder table and no "New folder" button in the
  Models tab: saving `acme/eval/x`, or moving a model to `eval/x`, makes the folder. Deleting the
  last model in a folder removes it.
- **A path is a model or a folder, never both.** `eval/olmo2` can't be a model while
  `eval/olmo2/1b` is one, as a dataset can't hold another dataset (013, section 6). Creating,
  renaming, moving and restoring take an advisory lock on the scope (D159) and refuse with 409
  ("eval/olmo2 is a folder of models.", or "eval/olmo2 is a model.") when the new path is
  taken, is a folder, or is under a model. Names in the trash count, as D134 says.
- **The column stays `models.name`,** now holding the path, so no column is renamed during a
  deploy (020). An index on `(org_pk, project_pk, name text_pattern_ops)` makes folder listings
  and the prefix checks above index scans.
- **Listing a folder** shows one level, as D87 does: models first (name, latest version, primary
  version, size, updated), then folders with the number of models under them. 100 entries per
  page, keyed on the name.
- **Folders can't be selected** in the Models tab's list (D130), only models. Moving or deleting
  a whole folder of models is selecting the models in it. This keeps a model's delete what D109
  says it is, one trash entry per model.

### D159. Every dataset and model has a scope

- **A scope** is either the org (**global**) or one project of the org. Names are unique within
  a scope only: the global `acme/eval/mmlu` and `eval/mmlu` in project `pretrain` are different
  datasets.
- **Models:** `models.project_pk`, null for global. Uniqueness moves from `(org_pk, name)` to two
  partial unique indexes, one for each kind of scope. `org_pk` stays set on project models, so
  everything the org owns is still one query (org deletion, retention, the sweep).
- **Datasets** have no table (D85), so their scope is where they are in S3 (D160).
- **The project is named, the data is keyed by id.** Refs, URLs and routes name the project, as
  runs' do. Storage and rows use the project's pk, so renaming a project moves nothing (as D12
  keeps runs' files in place) and a new project that reuses a deleted one's name starts empty.
- **Why the folder is named by pk, not by project name** (`-projects/412/`, not
  `-projects/pretrain/`): a name would tie the data to the project's name, so a rename would copy
  every byte, and a project deleted and created again would find the old one's data.

### D160. Where a project's datasets live

- **In `flock-datasets`, under a reserved root folder.** A project's datasets are under
  `-projects/{project pk}/` of the org's `flock-datasets`. `eval/mmlu` in project `pretrain`
  (pk 412) is `s3://flock-datasets/-projects/412/eval/mmlu`. No new bucket, no new managed kind,
  and nothing changes in D85.
- **D87's detection runs unchanged** with `-projects/{pk}/` as the root, so a project's Datasets
  tab is the Datasets tab with a different root.
- **`-projects` is reserved in the global scope.** The global Datasets tab's root listing skips
  it, and every global path the API takes (D87's `at`, D95's new paths, D131's moves, D115's
  grants, D93's restores) refuses a first segment of `-projects`, with "-projects is where
  Flock keeps projects' datasets." The name starts with `-` so it reads as Flock's own, as the
  model trash routes sit under `-/`.
- **The proxy guards the prefix.** A PUT, copy or multipart upload under `-projects/` of an
  org's `flock-datasets` is refused (`AccessDenied`, "-projects/412/ isn't a project of acme")
  unless its second segment is the pk of a live project of that org. The proxy caches each
  org's live project pks and drops the cache over the bus when a project is created or deleted,
  as it does for open model uploads (D101). Reads and deletes are allowed, so the Storage tab
  and S3 clients can still clean up. This stops:
  - a job that keeps writing into a project after its deletion, which would recreate the folder
    after the purge (D167);
  - a write to another org's project pk, or to a folder under `-projects/` that isn't a project.
- **Writers name the dataset,** not the URI. `datasets.location(path, project=)` (D164) asks the
  API where a dataset lives and returns the URI with storage options, so nobody types a
  project's pk. The dataset page's "Access in S3" dialog shows the real URI and the snippet
  with `project=`.
- **An org that already has a root folder named `-projects`.** Dataset paths allow it
  (`[A-Za-z0-9._-]+`), so Flock can't rule it out, and it would disappear from the global tab
  (still in Storage) and become read-only through the proxy. Before deploying, the plan lists
  `-projects/` in every org's `flock-datasets` (one LIST each). If any org has one, it's
  renamed by hand first.
- **Why not a bucket of its own.** It would add a third managed bucket kind for every org, and
  moves between scopes would cross buckets. One bucket keeps moves, the trash and the Storage
  tab as they are, at the cost of one reserved name.

### D161. Where a model's folder goes

D134 already separates a model's name from its folder (`models.prefix`), so scope and folders
only change where a **new** model's folder goes:

| Model | Folder in `flock-models` |
| --- | --- |
| global, `eval/olmo2-1b` | `eval/olmo2-1b/` |
| in project 412, `sweeps/lr-3e-4` | `-projects/412/sweeps/lr-3e-4/` |

- **`-projects/`** can't clash with a global model's folder, since model names start with a
  letter or digit (the same reason the trash routes sit under `-/`). It's the same reserved
  folder as in `flock-datasets` (D160), so both buckets read the same way.
- **Uploads stay in `{prefix}u/{ulid}/`.** The stray sweep's pattern,
  `([^/]+)/u/([0-9A-HJKMNP-TV-Z]{26})/`, becomes `(.+)/u/(...)/`. A model segment can't be a
  ULID, since names are lowercase and ULIDs are upper case, so `x/u/{ulid}/` has one reading.
- **The proxy's write rule (D97) doesn't change.** It already checks writes against the open
  upload's prefix, read from the row.
- **Renames and moves keep the folder,** as D134 does. A model promoted from a project (D166)
  keeps its `-projects/412/…` folder. So every delete of model files, including a project's
  purge (D167), goes through the model rows and their prefixes, never through
  `-projects/{pk}/` as a whole.

### D162. Naming a project's datasets and models

The scope is an argument, not part of the name. Global refs stay `org/path`, and a project's
datasets and models are named by their path plus a `project`:

| In Python | On the CLI | Means |
| --- | --- | --- |
| `"acme/olmo2-1b"` | `acme/olmo2-1b` | global model `olmo2-1b`, as today |
| `"acme/eval/olmo2-1b@12"` | `acme/eval/olmo2-1b@12` | global, folder `eval`, version 12 |
| `"sweeps/lr-3e-4@12", project="pretrain"` | `sweeps/lr-3e-4@12 --project pretrain` | project `pretrain` of the default org, folder `sweeps`, version 12 |
| `"pretrain:sweeps/lr-3e-4@12"` | `pretrain:sweeps/lr-3e-4@12` | the same, as a shorthand: the org is `org=` (`--org`), or else the caller's default org |
| `"eval/mmlu@41", project="acme/pretrain"` | `eval/mmlu@41 --project acme/pretrain` | a dataset of project `pretrain` of org `acme`, at Lance version 41 |

- **`project=`** takes a project name, in `org=` or the default org (`api.default_org`, which
  `flock.hub.datasets` already uses), or `org/project` as wandb writes it. With a project, the
  ref is a path inside it, with no org. Without one, the ref is `org/path`, as in 014. A model
  path's first segment is never read as an org when `project` is given.
- **The `project:path` shorthand** is resolved in `flock.hub`, never by the API, which only ever
  sees the `project` query parameter (D163). A ref with a `:` before any `/` is a shorthand: the
  project is everything before that first `:`, in the default org or `org=`. Global refs always
  have a `/` before any `:`, since paths and versions can't contain `:`, so the two never meet.
  Giving both a shorthand and `project=` is an error.
- **The version** is everything after the first `@` of the path. Paths and versions can't contain
  `@`. A project name may contain `@` or spaces, and the parse above still finds it.
- **Projects named with `:`.** Names the wandb SDK sends are taken as is (`api/deps.py`). wandb
  itself refuses `:`, but a project that has one can only be named with `project=`.
- **Written-down refs.** Places that keep a model reference as data record its parts, not a
  string: eval's provenance gets `model_org`, `model_project` (null for global) and
  `model_path` beside `model` and `model_digest` (D106), so a result never depends on whose
  default org read it. The web app shows a project's model as `pretrain:sweeps/lr-3e-4@7`
  inside its own org, and its snippets use `project=`.
- **Old refs keep working.** Every ref written today is a global one-segment ref.
- **Renames.** A ref through an old project name or an old model path gets 404, as D134 says.
  Eval's provenance keeps the digest (D106), so the exact bytes are still named.
- **Why not one folder tree for everything** (`acme/pretrain/eval/mmlu`, with global root
  folders that share a project's name banned). It reads best, but the ban can't be kept:
  - `wandb.init` creates a missing project (D12), so a project can appear with the name of a
    global folder that already holds data, and refusing a run's init over a folder name would
    break logging.
  - The reverse check can't be kept for datasets either: any S3 client writes to
    `flock-datasets` (D85), so nothing stops a root folder named after a project.
  - Project names may hold upper case, spaces and `@`, which model segments can't (D98). Many
    projects would need a second, mangled name to appear in a path.
  - Renaming a project onto a global folder's name, and orgs that already have both, would need
    their own rules.

  Each of these needs a lookup or a refusal in a place that has neither today. An argument keeps
  the two namespaces apart without one.

### D163. The API

- **Scope is a query parameter,** `project` (a project name), on every dataset and model route.
  Absent means global. The datasets routes already take the dataset as `?path=` (D94), so they
  only gain `project`. A project that doesn't exist or is deleted gives 404.
- **Model routes take a path.** `/{name}` can't hold a `/` next to routes like
  `/{name}/versions`, so the model path and the operation are separated by `/-/`, which no name
  can contain:

  | Route | Does |
  | --- | --- |
  | `GET orgs/{org}/models?folder&project&cursor` | one level of a folder (D158) |
  | `GET\|PATCH\|DELETE orgs/{org}/models/{path…}/-` | a model, its rename or move (D166), its delete |
  | `GET orgs/{org}/models/{path…}/-/versions`, `/-/versions/{n}`, `/-/resolve`, `/-/primary`, `/-/tags/{tag}`, `/-/uploads…` | as in 014, per model |
  | `GET orgs/{org}/models/-/trash?project` | as in 014, for one scope |

- **The one-segment routes stay,** for global models only, so `flock-hub` installed on training
  machines before this stage keeps pushing and loading. They're the same handlers with
  `project` absent.
- **Locating a dataset:** `GET orgs/{org}/datasets/location?path&project` answers `{bucket,
  prefix, uri}` for a dataset or a path that doesn't exist yet. `datasets.location` (D164) calls
  it, then adds the storage options from the caller's own S3 key.
- **Imports** from Hugging Face (D121, D123, D124) take `project`, so an import can land in a
  project.

### D164. The client and the CLI

- **`flock.hub.models`:** every function that takes a ref also takes `project=` and `org=`, and
  the shorthand (D162). `Ref` gains `project` and parses paths. `ModelVersion` gains `project`,
  and prints as `acme/eval/olmo2-1b@12` when global or `pretrain:sweeps/lr-3e-4@7` in a
  project. The local cache becomes `~/.cache/flock/models/{org}/{digest}/`, since a digest
  already names the bytes and a path with a project and folders has nothing to add to it. Old
  cache folders are still read.
- **`save_to_hub` with a `run`** and no `project` still saves globally. Saving into the run's
  own project is `project=run.project`, written out, so where a checkpoint lands is always
  visible in the call.
- **`flock.hub.datasets.location(path, project=None, org=None)`** returns a `DatasetLocation`
  with `uri` and `storage_options`, for global and project datasets alike. `import_from_hf`
  takes `project=` too.
- **`flock models`:** every command takes `--project` (and `--org`) and the shorthand. `list`
  takes a folder (`acme/eval`, or `sweeps` with `--project pretrain`) and lists one level.
  New: `flock models move REF NEW_REF [--to-project P | --to-global]`, which renames, moves
  between folders and changes scope (D166).
- **`flock datasets`:** `ls` and imports take `--project`. New: `flock datasets move` with the
  same options as `flock models move`.
- **Eval:** a `flock:` model id takes the shorthand, so `flock eval` accepts
  `flock:pretrain:sweeps/lr-3e-4@12`, resolved in the default org. `ModelRef.parse` splits as
  D162 says, not on the last `@`, and the provenance records the parts (D162).

### D165. Who can do what

- **Members and admins** read and write every scope's datasets and models, as today (D54). A
  project is a namespace and a lifecycle, not a permission boundary, which matches runs: every
  member can already log to and delete runs in every project.
- **Outsiders never see project datasets and models,** even in a public project. The project's
  Datasets and Models tabs aren't shown to them, and the routes answer as D154 does for a
  private dataset. Public grants (`public_datasets`, D155) stay for global datasets only.
- **Why not follow the project's visibility:** 021 made every dataset private until an admin
  grants its exact path, because publishing a project shouldn't publish data with it. Following
  the project would bring that back.

### D166. Moving between folders and scopes

Moving is how a project's dataset or model becomes global ("promote"), and how a global one
is tucked into a folder.

- **Models move in the registry.** `PATCH models/{path}/- {name?, project?}`, where `project:
  null` means global, changes `name` and `project_pk` in one row update. Files stay in their
  folder (D161), so a move is instant whatever the size. It's refused while an upload to the
  model is open or an import into it runs, as D134's rename is.
- **Models get Move.** D134 had no Move for models because names had one segment. The Models
  tab's selection bar (D130) and row menu (D133) get Move, with D131's dialog: a destination
  folder, and a scope picker listing "Global" and the org's projects. Each model keeps its last
  segment, as datasets do.
- **Datasets move by copying,** as D95 and D131 do today, with the destination's bucket and
  prefix from its scope. Both scopes are in `flock-datasets`, so a move between them is the same
  copy within the bucket as any rename, `CopyObject` and `UploadPartCopy` above 5 GiB.
  D95's order, its write check and its limits (10,000 objects, 100 GiB) apply unchanged, and
  above the limits the API answers with an `aws s3 mv` command between the two URIs. `POST
  datasets/move` gains `to_project`.
- **Public grants on a move:** D156 still decides within the global scope. A dataset moved into
  a project loses its grant (D165). A dataset moved to global from a project arrives private, as
  every new path does (D155).
- **Runs keep their links.** A version's training run link uses the run's pk (D106), whichever
  scope the model is in.

### D167. A project's deletion takes its datasets and models

- **Deleting a project** already marks it and its runs deleted at once, and a background purge
  removes them (D80). The purge now also removes, in this order: the project's open uploads
  (aborted upstream), its models' files (through each model's prefix, D161) and their trash
  copies, its dataset trash entries' copies, and everything under `-projects/{pk}/` in
  `flock-datasets`. The proxy refuses writes there from the moment the project is deleted
  (D160), so nothing lands behind the purge. Then the rows go, with `project_pk` cascading.
- **For good, not to the trash.** Runs are deleted for good (D15), and a trash entry for a
  dataset whose project is gone has nowhere to be restored to. The delete dialog says what goes
  with the project: "3 datasets and 12 models (412 GB) are deleted with this project. Move
  anything you want to keep to Global first." with a link to each tab.
- **An org's deletion** is unchanged: its buckets go, project folders with them.

### D168. Trash entries know their scope

- **`dataset_trash.project_pk`,** null for global. Copies of a project's datasets go to
  `{org pk:05}/{trash pk}/` of the trash bucket, as D93 says, so nothing about the trash's
  layout changes. Restore puts a dataset back in its project, at its path or at another path in
  the same scope (D93's dialog).
- **Model trash entries** are model rows with `deleted_at`, which already carry `project_pk`.
- **Org settings' "Deleted datasets" and "Deleted models"** get a Project column (empty for
  global). A project's own tabs don't show its trash, since only admins restore (D93).

### D169. The web app

- **The org's tabs keep their URLs.** The sidebar's Datasets and Models stay the global scope.
  The Models tab becomes a folder browser at `/models/{org}/{path…}`, one route like the
  Datasets tab's, where the API decides whether a path is a folder or a model. Every existing
  `/models/{org}/{name}` URL is a one-segment path and still opens the model.
- **A project gets two tabs.** The project page's header gets a row of links, Runs, Datasets and
  Models, shown to members when S3 is on:

  | URL | Page |
  | --- | --- |
  | `/{entity}/{project}` | runs, as today |
  | `/{entity}/{project}/datasets/{path…}` | the project's datasets, a folder or a dataset |
  | `/{entity}/{project}/models/{path…}` | the project's models, a folder or a model |

  `datasets` and `models` sit next to `runs`, which already takes that place in the URL, so no
  project or run name is shadowed.
- **One set of components.** The folder listing, the dataset page, the model page and their
  dialogs take a scope, which picks the API's `project` and the URLs they link to. Breadcrumbs
  start with the project (Project › Datasets › folders › dataset).
- **Empty states** show the snippet for that scope: `location` and `save_to_hub` with
  `project=` filled in.
- **Moves** (D166) use one dialog with a scope picker, defaulting to the scope shown. Moving out
  of a project says "It will be visible to the whole org at acme/eval/mmlu."
- **The run page's Checkpoints section** shows each version as `ModelVersion` prints it (D164),
  so a checkpoint in the run's project reads `pretrain:sweeps/lr-3e-4@7`.
- **The Storage tab** shows `-projects/` in `flock-datasets` and `flock-models` as it is. Its
  folders are project pks, and each one links to its project's Datasets or Models tab.

### D170. Deploying it

The migration only adds (020), and the old release keeps working on it, with two effects during
the deploy:

- **The old release sees project models as global ones,** since it doesn't know `project_pk`, so
  they may show in the org's Models tab until the old release is gone. Nothing is written
  wrongly: its routes read and write by name, and the old unique index (below) keeps names
  apart.
- **The old unique index stays until the contract.** `models_org_name` on `(org_pk, name)` is
  dropped by `0024_models_scope.contract.sql`, once no old replica is left. Until then a project
  model can't take a name a global model has (409). The new indexes are added in the expand
  file, so both hold during the window.

---

## 3. Plan

One doc PR first, then the code on its own branch.

### Part 1. Migration

Before deploying, list `-projects/` (one LIST, `max-keys=1`) in every org's `flock-datasets` and
`flock-models`. Any org that already has the folder gets it renamed by hand first (D160).

`0023_project_scopes.sql`:

```sql
-- Model folders and project scopes (022): models get a project, and dataset trash entries too.
-- Projects' datasets live in flock-datasets under -projects/{pk}/, which needs no schema.
ALTER TABLE models ADD COLUMN project_pk bigint REFERENCES projects ON DELETE CASCADE;
CREATE UNIQUE INDEX models_global_name ON models (org_pk, name)
    WHERE project_pk IS NULL AND deleted_at IS NULL;
CREATE UNIQUE INDEX models_project_name ON models (project_pk, name)
    WHERE project_pk IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX models_folders ON models (org_pk, project_pk, name text_pattern_ops)
    WHERE deleted_at IS NULL;

ALTER TABLE dataset_trash ADD COLUMN project_pk bigint REFERENCES projects ON DELETE CASCADE;
```

`0024_models_scope.contract.sql` drops `models_org_name` (D170).

### Part 2. API

- `core/models.py`: path names and the folder checks (D158), scope on every query, folder
  listing, the new prefix for new models (D161), the stray pattern, moves (D166), and the
  project purge's part (D167).
- `core/datasets.py`: a scope that picks the root prefix (`""` or `-projects/{pk}/`) for every
  read, write, move, delete and trash call, `-projects` refused in global paths and skipped at
  the global root (D160), cross-scope moves (D166), and `location`.
- `s3_proxy`: the write rule for `-projects/` in `flock-datasets`, with its cache of live
  project pks per org, dropped over the bus on project creation and deletion (D160).
- `core/store.py`: the project purge calls both (D167). The delete dialog's counts come from a
  new `GET projects/{entity}/{project}/storage` (number of datasets and models, and bytes).
- `api/models.py` and `api/datasets.py`: `project`, the `/-/` routes beside the old ones (D163),
  and refusals for outsiders (D165).
- Imports (017) take `project`.

### Part 3. Client and eval

- `flock.hub.models._common.Ref` with `project=`, `org=` and the shorthand, and
  `flock.eval.model.ModelRef.parse` with the provenance's new fields (D162), the cache key,
  `datasets.location`, and both CLIs' `--project`, `move` and folder listings (D164).

### Part 4. Web

- The project header's links and the two project routes, the Models tab's folder browser, the
  scope passed through the shared components, Move for models, the scope picker, the Project
  column in org settings, the project delete dialog's counts, and the Storage tab's links (D169).

### Tests

- `tests/app/test_models.py`:
  - nested names: creating, listing one level with paging, a model under a model refused, a
    model at a folder's path refused, names in the trash counting;
  - scope: the same name in global and in a project, the same name in two projects, project
    models absent from the global list and the other way round;
  - moves: folder to folder, project to global and back, a taken destination, a move refused
    during an open upload, upload and download through the new ref, the old ref giving 404;
  - the one-segment routes still working for global models;
  - a project's purge removing its models' files and trash copies, and leaving a model promoted
    out of it, whose files are still under `-projects/{pk}/`, untouched;
  - the stray sweep finding nested `u/{ulid}/` folders.
- `tests/app/test_datasets.py`:
  - detection, info, rows, versions and tags at a project's root; `location`; `-projects`
    hidden at the global root and refused as a global path;
  - the proxy: writes under a live project's pk allowed, and refused under a deleted project,
    another org's project and a non-project folder of `-projects/`; reads and deletes allowed;
  - moving a dataset and a folder between scopes in both directions, with D95's write check and
    the limits; a public grant dropped on a move into a project, and none created on a move out;
  - the trash for a project's dataset: delete, restore, delete now, and its rows going with the
    project;
  - a project's purge emptying its prefix; a renamed project keeping its datasets;
  - outsiders refused on a public project's datasets.
- `tests/app/test_migrations.py`: the expand file on a database with models, and the contract.
- `tests/hub`, `tests/eval`: global refs with folders and versions; `project=` with a name and
  with `org/project`; the shorthand with `@` and spaces in the project name; the shorthand and
  `project=` together refused; `:` refused in paths; a provenance written in one default org
  read in another.
- Web specs: the project tabs, the folder browser for models, breadcrumbs per scope, the move
  dialog's scope picker, the project delete dialog's counts, and the Project column.
- **Manual,** on the local stack: save checkpoints from a short run into its project, load one
  by tag, write a Lance dataset with `location` into the same project, promote the best
  checkpoint and the dataset to global folders, rename the project and load through its new
  name, then delete the project and check the promoted ones remain.

### Docs

- The Datasets and Models guides: scopes, `project=` and the shorthand, with the snippets of
  section 1.
- API reference for `project`, the `/-/` routes and `location`.
- Mark this file `applied`, with a "How it was actually built" section.

---

## 4. Known gaps after this stage

- **No per-project permissions** (D165). Anyone in the org can change any project's data.
- **Project datasets and models are never public** (D165).
- **No storage usage per project,** beyond the delete dialog's counts.
- **Empty model folders** can't be renamed or moved, only made and deleted (section 6).
- **Deleting a project deletes its data for good** (D167), with no trash.
- **Raw S3 paths of project datasets** use the project's pk (D160), so S3 clients and the
  Storage tab show numbers rather than names. Refs hide this everywhere else.
- **Moving datasets between scopes copies bytes,** bounded by D95's limits (D166). Models move
  instantly.
- **A promoted model's files stay under `-projects/{pk}/`** in `flock-models` (D161).

## 5. Open for review

1. **Naming a project's data** with `project=` and a `project:path` shorthand resolved in
   `flock.hub` (D162), rather than one folder tree where projects are root folders. D162 says
   why the folder tree was set aside.
2. **Deleting a project deletes its datasets and models for good** (D167), or moves them to the
   trash, restorable to global? This proposal deletes them, as runs are, and asks first.
3. **Members-only even in public projects** (D165), or follow the project's visibility?
4. **`-projects` as the reserved folder name** in both buckets (D160, D161). The deploy checks
   that no org already uses it.
5. **Implicit model folders** (D158), or a folder table with empty folders and a "New folder"
   button like the Datasets tab's?
6. **"Database" in the request is read as "dataset".** If a per-project database of something
   else (run metadata, eval results) was meant, this doc covers only datasets and models.

## 6. How it was built

The plan above was followed, with these differences and details:

- **Model routes are registered twice.** A `both()` decorator in `api/models.py` puts each
  handler at the old one-segment route (`/{name}`, `/{name}/versions`, ...) and at
  `/{name:path}/-...`, so old clients keep working and nested paths need the `/-` separator.
- **Names stay unique in the org until the contract runs.** `0023_project_scopes.sql` adds the
  per-scope partial unique indexes, but `models_org_name` stays until
  `0024_models_scope.contract.sql` drops it (D170). Until then, a project model can't take the
  name of a global model or of another project's, and the API answers 409 `taken`.
- **`list_models` returns the folder listing** (a dict with `kind`, `models` and `folders`)
  rather than a list of models, since a folder's subfolders have to come back too.
- **The model cache moved** to `~/.cache/flock/models/{org}/{digest}/`, so two orgs' models
  never share a folder. Earlier downloads are fetched again once.
- **Project answers gain `id`,** the project's pk, so the Storage tab can link each
  `-projects/{id}/` folder to its project.
- **Hugging Face imports into a project** carry `project`, and the `command` a refused import
  answers names the project with `--project`.
- **A project's pages are in the sidebar,** not in a row of links in the project's header. On
  any page of a project the sidebar slides over to its own panel: Home to go back, the
  project's name, then Runs, Datasets and Models.
- **Empty model folders exist after all,** answering open question 5: a `model_folders` table
  holds the folders someone made, and the Models tab has a **New folder** button like the
  Datasets tab's. `POST -/folders` makes one and `DELETE -/folders` deletes one with no live
  model under it. A made folder's path is taken like a model's (D158), and a model can be saved
  inside it. Folders with models under them stay implicit.
- **The organization's scope is called "Workspace"** in the dashboard, such as in the Move
  dialog's project picker, rather than "Global". The API's `project: null` is unchanged.
- **`results.json` goes to schema 10** for the provenance's `model_org`, `model_project` and
  `model_path`. Eval stage 041 (PR #138) reads results files back and refuses fields its types
  don't have, so a file with the new fields must say it has a newer layout. `read_results`
  reads 8 to 10, and `flock eval log` logs 6 to 10.
- **Built on 023 (forks and blends, PR #139),** which landed its migration as `0022`, so this
  stage's are `0023_project_scopes.sql` and `0024_models_scope.contract.sql`. Forks and blends
  cross scopes as 023 says (D171): the API's fork, blend and make-independent routes take a
  dataset as a path or `project:path`, as `flock.hub` names them (D162), and answer the same
  way. The fork dialog has the Move dialog's project picker, and a blend is made in the scope
  shown. Deleting a project is refused while a dataset outside it reads from one of its
  datasets (D177); its own forks and blends go with it, releasing their pins (D173).
- **The trash lists' Project column** is hidden on narrow screens, where it left no room for
  the name.

Checks run in the orb: `tests/app` and `tests/s3` (577 passed; the 6 `docker compose` tests in
`test_settings.py` can't run without Docker), `tests/hub`, `tests/cli` and `tests/eval`, ruff,
the web app's `pnpm check`, `pnpm lint` and `pnpm test`, and the docs build and link check.
The web pages were looked at on a local stack (PostgreSQL, moto, the API and S3 proxy, Vite)
with models pushed and Lance datasets written into a project and globally. The manual run in
Tests (a short training run saving checkpoints into its project) wasn't done.
