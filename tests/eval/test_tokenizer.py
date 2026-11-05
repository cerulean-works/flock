import io
import math
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from sentencepiece import SentencePieceProcessor, SentencePieceTrainer
from transformers import LlamaConfig, LlamaForCausalLM

from flock.eval import model
from flock.eval.model import ModelRef, ScoringModel, load_tokenizer
from flock.eval.requests import Request
from flock.eval.tokenizer import SentencePieceTokenizer
from tests.eval.test_model import lm_eval_windows

CORPUS = [
    "Question: Which gas do plants take in?",
    "Answer: carbon dioxide",
    "The quick brown fox jumps over the lazy dog.",
    "臺灣最高的山是哪一座？答案是玉山。",
    "繁體中文的評估，黃色的花。",
    "A tag such as <s> or </s> or <unk> is text in a document.",
    "Compare a < b > c / d, and read s, u, n, k.",
]
TEXTS = [
    "The quick brown fox",
    "two  spaces and  more   of them",
    "first line\nsecond line\n\nthird",
    " leading space",
    "臺灣最高的山",
    "mixed 繁體中文 and English",
    "a <s> b",
    "a </s> b",
    "a <unk> b",
    "",
]


def train(path: Path, bos_id: int = 1, eos_id: int = 2) -> Path:
    """Writes a tiny SentencePiece model to `path`."""
    written = io.BytesIO()
    SentencePieceTrainer.train(
        sentence_iterator=iter(CORPUS),
        model_writer=written,
        vocab_size=96,
        hard_vocab_limit=False,
        character_coverage=1.0,
        num_threads=1,
        bos_id=bos_id,
        eos_id=eos_id,
    )
    path.write_bytes(written.getvalue())
    return path


@pytest.fixture(scope="module")
def model_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A tiny SentencePiece model, alone in a directory like the snapshot of such a repo."""
    return train(tmp_path_factory.mktemp("snapshot") / "tokenizer.model")


@pytest.fixture(scope="module")
def scorer(model_file) -> ScoringModel:
    tokenizer = SentencePieceTokenizer(model_file)
    torch.manual_seed(0)
    config = LlamaConfig(
        vocab_size=tokenizer.processor.vocab_size(),
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        max_position_embeddings=128,
    )
    return ScoringModel(LlamaForCausalLM(config).eval(), tokenizer, name="tiny@0")


def test_ids_equal_raw_encode_on_every_kind_of_text(model_file):
    tokenizer = SentencePieceTokenizer(model_file)
    raw = SentencePieceProcessor(model_file=str(model_file))
    ids = tokenizer(TEXTS, add_special_tokens=False)["input_ids"]

    assert ids == [raw.encode(text) for text in TEXTS]
    assert ids[-1] == []
    # transformers 5's `SentencePieceBackend` drops this leading piece on every string.
    assert raw.id_to_piece(ids[0][0]).startswith("▁")


def test_literal_special_token_text_is_ordinary_pieces(model_file):
    tokenizer = SentencePieceTokenizer(model_file)
    special = {tokenizer.bos_token_id, tokenizer.eos_token_id, tokenizer.processor.unk_id()}
    ids = tokenizer(["a <s> b", "a </s> b", "a <unk> b"], add_special_tokens=False)["input_ids"]
    for text_ids in ids:
        assert not special & set(text_ids)


def test_asking_for_special_tokens_raises(model_file):
    tokenizer = SentencePieceTokenizer(model_file)
    with pytest.raises(ValueError, match="adds no special tokens"):
        tokenizer(["The quick brown fox"], add_special_tokens=True)


def test_bos_and_eos_come_from_the_processor_and_may_be_absent(model_file, tmp_path):
    tokenizer = SentencePieceTokenizer(model_file)
    assert (tokenizer.bos_token_id, tokenizer.eos_token_id) == (1, 2)

    bare = SentencePieceTokenizer(train(tmp_path / "bare.model", bos_id=-1, eos_id=-1))
    assert (bare.bos_token_id, bare.eos_token_id) == (None, None)


def test_the_repos_files_choose_the_tokenizer(model_file, tmp_path, monkeypatch):
    ref = ModelRef("org/repo", "a" * 40)
    asked = []

    def snapshot(repo, revision, allow_patterns):
        asked.append((repo, revision))
        return str(folder)

    monkeypatch.setattr(model, "snapshot_download", snapshot)

    # Only `tokenizer.model`: `AutoTokenizer` must not be reached, so a load through it fails.
    folder = model_file.parent
    monkeypatch.setattr(model, "AutoTokenizer", None)
    assert isinstance(load_tokenizer(ref), SentencePieceTokenizer)
    assert asked == [("org/repo", "a" * 40)]

    sentinel = SimpleNamespace()
    auto = SimpleNamespace(from_pretrained=lambda repo, revision: sentinel)
    monkeypatch.setattr(model, "AutoTokenizer", auto)

    # Both files, as Gemma's repos ship: `tokenizer.json` wins, as it does today.
    folder = tmp_path / "both"
    folder.mkdir()
    (folder / "tokenizer.model").write_bytes(model_file.read_bytes())
    (folder / "tokenizer.json").write_text("{}")
    assert load_tokenizer(ref) is sentinel

    # Neither file: the choice stays `AutoTokenizer`'s.
    folder = tmp_path / "neither"
    folder.mkdir()
    assert load_tokenizer(ref) is sentinel


def test_a_multiple_choice_request_scores_end_to_end(scorer):
    requests = [
        Request("Question: Which gas do plants take in?\nAnswer:", " carbon dioxide"),
        Request("Question: 臺灣最高的山是哪一座？\nAnswer:", " 玉山"),
        Request("", "The fox"),
        Request("  ", "The fox"),
    ]
    scores = scorer.loglikelihood(requests)

    assert scorer.encode(requests[2]).context == [scorer.tokenizer.bos_token_id]
    # Scored together, each matches its own unbatched forward pass.
    for i, request in enumerate(requests):
        context, continuation = scorer.encode(request)
        ids = context + continuation
        with torch.no_grad():
            logits = scorer.model(torch.tensor([ids[:-1]])).logits[0].float()
        logprobs = logits.log_softmax(-1)
        start = len(ids) - 1 - len(continuation)
        expected = sum(logprobs[start + j, t].item() for j, t in enumerate(continuation))
        assert math.isclose(scores.logprob[i].item(), expected, abs_tol=2e-4)


def test_a_rolling_text_scores_end_to_end(scorer):
    text = " ".join(CORPUS)
    ids = scorer.tokenizer([text], add_special_tokens=False)["input_ids"][0]
    assert len(ids) > 2 * 8  # several windows of 8 tokens

    scores = scorer.rolling_loglikelihood([text, "臺灣最高的山"], window=8)

    # The windows lm-evaluation-harness cuts, starting from BOS, one unbatched forward pass each.
    expected = 0.0
    for context, continuation in lm_eval_windows(ids, scorer.tokenizer.bos_token_id, 8):
        window_ids = context + continuation
        with torch.no_grad():
            logits = scorer.model(torch.tensor([window_ids[:-1]])).logits[0].float()
        logprobs = logits.log_softmax(-1)
        start = len(window_ids) - 1 - len(continuation)
        expected += sum(logprobs[start + j, t].item() for j, t in enumerate(continuation))
    assert math.isclose(scores.logprob[0].item(), expected, abs_tol=2e-3)
