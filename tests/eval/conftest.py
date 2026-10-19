import pytest
import torch
from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
from transformers import LlamaConfig, LlamaForCausalLM, PreTrainedTokenizerFast

from flock.eval.model import ScoringModel
from flock.eval.tasks import Dataset, Fewshot, MultipleChoiceTask
from flock.eval.tasks.arc import arc_doc

CORPUS = [
    "Question: Which gas do plants take in?\nAnswer: carbon dioxide",
    "Question: 臺灣最高的山是哪一座？\nAnswer: 玉山",
    "The quick brown fox jumps over the lazy dog. 繁體中文的評估。",
]


@pytest.fixture(scope="session", autouse=True)
def one_thread():
    """Tiny models run fastest on one CPU thread; more threads only contend."""
    threads = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(threads)


@pytest.fixture(scope="session")
def tokenizer() -> PreTrainedTokenizerFast:
    """A byte-level BPE tokenizer trained on a tiny corpus, with no BOS (like OLMo 2)."""
    tok = Tokenizer(models.BPE())
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=300,
        special_tokens=["<|endoftext|>", "<|pad|>"],
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
    )
    tok.train_from_iterator(CORPUS, trainer)
    return PreTrainedTokenizerFast(
        tokenizer_object=tok, eos_token="<|endoftext|>", pad_token="<|pad|>"
    )


@pytest.fixture(scope="session")
def lm(tokenizer: PreTrainedTokenizerFast) -> LlamaForCausalLM:
    torch.manual_seed(0)
    config = LlamaConfig(
        vocab_size=len(tokenizer),
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        max_position_embeddings=128,
    )
    return LlamaForCausalLM(config).eval()


@pytest.fixture
def scorer(lm: LlamaForCausalLM, tokenizer: PreTrainedTokenizerFast) -> ScoringModel:
    return ScoringModel(lm, tokenizer, name="tiny@0")


ROWS = {
    "train": [
        {
            "id": "shot-1",
            "question": "Which gas do plants take in?",
            "choices": {"text": ["oxygen", "carbon dioxide"], "label": ["A", "B"]},
            "answerKey": "B",
        },
        {
            "id": "shot-2",
            "question": "臺灣最高的山是哪一座？",
            "choices": {"text": ["玉山", "雪山", "合歡山"], "label": ["1", "2", "3"]},
            "answerKey": "1",
        },
    ],
    "test": [
        {
            "id": "q-1",
            "question": "What do bees make?",
            "choices": {"text": ["honey", "milk", "silk"], "label": ["A", "B", "C"]},
            "answerKey": "A",
        },
        {
            "id": "q-2",
            "question": "哪一個是水果？",
            "choices": {"text": ["石頭", "蘋果", "椅子", "雲"], "label": ["1", "2", "3", "4"]},
            "answerKey": "2",
        },
    ],
}

TASK = MultipleChoiceTask(
    name="toy",
    dataset=Dataset("cerulean/toy", "0" * 40, None, "test"),
    to_doc=arc_doc,
    primary_metric="acc_per_char",
    fewshot=Fewshot("train", ("shot-2", "shot-1")),
)


@pytest.fixture(autouse=True)
def offline_rows(monkeypatch):
    """Tasks read the rows above instead of downloading."""
    monkeypatch.setattr(Dataset, "load", lambda self, split=None: ROWS[split or self.split])
