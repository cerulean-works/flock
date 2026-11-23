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

An eval run that `flock eval run --wandb-project` logged carries its `results.json` and one predictions file per task. Every eval run's page has a **Results** tab and opens on it; a run without the file says so there:

- **Suites:** each suite's score, its metric, how many members it has and its suite hash.
- **Tasks:** each task's primary metric and score, its number of docs, its task hash, its dataset (with a lock on a private one) and, when any task lost tokens, how many docs had their context cut to fit the model.
- **Items:** each doc of a task whose primary metric is an accuracy, with its question and choices and the run's pick, its wrong ones by default. See [read the items](#read-eval-items).
- **Provenance:** the model and its digest, the instrument (dtype, batch tokens, attention, SDPA kernel), the libraries, the flock commit and the command.

Scores show four decimals; hover one for its exact value. Hashes show their first 12 characters. Hover one for the whole value, or copy it. Two scores are comparable only when their hashes are equal, as [comparing results](../evals/comparison) explains.

### Compare two eval runs[[compare-two-eval-runs]]

1. In **Table**, check exactly two eval runs and click **Compare items**. From a model version, open **Evals**, check two runs and click the same button. From an eval run, click **Compare with…** on its **Results** tab or in its **⋯** menu, then pick the other run.
2. **Items** shows one task's docs side by side, with what each run picked. Choose the task, then one of four groups: docs only A got right, only B got right, both got wrong or both got right. Each shows its count.
3. Below, every task both runs scored under the same task hash is listed with each run's accuracy on the docs compared, A's lead in points and the four counts. Click a task to read its items above. On two large runs the counts take a few seconds to arrive after the page. A link that names a task shows its items meanwhile.
4. To compare another pair, click either run, or press <kbd>A</kbd> or <kbd>B</kbd>. Type to search the project's eval runs, use the arrow keys and press <kbd>Enter</kbd>. Press <kbd>Backspace</kbd> in the empty search box to pick another project.

Tasks that can't be compared are listed below with the reason, such as another task hash or a primary metric that isn't an accuracy. Runs scored with another instrument aren't compared at all, and the page says why. The counts are those of `flock eval diff`, and the items those of [`flock eval items`](../evals/commands#flock-eval-items), computed by Flock from the runs' files. A task on a private dataset is compared, and its text shown, for your organization's members only: anyone else sees it among the tasks not compared, with no predictions.

### Read the items[[read-eval-items]]

Each doc is one row: its id, the part of its question that varies (the lines every question of the list shares, such as `Answer:`, are left out), the gold choice, and for each run its pick with a check or a cross, and its margin. With two runs, the last column is the gap between their margins.

A run's margin is its gold choice's score minus its best wrong choice's, in the task's primary metric: per character for `acc_per_char`, the log-probability for `acc_raw`. Above zero the run got the doc right, and the further from zero, the surer it was. A margin that rounds to zero can show either sign: the scorer picked in FP32, and Flock computes the margin in double precision. The tint follows the sign and grows with the margin. A task scored by `acc_greedy` ranks no choices, so its rows have no margin.

By default the rows that tell most come first: with two runs, the biggest gap between their margins, and with one run, its surest docs, right or wrong. **Dataset order** switches back. Above the table, the task's score for each run, the difference in points and how many docs only one run got right show whether a net gain hides churn.

Open a row and it grows in place, one row at a time: the whole question as scored, then a line per choice in the same columns, the gold one marked, with each run's share (a softmax of the metric's scores, a probability only for `acc_raw`), probability and log-probability. A run's pick has a solid bar and darker numbers.

Drag a column's edge to resize it, or double-click the edge to reset it; your browser remembers the widths. On the compare page, a table wider than the text grows toward the window's edge and then scrolls sideways.

Move with the keys named under the list:

| Key | Does |
|---|---|
| <kbd>J</kbd>, <kbd>K</kbd> | The next or previous doc, turning the page at its ends. With a doc open, open the next or previous one instead. |
| <kbd>Enter</kbd> or <kbd>O</kbd> | Open the doc whose row has the focus. |
| <kbd>Esc</kbd> | Close the open doc. |

The page's address follows the list: the task, the group, the order, the page and the open doc. Copy it to send someone straight to that doc. A doc of a task on a private dataset opens for your organization's members only.

Items are 20 to a page. Runs scored before predictions held text (results schema 10 and older) have no items: the Results tab says so, and the compare page keeps the doc ids of the docs only one run got right. Score the model again to read them.

## Organize projects and runs

Members can change their own runs; organization admins can also change other people's runs and manage projects.

1. Select runs with the table checkboxes. Use **Tags** to organize a group, or open a single run to edit its details.
2. Use **Move** for finished runs that belong in another project of the same organization. Review the destination, then check the runs there. Their run IDs and logged data are retained.
3. To rename a project, finish its active runs first and use the project menu. Check its new entry in the organization afterwards.
4. If deleting runs or a project, review the affected resources before confirming.

> [!CAUTION]
> Run and project deletes permanently remove their history, logs and files. They have no trash; recovery requires a backup.

[Run mutation rules](run-management) explains active-run conflicts, bulk dry runs, forced deletion and clients that resume at old paths. [Permissions](permissions) lists who can change each resource. Developers building another frontend can use [Build a dashboard client](build-dashboard-client).
