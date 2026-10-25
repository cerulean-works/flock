# Hub quickstart

This page pushes a small dataset to the hub, loads it back at a pinned version and trains on it with a PyTorch `DataLoader`.

## Connect to the hub

`flock.hub` talks to the Flock API with a Flock API key. With a key, set:

```bash
export FLOCK_API_URL=https://flock.cerulean.works
export FLOCK_API_KEY=flk_...
```

You can also save both in `~/.config/flock/hub.json`:

```json
{"api_url": "https://flock.cerulean.works", "api_key": "flk_..."}
```

The library exchanges the key for temporary S3 credentials and renews them before they expire, so a long training run keeps reading.

## Push a dataset

Create the dataset once, then push rows to it. `push` takes an HF dataset, an Arrow table, a pandas `DataFrame`, a list of rows or a dict of columns:

```python
from flock.hub import create_dataset, push

create_dataset("flock/arc-sample", description="ARC questions for smoke tests")

ds = push(
    {"question": ["Which gas do plants absorb?", "What melts ice?"], "label": [2, 0]},
    "flock/arc-sample",
    message="first two questions",
)
print(ds.version)  # 1
```

Each `push` makes one new version. By default it appends; pass `mode="overwrite"` to replace every row.

## Load it back

```python
from flock.hub import load_dataset

ds = load_dataset("flock/arc-sample")
print(ds.version, ds.num_rows)  # 1 2
for row in ds:
    print(row["question"])
```

`load_dataset` resolves the latest version once and keeps it. Pass `version=1` or a tag such as `version="v1.0"` to load another one. [Loading datasets](loading) covers versions, filters and random access.

## Train on it

A streaming dataset is a PyTorch `IterableDataset`, and HF splits its shards between the `DataLoader`'s workers:

```python
import torch

loader = torch.utils.data.DataLoader(ds, batch_size=32, num_workers=4)
for batch in loader:
    ...
```

`map`, `filter`, `shuffle` and the other HF operations return a `flock.hub.Dataset` too, still pinned to the same version.

## Work without the API

For local experiments and tests, point the hub at a folder or a bucket directly with a [`~hub.config.HubConfig`]. Datasets are then `<root>/<org>/<name>`:

```python
from flock.hub import HubConfig, load_dataset, push

local = HubConfig(root="/data/hub")
push({"text": ["hello"]}, "flock/scratch", hub_config=local)
ds = load_dataset("flock/scratch", hub_config=local)
```

Calls that only exist on the server do nothing in this mode: `create_dataset` only returns the URI, and the messages of edits that can't carry one are dropped.
