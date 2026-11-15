# Share projects and datasets publicly

Anyone with a public project's or dataset's link can read it, signed in or not, without being invited into your organization. Public access is read-only, like a public Hugging Face repository. Projects and new dataset paths start private. You choose which ones to make public. Existing public datasets retain their access when the visibility migration completes.

## What you need

- To be an admin of the organization, or the member who created the project. Other members can't change visibility, and only admins can change dataset visibility.
- The project or dataset to share.

## Check for secrets first

Outsiders see everything a public project's runs logged: configs, summaries, metrics, system metrics, files and console logs. Flock can't tell which config value or log line is a secret, so it shows them all.

> [!WARNING]
> Before you make a project public, check that none of its runs logged API keys, tokens, passwords, internal hostnames, or exam and held-out text or identifiers. A private source dataset does not protect text copied into a public run. Runs you move into a public project later become public too.

## Make a project public

1. Open the project and go to its settings.
2. Under **Visibility**, choose **Public**.
3. Read the confirmation, then confirm.

The project's header now shows a **Public** badge. Copy the project's URL from your browser and share it.

To make it private again, choose **Private**. No confirmation is asked. Outsiders who have the project open lose access at once: their live updates stop, and reloading shows that the project doesn't exist.

Renaming a public project keeps it public, but its old URL stops working, as for any rename.

## Share a dataset

A new dataset path in `flock-datasets` is private until an admin makes it public, including paths populated by Hugging Face imports. Outsiders read a public dataset's README, rows, schema and versions on its page, at every version and tag. They can't browse `flock-datasets` or list its datasets, and they can't download a dataset's files, because S3 access always needs a key. So a public dataset is found only through the links you share.

To make a dataset private:

1. Open the dataset's page in **Datasets**.
2. Open its menu and choose **Make private**. No confirmation is asked.

Only that exact path becomes private. Datasets inside its folder, or beside it, keep their own visibility. To make it public again, choose **Make public**, then confirm.

> [!TIP]
> To reserve an exact path as private even when a public dataset is moved there, use `DELETE /api/v1/orgs/{org}/datasets/public?path=...` ([Datasets API](api/datasets#public)).

A private dataset stays private when you rename it, and a dataset renamed onto a private path becomes private. Deleting a private dataset keeps its path private: if an admin restores it from the trash, to its path or another, it comes back private, and so does a new dataset written at its path.

The upgrade preserves existing public dataset paths as explicit grants, without publishing private datasets. During its inventory, dataset operations briefly ask you to retry. See [the visibility upgrade](operate#dataset-visibility-upgrade) for the cutover and verification. Public access is attached to an exact path across versions and deletion; writing new data to an already public path retains that grant. Revoke it before reusing the path for private content.

## What outsiders see

Anyone outside the organization, whether signed out or signed in to another organization, sees the same thing:

| Outsiders can | Outsiders can't |
|---|---|
| Open the project's runs table, charts and run pages, with live updates | Edit, move or delete runs, or log to the project |
| Read run configs, summaries, metrics, files and console logs | See GPU cost, budgets or billing |
| See who logged each run, by name and avatar | See anyone's email, or the organization's members |
| Open the organization's page, which lists its public projects | See its private projects, datasets, Storage or settings |
| Read a public dataset's page at any version | Browse or list `flock-datasets`, or read the dataset's objects through S3 |

A private project or dataset looks exactly like one that doesn't exist. A signed-out visitor who opens one is asked to sign in, in case they're a member.

Visitors find your public projects through the links you share, or on your organization's page. Flock has no search or listing of public projects across organizations.

## Check it worked

Open the link in a private browser window, where you aren't signed in. You should see the project or dataset with a **Public** badge, and no actions to change it.

[Permissions](permissions#public-projects-and-datasets) lists exactly what outsiders can do, and the [HTTP API overview](api/index#anonymous-reads) lists the endpoints that answer without credentials.
