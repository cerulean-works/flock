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

## Organize projects and runs

Members can change their own runs; organization admins can also change other people's runs and manage projects.

1. Select runs with the table checkboxes. Use **Tags** to organize a group, or open a single run to edit its details.
2. Use **Move** for finished runs that belong in another project of the same organization. Review the destination, then check the runs there. Their run IDs and logged data are retained.
3. To rename a project, finish its active runs first and use the project menu. Check its new entry in the organization afterwards.
4. If deleting runs or a project, review the affected resources before confirming.

> [!CAUTION]
> Run and project deletes permanently remove their history, logs and files. They have no trash; recovery requires a backup.

[Run mutation rules](run-management) explains active-run conflicts, bulk dry runs, forced deletion and clients that resume at old paths. [Permissions](permissions) lists who can change each resource. Developers building another frontend can use [Build a dashboard client](build-dashboard-client).
