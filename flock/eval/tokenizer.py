"""A SentencePiece model's own tokenization, and the type of every tokenizer scoring accepts."""

from pathlib import Path

from sentencepiece import SentencePieceProcessor
from transformers import PreTrainedTokenizerBase


class SentencePieceTokenizer:
    """
    A SentencePiece model's own tokenization, for a Hugging Face repo that ships only `tokenizer.model`.

    transformers 5 has no tokenizer that equals it: `AutoTokenizer` converts the model to a Tokenizers BPE that segments text differently, and `SentencePieceBackend` drops the leading `▁`. This class calls the model directly and offers only what [`~eval.model.ScoringModel`] reads. Literal `<s>`, `</s>` or `<unk>` in a text are ordinary pieces, because a document's text is text.

    Args:
        model_file (`str` or `Path`):
            The `tokenizer.model` file.
    """

    def __init__(self, model_file: str | Path) -> None:
        self.processor = SentencePieceProcessor(model_file=str(model_file))
        # SentencePiece writes "this model has no such token" as -1.
        bos, eos = self.processor.bos_id(), self.processor.eos_id()
        self.bos_token_id = bos if bos >= 0 else None
        self.eos_token_id = eos if eos >= 0 else None

    def __call__(self, texts: list[str], *, add_special_tokens: bool) -> dict[str, list[list[int]]]:
        """
        Returns each text's token ids as `input_ids`, as a transformers tokenizer does, from `encode` with the model file's own settings.

        Raises:
            `ValueError`: if `add_special_tokens` is true, which this tokenizer never does.
        """
        if add_special_tokens:
            raise ValueError("SentencePieceTokenizer adds no special tokens")

        return {"input_ids": self.processor.encode(texts)}


type Tokenizer = PreTrainedTokenizerBase | SentencePieceTokenizer
