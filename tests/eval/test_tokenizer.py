import io
import json
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import torch
from huggingface_hub import constants
from huggingface_hub.errors import LocalEntryNotFoundError, RemoteEntryNotFoundError
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


def test_a_tokenizer_model_in_another_format_is_an_error_not_another_tokenization(tmp_path):
    # Llama 3's `tokenizer.model` is a tiktoken file: base64 of a token, then its rank.
    path = tmp_path / "tokenizer.model"
    path.write_text("IQ== 0\nIg== 1\n")
    with pytest.raises(RuntimeError):
        SentencePieceTokenizer(path)


SHA = "a" * 40
REF = ModelRef("org/repo", SHA)
SENTINEL = SimpleNamespace(vocab_files_names={"tokenizer_file": "tokenizer.json"})


@pytest.fixture
def auto(monkeypatch) -> list[tuple[str, str]]:
    """Stands in for `AutoTokenizer`, and returns what it was asked to load."""
    asked = []

    def from_pretrained(repo, revision):
        asked.append((repo, revision))
        return SENTINEL

    monkeypatch.setattr(model, "AutoTokenizer", SimpleNamespace(from_pretrained=from_pretrained))
    return asked


@pytest.fixture
def offline_cache(tmp_path, monkeypatch) -> Callable[..., None]:
    """Returns a function that writes `org/repo` into an empty Hub cache, with the Hub unreachable: `cached` files are in the snapshot, and `absent` ones are recorded as not in the repo, as a 404 records them."""
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    monkeypatch.setattr(constants, "HF_HUB_OFFLINE", True)

    def write(cached: Sequence[str] = (), absent: Sequence[str] = (), source: Path | None = None):
        repo = tmp_path / "models--org--repo"
        (repo / "snapshots" / SHA).mkdir(parents=True, exist_ok=True)
        (repo / ".no_exist" / SHA).mkdir(parents=True, exist_ok=True)
        for name in cached:
            path = repo / "snapshots" / SHA / name
            path.write_bytes(source.read_bytes() if source and name.endswith(".model") else b"{}")
        for name in absent:
            (repo / ".no_exist" / SHA / name).touch()

    return write


def test_a_repo_recorded_without_tokenizer_json_gets_sentencepiece(model_file, offline_cache, auto):
    offline_cache(["tokenizer.model"], ["tokenizer.json"], model_file)
    assert isinstance(load_tokenizer(REF), SentencePieceTokenizer)
    assert auto == []


def test_an_interrupted_download_does_not_look_like_a_sentencepiece_only_repo(
    model_file, offline_cache, auto
):
    # `tokenizer.model` landed, and nothing says the repo has no `tokenizer.json`.
    offline_cache(["tokenizer.model"], source=model_file)
    with pytest.raises(LocalEntryNotFoundError):
        load_tokenizer(REF)
    assert auto == []


def test_tokenizer_json_decides_without_fetching_tokenizer_model(offline_cache, auto):
    # Offline, asking for the uncached `tokenizer.model` would raise.
    offline_cache(["tokenizer.json"])
    assert load_tokenizer(REF) is SENTINEL
    assert auto == [("org/repo", SHA)]


def test_a_repo_with_neither_file_goes_to_auto_tokenizer(offline_cache, auto):
    # Nothing records `tokenizer.model`: only transformers has loaded this repo.
    offline_cache(["vocab.json", "merges.txt"], ["tokenizer.json"])
    assert load_tokenizer(REF) is SENTINEL


def test_an_unrecorded_cache_without_tokenizer_model_goes_to_auto_tokenizer(offline_cache, auto):
    # As `hf download` leaves such a repo: no records at all.
    offline_cache(["vocab.json", "merges.txt"])
    assert load_tokenizer(REF) is SENTINEL


def test_a_file_the_hub_answers_404_for_is_absent(model_file, monkeypatch, auto):
    def download(repo, filename, revision):
        if filename == "tokenizer.model":
            return str(model_file)
        raise RemoteEntryNotFoundError("404", response=Mock())

    monkeypatch.setattr(model, "hf_hub_download", download)
    assert isinstance(load_tokenizer(REF), SentencePieceTokenizer)
    assert auto == []


@pytest.mark.parametrize("failing", ["tokenizer.json", "tokenizer.model"])
def test_a_failed_hub_request_chooses_nothing(failing, model_file, monkeypatch, auto):
    def download(repo, filename, revision):
        if filename == failing:
            raise LocalEntryNotFoundError("timed out")
        if filename == "tokenizer.model":
            return str(model_file)
        raise RemoteEntryNotFoundError("404", response=Mock())

    monkeypatch.setattr(model, "hf_hub_download", download)
    # The next request may succeed and choose differently, so this one doesn't fall through.
    with pytest.raises(LocalEntryNotFoundError):
        load_tokenizer(REF)
    assert auto == []


@pytest.mark.parametrize(
    ("tokenizer_class", "name"),
    [
        ("RemBertTokenizer", "sentencepiece.model"),
        ("T5Tokenizer", "spiece.model"),
        ("T5Tokenizer", "sp.model"),
        ("XLMRobertaTokenizer", "sentencepiece.bpe.model"),
        ("SpeechT5Tokenizer", "spm_char.model"),
    ],
)
def test_a_conversion_under_another_name_is_refused(tokenizer_class, name, model_file, tmp_path):
    (tmp_path / name).write_bytes(model_file.read_bytes())
    (tmp_path / "tokenizer_config.json").write_text(
        json.dumps({"tokenizer_class": tokenizer_class})
    )
    with pytest.raises(ValueError, match=f"{tokenizer_class} is built on a SentencePiece model"):
        load_tokenizer(ModelRef(str(tmp_path), SHA))


def test_another_name_beside_tokenizer_json_goes_to_auto_tokenizer(tmp_path, auto):
    (tmp_path / "spiece.model").write_bytes(b"")
    (tmp_path / "tokenizer.json").write_text("{}")
    assert load_tokenizer(ModelRef(str(tmp_path), SHA)) is SENTINEL


def test_a_local_folder_chooses_by_its_own_files(model_file, tmp_path, auto):
    # Not a valid Hub repo id, so reaching the Hub would raise.
    assert isinstance(load_tokenizer(ModelRef(str(model_file.parent), SHA)), SentencePieceTokenizer)

    (tmp_path / "tokenizer.model").write_bytes(model_file.read_bytes())
    (tmp_path / "tokenizer.json").write_text("{}")
    assert load_tokenizer(ModelRef(str(tmp_path), SHA)) is SENTINEL


def test_a_resegmented_context_is_refused(scorer, model_file, monkeypatch):
    tokenizer = SentencePieceTokenizer(model_file)
    # A unigram model does this: the added text merges the context's last pieces into one.
    ids = {"黃色": [5, 6], "黃色的": [7], "黃色的花": [7, 8], "紅": [9], "紅色": [9, 10]}
    processor = SimpleNamespace(encode=lambda texts: [ids[text] for text in texts])
    monkeypatch.setattr(tokenizer, "processor", processor)
    resegmented = ScoringModel(scorer.model, tokenizer, name="tiny@0")

    assert resegmented.encode(Request("紅", "色")) == ([9], [10])
    for continuation in ("的", "的花"):
        with pytest.raises(ValueError, match="changes the context's tokens"):
            resegmented.encode(Request("黃色", continuation))


def test_a_context_that_tokenizes_to_nothing_gets_the_prefix_token(scorer, model_file, monkeypatch):
    tokenizer = SentencePieceTokenizer(model_file)
    ids = {"\u200b": [], "\u200b Paris": [5, 6]}
    processor = SimpleNamespace(encode=lambda texts: [ids[text] for text in texts])
    monkeypatch.setattr(tokenizer, "processor", processor)
    empty = ScoringModel(scorer.model, tokenizer, name="tiny@0")

    assert empty.encode(Request("\u200b", " Paris")) == ([tokenizer.bos_token_id], [5, 6])


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
