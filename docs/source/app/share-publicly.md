# Share projects and datasets publicly

Make a project or a dataset public so that anyone with its link can read it, signed in or not, without being invited into your organization. Public access is read-only, like a public Hugging Face repository.

## What you need

- To be an admin of the organization. Members can't change visibility.
- The project or dataset to share. Everything starts private.

## Check for secrets first

Outsiders see everything a public project's runs logged: configs, summaries, metrics, system metrics, files and console logs. Flock can't tell which config value or log line is a secret, so it shows them all.

> [!WARNING]
> Before you make a project public, check that none of its runs logged API keys, tokens, passwords or internal hostnames. Runs you move into a public project later become public too.

## Make a project public

1. Open the project and go to its settings.
2. Under **Visibility**, choose **Public**.
3. Read the confirmation, then confirm.

The project's header now shows a **Public** badge. Copy the project's URL from your browser and share it.

To make it private again, choose **Private**. No confirmation is asked. Outsiders who have the project open lose access at once: their live updates stop, and reloading shows that the project doesn't exist.

Renaming a public project keeps it public, but its old URL stops working, as for any rename.

## Make a dataset public

1. Open the dataset's page in **Datasets**.
2. Open its menu and choose **Make public**, then confirm.

Only that dataset becomes public, at every version and tag. Its folder, the datasets beside it and the rest of `flock-datasets` stay private. Outsiders read its README, rows, schema and versions on its page. They can't download its files, because S3 access always needs a key.

A public dataset stays public when you rename it. Deleting it makes it private: if an admin restores it from the trash, it comes back private, and you decide again whether to publish it.

## What outsiders see

Anyone outside the organization, whether signed out or signed in to another organization, sees the same thing:

| Outsiders can | Outsiders can't |
|---|---|
| Open the project's runs table, charts and run pages, with live updates | Edit, move or delete runs, or log to the project |
| Read run configs, summaries, metrics, files and console logs | See GPU cost, budgets or billing |
| See who logged each run, by name and avatar | See anyone's email, or the organization's members |
| Open the organization's page, which lists its public projects and datasets | See its private projects, datasets, Storage or settings |
| Read a public dataset's page at any version | Browse `flock-datasets`, or read the dataset's objects through S3 |

A private project or dataset looks exactly like one that doesn't exist. A signed-out visitor who opens one is asked to sign in, in case they're a member.

Visitors find your public projects through the links you share, or on your organization's page. Flock has no search or listing of public projects across organizations.

## Check it worked

Open the link in a private browser window, where you aren't signed in. You should see the project or dataset with a **Public** badge, and no actions to change it.

[Permissions](permissions#public-projects-and-datasets) lists exactly what outsiders can do, and the [HTTP API overview](api/index#anonymous-reads) lists the endpoints that answer without credentials.
