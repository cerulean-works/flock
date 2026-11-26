# Compare runs with groups

Groups turn a project's runs into a few named experiments, such as a learning rate sweep or an ablation, and compare each experiment's best run. This page covers creating groups, reading the **Groups** page, and working inside one group.

## What a group is

A group is a saved list of runs in one project, with the filter that found them. A run can be in several groups. Runs that are in no group are **ungrouped**.

A group never changes by itself. When a new run matches its filter, the group offers it as a **new match**, and you decide whether to add it. This keeps a group a record of the experiment as you meant it: a stray debug run that happens to match can't move its best result without you noticing.

Each group has a **best by** setting: a summary metric and a direction, such as `loss ↓` (lowest is best) or `val_acc ↑` (highest is best). It uses the project's setting unless you change it for that group. A run without the metric, or with `NaN`, is never best.

## Create groups

Open a project and select **Groups** in the sidebar. If the project has no groups yet, you land on **New group**. Otherwise, select **New group** under the list of groups.

**New group** has two tabs at the top: **Auto** and **Manual**.

### Auto

**Auto** proposes groups for you, as a conversation.

1. When the tab opens, Flock reads the project's runs and proposes groups that each compare one change, such as a sweep over two settings or a feature turned on and off. Runs that don't fit any comparison stay ungrouped. This takes from a few seconds to about a minute.
2. Read the proposals. Each card shows:
   - the group's name and what it compares
   - how many runs it holds
   - the settings that vary, with their values
   - for a sweep over one or two settings, a grid of the combinations that were run, shaded from worse to better result, with the best one outlined
   - the best run and its value, and how many seeds each configuration has
3. To change the proposals, type what you want in the box at the bottom, for example `leave out crashed runs` or `split the lr sweep by optimizer`, and press Enter. You can also select one of the suggestions under the box. The new proposals replace the old ones, which collapse above them.
4. Clear **Create** on any card you don't want, or select **×** to remove it. You can rename a card by editing its name.
5. Select **Create N groups**. Flock creates the groups and opens the **Groups** page.

Nothing is saved until you select **Create N groups**. To fine-tune one proposal by hand first, select **Edit manually**: it opens the **Manual** tab with that proposal's runs.

> [!NOTE]
> **Auto** needs the assistant, which an organization admin turns on. Without it, the tab explains this and **Manual** opens instead. See [Turn on the assistant](#assistant).

### Manual

**Manual** shows every run in the project as a table, and you build the group from it.

- **Filters** choose runs by their values, for example `state` is `finished` and `config.lr` is less than `0.001`. Matching runs are in the group, and the filter is saved with it.
- **Checkboxes** add or remove single runs by hand, whether or not they match the filter. Runs added by hand are tinted differently from runs added by the filter.
- The **assistant** panel on the right turns a request in plain words into filters or hand picks, for example `finished runs with batch size 64`. Each change it makes is shown, and you can undo it. You can drag the panel's edge to resize it.
- **All runs** and **In group** switch between the whole project and the group as it stands.

Name the group in the title, then select **Create group**.

### Start from a suggestion

The sidebar lists groups that Flock spots on its own, marked **Suggested**. They come from runs that share a W&B group (`wandb.init(group=...)`), and from runs whose configs differ in only one or two settings. A W&B group that a saved group already filters on isn't suggested again: its later runs show up as that group's new matches. Select one to open it in **Manual**, ready to create.

## Read the Groups page

The **Groups** page compares the groups side by side, with one row per group and its best run.

| Column | What it shows |
|---|---|
| Metric (for example **loss ↓**) | The best run's value of the best-by metric |
| **vs best** | How far the group's best run is from the best group's; the best group reads **best** |
| **± repeats** | The spread over seeds, when the best configuration was run with several seeds |
| **runs** | How many runs the group holds |
| **Suggested** | The settings and metrics that matter most for comparing these groups |
| Other settings | Config values; with **Differences only** on, only those that differ between groups |

Values that differ from the best group's are highlighted, so you can see what changed between two experiments at a glance.

Below the table, two charts fill the rest of the page:
- **each group's best run**: the best-by metric over steps, one line per group in the group's color
- **every run, by group**: every run's final value as a faint dot, in one column per group, with the best run drawn solid

When the best-by metric was logged only as a summary, such as an eval suite's score, the first chart is left out. An eval suite is named and written as on the eval pages: `zh_tw:bpb`, a bits-per-byte score to three decimals, an accuracy as a percent such as `62.4%`.

Hover a row, a line or a dot to highlight that run everywhere on the page. Select **Best by** to change the metric or its direction for the project.

**Ungrouped runs** are folded under the groups when there are any, ranked by the same metric so a strong run outside any group isn't missed. Select **Add to group** on one to add it to one or more groups.

## Work inside a group

Select a group in the sidebar to open it. A group's page has the same tools as the project's **Runs** page, scoped to the group's runs: search, filters, columns, smoothing, full-screen panels and the eye control that shows or hides a run's curves.

### Views

Switch views with the selector at the top left, or with the keyboard:

| View | Key | Shows |
|---|---|---|
| **Table** | `T` | The table only |
| **Visualize** | `V` | The table and the charts side by side (the default for a group) |
| **Charts** | `C` | The charts, with run names beside them |

In **Visualize**, drag the line between the table and the charts to resize them.

A group of eval runs, whose runs have suite scores from `flock eval log`, opens on **Table** with **Evals** on: its runs by suites, ranked, as on the project's **Runs** page. Select **Evals** to switch back to the plain table.

### Suggested columns

The table starts with a **Suggested** section: the few settings and metrics that matter most for comparing the group's runs, such as the swept settings and the main metric. A bold line marks where it ends, and the rest of the table follows. If the assistant is off, the section holds the settings that vary between the runs, then the best-by metric and the metrics most runs log.

Hover a column header to show its grip (⠿), then drag the header sideways to move the column. The order of the suggested columns is saved with the group. Other columns move the same way as in **Columns**, within their folder (for example, a `config` key among the other `config` keys).

### Display

The **Display** menu at the top right sets how the charts draw the group's runs:

| Setting | What it does |
|---|---|
| **Group by** | Gives each value of a setting its own color, for example blue for `epochs` 3 and orange for `epochs` 10 |
| **Color by** | Shades each group's runs from light to dark by another setting, for example by `lr`, so trends within a color are easy to follow |
| **Show** | **All runs**, or **Best per group** for only the best run of each **Group by** value |
| **Repeats** | **Average (± std)** draws runs that differ only in their seed as one mean line with a shaded band. **Each run** draws them all. It appears only when the group has repeats. |

The trophy button beside **Display** sets the group's own **best by**. Its tooltip says whether the group follows the project's setting.

### Edit, review new matches and delete

The **…** menu beside the group's name holds the group's actions:
- **Edit group…** opens the group in the editor described in [Manual](#manual), in a dialog.
- **Review N new matches…** appears when runs logged since the group was created match its filter. It opens the editor with the new matches first, highlighted and not yet added. Tick the ones to add, then select **Save**. Runs you leave unticked stay on offer. A run you remove from the group is never offered again.
- **Delete group…** deletes the group after you confirm. Its runs stay in the project and in any other group that holds them.

## Turn on the assistant[[assistant]]

**Auto**, the **Manual** assistant panel and the model's **Suggested** columns use OpenAI's models from the Flock server. They need two things:

1. The server has an OpenAI API key, set in `FLOCK_OPENAI_API_KEY`. `FLOCK_OPENAI_PROPOSAL_MODEL` sets the default model that proposes groups. Both are described in [Configuration](configuration).
2. An organization admin turns the assistant on in **Run groups assistant** on the organization's page, or with `assistant_enabled` in the [Orgs API](api/orgs).

The same section picks the **Proposal model** for the organization, an OpenAI model ID such as `gpt-6.2-sol`. Leave it empty to use the server's default. **Speed** picks OpenAI's service tier for proposals: **Standard**, **Fast** (faster, at a premium per token) or **Ultrafast** (the fastest, on the models that offer it). A faster tier makes each **Auto** turn quicker. It doesn't change the **Manual** assistant or **Suggested** columns.

While the server has no key, the section says so and its controls are disabled.

Only the organization's members can use the assistant. People viewing a public project see groups and their charts, but can't create or change them.

For scripts, the [Projects API](api/projects#groups) exposes groups, their runs, Compare and proposals.
