"""Hugging Face model imports (017, D123): the safetensors header rewrite, and
`models.import_from_hf` and `flock models import` against a flock with S3 on, with tiny
transformers models saved to a local folder that stands in for a Hub repository."""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Never, cast

import pytest
import torch
from safetensors import safe_open
from transformers import AutoModelForCausalLM, LlamaConfig, Qwen3Config

from flock.cli import credentials
from flock.hub import models
from flock.hub.models import headers, hf, upload
from flock.hub.models.headers import hf_to_flock, rename_file, rename_index, rename_stream
from flock.training.core.models import HFCompatibleModel
from tests.app.conftest import Flocks, S3Flock, User
from tests.app.utils import cli_login
from tests.conftest import S3Server

if TYPE_CHECKING:
    from collections.abc import Callable

    from click.testing import Result
    from transformers import PreTrainedModel

SIZES: dict[str, Any] = {
    "vocab_size": 64,
    "hidden_size": 16,
    "intermediate_size": 32,
    "num_hidden_layers": 2,
    "num_attention_heads": 2,
    "num_key_value_heads": 1,
    "max_position_embeddings": 32,
    "bos_token_id": 1,
    "eos_token_id": 1,
}


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


@pytest.fixture
def alice(flock: S3Flock) -> User:
    return flock.add_user("alice@lab.test")


@pytest.fixture(autouse=True)
def cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The test's own cache (after ``isolated`` clears FLOCK_* settings)."""
    folder = tmp_path / "cache"
    monkeypatch.setenv("FLOCK_CACHE", str(folder))
    return folder


@pytest.fixture
def signed_in(flock: S3Flock, alice: User) -> str:
    token = cli_login(flock, flock.client(token=alice.signup["access_token"]), "laptop")
    credentials.save(flock.url, token, flock.client(token=token).get("/api/v1/me").json())
    return token


def save_repo(
    folder: Path, *, tied: bool = False, shard: str | None = None, dtype: torch.dtype | None = None
) -> PreTrainedModel:
    """A tiny model saved as a Hub repository holds it, with a README and tokenizer files."""
    torch.manual_seed(0)
    config = (
        Qwen3Config(**SIZES, head_dim=8, tie_word_embeddings=True) if tied else LlamaConfig(**SIZES)
    )
    model = AutoModelForCausalLM.from_config(config).eval()
    if dtype is not None:
        model = model.to(dtype)
    model.save_pretrained(folder, **({"max_shard_size": shard} if shard else {}))
    (folder / "README.md").write_text("# tiny\n")
    (folder / "tokenizer_config.json").write_text("{}")
    (folder / "notes.txt").write_text("not copied")
    return model


def staging_folders(cache_dir: Path) -> list[str]:
    root = cache_dir / "uploads"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


# ---------------------------------------------------------------------- the header rewrite


def test_the_header_rewrite_keeps_every_byte_of_every_tensor(tmp_path: Path) -> None:
    save_repo(tmp_path / "repo", shard="20KB")
    index = json.loads((tmp_path / "repo" / "model.safetensors.index.json").read_text())
    shards = sorted(set(index["weight_map"].values()))
    assert len(shards) > 1
    for shard in shards:
        source, target = tmp_path / "repo" / shard, tmp_path / shard
        written = rename_file(source, target, hf_to_flock)
        old, new = source.read_bytes(), target.read_bytes()
        old_length = struct.unpack("<Q", old[:8])[0]
        new_length = struct.unpack("<Q", new[:8])[0]
        assert old[8 + old_length :] == new[8 + new_length :]  # the data, byte for byte
        assert (8 + new_length) % 8 == 0
        with safe_open(str(source), "pt") as before, safe_open(str(target), "pt") as after:
            names = before.keys()
            assert list(after.keys()) == sorted(f"model.{name}" for name in names)
            assert after.metadata() == before.metadata()
            for name in names:
                tensor = after.get_tensor(f"model.{name}")
                assert torch.equal(tensor, before.get_tensor(name))
                assert written[f"model.{name}"].shape == tuple(tensor.shape)

    renamed = rename_index(index, hf_to_flock)
    assert renamed["metadata"] == index["metadata"]
    assert renamed["weight_map"] == {f"model.{k}": v for k, v in index["weight_map"].items()}


class Unseekable:
    """A stream that can only be read forward, as an HTTP response body, in short reads."""

    def __init__(self, data: bytes) -> None:
        self._data = io.BytesIO(data)

    def read(self, size: int) -> bytes:
        return self._data.read(min(size, 7))


def test_the_header_rewrite_streams(tmp_path: Path) -> None:
    save_repo(tmp_path)
    data = (tmp_path / "model.safetensors").read_bytes()
    target = io.BytesIO()
    written = rename_stream(Unseekable(data), target, hf_to_flock)
    assert all(name.startswith("model.") for name in written)
    (tmp_path / "renamed.safetensors").write_bytes(target.getvalue())
    with safe_open(str(tmp_path / "renamed.safetensors"), "pt") as file:
        assert set(file.keys()) == set(written)

    with pytest.raises(ValueError, match="already has"):
        headers.rename_header(headers.read_header(io.BytesIO(data)), lambda name: "same")
    with pytest.raises(ValueError, match="ended"):
        headers.read_header(io.BytesIO(data[:20]))


# ---------------------------------------------------------------------- refusals


def test_bin_only_repos_are_refused(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps(LlamaConfig(**SIZES).to_dict()))
    (tmp_path / "pytorch_model.bin").write_bytes(b"pickled")
    with pytest.raises(models.ModelError, match=r"only has pickled weights \(.bin\)"):
        models.import_from_hf(str(tmp_path), "flock/m")
    (tmp_path / "pytorch_model.bin").unlink()
    with pytest.raises(models.ModelError, match="no safetensors weights"):
        models.import_from_hf(str(tmp_path), "flock/m")
    (tmp_path / "config.json").unlink()
    with pytest.raises(models.ModelError, match="no config.json"):
        models.import_from_hf(str(tmp_path), "flock/m")


def test_remote_code_configs_are_refused(tmp_path: Path) -> None:
    save_repo(tmp_path)
    config = json.loads((tmp_path / "config.json").read_text())
    config |= {"model_type": "custom", "auto_map": {"AutoModelForCausalLM": "modeling.Custom"}}
    (tmp_path / "config.json").write_text(json.dumps(config))
    with pytest.raises(ValueError, match="doesn't ship"):
        models.import_from_hf(str(tmp_path), "flock/m")


def test_a_name_mismatch_aborts_before_anything_is_uploaded(
    tmp_path: Path, cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*args: Any, **kwargs: Any) -> Never:
        raise AssertionError("nothing may be uploaded")

    monkeypatch.setattr(upload, "push", never)
    save_repo(tmp_path / "repo")
    config = json.loads((tmp_path / "repo" / "config.json").read_text())
    (tmp_path / "repo" / "config.json").write_text(json.dumps({**config, "num_hidden_layers": 3}))
    with pytest.raises(models.ModelError, match="missing model.model.layers.2"):
        models.import_from_hf(str(tmp_path / "repo"), "flock/m")
    (tmp_path / "repo" / "config.json").write_text(json.dumps({**config, "hidden_size": 32}))
    with pytest.raises(models.ModelError, match="wrong shapes"):
        models.import_from_hf(str(tmp_path / "repo"), "flock/m")
    assert staging_folders(cache_dir) == []


# ---------------------------------------------------------------------- imports


@pytest.mark.parametrize(
    "repo_kind", ["sharded", "tied-bfloat16"], ids=["sharded", "tied-bfloat16"]
)
def test_import_then_load_gives_the_same_logits(
    flock: S3Flock, signed_in: str, tmp_path: Path, cache_dir: Path, repo_kind: str
) -> None:
    folder = tmp_path / "repo"
    if repo_kind == "sharded":
        save_repo(folder, shard="20KB")
    else:
        save_repo(folder, tied=True, dtype=torch.bfloat16)
    # Loaded as transformers loads it: casting a model to bfloat16 casts its rotary buffers too.
    reference = AutoModelForCausalLM.from_pretrained(folder, dtype="auto").eval()
    version = models.import_from_hf(str(folder), "flock/tiny", tags=["upstream"])
    assert str(version) == "flock/tiny@1" and version.tags == ("upstream",)
    assert version.step is None and version.run is None
    assert version.files is not None
    paths = {f.path for f in version.files}
    assert {"config.json", "flock.json", "README.md", "generation_config.json"} <= paths
    assert "tokenizer_config.json" in paths and "notes.txt" not in paths
    assert staging_folders(cache_dir) == []

    loaded = models.load_from_hub("flock/tiny", device="cpu")
    assert isinstance(loaded, HFCompatibleModel)
    downloaded = models.download("flock/tiny")
    config = json.loads((downloaded / "config.json").read_text())
    assert config["hf_config"] == json.loads((folder / "config.json").read_text())
    assert config["hf_repo"] == str(folder.resolve())
    described = json.loads((downloaded / "flock.json").read_text())
    assert described["architecture"] == "flock.training.core.models.hf:HFCompatibleModel"
    assert described["source"] == {"hub": "huggingface", "repo": str(folder), "revision": None}
    assert (described["run"], described["step"]) == (None, None)

    ids = torch.tensor([[5, 6, 7, 8, 9]])
    batch = {"input_ids": ids, "attention_mask": torch.ones_like(ids), "labels": ids}
    with torch.no_grad():
        torch.testing.assert_close(loaded.eval()(batch), reference(input_ids=ids).logits)
    if repo_kind == "sharded":
        assert "model.safetensors.index.json" in paths
        assert described["dtype"] == "float32"
    else:
        assert described["dtype"] == "bfloat16"
        assert loaded.model.lm_head.weight.dtype == torch.bfloat16
        assert loaded.model.lm_head.weight is loaded.model.get_input_embeddings().weight


def test_a_hub_repository_is_downloaded_at_a_commit(
    flock: S3Flock, signed_in: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "repo"
    reference = save_repo(folder)
    sha = "a" * 40
    asked: dict[str, Any] = {}

    def model_info(self: Any, repo: str, revision: str | None = None) -> SimpleNamespace:
        asked["info"] = (repo, revision)
        siblings = [SimpleNamespace(rfilename=p.name) for p in folder.iterdir()]
        siblings.append(SimpleNamespace(rfilename="pytorch_model.bin"))
        return SimpleNamespace(sha=sha, siblings=siblings)

    def snapshot_download(repo: str, revision: str, allow_patterns: list[str]) -> str:
        asked["download"] = (repo, revision, allow_patterns)
        return str(folder)

    monkeypatch.setattr("huggingface_hub.HfApi.model_info", model_info)
    monkeypatch.setattr("huggingface_hub.snapshot_download", snapshot_download)
    version = models.import_from_hf("acme/tiny", "flock/tiny", revision="v1")
    assert asked["info"] == ("acme/tiny", "v1")
    repo, revision, patterns = asked["download"]
    assert (repo, revision) == ("acme/tiny", sha)
    assert "model.safetensors" in patterns and "pytorch_model.bin" not in patterns
    assert "README.md" in patterns and "notes.txt" not in patterns

    downloaded = models.download("flock/tiny")
    described = json.loads((downloaded / "flock.json").read_text())
    assert described["source"] == {"hub": "huggingface", "repo": "acme/tiny", "revision": sha}
    assert json.loads((downloaded / "config.json").read_text())["hf_repo"] == "acme/tiny"
    loaded = cast("HFCompatibleModel", models.load_from_hub("flock/tiny", device="cpu"))
    assert all(
        torch.equal(v, reference.state_dict()[k]) for k, v in loaded.model.state_dict().items()
    )

    # The same commit again is the version already imported.
    assert models.import_from_hf("acme/tiny", "flock/tiny", revision="v1") == version


def test_a_folder_in_the_hf_cache_gives_its_repository_and_commit(tmp_path: Path) -> None:
    folder = tmp_path / "hub" / "models--acme--tiny" / "snapshots" / ("b" * 40)
    folder.mkdir(parents=True)
    save_repo(folder)
    found = hf.snapshot(str(folder))
    assert (found.repo, found.revision) == ("acme/tiny", "b" * 40)
    with pytest.raises(ValueError, match="no revisions"):
        hf.snapshot(str(folder), revision="main")


def test_cli_import(
    flock: S3Flock, signed_in: str, flock_cli: Callable[..., Result], tmp_path: Path
) -> None:
    save_repo(tmp_path / "repo")
    imported = flock_cli(
        "models", "import", str(tmp_path / "repo"), "flock/tiny", "--tag", "base", "--primary"
    )
    assert imported.exit_code == 0, imported.output
    assert imported.output.splitlines()[-1] == "flock/tiny@1"
    rows = flock_cli("models", "versions", "flock/tiny").output.splitlines()
    assert rows[0].split("\t")[:3] == ["@1", "primary", "base"]

    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "config.json").write_text("{}")
    (tmp_path / "bin" / "pytorch_model.bin").write_bytes(b"pickled")
    refused = flock_cli("models", "import", str(tmp_path / "bin"), "flock/other")
    assert refused.exit_code == 1 and "pickled weights" in refused.output
    (tmp_path / "bin" / "model.safetensors").write_bytes(b"")
    (tmp_path / "bin" / "config.json").write_text(json.dumps({"model_type": "custom"}))
    custom = flock_cli("models", "import", str(tmp_path / "bin"), "flock/other")
    assert custom.exit_code == 1 and "doesn't ship" in custom.output


def test_the_modules_import_no_heavy_library() -> None:
    for module in (hf, headers):
        source = Path(module.__file__).read_text()
        top_level = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
        assert all("torch" not in line and "transformers" not in line for line in top_level)
