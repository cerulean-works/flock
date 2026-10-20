"""Batched loglikelihood scoring with a causal language model, kept on the model's device."""

import logging
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

import torch
from huggingface_hub import HfApi
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    PreTrainedModel,
    PreTrainedTokenizerBase,
)

from flock.eval.requests import Request, Scores

DEFAULT_BATCH_TOKENS = 32768
COMMIT_HASH = re.compile(r"[0-9a-f]{40}")

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModelRef:
    """A Hugging Face model repo pinned to a revision, written `repo@revision`."""

    repo: str
    revision: str

    @classmethod
    def parse(cls, model_id: str) -> ModelRef:
        """
        Parses `repo@revision`.

        Raises:
            `ValueError`: if the id has no revision, so an unpinned model can't be scored.
        """
        repo, _, revision = model_id.partition("@")
        if not repo or not revision:
            raise ValueError(f"model id {model_id!r} must be repo@revision")
        return cls(repo, revision)

    def __str__(self) -> str:
        return f"{self.repo}@{self.revision}"

    def resolve(self) -> ModelRef:
        """
        This ref with its revision as a commit hash. A full hash is returned as is, without asking the Hub, so pinned models load offline from the cache.

        Raises:
            `ValueError`: if the Hub doesn't report a commit hash for the revision.
        """
        if COMMIT_HASH.fullmatch(self.revision):
            return self
        sha = HfApi().model_info(self.repo, revision=self.revision).sha
        if sha is None:
            raise ValueError(f"the Hub reports no commit hash for {self}")
        return ModelRef(self.repo, sha)


class ScoringModel:
    """
    A causal LM and its tokenizer that answer loglikelihood [`~eval.requests.Request`]s in batches.

    Context and continuation are tokenized as OLMES does: whitespace at the end of the context moves to the start of the continuation, the pair is tokenized together, and the continuation's tokens are those after the context's own tokenization. No special tokens are added. Requests are sorted longest first and packed into left-padded batches of at most `batch_tokens` padded tokens. Inputs longer than `max_length` lose tokens from the left.

    Args:
        model (`PreTrainedModel`):
            A causal LM, already on its device and in its dtype.
        tokenizer (`PreTrainedTokenizerBase`):
            The model's tokenizer.
        name (`str`):
            What results call the model, usually `repo@revision`.
        max_length (`int`, *optional*):
            The longest input the model sees. Defaults to the config's `max_position_embeddings`.
        batch_tokens (`int`, *optional*, defaults to 32768):
            The most padded tokens in one forward pass.

    Raises:
        `ValueError`: if `batch_tokens` is less than 1.
    """

    def __init__(
        self,
        model: PreTrainedModel,
        tokenizer: PreTrainedTokenizerBase,
        *,
        name: str,
        max_length: int | None = None,
        batch_tokens: int = DEFAULT_BATCH_TOKENS,
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.name = name
        if batch_tokens < 1:
            raise ValueError(f"batch_tokens must be at least 1, got {batch_tokens}")
        if max_length is None:
            max_length = getattr(model.config, "max_position_embeddings", None)
            if max_length is None:
                raise ValueError(f"{name}: config has no max_position_embeddings; pass max_length")
        if max_length < 1:
            raise ValueError(f"max_length must be at least 1, got {max_length}")
        self.max_length = max_length
        self.batch_tokens = batch_tokens

    @classmethod
    def from_pretrained(
        cls,
        model_id: str,
        *,
        device: str | torch.device | None = None,
        dtype: torch.dtype = torch.bfloat16,
        batch_tokens: int = DEFAULT_BATCH_TOKENS,
    ) -> ScoringModel:
        """
        Loads `repo@revision` from the Hugging Face Hub.

        The revision is resolved to its commit hash, which becomes the model's `name`, so a branch or tag in `model_id` is recorded as the exact weights that were scored.

        Args:
            model_id (`str`):
                The model as `repo@revision`, for example `allenai/OLMo-2-0425-1B@main`. Pin a full 40-character commit hash to load offline from the cache.
            device (`str` or `torch.device`, *optional*):
                Where to put the weights. Defaults to CUDA when available, else the CPU.
            dtype (`torch.dtype`, *optional*, defaults to `torch.bfloat16`):
                The weights' dtype. Log-softmax always runs in float32.
            batch_tokens (`int`, *optional*, defaults to 32768):
                The most padded tokens in one forward pass.
        """
        ref = ModelRef.parse(model_id).resolve()
        device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        tokenizer = AutoTokenizer.from_pretrained(ref.repo, revision=ref.revision)
        assert tokenizer is not None  # The stubs allow None; loading raises instead.
        model = AutoModelForCausalLM.from_pretrained(
            ref.repo, revision=ref.revision, dtype=dtype, device_map=device
        )
        return cls(model, tokenizer, name=str(ref), batch_tokens=batch_tokens)

    @property
    def device(self) -> torch.device:
        """The device the weights and every returned tensor live on."""
        return self.model.device

    def encode(self, request: Request) -> tuple[list[int], list[int]]:
        """The (context, continuation) token ids the model scores for `request`."""
        return self._encode([request])[0]

    @torch.no_grad()  # Not inference_mode: buffers rebuilt here must stay usable in training.
    def loglikelihood(self, requests: Sequence[Request]) -> Scores:
        """
        Scores every request, in order.

        The model runs in eval mode, and each module is put back in the mode it was in. The scores stay on the model's device: nothing here waits for the GPU.

        Raises:
            `ValueError`: if a continuation has no tokens (it would score 0, above every real choice), or is longer than `max_length` on its own.
        """
        encoded = self._encode(requests)
        for request, (_, continuation) in zip(requests, encoded, strict=True):
            if not continuation:
                raise ValueError(f"continuation has no tokens: {request}")
            if len(continuation) > self.max_length:
                raise ValueError(f"continuation longer than {self.max_length} tokens: {request}")
        # Too-long inputs keep their whole continuation and lose context from the left, as in
        # lm-evaluation-harness. That changes what is scored, so say how often it happens.
        truncated = sum(len(c) + len(t) > self.max_length + 1 for c, t in encoded)
        if truncated:
            logger.warning(
                "%s: %d of %d requests exceed %d tokens; their contexts are left-truncated",
                self.name,
                truncated,
                len(encoded),
                self.max_length,
            )

        lengths = [min(len(c) + len(t), self.max_length + 1) - 1 for c, t in encoded]
        order = sorted(range(len(encoded)), key=lambda i: -lengths[i])

        logprob = torch.zeros(len(encoded), dtype=torch.float32, device=self.device)
        modes = [(module, module.training) for module in self.model.modules()]
        self.model.eval()
        try:
            for batch in self._batches(order, lengths):
                index = torch.tensor(batch).to(self.device, non_blocking=True)
                logprob[index] = self._score([encoded[i] for i in batch])
        finally:
            for module, training in modes:
                module.training = training
        num_tokens = torch.tensor([len(t) for _, t in encoded], dtype=torch.int64)
        return Scores(logprob, num_tokens.to(self.device, non_blocking=True))

    def _encode(self, requests: Sequence[Request]) -> list[tuple[list[int], list[int]]]:
        """[`encode`] for many requests, with one batched tokenizer call per kind of text."""
        pairs = []
        for request in requests:
            context = request.context.rstrip()
            pairs.append((context, request.context[len(context) :] + request.continuation))
        # Few-shot contexts repeat once per choice, so each is tokenized alone only once.
        contexts = sorted({context for context, _ in pairs if context})
        context_ids = dict(zip(contexts, self._tokenize(contexts), strict=True))

        # Not padding: an empty context still needs one real token for the continuation's first
        # token to be predicted from. lm-evaluation-harness's `prefix_token_id` uses BOS, else EOS.
        prefix = self.tokenizer.bos_token_id
        if prefix is None:
            prefix = self.tokenizer.eos_token_id
        if prefix is None and not all(context for context, _ in pairs):
            raise ValueError(
                "an empty context needs a BOS or EOS token, and the tokenizer has neither"
            )

        encoded = []
        for (context, _), ids in zip(pairs, self._tokenize([c + t for c, t in pairs]), strict=True):
            if not context:
                encoded.append(([prefix], ids))
            else:
                encoded.append((context_ids[context], ids[len(context_ids[context]) :]))
        return encoded

    def _tokenize(self, texts: list[str]) -> list[list[int]]:
        if not texts:
            return []
        return self.tokenizer(texts, add_special_tokens=False)["input_ids"]

    def _batches(self, order: list[int], lengths: list[int]) -> Iterator[list[int]]:
        """Splits `order` (longest first) into runs whose padded size fits `batch_tokens`."""
        batch: list[int] = []
        for i in order:
            if batch and (len(batch) + 1) * lengths[batch[0]] > self.batch_tokens:
                yield batch
                batch = []
            batch.append(i)
        if batch:
            yield batch

    def _score(self, encoded: list[tuple[list[int], list[int]]]) -> torch.Tensor:
        """Summed continuation log-probabilities of one left-padded batch."""
        windows = [
            (context + continuation)[-(self.max_length + 1) :] for context, continuation in encoded
        ]
        width = max(len(window) for window in windows) - 1
        # Left padding ends every continuation at the last position, so the model's own forward
        # computes logits for only the last `keep` positions: full-vocabulary logits for the
        # few-shot context would dominate memory.
        keep = max(len(continuation) for _, continuation in encoded)
        # The pad id never matters: padding is masked out of attention and never scored. 0 is used
        # instead of `tokenizer.pad_token_id` because that can be None, or added after the
        # embedding matrix was sized.
        pad = 0
        inputs, mask, positions, targets, scored = [], [], [], [], []
        for window, (_, continuation) in zip(windows, encoded, strict=True):
            length = len(window) - 1
            inputs.append([pad] * (width - length) + window[:-1])
            mask.append([0] * (width - length) + [1] * length)
            positions.append([0] * (width - length) + list(range(length)))
            targets.append([pad] * (keep - len(continuation)) + window[-len(continuation) :])
            scored.append([False] * (keep - len(continuation)) + [True] * len(continuation))

        device = self.device
        inputs_t, mask_t, positions_t = (
            torch.tensor(x).to(device, non_blocking=True) for x in (inputs, mask, positions)
        )
        logits = self.model(
            input_ids=inputs_t,
            attention_mask=mask_t,
            position_ids=positions_t,
            use_cache=False,
            logits_to_keep=keep,
        ).logits

        if logits.shape[1] != keep:
            raise ValueError(f"{self.name} ignored logits_to_keep: got {logits.shape[1]} positions")
        # With weights on several devices, the logits come out on the last one.
        targets_t, scored_t = (
            torch.tensor(x).to(logits.device, non_blocking=True) for x in (targets, scored)
        )
        # log_softmax at the target only: one float32 copy of the logits, not two.
        target_logits = logits.gather(-1, targets_t[..., None]).squeeze(-1).float()
        token_logprob = target_logits - logits.float().logsumexp(-1)
        # `where`, not a product: logits at padding positions may not be finite.
        return token_logprob.where(scored_t, 0.0).sum(-1).to(device, non_blocking=True)
