"""Run files in the s3 files backend (012, D82), and moving them there from the directory."""

import asyncio
import dataclasses

from app.api.scripts.migrate_files import migrate_files
from tests.app.conftest import Flocks
from tests.app.utils import history_rows
from tests.conftest import S3Server

FILES = "/api/v1/projects/flock/p/runs/r1/files"


def keys(s3_server: S3Server, bucket: str) -> list[str]:
    listed = s3_server.client().list_objects_v2(Bucket=bucket).get("Contents", [])
    return sorted(item["Key"] for item in listed)


def test_run_files_live_in_the_bucket(make_flock: Flocks, s3_server: S3Server):
    bucket = f"{s3_server.bucket_prefix()}files"
    flock = make_flock.s3(s3_server, files_backend="s3", files_bucket=bucket)
    store = flock.store
    api = flock.client()
    api.create_run("p", "r1")
    api.stream("p", "r1", history_rows(2, loss=float), complete=0)
    run = flock.call(store.get_run("flock", "p", "r1"))
    flock.call(store.save_file(run, "media/table.json", b'{"a": 1}'))
    flock.call(store.save_file(run, "wandb-metadata.json", b'{"gpu": "H100", "gpu_count": 2}'))

    assert keys(s3_server, bucket) == [
        f"runs/{run.pk}/media/table.json",
        f"runs/{run.pk}/wandb-metadata.json",
    ]
    assert not (flock.files_dir / str(run.pk)).exists()
    got = api.get(f"{FILES}/media/table.json")
    assert (got.status, got.body) == (200, b'{"a": 1}')
    assert got.headers["content-type"].startswith("application/json")
    assert api.get(f"{FILES}/missing.txt").status == 404
    assert api.get("/api/v1/projects/flock/p/runs/r1").json()["gpu_type"] == "H100"

    assert api.delete("/api/v1/projects/flock/p/runs/r1").status == 204
    assert keys(s3_server, bucket) == []


def test_files_move_to_the_bucket_while_served(make_flock: Flocks, s3_server: S3Server):
    """A flock on the local backend switches to s3: files not copied yet are still served from
    the directory, and ``scripts/migrate_files.py`` copies them (again: it resumes)."""
    local = make_flock.s3(s3_server)
    api = local.client()
    api.create_run("p", "r1")
    run = local.call(local.store.get_run("flock", "p", "r1"))
    local.call(local.store.save_file(run, "config.yaml", b"lr: 0.1\n"))
    local.call(local.store.save_file(run, "media/a.txt", b"a"))
    local.stop()

    bucket = f"{local.bucket_prefix}files"
    moved = make_flock.s3(s3_server, reuse=local, files_backend="s3", files_bucket=bucket)
    api = moved.client()
    assert keys(s3_server, bucket) == []
    assert api.get(f"{FILES}/config.yaml").body == b"lr: 0.1\n"  # from the directory

    settings = moved.store.settings
    assert asyncio.run(migrate_files(settings, report=lambda _: None)) == (2, 0)
    assert asyncio.run(migrate_files(settings, report=lambda _: None)) == (0, 2)
    assert keys(s3_server, bucket) == [f"runs/{run.pk}/config.yaml", f"runs/{run.pk}/media/a.txt"]
    removed = dataclasses.replace(settings)
    assert asyncio.run(migrate_files(removed, remove=True, report=lambda _: None)) == (0, 2)
    assert not (moved.files_dir / str(run.pk)).exists()
    assert api.get(f"{FILES}/media/a.txt").body == b"a"  # from the bucket
