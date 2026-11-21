# Explore and organize experiments

Compare logged runs in Cerulean's dashboard, inspect the evidence behind a curve, and organize experiments without losing their identity.

## What the dashboard shows

Open a project in [the dashboard](https://flock.cerulean.works). **Table** shows one row per run, including attributes, configuration and summary values. A column such as `config.lr` comes from the run's config, while `summary.loss` comes from its latest summarized loss.

**Charts** plots measurements over time. The project's panels and layout form its **workspace**. Opening a run gives its own overview, charts, logs and files. These are different views of the same logged experiment.

## Filter and compare runs

1. In **Table**, add filters for a meaningful comparison, such as experiment tags, state or learning rate. Sort a column to put related experiments together.
2. Use the eye control beside each run to choose which curves are plotted, then switch to **Charts**. Table checkboxes instead select runs for bulk edits.
3. Open a metric panel and check that its run names, colors and x-axis match your intended comparison. Use consistent step definitions across training scripts.
4. Open an individual run when a point or curve needs closer investigation.

A hidden curve remains logged. Filters choose what you see, and editing a layout does not rewrite measurements. [Dashboard display rules](run-management#dashboard-display) covers live updates and workspace behavior; [step metrics](logging-behavior#step-metrics) explains custom x-axes.

## Inspect logs and files

On a run's page, open **Logs** to inspect its console output and **Files** to browse uploaded files. Download a configuration or machine-metadata file when you need to check what produced a result.

An expected file appears only if the client uploaded it. Imported W&B runs include a [limited set of files](import-behavior#what-is-copied). For scripts, the [Projects API](api/projects) exposes the same logs and file routes.

## Eval results[[eval-results]]

An eval run that `flock eval run --wandb-project` logged carries its `results.json` and one predictions file per task. Its page then has a **Results** tab:

- **Suites:** each suite's score, its metric, how many members it has and its suite hash.
- **Tasks:** each task's primary metric and score, its number of docs, its task hash, its dataset (with a lock on a private one) and how many docs had their context cut to fit the model.
- **Provenance:** the model and its digest, the instrument (dtype, batch tokens, attention, SDPA kernel), the libraries, the flock commit and the command.

Hashes show their first 12 characters. Hover one for the whole value, or copy it. Two scores are comparable only when their hashes are equal, as [comparing results](../evals/comparison) explains.

### Compare two eval runs[[compare-two-eval-runs]]

1. In **Table**, check exactly two eval runs and click **Compare items**. From a model version, open **Evals**, check two runs and click the same button.
2. The page lists every task both runs scored under the same task hash, with four counts: docs both got right, both got wrong, only A got right and only B got right.
3. Click a task to see the ids of the docs only one run got right. **Copy ids** puts a list on the clipboard.

Tasks that can't be compared are listed below with the reason, such as another task hash or a primary metric that isn't an accuracy. Runs scored with another instrument aren't compared at all, and the page says why. The counts are those of `flock eval diff`, computed by Flock from the runs' files, and the page shows ids only, never a doc's text.

## Organize projects and runs

Members can change their own runs; organization admins can also change other people's runs and manage projects.

1. Select runs with the table checkboxes. Use **Tags** to organize a group, or open a single run to edit its details.
2. Use **Move** for finished runs that belong in another project of the same organization. Review the destination, then check the runs there. Their run IDs and logged data are retained.
3. To rename a project, finish its active runs first and use the project menu. Check its new entry in the organization afterwards.
4. If deleting runs or a project, review the affected resources before confirming.

> [!CAUTION]
> Run and project deletes permanently remove their history, logs and files. They have no trash; recovery requires a backup.

[Run mutation rules](run-management) explains active-run conflicts, bulk dry runs, forced deletion and clients that resume at old paths. [Permissions](permissions) lists who can change each resource. Developers building another frontend can use [Build a dashboard client](build-dashboard-client).
