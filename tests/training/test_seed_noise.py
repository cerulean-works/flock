import lance
import pyarrow as pa
import pytest
import torch
from lance.torch.data import SafeLanceDataset
from tokenizers import Tokenizer, models, pre_tokenizers
from transformers import TokenizersBackend

from flock.training.core.data_utils import LanceBatchCollator
from flock.training.core.models import OLMO2_190M_CONFIG, Olmo2
from flock.training.experiments.baselines.seed_noise import PackedDocs, spikes


@pytest.fixture
def docs(tmp_path):
    path = tmp_path / "docs.lance"
    lance.write_dataset(pa.table({"text": [f"d{index}" for index in range(12)]}), str(path))
    return SafeLanceDataset(str(path))


def test_190m_config_has_the_olmo_ladder_parameter_count():
    with torch.device("meta"):
        model = Olmo2(OLMO2_190M_CONFIG)
    total = sum(p.numel() for p in model.parameters())
    assert total == 267_424_512
    # Bhagia et al. 2024, Table 1, counts one of the two embedding matrices.
    assert total - model.model.embed_tokens.weight.numel() == 190_354_176


def test_packed_rows_join_consecutive_documents_spread_over_the_corpus(docs):
    packed = PackedDocs(docs, rows=3, docs_per_row=2, max_chars=100, eos="|")

    assert packed.stride == 4
    assert len(packed) == 3
    # Rows come back in the order asked for, as the shuffled sampler asks for them.
    assert packed.__getitems__([2, 0]) == [{"text": "d8|d9"}, {"text": "d0|d1"}]
    assert packed[1] == {"text": "d4|d5"}
    assert packed[1] == packed[1]  # the same row whatever the seed or the call

    with pytest.raises(IndexError):
        packed[3]


def test_packed_rows_are_cut_at_max_chars(docs):
    packed = PackedDocs(docs, rows=2, docs_per_row=3, max_chars=4, eos="|")
    assert [packed[0], packed[1]] == [{"text": "d0|d"}, {"text": "d6|d"}]


def test_more_documents_than_the_corpus_holds_are_refused(docs):
    with pytest.raises(ValueError, match="need more than 12 documents"):
        PackedDocs(docs, rows=7, docs_per_row=2, max_chars=100, eos="|")


def test_packed_rows_tokenize_with_eos_between_documents_and_no_padding(tmp_path, tokenizer):
    path = tmp_path / "words.lance"
    lance.write_dataset(pa.table({"text": ["a b", "c d", "b a", "d c"]}), str(path))
    packed = PackedDocs(
        SafeLanceDataset(str(path)), rows=2, docs_per_row=2, max_chars=100, eos=tokenizer.eos_token
    )

    batch = LanceBatchCollator(tokenizer, "text").collate_fn(packed.__getitems__([0, 1]))

    # a=2 b=3 c=4 d=5 [EOS]=1: each seam is one EOS, so documents never run together.
    assert batch["input_ids"].tolist() == [[2, 3, 1, 4], [3, 2, 1, 5]]
    assert batch["labels"].tolist() == [[3, 1, 4, 5], [2, 1, 5, 4]]
    assert batch["attention_mask"].all()


def test_full_rows_end_mid_document_only_without_an_appended_eos(tmp_path):
    # OLMo 2's tokenizer appends EOS (add_eos_token=True) after truncation, as this one does.
    backend = Tokenizer(models.WordLevel({"[UNK]": 0, "[EOS]": 1, "a": 2, "b": 3, "c": 4}, "[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer = TokenizersBackend(
        tokenizer_object=backend,
        eos_token="[EOS]",
        pad_token="[EOS]",
        model_max_length=4,
        padding_side="left",
        add_eos_token=True,
    )
    path = tmp_path / "long.lance"
    lance.write_dataset(pa.table({"text": ["a b c a b c"]}), str(path))
    packed = PackedDocs(
        SafeLanceDataset(str(path)), rows=1, docs_per_row=1, max_chars=100, eos="[EOS]"
    )
    collator = LanceBatchCollator(tokenizer, "text")

    # The cut row's last target would be a document end that isn't there.
    assert collator.collate_fn([packed[0]])["labels"].tolist() == [[3, 4, 1]]
    tokenizer.add_eos_token = False
    assert collator.collate_fn([packed[0]])["labels"].tolist() == [[3, 4, 2]]


def test_spikes_flag_a_window_above_the_running_maximum_of_the_ten_before():
    assert spikes([4.0] * 10 + [6.1]) == [10]
    assert spikes([4.0] * 10 + [6.0]) == []  # 1.5x exactly is not a spike
    # The window maximum of the previous 100 steps falls as training does.
    assert spikes([9.0] + [4.0] * 10 + [6.1]) == [11]
    assert spikes([1.0, 100.0] + [100.0] * 8) == []  # no full history yet
