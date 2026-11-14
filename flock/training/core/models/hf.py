"""Train `transformers` causal LM architectures as Flock models (D125 to D128)."""

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Self

import torch
import transformers
from transformers import CONFIG_MAPPING, PretrainedConfig, PreTrainedModel
from transformers.models.auto.modeling_auto import MODEL_FOR_CAUSAL_LM_MAPPING

from flock.training.core.data_utils import Batch

from .base import BaseModel


def _causal_lm_class(
    hf_config: dict[str, Any],
) -> tuple[type[PretrainedConfig], type[PreTrainedModel]]:
    """Find the built-in config and causal LM classes a `config.json` names, or refuse it."""
    model_type = hf_config.get("model_type")
    if not isinstance(model_type, str) or model_type not in CONFIG_MAPPING:
        raise ValueError(
            f"hf_config's model_type is {model_type!r}, which the installed transformers doesn't "
            "ship. Models whose code lives in their repository aren't supported."
        )
    config_class = CONFIG_MAPPING[model_type]
    model_class = MODEL_FOR_CAUSAL_LM_MAPPING.get(config_class, None)
    built_in = (
        isinstance(model_class, type)
        and issubclass(model_class, PreTrainedModel)
        and model_class.__module__.startswith("transformers.")
    )
    if not built_in:
        raise ValueError(f"{model_type} isn't a causal language model in transformers")
    for name in hf_config.get("architectures") or ():
        if not isinstance(name, str) or not hasattr(transformers, name):
            raise ValueError(
                f"hf_config names the architecture {name!r}, which the installed transformers "
                "doesn't ship. Models whose code lives in their repository aren't supported."
            )
    return config_class, model_class


@dataclass(frozen=True, kw_only=True)
class HFCompatibleConfig:
    """Define a `transformers` causal LM architecture and Flock's settings for it.

    The config holds only plain JSON, so `config.json` rebuilds it and checkpoints compare
    equal. To change the architecture, change `hf_config`, as
    [`training.HFCompatibleModel.from_hf`] does with its overrides.

    Args:
        hf_config (`dict[str, Any]`): A `transformers` `config.json`, as a dict. Its
            `model_type` must name a causal LM that the installed `transformers` ships.
        max_position_embeddings (`int`, *optional*): Input length limit. Defaults to
            `hf_config`'s `max_position_embeddings`.
        hf_repo (`str`, *optional*): Tokenizer repository or directory, never weights.
        initializer_range (`float`, *optional*): Positive normal-initialization standard
            deviation. Defaults to `hf_config`'s `initializer_range`, then `0.02`.
        init_seed (`int`, *optional*, defaults to `42`): Model weight initialization seed.
        attn_implementation (`str`, *optional*, defaults to `"sdpa"`): The `transformers`
            attention kernel, such as `"eager"`, `"sdpa"` or `"flash_attention_2"`.

    Raises:
        `TypeError`: If `hf_config` isn't a dict.
        `ValueError`: If `hf_config` isn't JSON, names an architecture `transformers` doesn't
            ship or that isn't a causal LM, or the length limit or scale is invalid.
    """

    hf_config: dict[str, Any]
    max_position_embeddings: int | None = None
    hf_repo: str | None = None
    initializer_range: float | None = None
    init_seed: int = 42
    attn_implementation: str = "sdpa"

    def __post_init__(self) -> None:
        """Keep a JSON copy of `hf_config` and refuse architectures transformers doesn't ship."""
        if not isinstance(self.hf_config, dict):
            raise TypeError("hf_config must be a dict, as read from config.json")
        try:
            plain = json.loads(json.dumps(self.hf_config))
        except (TypeError, ValueError) as error:
            raise ValueError(f"hf_config must hold only JSON values: {error}") from None
        object.__setattr__(self, "hf_config", plain)
        _causal_lm_class(plain)
        if self.max_position_embeddings is not None and self.max_position_embeddings <= 0:
            raise ValueError("max_position_embeddings must be positive")
        if self.initializer_range is not None and self.initializer_range <= 0:
            raise ValueError("initializer_range must be positive")

    def to_transformers(self) -> PretrainedConfig:
        """Build the `transformers` config `hf_config` describes.

        Returns:
            `PretrainedConfig`: A new config of the class `model_type` names. No code from a
            model repository is run.
        """
        config_class, _ = _causal_lm_class(self.hf_config)
        fields = {k: v for k, v in self.hf_config.items() if k != "model_type"}
        return config_class(**fields)


class HFCompatibleModel(BaseModel):
    """Compute full-sequence logits with a `transformers` causal LM architecture.

    Only the definition comes from `transformers`. Construction initializes fresh weights with
    Flock's seeded initialization and never loads weights or contacts the Hub. The
    `transformers` model is `self.model`, so state-dict names are the Hugging Face names with
    `model.` in front. Use [`training.HFCompatibleModel.from_hf`] to start from a repository's
    architecture or weights.

    Args:
        config ([`training.HFCompatibleConfig`]): Architecture, tokenizer and initialization
            settings.

    Raises:
        `ValueError`: If neither the config nor `hf_config` sets a positive input length limit.

    The forward pass returns `[batch, sequence, vocabulary]` logits. Labels aren't passed to
    `transformers`, so the trainer computes the loss as it does for `Olmo2`.
    """

    config: HFCompatibleConfig

    def __init__(self, config: HFCompatibleConfig) -> None:
        """Build the `transformers` model inside Flock's seeded initialization."""
        hf_config = config.to_transformers()
        max_positions = config.max_position_embeddings or getattr(
            hf_config, "max_position_embeddings", None
        )
        if not isinstance(max_positions, int):
            raise ValueError(  # noqa: TRY004 - a missing setting, not a wrong type
                "hf_config has no max_position_embeddings: set it in HFCompatibleConfig"
            )
        initializer_range = (
            config.initializer_range or getattr(hf_config, "initializer_range", None) or 0.02
        )
        super().__init__(
            max_position_embeddings=max_positions,
            hf_repo=config.hf_repo,
            initializer_range=initializer_range,
            init_seed=config.init_seed,
        )
        self.config = config
        _, model_class = _causal_lm_class(config.hf_config)
        with self._initialize_weights():
            # transformers' own init draws from the default generator for anything Flock's
            # init doesn't cover, so seed it too. `_initialize_weights` restores it afterwards.
            torch.random.default_generator.manual_seed(config.init_seed)
            self.model: PreTrainedModel = model_class._from_config(
                hf_config,
                attn_implementation=config.attn_implementation,
                dtype=torch.get_default_dtype(),
            )
        self._materialize_buffers()

    def _materialize_buffers(self) -> None:
        """Give buffers missing from the state dict their values when built on the meta device.

        Rotary frequencies are such buffers. Loading weights never fills them, so compute them
        on the CPU, with `transformers`' own initialization, as its `from_pretrained` does.
        """
        meta = [
            (module, name, buffer)
            for module in self.model.modules()
            for name in module._non_persistent_buffers_set
            if (buffer := module._buffers.get(name)) is not None and buffer.is_meta
        ]
        if not meta:
            return
        for module, name, buffer in meta:
            module._buffers[name] = torch.empty_like(buffer, device="cpu")
        with torch.device("cpu"):
            self.model.initialize_weights()

    def load_state_dict(self, state_dict: Any, strict: bool = True, assign: bool = False) -> Any:
        """Load weights as `nn.Module` does, then tie the embeddings again if the config ties them.

        Loading with `assign=True` replaces parameters, which would leave a tied output
        projection as a separate parameter sharing storage with the input embeddings.
        """
        result = super().load_state_dict(state_dict, strict=strict, assign=assign)
        self.model.tie_weights()
        return result

    @classmethod
    def from_hf(
        cls,
        repo: str,
        *,
        revision: str | None = None,
        pretrained: bool = False,
        **overrides: Any,
    ) -> Self:
        """Build a model with a Hugging Face repository's architecture.

        `hf_repo` is set to the repository, so [`~training.HFCompatibleModel.get_hf_tokenizer`]
        loads its tokenizer. Either way the model saves in Flock's format.

        Args:
            repo (`str`): A Hub repository, such as `Qwen/Qwen3-0.6B`, or a local folder.
            revision (`str`, *optional*): A branch, tag or commit of a Hub repository.
            pretrained (`bool`, *optional*, defaults to `False`): Also load the repository's
                safetensors weights, built on the meta device and renamed as an import does.
                Otherwise the weights are fresh.
            **overrides: Fields of [`training.HFCompatibleConfig`], such as `init_seed`, or
                keys of the repository's `config.json` to replace, such as
                `num_hidden_layers=8`. A `layer_types` list of one repeated type follows a new
                `num_hidden_layers`. Mixed layer types need `layer_types` given too.

        Returns:
            `HFCompatibleModel`: The model, on the CPU when pretrained.

        Raises:
            [`~hub.models.ModelError`]: If the repository has no `config.json`, or with
                `pretrained=True`, no safetensors weights.
            `ValueError`: If the architecture isn't a causal LM `transformers` ships.
            `RuntimeError`: If the weights don't match the architecture.

        Example:

        ```python
        from flock.training.core.models import HFCompatibleModel

        fresh = HFCompatibleModel.from_hf("Qwen/Qwen3-0.6B", num_hidden_layers=8)
        tuned = HFCompatibleModel.from_hf("Qwen/Qwen3-0.6B", pretrained=True)
        ```
        """
        from flock.hub.models.files import assign_weights, weight_files
        from flock.hub.models.headers import hf_to_flock
        from flock.hub.models.hf import snapshot

        found = snapshot(repo, revision, weights=pretrained)
        hf_config = json.loads((found.folder / "config.json").read_text())
        fields = {field.name for field in dataclasses.fields(HFCompatibleConfig)} - {"hf_config"}
        settings = {k: v for k, v in overrides.items() if k in fields}
        changes = {k: v for k, v in overrides.items() if k not in fields}
        layer_types = hf_config.get("layer_types")
        if (
            "num_hidden_layers" in changes
            and "layer_types" not in changes
            and isinstance(layer_types, list)
            and len(set(layer_types)) == 1
        ):
            # A list of one repeated type is what transformers derives, so follow the new depth.
            changes["layer_types"] = layer_types[:1] * changes["num_hidden_layers"]
        hf_config.update(changes)
        settings.setdefault("hf_repo", found.repo)
        config = HFCompatibleConfig(hf_config=hf_config, **settings)
        if not pretrained:
            return cls(config)

        from safetensors.torch import load_file

        with torch.device("meta"):
            model = cls(config)
        state = {}
        for path in weight_files(found.folder):
            state.update({hf_to_flock(k): v for k, v in load_file(str(path)).items()})
        assign_weights(model, state)
        return model

    def forward(self, batch: Batch) -> torch.Tensor:
        """Return full-sequence logits for a collated batch.

        `transformers` builds its own attention mask from the 2-D one, and picks its fast
        path when nothing is padded.

        Args:
            batch (`Batch`): Token IDs and a matching mask shaped `[batch, sequence]`.
                Mask values `True` or `1` identify real tokens.

        Returns:
            `torch.Tensor`: Logits shaped `[batch, sequence, vocabulary]`.

        Raises:
            `ValueError`: If IDs are not rank two, length is outside the configured
                range, or the attention mask shape differs from the IDs.
        """
        input_ids, attention_mask, position_ids = self._check_batch(batch)
        return self._forward(input_ids, attention_mask, position_ids)

    def _forward(
        self,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
    ) -> torch.Tensor:
        """Run the `transformers` model with a 2-D boolean mask and no cache."""
        output = self.model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            position_ids=position_ids,
            use_cache=False,
        )
        return output.logits
