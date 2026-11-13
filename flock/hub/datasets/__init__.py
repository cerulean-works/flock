"""Datasets in Flock (017): Hugging Face datasets imported as Lance datasets in each org's
`flock-datasets` bucket.

```python
from flock.hub import datasets

folder = datasets.download_from_hf(
    "HuggingFaceFW/fineweb-edu", "/scratch/fineweb-edu", config="sample-10BT"
)
```

Downloading needs only the Hugging Face Hub.
"""

from flock.hub.datasets._common import MANIFEST, DataFile, DatasetError, Manifest
from flock.hub.datasets._download import download_from_hf

__all__ = [
    "MANIFEST",
    "DataFile",
    "DatasetError",
    "Manifest",
    "download_from_hf",
]
