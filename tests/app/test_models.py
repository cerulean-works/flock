"""The model registry (014): the managed flock-models bucket (D97), upload sessions and their
commits (D99, D100), the proxy's write rule (D97, D101), resolving (D103), tags and the
primary version (D107), deleting, the trash and retention (D109), the sweep (D102), and the
links to training and eval runs (D106).
Files are written through the S3 proxy with boto3, as the client will write them."""

import asyncio
import base64
import hashlib
import json
import threading
from typing import Any

import asyncpg
import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from app.api.src.core.models import Models, digest
from tests.app.conftest import Client, Flocks, FlockServer, S3Flock
from tests.conftest import S3Server

MODELS = "/api/v1/orgs/flock/models"
MIB = 1024 * 1024


# ---------------------------------------------------------------------- helpers


def sql(flock: FlockServer, query: str, *args: Any) -> list[asyncpg.Record]:
    async def run() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(flock.database_url)
        try:
            return await conn.fetch(query, *args)
        finally:
            await conn.close()

    return asyncio.run(run())


def safetensors(*names: str, values: int = 4) -> bytes:
    """A safetensors file holding ``names``, each ``values`` float32 zeros."""
    header, offset = {}, 0
    for name in names:
        header[name] = {
            "dtype": "F32",
            "shape": [values],
            "data_offsets": [offset, offset + 4 * values],
        }
        offset += 4 * values
    raw = json.dumps(header).encode()
    return len(raw).to_bytes(8, "little") + raw + b"\0" * offset


def single() -> dict[str, bytes]:
    """The files of a model saved in one shard."""
    return {
        "config.json": b'{"d_model": 8}',
        "flock.json": b'{"format": 1}',
        "model.safetensors": safetensors("embed.weight", "head.weight"),
    }


def sharded() -> dict[str, bytes]:
    """The files of a model saved in two shards, with Hugging Face's index."""
    index = {
        "metadata": {"total_size": 32},
        "weight_map": {
            "embed.weight": "model-00001-of-00002.safetensors",
            "head.weight": "model-00002-of-00002.safetensors",
        },
    }
    return {
        "config.json": b"{}",
        "flock.json": b"{}",
        "model.safetensors.index.json": json.dumps(index).encode(),
        "model-00001-of-00002.safetensors": safetensors("embed.weight"),
        "model-00002-of-00002.safetensors": safetensors("head.weight"),
        "README.md": b"# a model",
    }


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def proxy_s3(flock: S3Flock, client: Client | None = None, read_only: bool = False) -> Any:
    """A boto3 client through the proxy, with an ephemeral key of ``client``'s user."""
    creds = flock.credentials(client, read_only=read_only)
    return boto3.client(
        "s3",
        endpoint_url=flock.proxy.url,
        aws_access_key_id=creds["access_key_id"],
        aws_secret_access_key=creds["secret_access_key"],
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 1}),
    )


def code(error: pytest.ExceptionInfo[ClientError]) -> str:
    return error.value.response["Error"]["Code"]


def upstream_name(flock: S3Flock, org: str = "flock") -> str:
    return f"{flock.bucket_prefix}{flock.call(flock.store.org(org)).pk:05d}-flock-models"


def keys(flock: S3Flock, bucket: str, prefix: str = "") -> list[str]:
    pages = (
        flock.s3.client().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
    )
    return sorted(o["Key"] for page in pages for o in page.get("Contents", []))


def models(flock: S3Flock) -> Models:
    return flock.store.models


class Pusher:
    """Opens uploads through the API and writes their files through the proxy, as one user."""

    def __init__(self, flock: S3Flock, client: Client | None = None) -> None:
        self.flock = flock
        self.api = client or flock.client()
        self.s3 = proxy_s3(flock, client)

    def open(self, name: str, files: dict[str, bytes], **body: Any) -> Any:
        declared = [{"path": p, "size": len(d)} for p, d in files.items()]
        return self.api.post(f"{MODELS}/{name}/uploads", {"files": declared, **body})

    def put(self, upload: dict, files: dict[str, bytes], hashes: dict[str, str] | None = None):
        for path, data in files.items():
            metadata = {"sha256": (hashes or {}).get(path, sha(data))}
            self.s3.put_object(
                Bucket="flock-models", Key=upload["prefix"] + path, Body=data, Metadata=metadata
            )

    def commit(self, name: str, ulid: str, files: dict[str, bytes]) -> Any:
        body = {"files": [{"path": p, "sha256": sha(d)} for p, d in files.items()]}
        return self.api.post(f"{MODELS}/{name}/uploads/{ulid}/commit", body)

    def push(self, name: str, files: dict[str, bytes] | None = None, **body: Any) -> dict:
        """Open, write and commit a version; returns it."""
        files = files if files is not None else single()
        opened = self.open(name, files, **body)
        assert opened.status == 201, opened.body
        upload = opened.json()
        self.put(upload, files)
        committed = self.commit(name, upload["ulid"], files)
        assert committed.status == 200, committed.body
        return committed.json()


@pytest.fixture
def flock(make_flock: Flocks, s3_server: S3Server) -> S3Flock:
    return make_flock.s3(s3_server)


@pytest.fixture
def pusher(flock: S3Flock) -> Pusher:
    return Pusher(flock)


# ---------------------------------------------------------------------- the managed bucket (D97)


def test_managed_bucket_is_created_adopted_and_kept(
    make_flock: Flocks, s3_server: S3Server, wait_for
):
    flock = make_flock.s3(s3_server)
    api = flock.client()
    buckets = {b["name"]: b for b in api.get("/api/v1/orgs/flock/buckets").json()["buckets"]}
    assert buckets["flock-models"]["managed"] == "models"
    assert buckets["flock-models"]["created_by"] is None
    assert flock.s3.client().head_bucket(Bucket=upstream_name(flock))
    refused = api.delete("/api/v1/orgs/flock/buckets/flock-models")
    assert refused.status == 409
    assert refused.json()["error"] == "flock-models is managed by Flock and can't be deleted"
    # Every member reads it, through the proxy too.
    assert "flock-models" in [b["Name"] for b in proxy_s3(flock).list_buckets()["Buckets"]]

    # A server where an org already had a bucket called flock-models adopts it at startup.
    plain = make_flock()
    prefix = s3_server.bucket_prefix()
    pk = plain.call(plain.store.org("flock")).pk
    old = f"{prefix}{pk:05d}-flock-models"
    s3_server.client().create_bucket(Bucket=old)
    s3_server.client().put_object(Bucket=old, Key="mine.txt", Body=b"kept")
    (row,) = sql(
        plain,
        "INSERT INTO s3_buckets (org_pk, name, upstream_name, created_at) "
        "VALUES ($1, 'flock-models', $2, now()) RETURNING pk",
        pk,
        old,
    )
    plain.stop()
    make_flock.servers.remove(plain)
    restarted = S3Flock(
        plain.database_url,
        plain.files_dir,
        s3_server,
        bucket_prefix=prefix,
        api_key=plain.api_key,
        token=plain.token,
    )
    make_flock.servers.append(restarted)

    def managed() -> list:
        listed = restarted.client().get("/api/v1/orgs/flock/buckets").json()["buckets"]
        return sorted((b["name"], b["managed"]) for b in listed)

    wait_for(lambda: managed() == [("flock-datasets", "datasets"), ("flock-models", "models")])
    found = sql(restarted, "SELECT pk FROM s3_buckets WHERE managed = 'models'")
    assert [r["pk"] for r in found] == [row["pk"]]
    assert keys(restarted, old) == ["mine.txt"]


def test_the_models_routes_create_a_missing_bucket(flock: S3Flock):
    flock.s3.client().delete_bucket(Bucket=upstream_name(flock))
    sql(flock, "DELETE FROM s3_buckets WHERE managed = 'models'")
    listed = flock.client().get(MODELS)
    assert listed.status == 200, listed.body
    assert listed.json()["models"] == []
    assert flock.s3.client().head_bucket(Bucket=upstream_name(flock))


# ---------------------------------------------------------------------- the proxy (D97, D101)


def test_the_proxy_lets_writes_only_into_the_caller_s_open_upload(flock: S3Flock, notification):
    alice = flock.add_user("alice@lab.test")
    bob = flock.add_user("bob@lab.test")
    pusher = Pusher(flock, alice.api)
    files = single()
    upload = pusher.open("m", files).json()
    prefix = upload["prefix"]
    assert prefix == f"m/u/{upload['ulid']}/"
    assert upload["bucket"] == "flock-models" and upload["part_size"] == 64 * MIB
    s3 = pusher.s3
    message = "flock-models is written through flock models push"

    def refused(call, *args, **kwargs) -> None:
        with pytest.raises(ClientError) as raised:
            call(*args, **kwargs)
        assert code(raised) == "AccessDenied"
        assert raised.value.response["Error"]["Message"] == message

    # Outside any upload, in another model's folder, or under a made-up ULID.
    refused(s3.put_object, Bucket="flock-models", Key="loose.txt", Body=b"x")
    refused(s3.put_object, Bucket="flock-models", Key=f"other/u/{upload['ulid']}/x", Body=b"x")
    refused(s3.put_object, Bucket="flock-models", Key="m/u/01JD3KQ8000000000000000000/x", Body=b"x")
    refused(s3.create_multipart_upload, Bucket="flock-models", Key="loose.bin")
    # Another member of the org can't write into alice's upload.
    refused(proxy_s3(flock, bob.api).put_object, Bucket="flock-models", Key=prefix + "x", Body=b"x")
    # A read-only key can't either.
    refused_read_only = proxy_s3(flock, alice.api, read_only=True)
    with pytest.raises(ClientError) as raised:
        refused_read_only.put_object(Bucket="flock-models", Key=prefix + "x", Body=b"x")
    assert code(raised) == "AccessDenied"

    # Into her own upload: PUTs and the multipart calls.
    pusher.put(upload, files)
    created = s3.create_multipart_upload(Bucket="flock-models", Key=prefix + "big.bin")
    part = s3.upload_part(
        Bucket="flock-models",
        Key=prefix + "big.bin",
        UploadId=created["UploadId"],
        PartNumber=1,
        Body=b"y" * 10,
    )
    assert (
        s3.list_parts(Bucket="flock-models", Key=prefix + "big.bin", UploadId=created["UploadId"])[
            "Parts"
        ][0]["ETag"]
        == part["ETag"]
    )
    s3.abort_multipart_upload(
        Bucket="flock-models", Key=prefix + "big.bin", UploadId=created["UploadId"]
    )
    # Never a delete, a copy or a DeleteObjects, even in her upload.
    refused(s3.delete_object, Bucket="flock-models", Key=prefix + "config.json")
    refused(
        s3.delete_objects,
        Bucket="flock-models",
        Delete={"Objects": [{"Key": prefix + "config.json"}]},
    )
    refused(
        s3.copy_object,
        Bucket="flock-models",
        Key=prefix + "copy.json",
        CopySource={"Bucket": "flock-models", "Key": prefix + "config.json"},
    )
    # Reads work for any member.
    bob_s3 = proxy_s3(flock, bob.api)
    listed = bob_s3.list_objects_v2(Bucket="flock-models", Prefix=prefix)
    assert sorted(o["Key"] for o in listed["Contents"]) == sorted(prefix + p for p in files)
    got = bob_s3.get_object(Bucket="flock-models", Key=prefix + "config.json")
    assert got["Body"].read() == files["config.json"]
    assert got["Metadata"]["sha256"] == sha(files["config.json"])

    # Once committed, the folder is closed: the bus drops it from the proxy's cache.
    proxy = flock.proxy.app.state.proxy
    with notification(proxy.bus, flock.proxy.loop, "flock_model_uploads", upload["ulid"]):
        assert pusher.commit("m", upload["ulid"], files).status == 200
    refused(s3.put_object, Bucket="flock-models", Key=prefix + "late.txt", Body=b"x")
    # An aborted upload is closed too.
    other = pusher.open("m", files).json()
    pusher.put(other, {"config.json": b"{}"})
    with notification(proxy.bus, flock.proxy.loop, "flock_model_uploads", other["ulid"]):
        assert alice.api.delete(f"{MODELS}/m/uploads/{other['ulid']}").status == 204
    refused(s3.put_object, Bucket="flock-models", Key=other["prefix"] + "x", Body=b"x")


def test_writes_touch_the_upload_s_activity(flock: S3Flock, pusher: Pusher, wait_for):
    upload = pusher.open("m", single()).json()
    sql(
        flock,
        "UPDATE model_uploads SET last_activity_at = now() - interval '3 days' WHERE ulid = $1",
        upload["ulid"],
    )
    pusher.put(upload, {"config.json": b"{}"})

    def touched() -> bool:
        (row,) = sql(
            flock,
            "SELECT last_activity_at > now() - interval '1 minute' AS fresh FROM model_uploads "
            "WHERE ulid = $1",
            upload["ulid"],
        )
        return row["fresh"]

    wait_for(touched)


# ---------------------------------------------------------------------- uploads (D100)


def test_open_commit_and_commit_again(flock: S3Flock, pusher: Pusher):
    api = flock.client()
    files = sharded()
    opened = pusher.open("olmo2-1b", files, metadata={"lr": 0.001}, step=12)
    assert opened.status == 201, opened.body
    upload = opened.json()
    assert upload["state"] == "open" and upload["user"]["email"] == flock.ADMIN_EMAIL
    assert [f["path"] for f in upload["files"]] == sorted(files)
    listed = api.get(f"{MODELS}/olmo2-1b/uploads").json()["uploads"]
    assert [u["ulid"] for u in listed] == [upload["ulid"]]
    pusher.put(upload, files)
    committed = pusher.commit("olmo2-1b", upload["ulid"], files)
    assert committed.status == 200, committed.body
    version = committed.json()
    expected = [{"path": p, "size": len(d), "sha256": sha(d)} for p, d in sorted(files.items())]
    assert version["number"] == 1 and version["primary"] is True
    assert version["files"] == expected
    assert version["digest"] == digest(expected)
    assert version["total_bytes"] == sum(len(d) for d in files.values())
    assert version["metadata"] == {"lr": 0.001} and version["step"] == 12
    assert version["prefix"] == upload["prefix"]
    assert version["model"] == "flock/olmo2-1b"

    # Committing again (a client that crashed waiting) answers the same version.
    again = pusher.commit("olmo2-1b", upload["ulid"], files)
    assert again.status == 200 and again.json()["number"] == 1
    assert api.get(f"{MODELS}/olmo2-1b/uploads/{upload['ulid']}").json()["state"] == "committed"
    assert api.get(f"{MODELS}/olmo2-1b/uploads").json()["uploads"] == []

    # manifest.json is in the folder.
    bucket = upstream_name(flock)
    manifest = flock.s3.client().get_object(Bucket=bucket, Key=upload["prefix"] + "manifest.json")
    body = json.loads(manifest["Body"].read())
    assert body["number"] == 1 and body["digest"] == version["digest"]
    assert body["files"] == expected and body["model"] == "flock/olmo2-1b"

    models_list = api.get(MODELS).json()["models"]
    assert [(m["name"], m["latest"], m["primary"], m["versions"]) for m in models_list] == [
        ("olmo2-1b", 1, 1, 1)
    ]


def test_opening_checks_names_and_files(flock: S3Flock, pusher: Pusher):
    assert pusher.open("Bad_Name", single()).status == 422
    assert pusher.open("m", {"config.json": b"{}", "flock.json": b"{}"}).status == 422
    assert pusher.open("m", {**single(), "manifest.json": b"{}"}).status == 422
    assert pusher.open("m", {**single(), "../escape": b"x"}).status == 422
    assert pusher.open("m", single(), tags=["12"]).status == 422
    assert pusher.open("m", single(), tags=["primary"]).status == 422
    assert pusher.open("m", single(), tags=["a", "a"]).status == 422
    assert flock.client().get(MODELS).json()["models"] == []


def test_commit_refuses_what_wasnt_declared(flock: S3Flock, pusher: Pusher):
    files = single()
    upload = pusher.open("m", files).json()
    ulid, prefix = upload["ulid"], upload["prefix"]

    # Nothing written yet: everything is missing.
    missing = pusher.commit("m", ulid, files)
    assert missing.status == 409
    assert missing.json()["missing"] == sorted(files)

    pusher.put(upload, files)
    pusher.put(upload, {"extra.txt": b"x"})
    extra = pusher.commit("m", ulid, files)
    assert extra.status == 409 and extra.json()["extra"] == ["extra.txt"]
    assert "missing" not in extra.json()

    # Rewritten (the proxy has no delete) at another size, under the same upload.
    pusher.put(upload, {"config.json": b'{"d_model": 16}'})
    resized = pusher.commit("m", ulid, files)
    assert resized.status == 409 and resized.json()["resized"] == ["config.json"]

    # A multipart upload still open under the prefix.
    upload2 = pusher.open("m", files).json()
    pusher.put(upload2, files)
    s3 = pusher.s3
    created = s3.create_multipart_upload(Bucket="flock-models", Key=upload2["prefix"] + "x.bin")
    uploading = pusher.commit("m", upload2["ulid"], files)
    assert uploading.status == 409 and uploading.json()["uploading"] == ["x.bin"]
    s3.abort_multipart_upload(
        Bucket="flock-models", Key=upload2["prefix"] + "x.bin", UploadId=created["UploadId"]
    )
    # A sha256 that isn't the object's metadata.
    wrong = {**{p: sha(d) for p, d in files.items()}, "config.json": "0" * 64}
    upload3 = pusher.open("m", files).json()
    pusher.put(upload3, files, wrong)
    mismatched = pusher.commit("m", upload3["ulid"], files)
    assert mismatched.status == 409 and mismatched.json()["mismatched"] == ["config.json"]
    # The commit's files must be the declared ones.
    partial = flock.client().post(
        f"{MODELS}/m/uploads/{upload2['ulid']}/commit",
        {"files": [{"path": "config.json", "sha256": sha(files["config.json"])}]},
    )
    assert partial.status == 422

    # Every refusal left the uploads open, and committing the right files works.
    states = sql(flock, "SELECT state FROM model_uploads ORDER BY pk")
    assert [r["state"] for r in states] == ["open", "open", "open"]
    assert pusher.commit("m", upload2["ulid"], files).status == 200
    assert keys(flock, upstream_name(flock), prefix) == sorted(
        prefix + p for p in [*files, "extra.txt"]
    )


def test_commit_reads_the_safetensors_headers(flock: S3Flock, pusher: Pusher):
    def refused(files: dict[str, bytes]) -> Any:
        upload = pusher.open("m", files).json()
        pusher.put(upload, files)
        return pusher.commit("m", upload["ulid"], files)

    not_safetensors = {**single(), "model.safetensors": b"\x10" * 64}
    answer = refused(not_safetensors)
    assert (
        answer.status == 422
        and "model.safetensors isn't a safetensors file" in answer.json()["error"]
    )
    short = {**single(), "model.safetensors": b"abc"}
    assert refused(short).status == 422

    files = sharded()
    index = json.loads(files["model.safetensors.index.json"])
    index["weight_map"]["lm.weight"] = "model-00002-of-00002.safetensors"
    files["model.safetensors.index.json"] = json.dumps(index).encode()
    answer = refused(files)
    assert answer.status == 422
    assert "lm.weight" in answer.json()["error"]

    files = sharded()
    index = json.loads(files["model.safetensors.index.json"])
    index["weight_map"]["lm.weight"] = "model-00003-of-00003.safetensors"
    files["model.safetensors.index.json"] = json.dumps(index).encode()
    answer = refused(files)
    assert answer.status == 422 and "isn't in the upload" in answer.json()["error"]


def test_concurrent_commits_get_consecutive_numbers(flock: S3Flock, pusher: Pusher):
    files = single()
    uploads = []
    for _ in range(4):
        upload = pusher.open("m", files).json()
        pusher.put(upload, files)
        uploads.append(upload["ulid"])
    clients = [flock.client() for _ in uploads]
    numbers: list[int] = []
    barrier = threading.Barrier(len(uploads))

    def commit(client: Client, ulid: str) -> None:
        barrier.wait()
        body = {"files": [{"path": p, "sha256": sha(d)} for p, d in files.items()]}
        answer = client.post(f"{MODELS}/m/uploads/{ulid}/commit", body)
        assert answer.status == 200, answer.body
        numbers.append(answer.json()["number"])

    threads = [
        threading.Thread(target=commit, args=pair) for pair in zip(clients, uploads, strict=True)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(numbers) == [1, 2, 3, 4]


def test_dedupe_key_returns_the_existing_version(flock: S3Flock, pusher: Pusher):
    files = single()
    first = pusher.push("m", files, dedupe_key="run:12000")
    again = pusher.open("m", files, dedupe_key="run:12000")
    assert again.status == 200
    assert again.json()["number"] == first["number"]
    assert sql(flock, "SELECT count(*) FROM model_uploads")[0][0] == 1

    # Two uploads opened before either committed: the second commit answers the first's
    # version, and its own folder goes.
    a = pusher.open("m", files, dedupe_key="run:13000").json()
    b = pusher.open("m", files, dedupe_key="run:13000").json()
    pusher.put(a, files)
    pusher.put(b, files)
    assert pusher.commit("m", a["ulid"], files).json()["number"] == 2
    answered = pusher.commit("m", b["ulid"], files)
    assert answered.status == 200 and answered.json()["number"] == 2
    assert keys(flock, upstream_name(flock), b["prefix"]) == []
    assert flock.client().get(f"{MODELS}/m/uploads/{b['ulid']}").json()["state"] == "aborted"
    assert pusher.commit("m", b["ulid"], files).json()["number"] == 2


def test_training_runs_are_linked(flock: S3Flock, pusher: Pusher):
    api = flock.client()
    api.create_run("pretrain", "3k9x2")
    version = pusher.push("m", run="flock/pretrain/3k9x2", step=12000)
    assert version["run"] == "flock/pretrain/3k9x2" and version["step"] == 12000
    # An unknown run, or another org's, is stored nowhere, and the version is still made.
    unknown = pusher.push("m", run="other/pretrain/3k9x2", step=1)
    assert unknown["run"] is None and unknown["number"] == 2


def eval_links(flock: S3Flock) -> list[tuple[str, int]]:
    """Every eval link, as (run, version number)."""
    rows = sql(
        flock,
        "SELECT r.name, v.number FROM model_version_runs e JOIN runs r ON r.pk = e.run_pk "
        "JOIN model_versions v ON v.pk = e.version_pk WHERE e.kind = 'eval' "
        "ORDER BY r.name, v.number",
    )
    return [(name, number) for name, number in rows]


def test_eval_runs_are_linked_from_their_config(flock: S3Flock, pusher: Pusher):
    api = flock.client()
    pusher.push("m")
    pusher.push("m", tags=["release"])
    # As eval's log_to_tracker sends it: the provenance's fields, `model` among them.
    config = {"model": "flock:flock/m@2", "model_digest": "d", "flock_commit": "abc"}
    created = api.create_run("evals", "e1", config=config)
    assert created["inserted"]
    assert eval_links(flock) == [("e1", 2)]
    version = api.get(f"{MODELS}/m/versions/2").json()
    assert version["evals"] == ["flock/evals/e1"] and version["eval_runs"] == 1
    assert api.get(f"{MODELS}/m/resolve", ref="release").json()["evals"] == ["flock/evals/e1"]
    # The SDK sends the config again as it changes: the link is made once, and stays when
    # another version is named.
    api.create_run("evals", "e1", config={**config, "limit": 10})
    assert eval_links(flock) == [("e1", 2)]
    api.create_run("evals", "e1", config={**config, "model": "flock:flock/m@1"})
    assert eval_links(flock) == [("e1", 1), ("e1", 2)]
    # A bare value, as a client that doesn't wrap config entries sends it.
    query = (
        "mutation U($name: String, $project: String, $config: JSONString) { upsertBucket("
        "input: {name: $name, modelName: $project, config: $config}) { inserted } }"
    )
    bare = {"name": "e2", "project": "evals", "config": json.dumps({"model": "flock:flock/m@1"})}
    assert "errors" not in api.graphql(query, bare).json()
    assert eval_links(flock) == [("e1", 1), ("e1", 2), ("e2", 1)]


def test_eval_configs_that_name_no_live_version_link_nothing(
    flock: S3Flock, pusher: Pusher, wait_for
):
    api = flock.client()
    pusher.push("m", tags=["release"])
    pusher.push("m")
    pusher.push("gone")
    api.delete(f"{MODELS}/m/versions/2")
    api.delete(f"{MODELS}/gone")
    refused = [
        "flock:flock/m",  # no version
        "flock:flock/m@primary",
        "flock:flock/m@release",  # a tag, which provenance never writes
        "flock:flock/m@01",
        "flock:flock/m@3",  # no such version
        "flock:flock/m@99999999999999999999",
        "flock:flock/m@2",  # deleted
        "flock:flock/gone@1",  # a deleted model
        "flock:flock/missing@1",
        "flock:flock/m@1 ",
        "flock:flock/a/m@1",
        "allenai/OLMo-2-0425-1B@main",
        12,
        {"nested": "flock:flock/m@1"},
    ]
    for i, model in enumerate(refused):
        created = api.create_run("evals", f"e{i}", config={"model": model})
        assert created["inserted"], model
    api.create_run("evals", "no-model", config={"lr": 0.1})
    # Another org's run naming this org's version.
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    olga = flock.add_user("olga@lab.test", org="lab")
    other = olga.api.create_run("evals", "theirs", config={"model": "flock:flock/m@1"})
    assert other["bucket"]["project"]["entity"]["name"] == "lab"
    assert eval_links(flock) == []
    # Every run was stored as usual.
    assert api.get("/api/v1/projects/flock/evals/runs").json()["total"] == len(refused) + 1


def test_a_runs_versions(flock: S3Flock, pusher: Pusher):
    api = flock.client()
    api.create_run("train", "t1")
    pusher.push("m", run="flock/train/t1", step=100, tags=["checkpoint-100"])
    pusher.push("m", run="flock/train/t1", step=200, tags=["checkpoint-200"])
    pusher.push("a", run="flock/train/t1", step=200)
    pusher.push("m")  # made by no run
    api.create_run("evals", "e1", config={"model": "flock:flock/m@2"})
    api.create_run("evals", "e2", config={"model": "flock:flock/m@2"})
    api.create_run("evals", "e3", config={"model": "flock:flock/m@3"})
    runs = "/api/v1/projects/flock/{}/runs/{}/models"

    made = api.get(runs.format("train", "t1"))
    assert made.status == 200, made.body
    body = made.json()
    assert body["scored"] == []
    assert [(v["model"], v["number"], v["step"], v["tags"]) for v in body["made"]] == [
        ("flock/a", 1, 200, []),
        ("flock/m", 1, 100, ["checkpoint-100"]),
        ("flock/m", 2, 200, ["checkpoint-200"]),
    ]
    assert [v["evals"] for v in body["made"]] == [[], [], ["flock/evals/e1", "flock/evals/e2"]]
    assert body["made"][0]["run"] == "flock/train/t1"
    assert body["made"][0]["created_by"]["email"] == flock.ADMIN_EMAIL

    scored = api.get(runs.format("evals", "e1")).json()
    assert scored["made"] == []
    assert [(v["model"], v["number"]) for v in scored["scored"]] == [("flock/m", 2)]
    assert scored["scored"][0]["evals"] == ["flock/evals/e1", "flock/evals/e2"]

    # A deleted eval run drops out of `evals`, and a deleted version out of both lists.
    assert api.delete("/api/v1/projects/flock/evals/runs/e2", force="true").status == 204
    assert api.get(runs.format("evals", "e1")).json()["scored"][0]["evals"] == ["flock/evals/e1"]
    assert api.delete(f"{MODELS}/m/versions/2").status == 200
    assert api.get(runs.format("evals", "e1")).json()["scored"] == []
    assert [v["number"] for v in api.get(runs.format("train", "t1")).json()["made"]] == [1, 1]

    assert api.get(runs.format("train", "nope")).status == 404
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    olga = flock.add_user("olga@lab.test", org="lab")
    assert olga.api.get(runs.format("train", "t1")).status == 404
    assert olga.api.get("/api/v1/projects/flock/train/runs/t1").status == 404


# ---------------------------------------------------------------------- aborting and the sweep


def test_abort_removes_what_landed(flock: S3Flock, pusher: Pusher):
    alice = flock.add_user("alice@lab.test")
    files = single()
    upload = pusher.open("m", files).json()
    pusher.put(upload, files)
    created = pusher.s3.create_multipart_upload(Bucket="flock-models", Key=upload["prefix"] + "big")
    # Only who opened it, or an admin, may abort it.
    assert alice.api.delete(f"{MODELS}/m/uploads/{upload['ulid']}").status == 403
    assert flock.client().delete(f"{MODELS}/m/uploads/{upload['ulid']}").status == 204
    bucket = upstream_name(flock)
    assert keys(flock, bucket, upload["prefix"]) == []
    uploads = flock.s3.client().list_multipart_uploads(Bucket=bucket).get("Uploads", [])
    assert created["UploadId"] not in [u["UploadId"] for u in uploads]
    assert pusher.commit("m", upload["ulid"], files).status == 409
    # Aborting again is fine; a committed upload can't be.
    assert flock.client().delete(f"{MODELS}/m/uploads/{upload['ulid']}").status == 204
    version = pusher.push("m")
    ulid = version["prefix"].split("/")[2]
    assert flock.client().delete(f"{MODELS}/m/uploads/{ulid}").status == 409


def test_sweep_expires_idle_uploads_and_removes_strays(
    flock: S3Flock, pusher: Pusher, monkeypatch: pytest.MonkeyPatch
):
    files = single()
    idle = pusher.open("m", files).json()
    pusher.put(idle, files)
    created = pusher.s3.create_multipart_upload(Bucket="flock-models", Key=idle["prefix"] + "big")
    busy = pusher.open("m", files).json()
    pusher.put(busy, files)
    kept = pusher.push("m")
    sql(
        flock,
        "UPDATE model_uploads SET last_activity_at = now() - interval '8 days' WHERE ulid = $1",
        idle["ulid"],
    )
    bucket = upstream_name(flock)
    client = flock.s3.client()
    # A folder with no upload row (as if the row's insert never committed), and an aborted
    # upload's folder whose cleanup failed.
    client.put_object(Bucket=bucket, Key="m/u/01JD3KQ8000000000000000000/x", Body=b"x")
    aborted = pusher.open("m", files).json()
    pusher.put(aborted, files)
    sql(flock, "UPDATE model_uploads SET state = 'aborted' WHERE ulid = $1", aborted["ulid"])

    done = flock.call(models(flock).sweep())
    assert done["expired"] == 1
    assert done["strays"] == 1  # the aborted one; the row-less one is too new
    assert keys(flock, bucket, idle["prefix"]) == []
    uploads = client.list_multipart_uploads(Bucket=bucket).get("Uploads", [])
    assert created["UploadId"] not in [u["UploadId"] for u in uploads]
    assert keys(flock, bucket, aborted["prefix"]) == []
    assert keys(flock, bucket, busy["prefix"]) != []
    assert keys(flock, bucket, kept["prefix"]) != []
    state = flock.client().get(f"{MODELS}/m/uploads/{idle['ulid']}").json()["state"]
    assert state == "expired"
    assert pusher.commit("m", idle["ulid"], files).status == 409

    monkeypatch.setattr(models(flock), "STRAY_AFTER", 0)  # as if a day had passed
    assert flock.call(models(flock).sweep())["strays"] == 1
    assert keys(flock, bucket, "m/u/01JD3KQ8000000000000000000/") == []


def test_missing_manifests_are_written(flock: S3Flock, pusher: Pusher, monkeypatch):
    upstream = flock.store.s3.upstream
    assert upstream is not None
    original = upstream.put_object

    async def failing(bucket: str, key: str, *args: Any) -> None:
        if key.endswith("manifest.json"):
            raise OSError("down")
        await original(bucket, key, *args)

    monkeypatch.setattr(upstream, "put_object", failing)
    version = pusher.push("m")
    bucket = upstream_name(flock)
    assert keys(flock, bucket, version["prefix"] + "manifest.json") == []
    monkeypatch.setattr(upstream, "put_object", original)
    assert flock.call(models(flock).write_manifests()) == 1
    assert keys(flock, bucket, version["prefix"] + "manifest.json") != []
    assert flock.call(models(flock).write_manifests()) == 0


# ---------------------------------------------------------------------- tags and the primary


def test_tags(flock: S3Flock, pusher: Pusher):
    api = flock.client()
    pusher.push("m", tags=["checkpoint-1000", "latest"])
    pusher.push("m", tags=["checkpoint-2000", "latest"])  # latest moves at upload
    info = api.get(f"{MODELS}/m").json()
    assert [(t["name"], t["version"]) for t in info["tags"]] == [
        ("checkpoint-1000", 1),
        ("checkpoint-2000", 2),
        ("latest", 2),
    ]
    assert info["tags"][0]["moved_by"]["email"] == flock.ADMIN_EMAIL
    # A second checkpoint-1000 is refused when it's opened.
    taken = pusher.open("m", single(), tags=["checkpoint-1000"])
    assert taken.status == 409 and taken.json()["version"] == 1

    tag = f"{MODELS}/m/tags"
    assert api.request("PUT", f"{tag}/release", body={"version": 1}).status == 200
    assert api.request("PUT", f"{tag}/release", body={"version": 1}).status == 200
    moved = api.request("PUT", f"{tag}/release", body={"version": 2})
    assert moved.status == 409 and moved.json()["version"] == 1
    assert api.request("PUT", f"{tag}/release", body={"version": 2, "move": True}).status == 200
    assert api.get(f"{MODELS}/m/resolve", ref="release").json()["number"] == 2
    # Checkpoint tags never move.
    stuck = api.request("PUT", f"{tag}/checkpoint-1000", body={"version": 2, "move": True})
    assert stuck.status == 409 and stuck.json()["version"] == 1
    for bad in ("12", "primary", "-x"):
        assert api.request("PUT", f"{tag}/{bad}", body={"version": 1}).status == 422
    assert api.request("PUT", f"{tag}/x", body={"version": 9}).status == 404
    assert api.delete(f"{tag}/release").status == 204
    assert api.delete(f"{tag}/release").status == 404
    assert api.get(f"{MODELS}/m/resolve", ref="release").status == 404
    assert api.get(f"{MODELS}/m/resolve", ref="checkpoint-2000").json()["tags"] == [
        "checkpoint-2000",
        "latest",
    ]


def test_the_primary_version(flock: S3Flock, pusher: Pusher):
    api = flock.client()
    first = pusher.push("m")
    assert first["primary"] is True  # the first version
    second = pusher.push("m")
    assert second["primary"] is False
    third = pusher.push("m", primary=True)
    assert third["primary"] is True
    resolved = api.get(f"{MODELS}/m/resolve").json()
    assert resolved["number"] == 3 and resolved["files"]
    assert api.get(f"{MODELS}/m/resolve", ref="primary").json()["number"] == 3
    assert api.get(f"{MODELS}/m/resolve", ref="1").json()["number"] == 1

    alice = flock.add_user("alice@lab.test")
    made = alice.api.request("PUT", f"{MODELS}/m/primary", body={"version": 2})
    assert made.status == 200 and made.json()["previous"] == 3
    info = api.get(f"{MODELS}/m").json()
    assert info["primary"] == 2 and info["primary_set_by"]["email"] == "alice@lab.test"
    assert api.get(f"{MODELS}/m/resolve").json()["number"] == 2
    assert api.request("PUT", f"{MODELS}/m/primary", body={"version": 9}).status == 404

    refused = api.delete(f"{MODELS}/m/versions/2")
    assert refused.status == 409 and "primary" in refused.json()["error"]
    assert api.get(f"{MODELS}/nope/resolve").status == 404


def test_versions_page_and_checkpoint_filter(
    flock: S3Flock, pusher: Pusher, monkeypatch: pytest.MonkeyPatch
):
    for step in (1, 2, 3):
        pusher.push("m", tags=[f"checkpoint-{step}"] if step != 2 else [])
    monkeypatch.setattr(models(flock), "VERSION_PAGE", 2)
    api = flock.client()
    page = api.get(f"{MODELS}/m/versions").json()
    assert [v["number"] for v in page["versions"]] == [3, 2] and page["cursor"] == 2
    rest = api.get(f"{MODELS}/m/versions", before=2).json()
    assert [v["number"] for v in rest["versions"]] == [1] and rest["cursor"] is None
    checkpoints = api.get(f"{MODELS}/m/versions", checkpoints="true").json()
    assert [v["number"] for v in checkpoints["versions"]] == [3, 1]
    assert page["versions"][0]["created_by"]["email"] == flock.ADMIN_EMAIL


# ---------------------------------------------------------------------- deleting (D109)


def test_deleting_and_restoring_versions(flock: S3Flock, pusher: Pusher, wait_for):
    api = flock.client()
    pusher.push("m", tags=["checkpoint-1"])
    second = pusher.push("m", tags=["checkpoint-2", "release"])
    pusher.push("m", primary=True)
    refused = api.delete(f"{MODELS}/m/versions/2")
    assert refused.status == 409 and "release" in refused.json()["error"]
    assert api.delete(f"{MODELS}/m/tags/release").status == 204

    alice = flock.add_user("alice@lab.test")
    deleted = alice.api.delete(f"{MODELS}/m/versions/2")
    assert deleted.status == 200, deleted.body
    entry = deleted.json()
    assert entry["number"] == 2 and entry["tags"] == ["checkpoint-2"]
    assert entry["deleted_by"]["email"] == "alice@lab.test"
    assert api.get(f"{MODELS}/m/resolve", ref="2").status == 404
    assert api.get(f"{MODELS}/m/resolve", ref="checkpoint-2").status == 404
    # Its files move to the trash, in the background.
    org = flock.call(flock.store.org("flock")).pk
    trash = flock.store.datasets.trash
    bucket = upstream_name(flock)
    wait_for(lambda: keys(flock, bucket, second["prefix"]) == [])
    moved = keys(flock, trash, f"{org:05d}/models/{entry['id']}/")
    assert len(moved) == len(single()) + 1  # with its manifest
    # Members don't see the trash.
    assert alice.api.get(f"{MODELS}/-/trash").status == 403
    listed = api.get(f"{MODELS}/-/trash").json()["entries"]
    assert [(e["kind"], e["number"], e["moved"]) for e in listed] == [("version", 2, True)]

    # Saving checkpoint-2 again is possible meanwhile; the restore then leaves it be.
    pusher.push("m", tags=["checkpoint-2"])
    restored = api.post(f"{MODELS}/-/trash/versions/{entry['id']}/restore", None)
    assert restored.status == 200, restored.body
    assert restored.json()["number"] == 2 and restored.json()["tags"] == []
    trashed = f"{org:05d}/models/{entry['id']}/"
    back = sorted(second["prefix"] + key.removeprefix(trashed) for key in moved)
    assert keys(flock, bucket, second["prefix"]) == back
    assert keys(flock, trash, f"{org:05d}/models/{entry['id']}/") == []
    assert api.get(f"{MODELS}/m/resolve", ref="2").json()["digest"] == second["digest"]
    # Numbers aren't reused after a version is gone for good.
    entry = api.delete(f"{MODELS}/m/versions/1").json()
    wait_for(lambda: api.get(f"{MODELS}/-/trash").json()["entries"][0]["moved"])
    assert api.delete(f"{MODELS}/-/trash/versions/{entry['id']}").status == 204
    assert keys(flock, trash, f"{org:05d}/models/{entry['id']}/") == []
    assert pusher.push("m")["number"] == 5


def test_deleting_and_restoring_models(flock: S3Flock, pusher: Pusher, wait_for):
    api = flock.client()
    first = pusher.push("m", tags=["release"])
    pusher.push("m")
    lone = api.delete(f"{MODELS}/m/versions/2").json()
    wait_for(lambda: all(e["moved"] for e in api.get(f"{MODELS}/-/trash").json()["entries"]))
    open_upload = pusher.open("m", single()).json()
    deleted = api.delete(f"{MODELS}/m")
    assert deleted.status == 200
    assert deleted.json()["kind"] == "model" and deleted.json()["versions"] == 1
    assert api.get(f"{MODELS}/m").status == 404
    assert api.get(MODELS).json()["models"] == []
    assert api.get(f"{MODELS}/m/uploads/{open_upload['ulid']}").json()["state"] == "aborted"
    wait_for(lambda: all(e["moved"] for e in api.get(f"{MODELS}/-/trash").json()["entries"]))
    entries = api.get(f"{MODELS}/-/trash").json()["entries"]
    assert [e["kind"] for e in entries] == ["model"]  # the lone version is listed with it

    # A new model with the name blocks the restore, until it's deleted too.
    pusher.push("m")
    taken = api.post(f"{MODELS}/-/trash/models/{entries[0]['id']}/restore", None)
    assert taken.status == 409 and taken.json()["taken"] is True
    newer = api.delete(f"{MODELS}/m").json()
    restored = api.post(f"{MODELS}/-/trash/models/{entries[0]['id']}/restore", None)
    assert restored.status == 200, restored.body
    info = restored.json()
    assert info["primary"] == 1 and info["versions"] == 1
    assert [t["name"] for t in info["tags"]] == ["release"]
    bucket = upstream_name(flock)
    assert keys(flock, bucket, first["prefix"]) != []
    # The version deleted on its own before stays deleted.
    assert api.get(f"{MODELS}/m/resolve", ref="2").status == 404
    listed = api.get(f"{MODELS}/-/trash").json()["entries"]
    assert sorted((e["kind"], e["id"]) for e in listed) == [
        ("model", newer["id"]),
        ("version", lone["id"]),
    ]
    wait_for(lambda: all(e["moved"] for e in api.get(f"{MODELS}/-/trash").json()["entries"]))
    assert api.delete(f"{MODELS}/-/trash/models/{newer['id']}").status == 204
    assert sql(flock, "SELECT count(*) FROM models")[0][0] == 1


def test_sweep_empties_the_trash_after_14_days(
    flock: S3Flock, pusher: Pusher, wait_for, monkeypatch: pytest.MonkeyPatch
):
    api = flock.client()
    pusher.push("m")
    pusher.push("m")
    pusher.push("gone")
    version = api.delete(f"{MODELS}/m/versions/2").json()
    model = api.delete(f"{MODELS}/gone").json()
    wait_for(lambda: all(e["moved"] for e in api.get(f"{MODELS}/-/trash").json()["entries"]))
    sql(flock, "UPDATE model_versions SET deleted_at = deleted_at - interval '15 days'")
    sql(
        flock,
        "UPDATE models SET deleted_at = deleted_at - interval '15 days' WHERE pk = $1",
        model["id"],
    )
    org = flock.call(flock.store.org("flock")).pk
    trash = flock.store.datasets.trash
    # A trash prefix with no deleted version, and the dataset trash's sweep leaving models/ be.
    flock.s3.client().put_object(Bucket=trash, Key=f"{org:05d}/models/99999/x", Body=b"x")
    monkeypatch.setattr(flock.store.datasets, "ORPHAN_AFTER", 0)
    monkeypatch.setattr(models(flock), "STRAY_AFTER", 0)
    assert flock.call(flock.store.datasets.sweep())["orphans"] == 0
    done = flock.call(models(flock).sweep())
    assert done["purged"] == 2 and done["orphans"] == 1
    assert keys(flock, trash, f"{org:05d}/models/") == []
    assert api.get(f"{MODELS}/-/trash").json()["entries"] == []
    assert sql(flock, "SELECT pk FROM model_versions WHERE pk = $1", version["id"]) == []
    assert [m["name"] for m in api.get(MODELS).json()["models"]] == ["m"]


def test_retention(flock: S3Flock, pusher: Pusher, wait_for):
    api = flock.client()
    for step in range(1, 7):
        pusher.push("m", tags=[f"checkpoint-{step}"])
    # 1 is the primary, 2 has an eval run, 3 a release tag; 4 to 6 are untagged checkpoints.
    api.request("PUT", f"{MODELS}/m/tags/release", body={"version": 3})
    api.create_run("evals", "e1")
    (run,) = sql(flock, "SELECT pk FROM runs WHERE name = 'e1'")
    sql(
        flock,
        "INSERT INTO model_version_runs (version_pk, run_pk, kind) SELECT v.pk, $1, 'eval' "
        "FROM model_versions v WHERE v.number = 2",
        run["pk"],
    )
    assert flock.call(models(flock).sweep())["retained"] == 0  # off by default

    alice = flock.add_user("alice@lab.test")
    assert alice.api.patch("/api/v1/orgs/flock", {"model_retention": 1}).status == 403
    assert api.patch("/api/v1/orgs/flock", {"model_retention": 0}).status == 422
    set_ = api.patch("/api/v1/orgs/flock", {"model_retention": 1})
    assert set_.status == 200 and set_.json()["model_retention"] == 1
    done = flock.call(models(flock).sweep())
    # Untagged, newest first: 6 (kept), then 5, 4 and 2. 2 has an eval run.
    assert done["retained"] == 2 and done["moved"] == 2
    left = [v["number"] for v in api.get(f"{MODELS}/m/versions").json()["versions"]]
    assert left == [6, 3, 2, 1]
    entries = api.get(f"{MODELS}/-/trash").json()["entries"]
    assert sorted(e["number"] for e in entries) == [4, 5]
    assert all(e["deleted_by"] is None for e in entries)
    assert (
        api.patch("/api/v1/orgs/flock", {"model_retention": None}).json()["model_retention"] is None
    )


def test_the_dashboard_cant_write_files_into_flock_models(flock: S3Flock, pusher: Pusher):
    """The Storage tab's own writes go upstream with the global key, so the API refuses them in
    flock-models as the proxy refuses other clients' (D97)."""
    version = pusher.push("m")
    api = flock.client()
    path = "/api/v1/orgs/flock/buckets/flock-models"
    deleted = api.post(f"{path}/objects/delete", {"prefixes": [version["prefix"]]})
    assert deleted.status == 403, deleted.body
    assert "Models tab" in deleted.json()["error"]
    assert (
        api.post(f"{path}/objects/delete", {"keys": [version["prefix"] + "config.json"]}).status
        == 403
    )
    assert api.post(f"{path}/folders", {"prefix": "m/u/new/"}).status == 403
    assert keys(flock, upstream_name(flock), version["prefix"]) != []
    # Reading it is still fine.
    assert api.get(f"{path}/objects", prefix=version["prefix"]).status == 200


def test_deleted_eval_runs_dont_count(flock: S3Flock, pusher: Pusher):
    """``eval_runs`` counts the runs ``evals`` lists, so a deleted run counts nowhere, and
    retention no longer keeps a version for it."""
    api = flock.client()
    for step in (1, 2, 3):
        pusher.push("m", tags=[f"checkpoint-{step}"])
    api.create_run("evals", "e1", config={"model": "flock:flock/m@2"})
    api.create_run("evals", "e2", config={"model": "flock:flock/m@2"})
    version = api.get(f"{MODELS}/m/versions/2").json()
    assert version["eval_runs"] == 2 and len(version["evals"]) == 2

    assert api.delete("/api/v1/projects/flock/evals/runs/e1", force="true").status == 204
    version = api.get(f"{MODELS}/m/versions/2").json()
    assert version["evals"] == ["flock/evals/e2"] and version["eval_runs"] == 1
    listed = api.get(f"{MODELS}/m/versions").json()["versions"]
    assert [v["eval_runs"] for v in listed] == [0, 1, 0]
    # With e2 alive, retention keeps version 2 (1 is the primary, 3 the newest untagged).
    assert api.patch("/api/v1/orgs/flock", {"model_retention": 1}).status == 200
    assert flock.call(models(flock).sweep())["retained"] == 0

    assert api.delete("/api/v1/projects/flock/evals/runs/e2", force="true").status == 204
    assert api.get(f"{MODELS}/m/versions/2").json()["eval_runs"] == 0
    assert flock.call(models(flock).sweep())["retained"] == 1
    (entry,) = api.get(f"{MODELS}/-/trash").json()["entries"]
    assert entry["number"] == 2 and entry["eval_runs"] == 0


def test_a_non_member_gets_404(flock: S3Flock, pusher: Pusher):
    pusher.push("m")
    api = flock.client()
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    olga = flock.add_user("olga@lab.test", org="lab")
    for path in (f"{MODELS}", f"{MODELS}/m", f"{MODELS}/m/resolve", f"{MODELS}/-/trash"):
        assert olga.api.get(path).status == 404
    # Her own org's models are her own.
    assert olga.api.get("/api/v1/orgs/lab/models").json()["models"] == []


def test_deleting_an_org_removes_its_models(flock: S3Flock):
    api = flock.client()
    assert api.post("/api/v1/orgs", {"name": "lab"}).status == 201
    declared = [{"path": p, "size": len(d)} for p, d in single().items()]
    opened = api.post("/api/v1/orgs/lab/models/m/uploads", {"files": declared})
    assert opened.status == 201
    assert api.delete("/api/v1/orgs/lab").status == 204
    assert sql(flock, "SELECT count(*) FROM models")[0][0] == 0


def test_large_files_keep_their_metadata_through_the_trash(
    flock: S3Flock, pusher: Pusher, wait_for, monkeypatch: pytest.MonkeyPatch
):
    """Files over the copy limit are copied in parts, which carry no metadata of their own."""
    monkeypatch.setattr(models(flock), "COPY_LIMIT", 16)
    api = flock.client()
    files = single()
    pusher.push("m")
    version = pusher.push("m")
    entry = api.delete(f"{MODELS}/m/versions/2").json()
    wait_for(lambda: api.get(f"{MODELS}/-/trash").json()["entries"][0]["moved"])
    assert api.post(f"{MODELS}/-/trash/versions/{entry['id']}/restore", None).status == 200
    client = flock.s3.client()
    for path, data in files.items():
        key = version["prefix"] + path
        head = client.head_object(Bucket=upstream_name(flock), Key=key)
        assert head["Metadata"]["sha256"] == sha(data)
        assert client.get_object(Bucket=upstream_name(flock), Key=key)["Body"].read() == data


def test_multipart_uploads_through_the_proxy_keep_part_md5s_and_metadata(
    flock: S3Flock, pusher: Pusher
):
    """What resuming relies on (D102): ListParts gives each part's MD5 as its ETag, and the
    completed object keeps the x-amz-meta-sha256 set when it was created."""
    big = [b"a" * (5 * MIB), b"b" * 10]
    files = {**single(), "training_state/optimizer.bin": b"".join(big)}
    upload = pusher.open("m", files).json()
    pusher.put(upload, single())
    key = upload["prefix"] + "training_state/optimizer.bin"
    s3 = pusher.s3
    created = s3.create_multipart_upload(
        Bucket="flock-models",
        Key=key,
        Metadata={"sha256": sha(files["training_state/optimizer.bin"])},
    )
    for number, data in enumerate(big, 1):
        md5 = hashlib.md5(data)
        s3.upload_part(
            Bucket="flock-models",
            Key=key,
            UploadId=created["UploadId"],
            PartNumber=number,
            Body=data,
            ContentMD5=base64.b64encode(md5.digest()).decode(),
        )
    if flock.s3.real:  # moto doesn't check Content-MD5 on parts
        with pytest.raises(ClientError) as raised:
            s3.upload_part(
                Bucket="flock-models",
                Key=key,
                UploadId=created["UploadId"],
                PartNumber=3,
                Body=b"c",
                ContentMD5=base64.b64encode(hashlib.md5(b"d").digest()).decode(),
            )
        assert code(raised) == "BadDigest"
    parts = s3.list_parts(Bucket="flock-models", Key=key, UploadId=created["UploadId"])["Parts"]
    assert [p["ETag"].strip('"') for p in parts] == [hashlib.md5(d).hexdigest() for d in big]
    s3.complete_multipart_upload(
        Bucket="flock-models",
        Key=key,
        UploadId=created["UploadId"],
        MultipartUpload={
            "Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]
        },
    )
    head = s3.head_object(Bucket="flock-models", Key=key)
    assert head["Metadata"]["sha256"] == sha(files["training_state/optimizer.bin"])
    committed = pusher.commit("m", upload["ulid"], files)
    assert committed.status == 200, committed.body
