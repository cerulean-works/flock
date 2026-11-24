"""Models in Flock (014): numbered, immutable versions of safetensors in each org's
`flock-models` bucket, with tags and a primary version.

```python
from flock.hub import models

version = models.save_to_hub(model, "acme/olmo2-1b", tags=["checkpoint-12000"], step=12000)
model = models.load_from_hub("acme/olmo2-1b", device="cuda")  # the primary version

# A model of the project pretrain, in its folder sweeps
models.save_to_hub(model, "sweeps/lr-3e-4", project="pretrain", tags=["checkpoint-12000"])
model = models.load_from_hub("pretrain:sweeps/lr-3e-4@checkpoint-12000")
```

A model's name is a path with folders, such as `acme/eval/olmo2-1b`. Each model is global (the
org's) or belongs to one project: every function that takes a model takes `project=` and
`org=`, and the `project:path` shorthand, which is resolved in `org=` or your default org.

Saving and loading models needs torch and safetensors, imported only when used. Everything
signs in as [`~cli.credentials.resolve`] does.
"""

from flock.hub.models._common import ModelError, ModelFile, ModelVersion
from flock.hub.models.cache import download, load_from_hub, load_training_state
from flock.hub.models.hf import import_from_hf
from flock.hub.models.registry import list_models, move, set_primary, tag, untag, versions
from flock.hub.models.upload import PendingUpload, resume_uploads, save_to_hub

__all__ = [
    "ModelError",
    "ModelFile",
    "ModelVersion",
    "PendingUpload",
    "download",
    "import_from_hf",
    "list_models",
    "load_from_hub",
    "load_training_state",
    "move",
    "resume_uploads",
    "save_to_hub",
    "set_primary",
    "tag",
    "untag",
    "versions",
]
