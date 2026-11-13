# Import from Hugging Face

Bring a dataset or a model from the Hugging Face Hub into your organization from the **Datasets** and **Models** tabs. The Flock server downloads it and stores it the Flock way, as the `flock datasets import` and `flock models import` commands do. Imports run in the background, so you can close the page while they run.

## Before you start

- You need to be a member or an admin of the organization, and the server needs S3 storage.
- For a gated or private repository, create a Hugging Face access token that can read it. For a gated repository, accept its terms on huggingface.co first. Flock uses the token for that import only, and never stores it.
- The server imports repositories up to a size limit (50 GB by default, set with [`FLOCK_HF_IMPORT_MAX_BYTES`](configuration)). For anything larger, the dialog shows the command to run instead.

## Import a dataset

1. Open the **Datasets** tab, at the root or in the folder you want the dataset in.
2. Click **Import from Hugging Face**.
3. Enter the repository, such as `HuggingFaceFW/fineweb-edu`, and an access token if it needs one. Click **Continue**.
4. Choose the config, if the dataset has several, and the splits. Each split shows its size from the Hub's dataset viewer.
5. Check the **Folder**. It defaults to the repository's name in the current folder.
6. Click **Import**.

Each split becomes a Lance dataset at `{folder}/{split}`, with the dataset card as its README, as described in [Where the data lands](../hub/import-datasets-from-hf#where-the-data-lands). A folder that already holds a dataset is refused, so choose a new one to import again.

## Import a model

1. Open the **Models** tab and click **Import from Hugging Face**.
2. Enter the repository, such as `Qwen/Qwen3-0.6B`, and the **Revision** (`main` by default).
3. Check the **Model name**. It defaults to the repository's name in lowercase. An existing model gets a new version.
4. Add **Tags** if you want them, separated by spaces or commas, and choose whether to make the new version primary.
5. Add an access token if the repository needs one, and click **Import**.

The new version holds the same files as one made with `flock models import`. See [What a version holds](../hub/import-from-hf#what-a-version-holds). Importing a commit that the model already has gives back the version made before.

## Follow an import

A running import shows as a row at the top of the folder it writes to, or of the models list, with a progress bar:

- a dataset downloads its files, then converts them to Lance;
- a model uploads each file as it downloads, then Flock checks the files and numbers the version.

Click **Cancel** to stop it. Nothing it wrote stays: a dataset's splits are committed only at the end, and a model's upload is aborted.

When the import ends, its row says so:

| Row | Meaning |
|---|---|
| **Imported** with **Open** | The dataset or version is ready. **Open** goes to it. |
| **failed** | The error, and the `flock` command that does the same import. |
| **was cancelled** | Someone cancelled it. |
| **stopped when the server restarted** | The server stopped while it ran. Start it again from the dialog, since the token isn't kept. |

Rows of imports that ended stay for a day, and **Hide** removes one from your view.

## Refusals

The dialog refuses an import before it starts when:

- the repository isn't on the Hub, or it's private or gated and the token can't read it;
- a model has only pickled `.bin` weights, needs code from its repository, or isn't a causal language model that Flock can train;
- a dataset has no files Flock reads and no Parquet conversion on the Hub;
- the repository is larger than the server's limit. The dialog then shows the `flock datasets import` or `flock models import` command, filled in, to run from a machine [signed in](../cli/sign-in) to Flock.

To script imports, see the [Hugging Face imports API](api/hf-imports).
