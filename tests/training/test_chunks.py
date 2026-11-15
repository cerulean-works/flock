from itertools import pairwise
from pathlib import Path

import lance
import pyarrow as pa
import pytest
import torch
from lance.torch.data import SafeLanceDataset
from torch.utils.data import DataLoader
from transformers import TokenizersBackend

from flock.training import TokenChunkDataset
from flock.training.core.data_utils import LanceBatchCollator, ResumableBatchDataSampler


@pytest.mark.parametrize("context", [2, 4, 2048, 4096])
@pytest.mark.parametrize("padding_side", ["left", "right"])
def test_chunks_preserve_every_original_target_once(
    tmp_path: Path, tokenizer: TokenizersBackend, context: int, padding_side: str
) -> None:
    tokenizer.padding_side = padding_side
    texts = ["", "a", "b [EOS]"]
    for length in [context - 1, context, context + 1, 2 * context - 1, 2 * context]:
        texts.append("a " * (length - 1) + "[EOS]")
    texts.append("繁體中文 café [EOS]")
    path = str(tmp_path / "source.lance")
    lance.write_dataset(pa.table({"text": texts, "document_id": list(range(len(texts)))}), path)
    dataset = TokenChunkDataset.prepare(
        dataset=SafeLanceDataset(path, dataset_options={"version": 1}),
        tokenizer=tokenizer,
        input_column="text",
        cache_dir=tmp_path / "tokens",
        chunk_length=context,
    )
    expected = tokenizer(texts, truncation=False, padding=False)["input_ids"]
    actual = [[] for _ in texts]
    pairs = [[] for _ in texts]
    for row in dataset:
        document = row["document_id"]
        assert row["_flock_document_index"] == document
        ids = row["_flock_chunk_ids"]
        assert 2 <= len(ids) <= context
        actual[document].extend(ids[1:])
        pairs[document].extend(pairwise(ids))
    assert actual == [ids[1:] for ids in expected]
    assert pairs == [list(pairwise(ids)) for ids in expected]

    collator = LanceBatchCollator(tokenizer, "text", token_chunks=True)
    for batch_size in [1, 3, 8]:
        loader = DataLoader(dataset, batch_size=batch_size, collate_fn=collator.collate_fn)
        total = sum(int((batch["labels"] != -100).sum()) for batch in loader)
        assert total == sum(max(0, len(ids) - 1) for ids in expected)


@pytest.mark.parametrize("workers", [0, 1])
def test_chunk_sampler_resumes_the_same_suffix(
    tmp_path: Path, tokenizer: TokenizersBackend, workers: int
) -> None:
    path = str(tmp_path / "source.lance")
    lance.write_dataset(pa.table({"text": ["a b c d " * 7, "b c", "d a b " * 4]}), path)
    dataset = TokenChunkDataset.prepare(
        dataset=SafeLanceDataset(path, dataset_options={"version": 1}),
        tokenizer=tokenizer,
        input_column="text",
        cache_dir=tmp_path / "tokens",
        chunk_length=4,
    )
    collator = LanceBatchCollator(tokenizer, "text", token_chunks=True)
    outputs = []
    for skip in [0, 1]:
        sampler = ResumableBatchDataSampler(8, len(dataset), seed=37, skip_batches=skip)
        loader = DataLoader(
            dataset,
            batch_sampler=sampler,
            collate_fn=collator.collate_fn,
            num_workers=workers,
            multiprocessing_context="spawn" if workers else None,
        )
        outputs.append(list(loader))
    for expected, actual in zip(outputs[0][1:], outputs[1], strict=True):
        assert all(torch.equal(expected[key], actual[key]) for key in expected)
    assert all(batch["input_ids"].shape[0] <= 8 for batch in outputs[0])
    reopened = TokenChunkDataset(cache_dir=tmp_path / "tokens", chunk_length=4)
    assert reopened.fingerprint == dataset.fingerprint
    assert reopened.__getitems__([2, 0, 2]) == dataset.__getitems__([2, 0, 2])
    assert TokenChunkDataset(cache_dir=tmp_path / "tokens", chunk_length=5).fingerprint != (
        dataset.fingerprint
    )
    with pytest.raises(FileExistsError):
        TokenChunkDataset.prepare(
            dataset=SafeLanceDataset(path),
            tokenizer=tokenizer,
            input_column="text",
            cache_dir=tmp_path / "tokens",
        )
    with pytest.raises(IndexError):
        dataset[len(dataset)]
    with pytest.raises(ValueError, match="at least 2"):
        TokenChunkDataset(cache_dir=tmp_path / "tokens", chunk_length=1)
