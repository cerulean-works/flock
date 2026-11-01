"""Scoring a version from the Flock registry (014, D106): `flock:` refs saved with
`save_to_hub` to a flock with S3 on, loaded through `load_from_hub` and recorded in the
provenance with their digest."""

import dataclasses
import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner
from transformers import PreTrainedTokenizerFast

from flock.cli import credentials
from flock.eval import cli, run
from flock.eval.model import FlockCausalLM, ScoringModel
from flock.eval.run import EvalConfig, evaluate, score_task
from flock.hub import models
from flock.training.core.models import Olmo2, Olmo2Config
from flock.training.core.models.base import BaseModel
from tests.app.conftest import Flocks, Postgres, S3Flock, User
from tests.app.utils import cli_login
from tests.conftest import S3Server

from .toy import TASK


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No Flock settings from the environment, a config dir and cache of the test's own, the toy
    task, and the toy tokenizer for every Flock model."""
    for name in list(os.environ):
        if name.startswith("FLOCK_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("FLOCK_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("FLOCK_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("FLOCK_COMMIT", "abc123")
    monkeypatch.setitem(run.TASKS, TASK.name, TASK)


@pytest.fixture
def make_flock(postgres: Postgres, tmp_path: Path) -> Iterator[Flocks]:
    flocks = Flocks(postgres, tmp_path)
    yield flocks
    flocks.stop()


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


@pytest.fixture
def signed_in(flock: S3Flock) -> User:
    """``alice`` of the org ``flock``, signed in as ``flock login`` would sign her in."""
    alice = flock.add_user("alice@lab.test")
    token = cli_login(flock, flock.client(token=alice.signup["access_token"]), "laptop")
    credentials.save(flock.url, token, flock.client(token=token).get("/api/v1/me").json())
    return alice


@pytest.fixture
def olmo(tokenizer: PreTrainedTokenizerFast, monkeypatch: pytest.MonkeyPatch) -> Olmo2:
    """A tiny `Olmo2` that names a tokenizer, which loads as the toy one, offline."""
    monkeypatch.setattr(BaseModel, "get_hf_tokenizer", lambda self: tokenizer)
    config = Olmo2Config(
        vocab_size=len(tokenizer),
        hidden_size=32,
        intermediate_size=64,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        max_position_embeddings=128,
        hf_repo="toy/tokenizer",
    )
    return Olmo2(config)


def test_evaluate_scores_a_saved_version_and_records_its_number_and_digest(
    signed_in: User,
    olmo: Olmo2,
    tokenizer: PreTrainedTokenizerFast,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    first = models.save_to_hub(olmo, "flock/tiny")
    other = Olmo2(dataclasses.replace(olmo.config, init_seed=7))
    second = models.save_to_hub(other, "flock/tiny", tags=["release"])
    assert isinstance(first, models.ModelVersion) and isinstance(second, models.ModelVersion)

    # No version is the primary, which the first version became.
    results = evaluate(
        EvalConfig(model="flock:flock/tiny", tasks=("toy",), out=tmp_path / "a", dtype="float32")
    )

    provenance = json.loads((tmp_path / "a" / "results.json").read_text())["provenance"]
    assert provenance["model"] == "flock:flock/tiny@1"
    assert provenance["model_digest"] == first.digest
    assert (provenance["attention"], provenance["max_length"]) == ("sdpa", 128)
    # The same scores as the model in memory, scored as a training loop would.
    in_memory = ScoringModel(FlockCausalLM(olmo.eval()), tokenizer, name="tiny")
    assert results.tasks[0].metrics == score_task(in_memory, TASK).metrics

    # A tag resolves to its number, from the command line too (the toy task under a name the
    # command line knows).
    monkeypatch.setitem(run.TASKS, "arc_easy", TASK)
    out = tmp_path / "b"
    args = ["run", "--model", "flock:flock/tiny@release", "--task", "arc_easy", "--out", str(out)]
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 0, result.output
    assert result.output.startswith("model flock:flock/tiny@2  flock abc123")
    provenance = json.loads((out / "results.json").read_text())["provenance"]
    assert (provenance["model"], provenance["model_digest"]) == (
        "flock:flock/tiny@2",
        second.digest,
    )


def test_an_unknown_version_fails_before_scoring(signed_in: User, olmo: Olmo2, tmp_path: Path):
    models.save_to_hub(olmo, "flock/tiny")
    with pytest.raises(models.ModelError, match="version 9 of flock/tiny not found"):
        evaluate(EvalConfig(model="flock:flock/tiny@9", tasks=("toy",), out=tmp_path / "out"))
    assert not (tmp_path / "out").exists()
