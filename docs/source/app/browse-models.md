# Browse and manage models

Use the dashboard's **Models** tab to see your organization's stored models, choose which version loads by default, tag versions and delete the ones you no longer need. To push and load models, see [Store and load models](../hub/store-models).

**Models** is in the sidebar when the Flock server has S3 storage. It lists each model with its latest version, its primary version, the primary version's size and when it last changed. A model appears once its first upload starts, and its first version once that upload finishes.

## Look at a model

Open a model to see its primary version. The header shows the version, a **Primary** badge when it's the primary one, its size, who pushed it, and who made it primary and when.

- **The version picker** opens any version by number or tag. Checkpoint tags are sorted by their step, so `checkpoint-900` comes before `checkpoint-12000`. The choice is kept in the URL as `?version=`, so you can share a link to it.
- **Load** shows the Python and shell commands that load the version shown, such as `models.load_from_hub("acme/olmo2-1b@12")` and `flock models pull acme/olmo2-1b@12`.
- **Browse files** opens the version's folder in **Storage**.

The tabs show the version in more detail. The tab is kept in the URL as `?tab=`.

| Tab | Shows |
|---|---|
| **Overview** | The version's `README.md`, if it has one, and `config.json` and `flock.json` as tables, beside its size, file count, step, run, tags and digest. |
| **Versions** | Every version, newest first, with its tags, size, the run and step it came from, and who pushed it and when. |
| **Evals** | The eval runs that scored the version, with each metric of their summaries side by side, linked to the runs. |
| **Files** | Each file's path, size and sha256. |

To see only training checkpoints, turn on **Checkpoints** above the list in the **Versions** tab. It shows the versions with a `checkpoint-{step}` tag.

## Make a version primary

The primary version is what loads when nobody names a version.

1. In the **Versions** tab, open the **⋯** menu on the version's row.
2. Click **Make primary**. The confirmation says which version loads now and which loaded before, for example "Loading acme/olmo2-1b without a version will give version 14 instead of 12."
3. Confirm. The version gets the **Primary** badge.

The page keeps showing the version you were looking at, even if you opened it as the primary.

## Tag a version

1. In the **Versions** tab, open the version's **⋯** menu and click **Add tag…**.
2. Type the name, such as `release`, and click **Add tag**.

If the tag is already on another version, the dialog asks whether to move it, and **Move tag** moves it. Checkpoint tags never move, so the dialog says which version has one and stops there. `primary` and names of only digits can't be tags.

To remove a tag, click the **×** on it. The version stays.

## Delete a version or a model

1. In the **Versions** tab, open the version's **⋯** menu and click **Delete…**.
2. Type the version's full name, such as `acme/olmo2-1b@12`, and click **Delete version**.

The dialog refuses two kinds of version, and says what to do first:

- **The primary version:** make another version primary.
- **A version with a tag other than a checkpoint tag:** move or remove that tag. Checkpoint tags are deleted with their version.

A version that eval runs scored can be deleted. The dialog says how many runs scored it, since their results will name a version that no longer loads.

To delete a whole model with every version in it, click **Delete** in the model's header and type its name, such as `acme/olmo2-1b`.

Files in `flock-models` can't be deleted from **Storage**, so a version can't lose files under an eval that scored it.

## Restore from the trash

Deleted models and versions wait 14 days in the trash. Organization admins find them under **Deleted models** in the organization's settings:

- **Restore** brings a version back with its number and its checkpoint tags, or a model back with the versions deleted with it. A model can't be restored while another model has its name.
- **Delete now** removes it for good, after you type its name. There is no undo.

## Keep fewer old versions

Training saves many checkpoints, and most are never loaded again. An organization admin can turn on **Model retention** in the organization's settings: "Keep the newest N untagged versions of each model". It's off by default.

When it's on, Flock deletes older versions that aren't primary, have no tag other than a checkpoint tag and have no eval runs. They go to **Deleted models** like any deleted version, so you have 14 days to restore one.

To keep a checkpoint for good, tag it, for example `best`, or make it primary.

## Models on a run's page

A run's **Overview** lists the model versions it's linked to:

- **Checkpoints** are the versions the run saved, with their steps and tags.
- **Models scored** are the versions an eval run evaluated.

Each one links to the version in the Models tab. The [models reference](../hub/models) lists the naming rules and every command, and the [model endpoints](api/models) list what the tab calls.
