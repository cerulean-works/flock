# Browse and manage models

Use the dashboard's **Models** tab to see your organization's stored models, choose which version loads by default, tag versions, rename models and delete the ones you no longer need. To push and load models, see [Store and load models](../hub/store-models).

**Models** is in the sidebar when the Flock server has S3 storage. It lists each model with its latest version, its primary version, the primary version's size and when it last changed. A model appears once its first upload starts, and its first version once that upload finishes.

## Look at a model

Open a model to see its primary version. The header shows the version and a **Primary** badge when it's the primary one. The **Overview** sidebar lists its size, who pushed it and when, and for the primary, who made it primary and when.

- **The version picker** opens any version by number or tag. Checkpoint tags are sorted by their step, so `checkpoint-900` comes before `checkpoint-12000`. The choice is kept in the URL as `?version=`, so you can share a link to it.
- **Load** shows the Python and shell commands that load the primary version, such as `models.load_from_hub("acme/olmo2-1b")` and `flock models pull acme/olmo2-1b`, and those that load the version shown, such as `models.load_from_hub("acme/olmo2-1b@12")`.
- **Browse files** opens the version's folder in **Storage**.

The tabs show the version in more detail. The tab is kept in the URL as `?tab=`.

| Tab | Shows |
|---|---|
| **Overview** | The version's `README.md`, if it has one, and `config.json` and `flock.json` as tables, beside its size, file count, step, run, tags and digest. |
| **Versions** | Every version, newest first, with its tags, size, the run and step it came from, and who pushed it and when. |
| **Files** | Each file's path, size and sha256. |

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

To delete a whole model with every version in it, click **Delete** in the model's header, or **Delete…** in the model's **⋯** menu in the list. Type its name, such as `acme/olmo2-1b`.

To delete several models at once:

1. In the list, hover a model's row and click the checkbox on its left. Shift-click another checkbox to check every row in between, or click the checkbox in the header to check them all.
2. Click **Delete** in the bar that replaces the header's buttons.
3. Type the number of models, and click the delete button.

If one model can't be deleted, the rest still are, and the dialog lists the ones that weren't. Press Escape or click **Clear** to uncheck every row.

Every type-to-confirm prompt has a **Copy** button next to the text to type, so you can paste it instead.

Files in `flock-models` can't be deleted from **Storage**, so a version can't lose files once it's saved.

## Rename a model

Click **Rename** in the model's header, or **Rename…** in its **⋯** menu in the list. Type the new name and click **Rename**.

Only the name changes, so a rename is instant whatever the model's size. The model keeps its versions, tags and primary version, and runs that link to it keep linking.

- **Old refs stop working.** After renaming `acme/olmo2-1b` to `acme/olmo2-1b-base`, `acme/olmo2-1b@3` answers 404. There's no redirect, so update the code that loads it.
- **The files stay in the old folder.** In `flock-models`, the model's files stay under its old name, and the **Storage** tab shows that folder by the old name. **Browse files** on the model's page opens the right folder.
- **The new name must be free.** No other model can have it, including a deleted model in the trash. Delete that one for good first, or pick another name.
- **Wait for uploads and imports.** A rename is refused while an upload to the model is open, or a [Hugging Face import](import-hf) into it is running, since those use the old name.

## Restore from the trash

Deleted models and versions wait 14 days in the trash. Organization admins find them under **Deleted models** in the organization's settings:

- **Restore** brings a version back with its number and its checkpoint tags, or a model back with the versions deleted with it. A model can't be restored while another model has its name.
- **Delete now** removes it for good, after you type its name. There is no undo.

## Keep fewer old versions

Training saves many checkpoints, and most are never loaded again. An organization admin can turn on **Model retention** in the organization's settings: "Keep the newest N untagged versions of each model". It's off by default.

When it's on, Flock deletes older versions that aren't primary, and have no tag other than a checkpoint tag. They go to **Deleted models** like any deleted version, so you have 14 days to restore one.

To keep a checkpoint for good, tag it, for example `best`, or make it primary.

## Models on a run's page

A run's **Overview** lists the model versions the run saved under **Checkpoints**, with their steps and tags. Each one links to the version in the Models tab. [Models API and CLI reference](../hub/models) · [Model registry endpoints](api/models)
