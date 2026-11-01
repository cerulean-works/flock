"""Models in Flock (014): numbered, immutable versions of safetensors in each org's
`flock-models` bucket, with tags and a primary version.

```python
from flock.hub import models

version = models.save_to_hub(model, "acme/olmo2-1b", tags=["checkpoint-12000"], step=12000)
model = models.load_from_hub("acme/olmo2-1b", device="cuda")  # the primary version
```

Saving and loading models needs torch and safetensors (`flock-hub[models]`), imported only
when used. Everything signs in as [`~cli.credentials.resolve`] does.
"""

from flock.hub.models._common import ModelError, ModelFile, ModelVersion
from flock.hub.models.cache import download, load_from_hub, load_training_state
from flock.hub.models.registry import list_models, set_primary, tag, untag, versions
from flock.hub.models.upload import PendingUpload, resume_uploads, save_to_hub

__all__ = [
    "ModelError",
    "ModelFile",
    "ModelVersion",
    "PendingUpload",
    "download",
    "list_models",
    "load_from_hub",
    "load_training_state",
    "resume_uploads",
    "save_to_hub",
    "set_primary",
    "tag",
    "untag",
    "versions",
]
