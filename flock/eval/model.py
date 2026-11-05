"""Batched loglikelihood scoring with a causal language model, kept on the model's device."""

import itertools
import logging
import re
from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Literal, NamedTuple

import torch
from huggingface_hub import HfApi
from torch.nn.attention import SDPBackend, sdpa_kernel
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    PreTrainedModel,
    PreTrainedTokenizerBase,
)

from flock.eval.requests import Request, Scores

DEFAULT_BATCH_TOKENS = 32768
# What a model id starts with when it names a version in the Flock registry.
FLOCK = "flock:"

logger = logging.getLogger(__name__)


class Encoded(NamedTuple):
    """The token ids of one request, or one window of a text: the context the model conditions on, and the continuation it scores."""

    context: list[int]
    continuation: list[int]


@dataclass(frozen=True, slots=True)
class ModelRef:
    """
    A model to score: a Hugging Face model repo pinned to a revision, written `repo@revision`, or a version of a model in the Flock registry, written `flock:org/name@version`.

    Args:
        repo (`str`):
            The Hugging Face repo, or the Flock model as `org/name`.
        revision (`str`):
            The Hugging Face revision, or the Flock version: a number, a tag or `primary`.
        kind (`str`, *optional*, defaults to `"hf"`):
            `"hf"` for a Hugging Face model, `"flock"` for a Flock one.
        digest (`str`, *optional*):
            The Flock version's digest, which names its exact files. Set by [`resolve`], and `None` for a Hugging Face model, whose commit hash already does that.
    """

    repo: str
    revision: str
    kind: Literal["hf", "flock"] = "hf"
    digest: str | None = None

    @classmethod
    def parse(cls, model_id: str) -> ModelRef:
        """
        Parses `repo@revision`, or `flock:org/name` with an optional `@version`. A Flock model without a version means its primary version.

        Raises:
            `ValueError`: if a Hugging Face id has no revision, so an unpinned model can't be scored, or more than one `@`, or a Flock id isn't `org/name[@version]`.
        """
        if model_id.startswith(FLOCK):
            name, at, version = model_id.removeprefix(FLOCK).partition("@")
            org, _, model = name.partition("/")
            if not org or not model or "/" in model or (at and not version) or "@" in version:
                raise ValueError(f"model id {model_id!r} must be flock:org/name[@version]")

            return cls(name, version or "primary", "flock")

        repo, _, revision = model_id.partition("@")
        if not repo or not revision or "@" in revision:
            raise ValueError(f"model id {model_id!r} must be repo@revision or flock:org/name")

        return cls(repo, revision)

    def __str__(self) -> str:
        prefix = FLOCK if self.kind == "flock" else ""
        return f"{prefix}{self.repo}@{self.revision}"

    def resolve(self) -> ModelRef:
        """
        Returns this ref with its revision resolved to a commit hash, or its Flock version to a number and digest.

        A full hash is returned as is, without asking the Hub, so pinned models load offline from the cache. A Flock version is always resolved through the registry, which also gives its digest.

        Raises:
            `ValueError`: if the Hub doesn't report a commit hash for the revision.
            [`~hub.models.ModelError`]: if the registry has no such Flock version.
        """
        if self.kind == "flock":
            version = _flock_hub().resolve(f"{self.repo}@{self.revision}")
            return ModelRef(
                f"{version.org}/{version.name}", str(version.number), "flock", version.digest
            )

        # 40 lowercase hex characters is how the Hub writes a full commit hash.
        if re.fullmatch(r"[0-9a-f]{40}", self.revision):
            return self

        sha = HfApi().model_info(self.repo, revision=self.revision).sha
        if sha is None:
            raise ValueError(f"the Hub reports no commit hash for {self}")

        return ModelRef(self.repo, sha)


def _flock_hub() -> Any:
    """`flock.hub.models.cache`, which resolves and loads Flock models, imported only when one is scored."""
    try:
        from flock.hub.models import cache
    except ImportError as error:
        raise ImportError(
            f"scoring a flock: model needs flock-hub[models] and flock-training: {error}"
        ) from error

    return cache


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
        # What `ScoringModel` reads from a transformers config. Flock's attention calls
        # `scaled_dot_product_attention` with an explicit mask.
        self.config = SimpleNamespace(
            max_position_embeddings=getattr(model, "max_position_embeddings", None),
            _attn_implementation="sdpa",
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
        tokenizer (`PreTrainedTokenizerBase`):
            The model's tokenizer.
        name (`str`):
            Labels the model in log messages. [`~eval.run.evaluate`] records it as the provenance's `model`, `repo@commit` or `flock:org/name@number`.
        max_length (`int`, *optional*):
            The longest input the model sees. Defaults to the `max_position_embeddings` of the config's text config, which for a multimodal model, such as Gemma 4, is a sub-config.
        batch_tokens (`int`, *optional*, defaults to 32768):
            The most padded tokens in one forward pass.
        digest (`str`, *optional*):
            The digest of the Flock version the weights are, which [`~eval.run.evaluate`] records as the provenance's `model_digest`. `None` for any other model.
        add_bos (`bool`, *optional*, defaults to `False`):
            Start every non-empty context with the tokenizer's beginning-of-sequence (BOS) token, as OLMES does for the models it configures with `add_bos_token`, all of them Gemma. A model trained to always see BOS, such as Gemma, scores poorly without it.

    Raises:
        `ValueError`: if `batch_tokens` or `max_length` is less than 1, `max_length` isn't given and the text config has no `max_position_embeddings`, or `add_bos` is set and the tokenizer has no BOS token.
    """

    def __init__(
        self,
        model: PreTrainedModel | FlockCausalLM,
        tokenizer: PreTrainedTokenizerBase,
        *,
        name: str,
        max_length: int | None = None,
        batch_tokens: int = DEFAULT_BATCH_TOKENS,
        digest: str | None = None,
        add_bos: bool = False,
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
        Loads `repo@revision` from the Hugging Face Hub, or `flock:org/name@version` from the Flock registry.

        The revision is resolved to its commit hash, which becomes the model's `name`, so a branch or tag in `model_id` is recorded as the exact weights that were scored. A Flock version (a tag, `primary`, or none for the primary) is resolved to its number, so the model's `name` is `flock:org/name@number`, and its `digest` is the version's.

        A Flock model is loaded with [`~hub.models.load_from_hub`] and wrapped in [`FlockCausalLM`]. Its tokenizer is the Hugging Face one its config's `hf_repo` names.

        Args:
            model_id (`str`):
                The model as `repo@revision`, for example `allenai/OLMo-2-0425-1B@main`, or as `flock:org/name[@version]`, for example `flock:acme/olmo2-1b@12`. Pin a full 40-character commit hash to load a Hugging Face model offline from the cache.
            device (`str` or `torch.device`, *optional*):
                Where to put the weights. Defaults to CUDA when available, else the CPU.
            dtype (`torch.dtype`, *optional*, defaults to `torch.bfloat16`):
                The weights' dtype. Log-softmax always runs in FP32.
            batch_tokens (`int`, *optional*):
                Passed to [`ScoringModel`].
            add_bos (`bool`, *optional*, defaults to `False`):
                Passed to [`ScoringModel`].

        Raises:
            `ValueError`: if `batch_tokens` is less than 1, before anything downloads, or `model_id` isn't `repo@revision` or `flock:org/name[@version]`, or a Flock model's config has no `hf_repo` to load its tokenizer from, or `add_bos` is set and the tokenizer has no BOS token, before a Hugging Face model's weights load.
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
                f"{ref.repo}@{ref.revision}", device=device, dtype=dtype
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
            )

        tokenizer = AutoTokenizer.from_pretrained(ref.repo, revision=ref.revision)
        assert tokenizer is not None  # The stubs allow None; loading raises instead.
        # `__init__` checks it too, but only after the weights have loaded.
        if add_bos and tokenizer.bos_token_id is None:
            raise ValueError(f"{ref}: add_bos is set, but the tokenizer has no BOS token")
        model = AutoModelForCausalLM.from_pretrained(
            ref.repo, revision=ref.revision, dtype=dtype, device_map=device
        )

        return cls(model, tokenizer, name=str(ref), batch_tokens=batch_tokens, add_bos=add_bos)

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
    def sdpa_backend(self) -> str | None:
        """The SDPA kernel scoring is pinned to, `"EFFICIENT_ATTENTION"` on CUDA with `sdpa` attention, else `None`: the CPU has no such kernel, and other attention never calls SDPA."""
        if self.device.type == "cuda" and self.attention == "sdpa":
            return SDPBackend.EFFICIENT_ATTENTION.name
        return None

    def encode(self, request: Request) -> Encoded:
        """
        Returns the (context, continuation) token ids the model scores for `request`.

        An empty or whitespace-only context becomes one token: the tokenizer's beginning-of-sequence (BOS) token, or its end-of-sequence (EOS) token if it has no BOS. With `add_bos`, any other context starts with BOS.
        """
        return self.encode_requests([request])[0]

    @torch.no_grad()  # Not inference_mode: buffers rebuilt here must stay usable in training.
    def loglikelihood(
        self, requests: Sequence[Request], encoded: Sequence[Encoded] | None = None
    ) -> Scores:
        """
        Scores every request, in order, and counts the context tokens each lost to left truncation.

        The model runs in eval mode, and each module is put back in the mode it was in. The scores stay on the model's device: nothing here waits for the GPU.

        Args:
            requests (`Sequence[Request]`):
                The requests to score.
            encoded (`Sequence[Encoded]`, *optional*):
                The requests' token ids from [`encode_requests`] with this model's tokenizer, to skip tokenizing them again. Tokenized here if not given.

        Raises:
            `ValueError`: if a continuation has no tokens, or is longer than `max_length` on its own.
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
        logprob, is_greedy = self._logprobs(encoded)
        return Scores(
            logprob,
            num_tokens.to(self.device, non_blocking=True),
            torch.tensor(tokens_cut, dtype=torch.int64).to(self.device, non_blocking=True),
            is_greedy,
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
        window_logprob, window_is_greedy = self._logprobs(windows)
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

    def _logprobs(self, encoded: Sequence[Encoded]) -> tuple[torch.Tensor, torch.Tensor]:
        """Each encoded request's summed continuation log-probability (FP32) and whether its continuation is the greedy one, in order on the model's device, scored longest first in batches of at most `batch_tokens` padded tokens."""
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
                logprob[index], is_greedy[index] = self._score([encoded[i] for i in batch])

        return logprob, is_greedy

    def encode_requests(self, requests: Sequence[Request]) -> list[Encoded]:
        """[`encode`] for many requests, with one tokenizer call for the distinct contexts and one for the joined texts."""
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

        encoded = []
        for context, joined in zip(contexts, joined_ids, strict=True):
            if not context:
                encoded.append(Encoded([self._prefix_token()], joined))
                continue

            # As lm-evaluation-harness's `_encode_pair`: the continuation's ids are the joint ids
            # after as many leading ids as the context has when tokenized alone.
            continuation_ids = joined[len(ids_by_context[context]) :]
            encoded.append(Encoded(starts[context], continuation_ids))

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
        """The token an empty context becomes, and a rolling text's first window starts from: BOS if the tokenizer has one, else EOS, as lm-evaluation-harness's `prefix_token_id`. It isn't padding: the first scored token needs one real token to be predicted from."""
        prefix = self.tokenizer.bos_token_id
        if prefix is None:
            prefix = self.tokenizer.eos_token_id
        if prefix is None:
            raise ValueError(
                "scoring needs a BOS or EOS prefix token, and the tokenizer has neither"
            )

        return prefix

    def _batches(self, order: list[int], input_lengths: list[int]) -> Iterator[list[int]]:
        """Splits `order` (longest first) into runs whose padded size fits `batch_tokens`, given each request's model-input length."""
        batch: list[int] = []
        for i in order:
            # `batch[0]` is the batch's longest request, so every row pads to its length.
            if batch and (len(batch) + 1) * input_lengths[batch[0]] > self.batch_tokens:
                yield batch
                batch = []
            batch.append(i)

        if batch:
            yield batch

    def _score(self, encoded: list[Encoded]) -> tuple[torch.Tensor, torch.Tensor]:
        """One batch's summed continuation log-probabilities, and whether each continuation is the greedy one."""
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
        token_logprob, token_is_argmax = _target_logprobs(logits, targets)

        # `where`, not a product: logits at padding positions may not be finite.
        logprob = token_logprob.where(scored, 0.0).sum(-1)
        # Greedy only if every continuation token is the argmax, as OLMES's `is_greedy`
        # (`oe_eval/models/eleuther_huggingface.py`). Positions before the continuation don't count.
        is_greedy = (token_is_argmax | ~scored).all(-1)

        return logprob.to(device, non_blocking=True), is_greedy.to(device, non_blocking=True)


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
    logits: torch.Tensor, targets: torch.Tensor, chunk_positions: int = 4096
) -> tuple[torch.Tensor, torch.Tensor]:
    """
    The FP32 log-softmax of `logits` `[rows, positions, vocab]`, in any dtype, at the int64 `targets` `[rows, positions]`, and whether each target is its position's argmax, the first on a tie.

    OLMES runs `log_softmax` in the model's dtype and takes the argmax of its output (`oe_eval/models/eleuther_huggingface.py`). FP32 here is our choice, so BF16 rounding of the normalizer doesn't move scores, and the argmax is taken over the FP32 logits, which the normalizer doesn't reorder; in BF16 OLMES's rounded log-probabilities can tie where ours don't, so a near-tie can resolve differently. `logits` can hold `batch_tokens * vocab` values, so an FP32 copy of all of it would double peak memory: rows are converted a few at a time, at most `chunk_positions` positions (rows times positions per row) and at least one row, about 1.6 GB in FP32 for a 100k vocabulary.
    """
    rows_per_chunk = max(1, chunk_positions // logits.shape[1])
    token_logprob = torch.empty(targets.shape, dtype=torch.float32, device=logits.device)
    token_is_argmax = torch.empty(targets.shape, dtype=torch.bool, device=logits.device)

    for start in range(0, len(logits), rows_per_chunk):
        rows = slice(start, start + rows_per_chunk)
        chunk = logits[rows].float()
        target_logits = chunk.gather(-1, targets[rows, :, None]).squeeze(-1)
        token_logprob[rows] = target_logits - chunk.logsumexp(-1)
        token_is_argmax[rows] = chunk.argmax(-1) == targets[rows]

    return token_logprob, token_is_argmax


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
