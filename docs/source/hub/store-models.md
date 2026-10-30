# Store and load models

`flock.hub.models` saves a model to your organization as a numbered version, and loads it back by number, tag or primary version. This page covers pushing a model, choosing which version others get by default, tagging training checkpoints, and picking up an upload that was interrupted.

## How versions work

Each model, such as `acme/olmo2-1b`, holds a list of versions numbered from 1. A version never changes once it's saved, so `acme/olmo2-1b@12` always loads the same weights. Versions are stored as safetensors in your organization's `flock-models` bucket.

Three kinds of names point at a version:

| Name | Example | Moves? |
|---|---|---|
| Version number | `acme/olmo2-1b@12` | Never. |
| Primary version | `acme/olmo2-1b` or `acme/olmo2-1b@primary` | When someone makes another version primary. |
| Tag | `acme/olmo2-1b@checkpoint-12000`, `acme/olmo2-1b@release` | Only when moved on purpose. |

The primary version is what you get when you don't name a version. Each model has exactly one, and the first version saved becomes primary.

Every load records the version number it resolved to, so moving the primary or a tag later doesn't change what an earlier run says it used.

## Save a model

[Sign in](../cli/sign-in) first. The workspace install includes everything `flock.hub.models` needs. Outside the workspace, install the `models` extra of `flock-hub`, which adds torch and safetensors.

Save any Flock model, such as `Olmo2`:

```python
from flock.hub import models
from flock.training.core.models import OLMO2_1B_CONFIG, Olmo2

model = Olmo2(OLMO2_1B_CONFIG)
version = models.save_to_hub(model, "acme/olmo2-1b")
print(version)  # acme/olmo2-1b@1
```

`save_to_hub` writes the weights to a local folder, uploads them, and returns once the version exists. The model is created if it's new. A version holds:

- the weights, as `model.safetensors` or shards of up to 5 GB with `model.safetensors.index.json`;
- `config.json`, the model's config;
- `flock.json`, which records the model class, the flock commit and, if you passed them, the run and step.

To push a folder that already holds these files, pass its path instead of a model, or use the CLI:

```bash
flock models push ./olmo2-1b-step12000 acme/olmo2-1b
```

## Load a model

Load the primary version:

```python
from flock.hub import models

model = models.load_from_hub("acme/olmo2-1b", device="cuda")
```

Or name the version, by number or by tag:

```python
model = models.load_from_hub("acme/olmo2-1b@12")
model = models.load_from_hub("acme/olmo2-1b@checkpoint-12000")
```

`load_from_hub` downloads the version into `~/.cache/flock/models/`, checks every file's sha256, and builds the model with the weights in place. A second load of the same version reads from the cache. To get the files without building the model, use `models.download`, which returns the folder:

```python
path = models.download("acme/olmo2-1b@12")
```

From the shell, `flock models pull acme/olmo2-1b` downloads the primary version, and `--to DIR` copies it to a folder of your choice.

To evaluate a stored model, give `flock eval` the model with a `flock:` prefix, such as `flock:acme/olmo2-1b`. The results record the version number it resolved to.

## Choose the primary version

Make a version primary when it should be what others get by default, for example the best checkpoint of a run or the one you're releasing.

In the dashboard:

1. Open **Models**, then the model.
2. In the **Versions** tab, open the **⋯** menu on the version's row.
3. Click **Make primary** and confirm.

The version gets the **Primary** badge, and loading the model without a version now gives this one.

From the shell or Python:

```bash
flock models primary acme/olmo2-1b@14
```

```python
models.set_primary("acme/olmo2-1b@14")
```

To make a version primary as you save it, pass `primary=True` to `save_to_hub`, or `--primary` to `flock models push`. It becomes primary only once the upload has finished, so nobody loads a half-uploaded version.

`primary` isn't a tag. You can't add or remove it with `tag` or `untag`, and no tag can be named `primary`. The primary version can't be deleted until another version is made primary.

## Tag training checkpoints

Tag each version you save during training `checkpoint-{step}`, where `step` is the number of optimizer steps taken before the save. A checkpoint saved after step 12,000 is `checkpoint-12000`.

Flock doesn't add this tag for you: pass it in `tags`, with the same number as `step`. Every checkpoint of a model then has a name you can work out from the step alone, and the dashboard can sort and filter them:

- the version picker and the **Versions** tab sort checkpoint tags by their number, not alphabetically;
- the **Checkpoints** filter in the **Versions** tab shows only versions with a checkpoint tag;
- retention, if your organization turns it on, treats checkpoint tags as no tag at all, so old checkpoints can still be cleaned up.

Here is a training loop that saves a checkpoint every 1,000 steps:

```python
import wandb
from flock.hub import models

NAME = "acme/olmo2-1b"
SAVE_EVERY = 1_000

run = wandb.init(project="pretrain")
models.resume_uploads(NAME)  # finish anything a crashed run left

pending = []
for step, batch in enumerate(dataloader, start=1):
    loss = model(batch)
    loss.backward()
    optimizer.step()
    optimizer.zero_grad()

    if step % SAVE_EVERY == 0:
        pending.append(
            models.save_to_hub(
                model,
                NAME,
                tags=[f"checkpoint-{step}"],
                run=run,
                step=step,
                training_state={"optimizer": optimizer.state_dict()},
                blocking=False,
            )
        )
        # Keep at most two checkpoints waiting, so a slow upload can't fill the disk.
        while len(pending) > 2:
            pending.pop(0).result()

final = models.save_to_hub(model, NAME, tags=[f"checkpoint-{step}"], run=run, step=step)
for upload in pending:
    upload.result()
```

What each part does:

- **`blocking=False`** returns as soon as the weights are on local disk, and uploads in a background thread. Training waits only for the disk write. `result()` waits for the version.
- **`run` and `step`** link the version to the run, so the run's page lists its checkpoints and the model's page shows which run and step each version came from.
- **`training_state`** stores the optimizer next to the weights, in `training_state/`. `load_from_hub` and `download` skip it unless you pass `include_training_state=True`, so loading a model for eval stays fast.
- **Restarts are safe.** With a `run` and a `step`, saving the same step of the same run again returns the version already saved instead of making another. A checkpoint tag that's already on a version is refused rather than moved, so a tag always names the step it says.

When the run is done, make the checkpoint you want others to use primary, from the **⋯** menu or with `set_primary`.

## Resume an interrupted upload

An upload that stops partway, because the process was killed, the machine went down or the network dropped, is kept for 7 days. What already reached storage stays there, and resuming sends only what's missing. A version appears only when the whole upload has finished, so nobody can load a partial one.

Resuming needs the local files the upload started from. `save_to_hub` keeps them in `~/.cache/flock/uploads/` until the version exists.

- **In Python,** calling `save_to_hub` again with the same model and folder resumes, and so does `models.resume_uploads("acme/olmo2-1b")` for every upload of a model.
- **From the shell,** running the same `flock models push` again resumes. To see what's waiting and act on one upload:

  ```bash
  flock models uploads list
  flock models uploads resume 01JD3KQ8XW
  flock models uploads abort 01JD3KQ8XW
  ```

If a file changed on disk since the upload started, resuming stops and names the file, since the bytes already uploaded no longer match it. Start a new push instead.

An upload nobody resumes expires after 7 days of no activity, and what it uploaded is deleted.

Downloads resume too. If `load_from_hub` or `download` stops partway, the next call continues each file from where it stopped.

## Delete versions

Delete a version from the **⋯** menu in the **Versions** tab. You type the version's full name, such as `acme/olmo2-1b@12`, to confirm. Deleted versions wait 14 days in a trash that organization admins restore from in the organization settings. A deleted version's number is never reused.

Some versions can't be deleted until you change them:

- **The primary version:** make another version primary first.
- **A version with a tag** other than a checkpoint tag, such as `release`: move or remove the tag first. Checkpoint tags are deleted with their version.

A version that has been evaluated can be deleted, after a warning that says how many evaluations used it.

The [models reference](models) lists every function, command and naming rule.
