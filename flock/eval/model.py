"""Batched loglikelihood scoring with a causal language model, kept on the model's device."""

import hashlib
import itertools
import json
import logging
import posixpath
import re
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, Literal, NamedTuple

import torch
from huggingface_hub import (
    _CACHED_NO_EXIST,
    HfApi,
    hf_hub_download,
    is_offline_mode,
    try_to_load_from_cache,
)
from huggingface_hub.errors import LocalEntryNotFoundError, RemoteEntryNotFoundError
from torch.nn.attention import SDPBackend, sdpa_kernel
from transformers import AutoModelForCausalLM, AutoTokenizer, PreTrainedModel

from flock.eval.requests import ContextScores, Request, Scores, TokenValues
from flock.eval.tokenizer import SentencePieceTokenizer, Tokenizer

DEFAULT_BATCH_TOKENS = 32768
# What a model id starts with when it names a version in the Flock registry, or a local folder.
FLOCK = "flock:"
LOCAL = "local:"

logger = logging.getLogger(__name__)


class Encoded(NamedTuple):
    """The token ids of one request, or one window of a text: the context the model conditions on, and the continuation it scores."""

    context: list[int]
    continuation: list[int]


@dataclass(frozen=True, slots=True)
class ModelRef:
    """
    A model to score: a Hugging Face model repo pinned to a revision, written `repo@revision`, or a version of a model in the Flock registry, written `flock:org/path@version` for a global model, such as `flock:acme/eval/olmo2-1b@12`, or `flock:project:path@version` for a project's, such as `flock:pretrain:sweeps/lr-3e-4@12`, which is in your default org. A local folder pinned to its file digest is written `local:folder@digest`.

    Args:
        repo (`str`):
            The Hugging Face repo, the Flock model as `org/path` or `project:path`, or the local folder.
        revision (`str`):
            The Hugging Face revision, the Flock version (a number, a tag or `primary`), or the local folder's digest as given.
        kind (`str`, *optional*, defaults to `"hf"`):
            `"hf"` for a Hugging Face model, `"flock"` for a Flock one, `"local"` for a folder.
        digest (`str`, *optional*):
            The digest of the Flock version or local folder, which names its exact files. Set by [`resolve`], and `None` for a Hugging Face model, whose commit hash already does that.
        org (`str`, *optional*):
            The Flock model's org. A project's model has none until [`resolve`] finds it, in your default org. `None` for a Hugging Face model or a local folder.
    """

    repo: str
    revision: str
    kind: Literal["hf", "flock", "local"] = "hf"
    digest: str | None = None
    org: str | None = field(default=None, kw_only=True)

    @classmethod
    def parse(cls, model_id: str) -> ModelRef:
        """
        Parses `repo@revision`, or `flock:org/path` or `flock:project:path` with an optional `@version`, or `local:folder@digest`. A Flock model without a version means its primary version.

        A Flock id with a `:` before any `/` names a project of your default org: the project is everything before that `:`, so it may hold `@` and spaces. The version is everything after the path's first `@`, and paths can't hold `:` or `@`, as the hub's refs (D162).

        Raises:
            `ValueError`: if a Hugging Face id has no revision, so an unpinned model can't be scored, or more than one `@`, or a Flock id isn't `org/path[@version]` or `project:path[@version]`, or a local id's digest is not 64 lowercase hex characters.
        """
        if model_id.startswith(LOCAL):
            # The digest is the last `@`'s part, so a folder whose name has an `@` still parses.
            folder, _, digest = model_id.removeprefix(LOCAL).rpartition("@")
            if not folder or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(
                    f"model id {model_id!r} must be local:folder@digest, with the digest "
                    "`flock eval digest folder` prints"
                )

            return cls(folder, digest, "local")

        if model_id.startswith(FLOCK):
            parts = _flock_parts(model_id.removeprefix(FLOCK))
            if parts is None:
                raise ValueError(
                    f"model id {model_id!r} must be flock:org/path[@version] or "
                    "flock:project:path[@version]"
                )
            org, project, path, version = parts
            repo = f"{org}/{path}" if project is None else f"{project}:{path}"
            return cls(repo, version or "primary", "flock", org=org)

        repo, _, revision = model_id.partition("@")
        if not repo or not revision or "@" in revision:
            raise ValueError(
                f"model id {model_id!r} must be repo@revision, flock:org/path[@version], "
                "flock:project:path[@version] or local:folder@digest"
            )

        return cls(repo, revision)

    def __str__(self) -> str:
        # A folder's path isn't part of what was scored: the same files moved or copied are the
        # same model, and the command in the provenance still says where they were.
        if self.kind == "local":
            return f"{LOCAL}{self.revision}"
        prefix = FLOCK if self.kind == "flock" else ""
        return f"{prefix}{self.repo}@{self.revision}"

    @property
    def project(self) -> str | None:
        """The Flock model's project, or `None` for a global model or a Hugging Face one."""
        parts = _flock_parts(self.repo) if self.kind == "flock" else None
        return parts[1] if parts is not None else None

    @property
    def path(self) -> str | None:
        """The Flock model's path in its scope, such as `eval/olmo2-1b`, or `None` for a Hugging Face model."""
        parts = _flock_parts(self.repo) if self.kind == "flock" else None
        return parts[2] if parts is not None else None

    def resolve(self) -> ModelRef:
        """
        Returns this ref with its revision resolved to a commit hash, its Flock version to a number and digest, or its local folder checked against its digest.

        A full hash is returned as is, without asking the Hub, so pinned models load offline from the cache. A Flock version is always resolved through the registry, which also gives its digest. A local folder's files are hashed with [`folder_digest`] and must match the digest given.

        Raises:
            `ValueError`: if the Hub doesn't report a commit hash for the revision, a local folder's files don't match its digest, or a Hugging Face id names a local folder, which only `local:folder@digest` scores.
            [`~hub.models.ModelError`]: if the registry has no such Flock version.
        """
        if self.kind == "flock":
            version = _flock_hub().resolve(f"{self.repo}@{self.revision}")
            project = getattr(version, "project", None)
            repo = (
                f"{version.org}/{version.name}" if project is None else f"{project}:{version.name}"
            )
            return ModelRef(repo, str(version.number), "flock", version.digest, org=version.org)

        if self.kind == "local":
            # The real path, so a link moved to another export after this check can't be loaded.
            folder = Path(self.repo).resolve()
            digest = folder_digest(folder)
            if digest != self.revision:
                raise ValueError(
                    f"{self.repo} has the digest {digest}, not {self.revision}: its files changed, "
                    "or the id names other weights"
                )
            return ModelRef(str(folder), self.revision, "local", digest)

        # Transformers loads a folder of that name before the Hub, and a folder has no commit
        # for the revision to pin, so nothing would say which weights were scored.
        if Path(self.repo).is_dir():
            raise ValueError(
                f"{self.repo} is a local folder: score it as "
                f"local:{self.repo}@{folder_digest(Path(self.repo))}"
            )

        # 40 lowercase hex characters is how the Hub writes a full commit hash.
        if re.fullmatch(r"[0-9a-f]{40}", self.revision):
            return self

        sha = HfApi().model_info(self.repo, revision=self.revision).sha
        if sha is None:
            raise ValueError(f"the Hub reports no commit hash for {self}")

        return ModelRef(self.repo, sha)


def _flock_parts(ref: str) -> tuple[str | None, str | None, str, str] | None:
    """A Flock ref (without `flock:`) as its org, project, path and version (`""` for none), or `None` when it isn't one. A `:` before any `/` makes it `project:path`, whose org is your default one."""
    colon, slash = ref.find(":"), ref.find("/")
    if colon >= 0 and (slash < 0 or colon < slash):
        org, project, rest = None, ref[:colon], ref[colon + 1 :]
        if not project:
            return None
    else:
        org, _, rest = ref.partition("/")
        project = None
        if not org or "@" in org:
            return None
    path, at, version = rest.partition("@")
    if any(s in ("", ".", "..") or ":" in s for s in path.split("/")):
        return None
    if at and (not version or any(c in version for c in "@:/")):
        return None
    return org, project, path, version


def folder_digest(folder: Path) -> str:
    """
    The digest of every file in `folder`, computed as the Flock registry computes a version's: the sha256 of `{path}\t{size}\t{sha256}\n` per file, sorted by its path in the folder.

    Raises:
        `ValueError`: if `folder` isn't a folder, holds no files, holds a link or another file that isn't a regular file, has a file name with a control character, such as a tab, which the registry refuses too, names a weights shard or tokenizer file outside itself, or is an adapter, whose base model loads from elsewhere.
        `OSError`: if a folder in it can't be read.
    """
    if not folder.is_dir():
        raise ValueError(f"{folder} isn't a folder")

    # A link can point outside the folder and change there under the same digest, an unreadable
    # folder would drop out of the list unnoticed, and a pipe would hang the read. Exports have
    # none of them.
    errors: list[OSError] = []
    files = {}
    for root, folders, names in folder.walk(on_error=errors.append):
        for name in [*folders, *names]:
            if (root / name).is_symlink():
                raise ValueError(f"{folder}: {root / name} is a link, which the digest can't pin")
        for name in names:
            if not (root / name).is_file():
                raise ValueError(f"{folder}: {root / name} isn't a regular file")
            files[(root / name).relative_to(folder).as_posix()] = root / name
    if errors:
        raise errors[0]
    if not files:
        raise ValueError(f"{folder} holds no files")
    # Transformers loads an adapter's base model from wherever its config says.
    if "adapter_config.json" in files:
        raise ValueError(f"{folder} is an adapter, whose base model the digest can't pin")

    # Transformers opens the shards a weights index names, and the tokenizer file a tokenizer
    # config names, by joining them to the folder, so `../` or an absolute path reaches out. A
    # missing name inside is left to the loader: a tokenizer lists versions it never saved.
    named = []
    for index in ("model.safetensors.index.json", "pytorch_model.bin.index.json"):
        if index in files:
            named += json.loads(files[index].read_bytes())["weight_map"].values()
    if "tokenizer_config.json" in files:
        config = json.loads(files["tokenizer_config.json"].read_bytes())
        named += config.get("fast_tokenizer_files", [])
    outside = []
    for name in sorted(set(named)):
        if posixpath.isabs(name) or posixpath.normpath(name).split("/")[0] == "..":
            outside.append(name)
    if outside:
        raise ValueError(f"{folder}: its configs name {outside}, outside the folder")

    lines = []
    # Sorted as strings, as the registry sorts, which isn't `Path` order: `a-b` comes before `a/c`.
    for name in sorted(files):
        # A tab or newline in a name would let two file lists write the same lines. The
        # registry refuses the same characters.
        if any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError(f"{folder}: the file name {name!r} has a control character")
        file_digest = hashlib.sha256()
        with files[name].open("rb") as file:
            while chunk := file.read(8 * 2**20):
                file_digest.update(chunk)
        lines.append(f"{name}\t{files[name].stat().st_size}\t{file_digest.hexdigest()}\n")

    return hashlib.sha256("".join(lines).encode()).hexdigest()


def _flock_hub() -> Any:
    """`flock.hub.models.cache`, which resolves and loads Flock models, imported only when one is scored."""
    try:
        from flock.hub.models import cache
    except ImportError as error:
        raise ImportError(
            f"scoring a flock: model needs torch, safetensors and flock.training: {error}"
        ) from error

    return cache


def _repo_file(ref: ModelRef, filename: str) -> Path | None:
    """
    The path of `filename` at the pinned revision, downloading it if needed, or `None` if the repo doesn't have it.

    A local model's files are read from its folder. Only the Hub's answer, or the cache's record of it, counts as "doesn't have it": a file that merely isn't cached doesn't.

    Raises:
        `LocalEntryNotFoundError`: if the Hub can't be reached and the cache doesn't know.
    """
    if ref.kind == "local":
        path = Path(ref.repo) / filename
        return path if path.exists() else None

    if try_to_load_from_cache(ref.repo, filename, revision=ref.revision) is _CACHED_NO_EXIST:
        return None
    try:
        return Path(hf_hub_download(ref.repo, filename, revision=ref.revision))
    except RemoteEntryNotFoundError:
        return None


def load_tokenizer(ref: ModelRef) -> Tokenizer:
    """
    Loads a Hugging Face model's tokenizer, chosen by the repo's files at the pinned revision.

    A repo with `tokenizer.json` goes to `AutoTokenizer`, without fetching anything else to choose. A repo with `tokenizer.model` and no `tokenizer.json` gets [`~eval.tokenizer.SentencePieceTokenizer`], and `AutoTokenizer` never sees it, so installing `sentencepiece` can't switch it to a conversion. Any other repo goes to `AutoTokenizer`, which must not build its tokenizer from a SentencePiece model under another name. One model id therefore always means one tokenizer.

    Args:
        ref (`ModelRef`):
            The model, already resolved. A local one's repo is its folder.

    Raises:
        `ValueError`: if the repo has no `tokenizer.json` and `AutoTokenizer` gives a tokenizer class built on a SentencePiece model, which is a conversion, and whose ids the raw model may not match.
        `LocalEntryNotFoundError`: if a request to the Hub fails, so the repo's files are unknown, or offline if `tokenizer.model` is cached and the cache doesn't record whether the repo has `tokenizer.json`. Loading the model once online records it.
    """
    try:
        tokenizer_json = _repo_file(ref, "tokenizer.json")
        model_file = None if tokenizer_json else _repo_file(ref, "tokenizer.model")
    except LocalEntryNotFoundError:
        # Online the Hub failed, and the next request may succeed and choose differently. Offline,
        # a cached `tokenizer.model` may be an interrupted download of a repo with both files.
        if not is_offline_mode() or isinstance(
            try_to_load_from_cache(ref.repo, "tokenizer.model", revision=ref.revision), str
        ):
            raise
        # With no `tokenizer.model` on disk, `AutoTokenizer` can only read what else is there.
        tokenizer_json = model_file = None

    if model_file is not None:
        return SentencePieceTokenizer(model_file)

    # Code from another repo, which an `auto_map` can name, is pinned by neither the revision
    # nor a digest, so it is never run.
    tokenizer = AutoTokenizer.from_pretrained(
        ref.repo, revision=ref.revision, trust_remote_code=False
    )
    assert tokenizer is not None  # The stubs allow None; loading raises instead.

    if tokenizer_json is None and any(
        Path(name).suffix in {".model", ".spm"} for name in tokenizer.vocab_files_names.values()
    ):
        raise ValueError(
            f"{ref}: no tokenizer.json, and {type(tokenizer).__name__} is built on a "
            "SentencePiece model, whose ids the raw model may not match"
        )

    return tokenizer


class FlockCausalLM(torch.nn.Module):
    """
    A Flock model, such as `Olmo2`, wrapped to be called as [`ScoringModel`] calls a transformers causal LM.

    Flock models take a batch dict and return every position's logits, so the wrapper keeps only the last `logits_to_keep` positions after the forward pass. A batch's full logits are held for a moment, which a transformers model avoids: lower `batch_tokens` if that runs out of memory.

    Args:
        model (`BaseModel`):
            The Flock model, already on its device and in its dtype. It stays the wrapper's `model`, so the wrapper's modes and devices are its own.
    """

    def __init__(self, model: torch.nn.Module) -> None:
        super().__init__()
        self.model = model
        # Report the selected implementation so scoring only pins SDPA when it is used.
        self.config = SimpleNamespace(
            max_position_embeddings=getattr(model, "max_position_embeddings", None),
            _attn_implementation=getattr(
                getattr(model, "config", None), "attn_implementation", "sdpa"
            ),
        )

    @property
    def device(self) -> torch.device:
        """The device of the first weight."""
        return next(self.model.parameters()).device

    @property
    def dtype(self) -> torch.dtype:
        """The dtype of the first floating-point weight."""
        return next(p.dtype for p in self.model.parameters() if p.is_floating_point())

    def forward(  # ty: ignore[invalid-mutable-override]
        self,
        *,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        position_ids: torch.Tensor,
        use_cache: bool = False,
        logits_to_keep: int = 0,
    ) -> SimpleNamespace:
        """Returns the logits of the last `logits_to_keep` positions (every position for 0) as `.logits`."""
        # A Flock model counts positions from each row's first real token, as `position_ids`
        # does for the left-padded batches `ScoringModel` builds, and ignores `labels`.
        logits = self.model(
            {"input_ids": input_ids, "attention_mask": attention_mask, "labels": input_ids}
        )

        return SimpleNamespace(logits=logits[:, -logits_to_keep:] if logits_to_keep else logits)


class ScoringModel:
    """
    A causal LM and its tokenizer that answer loglikelihood [`~eval.requests.Request`]s in batches.

    Requests are tokenized as OLMES does, then scored longest first in left-padded batches of at most `batch_tokens` padded tokens. Inputs longer than `max_length` lose tokens from the left. On CUDA with `sdpa` attention, scoring is pinned to SDPA's memory-efficient kernel, so BF16 scores repeat run to run (see [`~eval.model.ScoringModel.sdpa_backend`]).

    Args:
        model (`PreTrainedModel` or `FlockCausalLM`):
            A causal LM, already on its device and in its dtype. Wrap a Flock model, such as `Olmo2`, in [`FlockCausalLM`].
        tokenizer (`PreTrainedTokenizerBase` or `SentencePieceTokenizer`):
            The model's tokenizer.
        name (`str`):
            Labels the model in log messages. [`~eval.run.evaluate`] records it as the provenance's `model`, `repo@commit`, `flock:org/path@number`, `flock:project:path@number` or `local:digest`.
        max_length (`int`, *optional*):
            The longest input the model sees. Defaults to the `max_position_embeddings` of the config's text config, which for a multimodal model, such as Gemma 4, is a sub-config.
        batch_tokens (`int`, *optional*, defaults to 32768):
            The most padded tokens in one forward pass.
        digest (`str`, *optional*):
            The digest of the Flock version or local folder the weights are, which [`~eval.run.evaluate`] records as the provenance's `model_digest`. `None` for a Hugging Face model.
        flock_ref (`ModelRef`, *optional*):
            The resolved Flock version the weights are, whose org, project and path [`~eval.run.evaluate`] records in the provenance. `None` for any other model.
        add_bos (`bool`, *optional*, defaults to `False`):
            Start every non-empty context with the tokenizer's beginning-of-sequence (BOS) token, as OLMES does for the models it configures with `add_bos_token`, all of them Gemma. A model trained to always see BOS, such as Gemma, scores poorly without it.

    Raises:
        `ValueError`: if `batch_tokens` or `max_length` is less than 1, `max_length` isn't given and the text config has no `max_position_embeddings`, or `add_bos` is set and the tokenizer has no BOS token.
    """

    def __init__(
        self,
        model: PreTrainedModel | FlockCausalLM,
        tokenizer: Tokenizer,
        *,
        name: str,
        max_length: int | None = None,
        batch_tokens: int = DEFAULT_BATCH_TOKENS,
        digest: str | None = None,
        add_bos: bool = False,
        flock_ref: ModelRef | None = None,
    ) -> None:
        if batch_tokens < 1:
            raise ValueError(f"batch_tokens must be at least 1, got {batch_tokens}")
        if max_length is None:
            # A multimodal config, such as Gemma 4's, keeps the length in its text config. A
            # text-only config is its own text config, and a Flock model's has no sub-configs.
            config = model.config
            if isinstance(model, PreTrainedModel):
                config = config.get_text_config(decoder=True)
            max_length = getattr(config, "max_position_embeddings", None)
        if max_length is None:
            raise ValueError(f"{name}: text config has no max_position_embeddings; pass max_length")
        if max_length < 1:
            raise ValueError(f"max_length must be at least 1, got {max_length}")
        if add_bos and tokenizer.bos_token_id is None:
            raise ValueError(f"{name}: add_bos is set, but the tokenizer has no BOS token")

        self.model = model
        self.tokenizer = tokenizer
        self.name = name
        self.max_length = max_length
        self.batch_tokens = batch_tokens
        self.digest = digest
        self.add_bos = add_bos
        self.flock_ref = flock_ref

    @classmethod
    def from_pretrained(
        cls,
        model_id: str,
        *,
        device: str | torch.device | None = None,
        dtype: torch.dtype = torch.bfloat16,
        batch_tokens: int = DEFAULT_BATCH_TOKENS,
        add_bos: bool = False,
    ) -> ScoringModel:
        """
        Loads `repo@revision` from the Hugging Face Hub, `flock:org/path@version` or `flock:project:path@version` from the Flock registry, or `local:folder@digest` from a folder in the Hugging Face format.

        The revision is resolved to its commit hash, which becomes the model's `name`, so a branch or tag in `model_id` is recorded as the exact weights that were scored. A Flock version (a tag, `primary`, or none for the primary) is resolved to its number, so the model's `name` is `flock:org/path@number` or `flock:project:path@number`, and its `digest` is the version's. A local folder is loaded only if [`folder_digest`] matches the digest given, and its `name` is `local:digest`, with that `digest`.

        A Hugging Face model's or local folder's tokenizer comes from [`load_tokenizer`]. A Flock model is loaded with [`~hub.models.load_from_hub`] and wrapped in [`FlockCausalLM`]. Its tokenizer is the Hugging Face one its config's `hf_repo` names, through `AutoTokenizer`.

        Args:
            model_id (`str`):
                The model as `repo@revision`, for example `allenai/OLMo-2-0425-1B@main`, as `flock:org/path[@version]` or `flock:project:path[@version]`, for example `flock:acme/olmo2-1b@12`, or as `local:folder@digest`. Pin a full 40-character commit hash to load a Hugging Face model offline from the cache. A repo with `tokenizer.model` needs one earlier online load, which records whether it has `tokenizer.json`.
            device (`str` or `torch.device`, *optional*):
                Where to put the weights. Defaults to CUDA when available, else the CPU.
            dtype (`torch.dtype`, *optional*, defaults to `torch.bfloat16`):
                The weights' dtype. Log-softmax always runs in FP32.
            batch_tokens (`int`, *optional*):
                Passed to [`ScoringModel`].
            add_bos (`bool`, *optional*, defaults to `False`):
                Passed to [`ScoringModel`].

        Raises:
            `ValueError`: if `batch_tokens` is less than 1, before anything downloads, or `model_id` isn't `repo@revision`, `flock:org/path[@version]`, `flock:project:path[@version]` or `local:folder@digest`, a local folder's files don't match its digest, before or after loading, or a Hugging Face id names a local folder, or a Flock model's config has no `hf_repo` to load its tokenizer from or names a repo whose tokenizer `AutoTokenizer` would convert from a SentencePiece model, [`load_tokenizer`] refuses the repo, or `add_bos` is set and the tokenizer has no BOS token, before a Hugging Face model's weights load.
            [`~hub.models.ModelError`]: if the Flock registry has no such version, or refuses to load it.
        """
        # `__init__` checks it too, but only after the weights have loaded.
        if batch_tokens < 1:
            raise ValueError(f"batch_tokens must be at least 1, got {batch_tokens}")
        ref = ModelRef.parse(model_id).resolve()
        device = device or ("cuda" if torch.cuda.is_available() else "cpu")

        if ref.kind == "flock":
            # By number, not as given, so a tag moved since resolving can't load other weights.
            flock_model = _flock_hub().load_from_hub(
                f"{ref.repo}@{ref.revision}", device=device, dtype=dtype, org=ref.org
            )
            flock_tokenizer = flock_model.get_hf_tokenizer()
            if flock_tokenizer is None:
                raise ValueError(f"{ref}: config has no hf_repo to load the tokenizer from")

            return cls(
                FlockCausalLM(flock_model),
                flock_tokenizer,
                name=str(ref),
                batch_tokens=batch_tokens,
                digest=ref.digest,
                add_bos=add_bos,
                flock_ref=ref,
            )

        tokenizer = load_tokenizer(ref)
        # `__init__` checks it too, but only after the weights have loaded.
        if add_bos and tokenizer.bos_token_id is None:
            raise ValueError(f"{ref}: add_bos is set, but the tokenizer has no BOS token")
        model = AutoModelForCausalLM.from_pretrained(
            ref.repo, revision=ref.revision, dtype=dtype, device_map=device, trust_remote_code=False
        )
        # Files rewritten between the check and the load would be scored under the old digest.
        if ref.kind == "local" and folder_digest(Path(ref.repo)) != ref.digest:
            raise ValueError(f"{ref.repo} changed while it loaded; score it again")

        return cls(
            model,
            tokenizer,
            name=str(ref),
            batch_tokens=batch_tokens,
            digest=ref.digest,
            add_bos=add_bos,
        )

    @property
    def device(self) -> torch.device:
        """The device the weights and every returned tensor live on."""
        return self.model.device

    @property
    def dtype(self) -> torch.dtype:
        """The weights' dtype."""
        return self.model.dtype

    @property
    def attention(self) -> str:
        """The attention implementation (`sdpa`, `eager`, ...), which changes BF16 scores."""
        # transformers has no public accessor for it, only this private config attribute.
        return self.model.config._attn_implementation

    @property
    def checks_resegmentation(self) -> bool:
        """Whether [`encode_requests`] refuses a continuation that changes its context's tokens: only on a [`~eval.tokenizer.SentencePieceTokenizer`], which segments a whole string at once. Other tokenizers keep lm-evaluation-harness's slice with no check, so the refusal can't move their scores."""
        return isinstance(self.tokenizer, SentencePieceTokenizer)

    @property
    def tokenizer_name(self) -> str:
        """Where the tokenizer was loaded from, its `name_or_path`: the repo or folder of a Hugging Face tokenizer, the model file of a [`~eval.tokenizer.SentencePieceTokenizer`]."""
        return self.tokenizer.name_or_path

    @property
    def tokenizer_hash(self) -> str | None:
        """The sha256 of the tokenizer's whole definition, so token ids written by a run can be checked against the tokenizer that made them: its `tokenizer.json` as the Tokenizers library serializes it, or the SentencePiece model's bytes. `None` for a tokenizer written in Python, with no Tokenizers backend: its vocabulary alone would not tell two tokenizations apart."""
        if isinstance(self.tokenizer, SentencePieceTokenizer):
            data = self.tokenizer.processor.serialized_model_proto()
        elif hasattr(self.tokenizer, "backend_tokenizer"):
            data = self.tokenizer.backend_tokenizer.to_str().encode()
        else:
            return None
        return hashlib.sha256(data).hexdigest()

    @property
    def sdpa_backend(self) -> str | None:
        """The SDPA kernel scoring is pinned to, `"EFFICIENT_ATTENTION"` on CUDA with `sdpa` attention, else `None`: the CPU has no such kernel, and other attention never calls SDPA."""
        if self.device.type == "cuda" and self.attention == "sdpa":
            return SDPBackend.EFFICIENT_ATTENTION.name
        return None

    def encode(self, request: Request) -> Encoded:
        """
        Returns the (context, continuation) token ids the model scores for `request`.

        An empty or whitespace-only context, or one that tokenizes to no ids, becomes one token: the tokenizer's beginning-of-sequence (BOS) token, or its end-of-sequence (EOS) token if it has no BOS. With `add_bos`, any other context starts with BOS.
        """
        return self.encode_requests([request])[0]

    @torch.no_grad()  # Not inference_mode: buffers rebuilt here must stay usable in training.
    def loglikelihood(
        self,
        requests: Sequence[Request],
        encoded: Sequence[Encoded] | None = None,
        *,
        tokens: bool = False,
    ) -> Scores:
        """
        Scores every request, in order, and counts the context tokens each lost to left truncation.

        The model runs in eval mode, and each module is put back in the mode it was in. The scores stay on the model's device: nothing here waits for the GPU. Per-token values never change the scores.

        Args:
            requests (`Sequence[Request]`):
                The requests to score.
            encoded (`Sequence[Encoded]`, *optional*):
                The requests' token ids from [`encode_requests`] with this model's tokenizer and `add_bos`, to skip tokenizing them again. Tokenized here if not given.
            tokens (`bool`, *optional*, defaults to `False`):
                Also return each continuation's per-token values as the scores' `continuation_tokens`. They read only the distributions the scores read, so the model runs as it does without them. A context's values come from [`score_contexts`].

        Raises:
            `ValueError`: if a continuation has no tokens or is longer than `max_length` on its own, or, when `encoded` isn't given, [`encode_requests`] raises.
        """
        if encoded is None:
            encoded = self.encode_requests(requests)
        # As lm-evaluation-harness asserts in `_loglikelihood_tokens`. An empty continuation would
        # score 0, above every real choice.
        for request, (_, continuation) in zip(requests, encoded, strict=True):
            if not continuation:
                raise ValueError(f"continuation has no tokens: {request}")
            if len(continuation) > self.max_length:
                raise ValueError(f"continuation longer than {self.max_length} tokens: {request}")

        # A window holds at most `max_length + 1` tokens: the model's input, then one last token
        # that is only ever a target. Anything beyond is cut from the left of the context.
        totals = [len(context) + len(continuation) for context, continuation in encoded]
        tokens_cut = [max(0, total - (self.max_length + 1)) for total in totals]
        # Left truncation changes what is scored, so it is never silent.
        truncated = sum(cut > 0 for cut in tokens_cut)
        if truncated:
            logger.warning(
                "%s: %d of %d requests exceed %d tokens; their contexts are left-truncated",
                self.name,
                truncated,
                len(requests),
                self.max_length,
            )

        num_tokens = torch.tensor(
            [len(continuation) for _, continuation in encoded], dtype=torch.int64
        )
        logprob, is_greedy, continuation_tokens = self._logprobs(encoded, tokens)
        return Scores(
            logprob,
            num_tokens.to(self.device, non_blocking=True),
            torch.tensor(tokens_cut, dtype=torch.int64).to(self.device, non_blocking=True),
            is_greedy,
            continuation_tokens,
        )

    @torch.no_grad()  # As in `loglikelihood`.
    def rolling_loglikelihood(
        self, texts: Sequence[str], window: int, token_ids: Sequence[list[int]] | None = None
    ) -> Scores:
        """
        Scores each text whole, in disjoint windows of at most `window` tokens, as lm-evaluation-harness's `loglikelihood_rolling` does.

        Each text is tokenized with no special tokens and split as `get_rolling_token_windows(..., context_len=1)` and `make_disjoint_window` split it (`lm_eval/utils.py` at lm-eval 0.4.3), which OLMES's Paloma tasks use (`loglikelihood_rolling_verbose` in `oe_eval/models/eleuther_huggingface.py`). The first window predicts the first `window` tokens after one prefix token: BOS if the tokenizer has one, else EOS, as lm-evaluation-harness's own `prefix_token_id` (OLMES always uses EOS). Each later window predicts the next `window` tokens, or what is left, from an input of exactly `window` tokens, so every token is predicted once. The windows of all texts are batched together as `loglikelihood` batches requests.

        Args:
            texts (`Sequence[str]`):
                The texts to score.
            window (`int`):
                The most tokens in one model input.
            token_ids (`Sequence[list[int]]`, *optional*):
                The texts' token ids from [`tokenize`] with this model's tokenizer, to skip tokenizing them again. Tokenized here if not given.

        Returns:
            `Scores`: per text, the FP64 sum of its windows' log-probabilities, its token count, `tokens_cut` all 0, since no token is cut, and whether every token is the greedy one.

        Raises:
            `ValueError`: if `window` is below 1 or above `max_length`, or a text has no tokens (it would score 0 bits over 0 bytes).
        """
        if not 1 <= window <= self.max_length:
            raise ValueError(f"window must be from 1 to max_length {self.max_length}, got {window}")

        if token_ids is None:
            token_ids = self.tokenize(list(texts))
        prefix = self._prefix_token()
        # Each text's windows, in order.
        per_text: list[list[Encoded]] = []
        for text, ids in zip(texts, token_ids, strict=True):
            if not ids:
                raise ValueError(f"text has no tokens: {text!r}")
            text_windows = [Encoded([prefix], ids[:window])]
            for start in range(window, len(ids), window):
                end = min(start + window, len(ids))
                # The input is the `window` tokens before `end`, so a last window that predicts
                # fewer than `window` tokens conditions on the rest of the previous one.
                text_windows.append(Encoded(ids[end - window - 1 : start], ids[start:end]))
            per_text.append(text_windows)
        windows = list(itertools.chain.from_iterable(per_text))

        # One row of window scores per text, summed along the row: unlike `index_add_` on CUDA,
        # which adds in any order, the sum is the same in every run. FP64, as OLMES adds
        # windows up in Python floats: a long text sums to about 1e5 nats, which FP32
        # resolves only to about 0.01. The cells a text fills are found on the host, row by row
        # as `windows` lists them, so placing the scores never waits for the GPU.
        counts = [len(text_windows) for text_windows in per_text]
        width = max(counts, default=0)
        filled = torch.arange(width) < torch.tensor(counts, dtype=torch.int64)[:, None]
        rows, columns = filled.nonzero(as_tuple=True)
        by_window = torch.zeros(len(per_text), width, dtype=torch.float64, device=self.device)
        cells = (
            rows.to(self.device, non_blocking=True),
            columns.to(self.device, non_blocking=True),
        )
        window_logprob, window_is_greedy, _ = self._logprobs(windows)
        by_window[cells] = window_logprob.double()
        logprob = by_window.sum(-1)
        # Cells a text doesn't fill count as greedy, so a text is greedy if all its windows are.
        greedy_by_window = torch.ones(len(per_text), width, dtype=torch.bool, device=self.device)
        greedy_by_window[cells] = window_is_greedy

        num_tokens = torch.tensor([len(ids) for ids in token_ids], dtype=torch.int64)
        return Scores(
            logprob,
            num_tokens.to(self.device, non_blocking=True),
            torch.zeros(len(token_ids), dtype=torch.int64, device=self.device),
            greedy_by_window.all(-1),
        )

    @torch.no_grad()  # As in `loglikelihood`.
    def score_contexts(
        self, contexts: Sequence[list[int]], num_tokens: int = 32, values: bool = False
    ) -> ContextScores:
        """
        Reads each context alone: the first `num_tokens` tokens greedy decoding writes after it, and with `values` the per-token values of its tokens. A context is read once however many choices share it, apart from the scoring of requests, so neither changes a score.

        Decoding has no stop: end-of-sequence tokens are written like any other. A model whose `max_length` isn't above `num_tokens` writes `max_length - 1`. A context longer than `max_length` less the tokens written loses tokens from the left, as scoring cuts one, and its values cover what is left but its first token, which nothing predicts. Batched longest first under scoring's kernel pin, in batches of up to 8 times `batch_tokens`, or `batch_tokens` with `values`. The values come from the first forward pass of decoding, which keeps every position's logits only when they are asked for.

        A [`FlockCausalLM`] keeps no key-value cache to decode with, and decoding without one would read each context `num_tokens` times over: it writes no greedy tokens, and its values take one forward pass.

        Args:
            contexts (`Sequence[list[int]]`):
                Token ids, as [`encode_requests`] gives a request's context.
            num_tokens (`int`, *optional*, defaults to 32):
                How many tokens to decode.
            values (`bool`, *optional*, defaults to `False`):
                Also return each context's per-token values.

        Raises:
            `ValueError`: if `num_tokens` is below 1, or `max_length` is 1, which leaves no room for a context.
        """
        if num_tokens < 1 or self.max_length < 2:
            raise ValueError(f"no tokens to decode: {num_tokens} after up to {self.max_length}")
        decodes = isinstance(self.model, PreTrainedModel)
        num_tokens = min(num_tokens, self.max_length - 1)

        kept = [context[-(self.max_length - num_tokens) :] for context in contexts]
        lengths = [len(context) + num_tokens for context in kept]
        order = sorted(range(len(kept)), key=lambda i: -lengths[i])
        greedy = torch.zeros(len(kept), num_tokens, dtype=torch.int64, device=self.device)
        # Each context token but the first is predicted from the ones before it.
        buffer = _TokenBuffer([context[1:] for context in kept], self.device) if values else None
        backend = self.sdpa_backend
        pin = sdpa_kernel(getattr(SDPBackend, backend)) if backend else nullcontext()
        with _eval_mode(self.model), pin:
            # Decoding spends its time on one-token steps, each costing about as much at any row
            # count, so a batch takes 8 times scoring's tokens: without context values the first
            # pass keeps one position's logits, and the cache and activations of that many tokens
            # take less memory than scoring's full-vocabulary logits do. With them, the first pass
            # keeps every position's logits, as scoring's budget allows.
            budget = self.batch_tokens if values else 8 * self.batch_tokens
            for batch in self._batches(order, lengths, budget):
                # Left-padded, so every row's next token comes from its last position.
                width = max(len(kept[i]) for i in batch)
                padding = [width - len(kept[i]) for i in batch]
                input_ids = [[0] * pad + kept[i] for pad, i in zip(padding, batch, strict=True)]
                mask = torch.tensor([[0] * pad + [1] * (width - pad) for pad in padding])
                positions = (mask.cumsum(-1) - 1).clamp(min=0)
                mask, positions = mask.to(self.device), positions.to(self.device)
                index = torch.tensor(batch).to(self.device, non_blocking=True)
                step_ids = torch.tensor(input_ids).to(self.device)
                output = self.model(
                    input_ids=step_ids,
                    attention_mask=mask,
                    position_ids=positions,
                    use_cache=decodes,
                    logits_to_keep=0 if values else 1,
                )
                # Every position but the last predicts the context's next token, so a batch of
                # one-token contexts has no values.
                if buffer is not None and width > 1:
                    targets = step_ids[:, 1:].to(output.logits.device)
                    logprob, _, stats = _target_logprobs(output.logits[:, :-1], targets, True)
                    assert stats is not None  # asked for
                    stats["logprob"] = logprob
                    buffer.write(batch, [width - 1] * len(batch), stats)
                if not decodes:
                    continue

                for step in range(num_tokens):
                    step_ids = output.logits[:, -1].argmax(-1, keepdim=True)
                    greedy[index, step] = step_ids[:, 0].to(self.device)
                    if step == num_tokens - 1:
                        break
                    # The next input is the token just written, one position on.
                    mask = torch.cat([mask, mask.new_ones(len(batch), 1)], dim=-1)
                    positions = positions[:, -1:] + 1
                    output = self.model(
                        input_ids=step_ids,
                        attention_mask=mask,
                        position_ids=positions,
                        past_key_values=output.past_key_values,
                        use_cache=True,
                        logits_to_keep=1,
                    )

        return ContextScores(
            greedy=greedy if decodes else None,
            values=buffer.values() if buffer is not None else None,
        )

    def _logprobs(
        self, encoded: Sequence[Encoded], tokens: bool = False
    ) -> tuple[torch.Tensor, torch.Tensor, TokenValues | None]:
        """Each encoded request's summed continuation log-probability (FP32) and whether its continuation is the greedy one, in order on the model's device, scored longest first in batches of at most `batch_tokens` padded tokens, and with `tokens` each continuation's per-token values."""
        # The model's input is the window without its last token, the window being at most
        # `max_length + 1` tokens (see `loglikelihood`).
        input_lengths = [
            min(len(context) + len(continuation), self.max_length + 1) - 1
            for context, continuation in encoded
        ]
        # Longest first, as lm-evaluation-harness sorts (`_collate` in `_loglikelihood_tokens`),
        # so the first request of each batch sets its padded width.
        order = sorted(range(len(encoded)), key=lambda i: -input_lengths[i])

        logprob = torch.zeros(len(encoded), dtype=torch.float32, device=self.device)
        is_greedy = torch.zeros(len(encoded), dtype=torch.bool, device=self.device)
        # A continuation is never cut, so its tokens are its own.
        values = None
        if tokens:
            values = _TokenBuffer([continuation for _, continuation in encoded], self.device)
        # Pinned to one kernel so BF16 scores repeat run to run: left to choose, PyTorch runs
        # cuDNN attention on some padded batches, and its results vary (023.D1, 023.D2). No
        # fallback: a model the kernel can't run fails here rather than mix kernels.
        # `sdpa_backend` names the kernel, so what is recorded is what runs.
        backend = self.sdpa_backend
        pin = sdpa_kernel(getattr(SDPBackend, backend)) if backend else nullcontext()
        with _eval_mode(self.model), pin:
            for batch in self._batches(order, input_lengths):
                # `non_blocking` on these small CPU tensors: the copy is staged at once and the
                # host never waits for GPU work already queued.
                index = torch.tensor(batch).to(self.device, non_blocking=True)
                scored = self._score([encoded[i] for i in batch], tokens)
                logprob[index], is_greedy[index] = scored.logprob, scored.is_greedy
                if values is not None and scored.values is not None:
                    # Right-aligned like the kept logits: a continuation fills its last columns.
                    width = scored.values["logprob"].shape[1]
                    values.write(batch, [width] * len(batch), scored.values)

        return logprob, is_greedy, values.values() if values is not None else None

    def encode_requests(self, requests: Sequence[Request]) -> list[Encoded]:
        """
        [`encode`] for many requests, with one tokenizer call for the distinct contexts and one for the joined texts.

        Raises:
            `ValueError`: if the tokenizer is a [`~eval.tokenizer.SentencePieceTokenizer`] and a joined text's ids don't start with its context's, or a context is empty or has no ids and the tokenizer has neither BOS nor EOS.
        """
        # The first step of lm-evaluation-harness's `_encode_pair`, which OLMES uses: whitespace at
        # the end of the context moves to the start of the continuation, so `"Answer:"` +
        # `" Paris"` and `"Answer: "` + `"Paris"` score the same tokens. Unlike
        # lm-evaluation-harness, which checks for an empty context first, a whitespace-only
        # context becomes empty and gets the prefix token.
        contexts = [request.context.rstrip() for request in requests]
        joined_ids = self.tokenize([request.context + request.continuation for request in requests])
        # Few-shot contexts repeat once per choice, so each distinct one is tokenized once.
        distinct = sorted({context for context in contexts if context})
        ids_by_context = dict(zip(distinct, self.tokenize(distinct), strict=True))
        # As OLMES's `tok_encode` with `add_bos_token`, which puts BOS before the context and the
        # joined text alike, so only the context changes. Once per distinct context, so the
        # requests of one few-shot context keep sharing one list.
        starts = ids_by_context
        if self.add_bos:
            bos = self.tokenizer.bos_token_id
            assert bos is not None  # `__init__` refuses `add_bos` without one.
            starts = {context: [bos, *ids] for context, ids in ids_by_context.items()}

        encoded = []
        for request, context, joined in zip(requests, contexts, joined_ids, strict=True):
            # A context can tokenize to nothing, such as a zero-width space under a normalizer that
            # drops it. For every tokenizer it starts as an empty context does, because the first
            # continuation token has no logit otherwise. OLMES's `assert len(context_enc) > 0`
            # refuses this case instead.
            context_ids = ids_by_context[context] if context else []
            if not context_ids:
                encoded.append(Encoded([self._prefix_token()], joined))
                continue

            # As lm-evaluation-harness's `_encode_pair`: the continuation's ids are the joint ids
            # after as many leading ids as the context has when tokenized alone. Where the added
            # text can resegment the context, the slice would score the wrong tokens.
            if self.checks_resegmentation and joined[: len(context_ids)] != context_ids:
                raise ValueError(f"the continuation changes the context's tokens: {request}")
            encoded.append(Encoded(starts[context], joined[len(context_ids) :]))

        return encoded

    def tokenize(self, texts: list[str]) -> list[list[int]]:
        """
        Each text's token ids, with no special tokens.

        OLMES adds none (`add_bos_token: False` in `oe_eval/default_configs.py`) except for the models it configures with `add_bos_token`, all of them Gemma. [`encode_requests`] adds that BOS when `add_bos` is set.
        """
        if not texts:
            return []

        return self.tokenizer(texts, add_special_tokens=False)["input_ids"]

    def _prefix_token(self) -> int:
        """The token an empty context, or one with no ids, becomes, and a rolling text's first window starts from: BOS if the tokenizer has one, else EOS, as lm-evaluation-harness's `prefix_token_id`. It isn't padding: the first scored token needs one real token to be predicted from."""
        prefix = self.tokenizer.bos_token_id
        if prefix is None:
            prefix = self.tokenizer.eos_token_id
        if prefix is None:
            raise ValueError(
                "scoring needs a BOS or EOS prefix token, and the tokenizer has neither"
            )

        return prefix

    def _batches(
        self, order: list[int], input_lengths: list[int], budget: int | None = None
    ) -> Iterator[list[int]]:
        """Splits `order` (longest first) into runs whose padded size fits `budget` tokens, `batch_tokens` if not given, given each request's model-input length."""
        budget = budget or self.batch_tokens
        batch: list[int] = []
        for i in order:
            # `batch[0]` is the batch's longest request, so every row pads to its length.
            if batch and (len(batch) + 1) * input_lengths[batch[0]] > budget:
                yield batch
                batch = []
            batch.append(i)

        if batch:
            yield batch

    def _score(self, encoded: list[Encoded], values: bool = False) -> _BatchScores:
        """One batch's summed continuation log-probabilities, whether each continuation is the greedy one, and with `values` the per-token values at every kept position."""
        batch = _pad_batch(encoded, self.max_length)
        device = self.device

        # TODO: try FlashAttention 3 from the `kernels` package and packed sequences (cu_seqlens)
        # instead of padding. Measure the speedup first. A kernel other than SDPA bypasses the pin
        # in `_logprobs`, so `sdpa_backend` must then record that kernel instead (023.D2): a
        # different kernel changes BF16 scores slightly. Packing also changes which
        # positions `logits_to_keep` must keep.
        logits = self.model(
            input_ids=batch.input_ids.to(device, non_blocking=True),
            attention_mask=batch.attention_mask.to(device, non_blocking=True),
            position_ids=batch.position_ids.to(device, non_blocking=True),
            use_cache=False,
            # Full-vocabulary logits for the few-shot context would dominate memory.
            logits_to_keep=batch.keep,
        ).logits
        if logits.shape[1] != batch.keep:
            raise ValueError(f"{self.name} ignored logits_to_keep: got {logits.shape[1]} positions")

        # With weights on several devices, the logits come out on the last one.
        targets = batch.targets.to(logits.device, non_blocking=True)
        scored = batch.scored.to(logits.device, non_blocking=True)
        token_logprob, token_is_argmax, stats = _target_logprobs(logits, targets, values)

        # `where`, not a product: logits at padding positions may not be finite.
        logprob = token_logprob.where(scored, 0.0).sum(-1)
        # Greedy only if every continuation token is the argmax, as OLMES's `is_greedy`
        # (`oe_eval/models/eleuther_huggingface.py`). Positions before the continuation don't count.
        is_greedy = (token_is_argmax | ~scored).all(-1)
        if stats is not None:
            stats = {name: value.to(device, non_blocking=True) for name, value in stats.items()}
            stats["logprob"] = token_logprob.to(device, non_blocking=True)

        return _BatchScores(
            logprob.to(device, non_blocking=True), is_greedy.to(device, non_blocking=True), stats
        )


class _BatchScores(NamedTuple):
    """What `ScoringModel._score` returns for one batch, on the model's device."""

    # float32 `[rows]`: each continuation's summed log-probability.
    logprob: torch.Tensor
    # bool `[rows]`: whether each continuation is the greedy one.
    is_greedy: torch.Tensor
    # Each per-token value of `TokenValues` but the ids, `[rows, kept positions]`, if asked for.
    values: dict[str, torch.Tensor] | None


class _TokenBuffer:
    """Per-token values of many token sequences, filled batch by batch into flat arrays on the device, a sequence's tokens one after another. One slot past the end takes what the columns of other tokens write, so placing a batch's values never waits for the GPU."""

    DTYPES: ClassVar[dict[str, torch.dtype]] = {
        "logprob": torch.float32,
        "rank": torch.int64,
        "entropy": torch.float32,
        "top1_logprob": torch.float32,
        "logprob_std": torch.float32,
    }

    def __init__(self, token_ids: list[list[int]], device: torch.device) -> None:
        self.lengths = [len(ids) for ids in token_ids]
        self.starts = [0, *itertools.accumulate(self.lengths)]
        self.token_ids = torch.tensor(
            list(itertools.chain.from_iterable(token_ids)), dtype=torch.int64
        )
        self.arrays = {
            name: torch.zeros(self.starts[-1] + 1, dtype=dtype, device=device)
            for name, dtype in self.DTYPES.items()
        }

    def write(self, batch: list[int], ends: list[int], values: dict[str, torch.Tensor]) -> None:
        """Copies each sequence of `batch` from its row of `values`, the columns that end at `ends[row]`."""
        if not any(self.lengths[i] for i in batch):
            return
        width = values["logprob"].shape[1]
        spare = self.starts[-1]
        index = []
        for i, end in zip(batch, ends, strict=True):
            columns = [spare] * width
            columns[end - self.lengths[i] : end] = range(self.starts[i], self.starts[i + 1])
            index.append(columns)
        index = torch.tensor(index).to(values["logprob"].device, non_blocking=True)
        for name, array in self.arrays.items():
            array[index] = values[name]

    def values(self) -> TokenValues:
        """The filled values, without the spare slot."""
        return TokenValues(
            lengths=tuple(self.lengths),
            token_ids=self.token_ids,
            **{name: array[:-1] for name, array in self.arrays.items()},
        )


@dataclass(frozen=True, slots=True)
class _PaddedBatch:
    """One batch of encoded requests as model inputs, left-padded so every continuation ends at the last position."""

    # int64 `[rows, width]`: left padding, then each window without its last token.
    input_ids: torch.Tensor
    # int64 `[rows, width]`: 0 at padding.
    attention_mask: torch.Tensor
    # int64 `[rows, width]`: counting from 0 at each row's first real token.
    position_ids: torch.Tensor
    # int64 `[rows, keep]`: the token each of the last `keep` positions predicts.
    targets: torch.Tensor
    # bool `[rows, keep]`: True where that target is a continuation token.
    scored: torch.Tensor

    @property
    def keep(self) -> int:
        """How many final positions need logits: the batch's longest continuation."""
        return self.targets.shape[1]


def _pad_batch(encoded: list[Encoded], max_length: int) -> _PaddedBatch:
    """Left-pads one batch of encoded requests into model inputs, on the CPU. Inputs longer than `max_length` lose tokens from the left, keeping the whole continuation, as lm-evaluation-harness's `(context_enc + continuation_enc)[-(self.max_length + 1) :]` in `_loglikelihood_tokens`."""
    # Each window is one token longer than the model's input: its last token is only a target.
    windows = [(context + continuation)[-(max_length + 1) :] for context, continuation in encoded]
    width = max(len(window) - 1 for window in windows)
    keep = max(len(continuation) for _, continuation in encoded)
    # The pad id never matters: padding is masked out of attention and never scored. 0 is used
    # instead of `tokenizer.pad_token_id` because that can be None, or added after the
    # embedding matrix was sized.
    pad = 0

    input_ids, attention_mask, position_ids, targets, scored = [], [], [], [], []
    for window, (_, continuation) in zip(windows, encoded, strict=True):
        length = len(window) - 1
        padding = width - length
        input_ids.append([pad] * padding + window[:-1])
        attention_mask.append([0] * padding + [1] * length)
        position_ids.append([0] * padding + list(range(length)))

        # Right-aligned like the kept logits: the last `len(continuation)` of the `keep`
        # positions predict the continuation's tokens.
        target_padding = keep - len(continuation)
        targets.append([pad] * target_padding + window[-len(continuation) :])
        scored.append([False] * target_padding + [True] * len(continuation))

    return _PaddedBatch(
        input_ids=torch.tensor(input_ids),
        attention_mask=torch.tensor(attention_mask),
        position_ids=torch.tensor(position_ids),
        targets=torch.tensor(targets),
        scored=torch.tensor(scored),
    )


def _target_logprobs(
    logits: torch.Tensor, targets: torch.Tensor, values: bool = False, chunk_positions: int = 4096
) -> tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor] | None]:
    """
    The FP32 log-softmax of `logits` `[rows, positions, vocab]`, in any dtype, at the int64 `targets` `[rows, positions]`, and whether each target is its position's argmax, the first on a tie. With `values`, also each target's rank and each position's entropy, top-1 log-probability and standard deviation of the log-probability, by name.

    OLMES runs `log_softmax` in the model's dtype and takes the argmax of its output (`oe_eval/models/eleuther_huggingface.py`). FP32 here is our choice, so BF16 rounding of the normalizer doesn't move scores, and the argmax is taken over the FP32 logits, which the normalizer doesn't reorder; in BF16 OLMES's rounded log-probabilities can tie where ours don't, so a near-tie can resolve differently. `logits` can hold `batch_tokens * vocab` values, so an FP32 copy of all of it would double peak memory: rows are converted a few at a time, at most `chunk_positions` positions (rows times positions per row) and at least one row, about 1.6 GB in FP32 for a 100k vocabulary. The values take about four more such copies at a time. The chunks are the same with or without them, so asking for them can't change a reduction's order, and with it a score's last bit.
    """
    rows_per_chunk = max(1, chunk_positions // logits.shape[1])
    token_logprob = torch.empty(targets.shape, dtype=torch.float32, device=logits.device)
    token_is_argmax = torch.empty(targets.shape, dtype=torch.bool, device=logits.device)
    stats = None
    if values:
        stats = {
            name: torch.empty(targets.shape, dtype=dtype, device=logits.device)
            for name, dtype in _TokenBuffer.DTYPES.items()
            if name != "logprob"
        }

    for start in range(0, len(logits), rows_per_chunk):
        rows = slice(start, start + rows_per_chunk)
        chunk = logits[rows].float()
        target_logits = chunk.gather(-1, targets[rows, :, None]).squeeze(-1)
        normalizer = chunk.logsumexp(-1)
        token_logprob[rows] = target_logits - normalizer
        token_is_argmax[rows] = chunk.argmax(-1) == targets[rows]
        if stats is None:
            continue

        # Min-K%++'s mu and sigma are the mean and standard deviation of log p under p itself
        # (Zhang et al. 2024, arXiv 2404.02936, eq. 3); mu is minus the entropy. A token of
        # probability 0 adds nothing, where `0 * -inf` would be NaN.
        logprobs = chunk - normalizer[..., None]
        probs = logprobs.exp()
        entropy = -torch.where(probs > 0, probs * logprobs, 0.0).sum(-1)
        deviation = (logprobs + entropy[..., None]).square()
        stats["entropy"][rows] = entropy
        stats["logprob_std"][rows] = torch.where(probs > 0, probs * deviation, 0.0).sum(-1).sqrt()
        stats["top1_logprob"][rows] = chunk.amax(-1) - normalizer
        stats["rank"][rows] = (chunk > target_logits[..., None]).sum(-1) + 1

    return token_logprob, token_is_argmax, stats


@contextmanager
def _eval_mode(model: torch.nn.Module) -> Generator[None]:
    """Puts `model` in eval mode, then gives each module back the mode it had, so a training loop can score between steps."""
    modes = [(module, module.training) for module in model.modules()]
    model.eval()

    try:
        yield
    finally:
        for module, training in modes:
            module.training = training
