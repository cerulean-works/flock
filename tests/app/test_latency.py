import asyncio
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Self

import orjson
import pytest

pytestmark = pytest.mark.latency


@dataclass
class Timing:
    """Latency percentiles (ms) of repeated requests, and the last response."""

    p50: float
    p95: float
    max: float
    data: Any = None

    @classmethod
    def of(cls, times: list[float], data: Any = None) -> Self:
        times = sorted(times)
        p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
        return cls(times[len(times) // 2], p95, times[-1], data)

    @classmethod
    def measure(cls, client, method: str, path: str, body=None, params=None, n: int = 40) -> Self:
        """Time ``n`` requests end to end (after warmup), including decoding the response."""
        times, data, size = [], None, 0
        for i in range(5 + n):
            start = time.perf_counter()
            resp = client.request(method, path, params=params, body=body)
            data = orjson.loads(resp.body)
            elapsed = (time.perf_counter() - start) * 1000
            assert resp.status == 200, data
            if i >= 5:
                times.append(elapsed)
            size = len(resp.body)
        timing = cls.of(times, data)
        print(
            f"\n{method} {path} {params or body or ''}"[:160]
            + f"\n  p50={timing.p50:.2f}ms p95={timing.p95:.2f}ms max={timing.max:.2f}ms"
            f" response={size / 1000:.0f}KB"
        )
        return timing

    def check(self, p50: float, p95: float) -> None:
        assert self.p50 < p50, f"p50 {self.p50:.2f}ms exceeds {p50}ms"
        assert self.p95 < p95, f"p95 {self.p95:.2f}ms exceeds {p95}ms"


class TestCurves:
    """Loss curves of ongoing runs, or of the newest 50 runs logged for 5k steps every 10."""

    P50_MS = 10.0
    P95_MS = 20.0
    RUNS = 200
    RUNNING = 8
    STEPS = 5_000
    LOG_EVERY = 10
    EXTRA_METRICS = 16
    PATH = "/api/v1/projects/flock/curves/history"

    @pytest.fixture(scope="class")
    @classmethod
    def curves(cls, make_module_flock):
        server = make_module_flock()
        rng = random.Random(0)
        rows = cls.STEPS // cls.LOG_EVERY

        async def populate() -> None:
            store = server.store
            for r in range(cls.RUNS):
                name = f"run{r:04d}"
                await store.upsert_run("flock", "curves", name, {"config": {"seed": {"value": r}}})
                lines = []
                for row in range(rows):
                    step = row * cls.LOG_EVERY
                    loss = 4.0 * (1 + step) ** -0.3 + rng.random() * 0.05
                    data = {
                        "_step": step,
                        "_runtime": step * 0.37,
                        "_timestamp": 1.79e9 + step * 0.37,
                        "loss": loss,
                        "val_loss": loss * 1.05,
                        "lr": 3e-4 * min(1.0, step / 500),
                        "grad_norm": rng.random() * 2,
                        "tokens_per_s": 50_000 + rng.random() * 1000,
                        "acc": 1 - loss / 4,
                        "ppl": 2.718**loss,
                        "mfu": 0.4 + rng.random() * 0.01,
                        **{f"layer_{k}/grad": rng.random() for k in range(cls.EXTRA_METRICS)},
                    }
                    lines.append(orjson.dumps(data).decode())
                for offset in range(0, rows, 50):  # wandb flushes history in small chunks
                    chunk = lines[offset : offset + 50]
                    files = {
                        "wandb-history.jsonl": {"offset": offset, "content": chunk},
                        "wandb-summary.json": {"offset": 0, "content": [chunk[-1]]},
                    }
                    await store.file_stream("flock", "curves", name, {"files": files})
                if r < cls.RUNS - cls.RUNNING:
                    done = {"files": {}, "complete": True, "exitcode": 0}
                    await store.file_stream("flock", "curves", name, done)
            await store.flush()

        server.call(populate())
        client = server.client()
        yield server, client
        client.close()

    def test_recent_50_runs_loss_curves(self, curves):
        _, client = curves
        timing = Timing.measure(client, "GET", self.PATH, params={"keys": "loss"})
        runs = timing.data["runs"]
        assert len(runs) == 50
        assert all(len(r["series"]["loss"]["x"]) == self.STEPS // self.LOG_EVERY for r in runs)
        assert runs[0]["series"]["loss"]["x"][-1] == self.STEPS - self.LOG_EVERY
        timing.check(self.P50_MS, self.P95_MS)

    def test_recent_50_runs_loss_curves_cold(self, curves):
        """Curves aren't cached in Flock (D78): every read, like this one, decodes the chunks
        PostgreSQL returns. Only PostgreSQL's own buffer cache is warm."""
        _, client = curves
        times = []
        for _ in range(25):
            start = time.perf_counter()
            runs = client.get(self.PATH, keys="loss").json()["runs"]
            times.append((time.perf_counter() - start) * 1000)
            assert len(runs) == 50 and all(len(r["series"]["loss"]["y"]) == 500 for r in runs)
        timing = Timing.of(times)
        print(f"\ncold recent-50 curves: p50={timing.p50:.2f}ms p95={timing.p95:.2f}ms")
        timing.check(self.P50_MS, self.P95_MS)

    def test_recent_50_runs_several_metrics(self, curves):
        _, client = curves
        timing = Timing.measure(client, "GET", self.PATH, params={"keys": "loss,val_loss,lr"})
        assert all(set(r["series"]) == {"loss", "val_loss", "lr"} for r in timing.data["runs"])
        timing.check(self.P50_MS, self.P95_MS)

    def test_ongoing_runs_curves_while_runs_are_logging(self, curves):
        """Read curves of running runs while those runs keep streaming history over HTTP."""
        server, client = curves
        project = server.store.project("flock", "curves")
        running = sorted(project.active)
        assert len(running) == self.RUNNING
        stop = threading.Event()
        sent = {"batches": 0}

        def writer():
            rng = random.Random(1)
            writer_client = server.client()
            offsets = {name: project.by_name[name].history_line_count for name in running}
            while not stop.is_set():
                for name in running:
                    offset = offsets[name]
                    lines = []
                    for row in range(offset, offset + 5):
                        step = row * self.LOG_EVERY
                        loss = 4.0 * (1 + step) ** -0.3 + rng.random() * 0.05
                        data = {"_step": step, "loss": loss, "val_loss": loss * 1.05}
                        lines.append(orjson.dumps(data).decode())
                    files = {
                        "wandb-history.jsonl": {"offset": offset, "content": lines},
                        "wandb-summary.json": {"offset": 0, "content": [lines[-1]]},
                    }
                    resp = writer_client.file_stream(
                        "flock", "curves", name, {"files": files}, gzipped=True
                    )
                    assert resp.status == 200
                    offsets[name] = offset + len(lines)
                    sent["batches"] += 1
                time.sleep(0.005)
            writer_client.close()

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            time.sleep(0.2)
            params = {"keys": "loss,val_loss", "running": "1"}
            ongoing = Timing.measure(client, "GET", self.PATH, params=params)
            recent = Timing.measure(client, "GET", self.PATH, params={"keys": "loss"})
        finally:
            stop.set()
            thread.join()
        print(f"  concurrent history batches written: {sent['batches']}")
        assert sent["batches"] > 0
        runs = ongoing.data["runs"]
        assert {r["id"] for r in runs} == set(running)
        assert all(r["state"] == "running" for r in runs)
        assert all(len(r["series"]["loss"]["x"]) > self.STEPS // self.LOG_EVERY for r in runs)
        ongoing.check(self.P50_MS, self.P95_MS)
        recent.check(self.P50_MS, self.P95_MS)


class TestSweep:
    """Run-table filters over a project of 10k runs with 1.5k columns."""

    P50_MS = 20.0
    P95_MS = 50.0
    RUNS = 10_000
    CONFIG_COLUMNS = 100
    SUMMARY_COLUMNS = 1_400
    PATH = "/api/v1/projects/flock/sweep/runs/query"

    @pytest.fixture(scope="class")
    @classmethod
    def sweep(cls, make_module_flock):
        server = make_module_flock()
        rng = random.Random(0)
        specs = []
        for _ in range(cls.RUNS):
            config = {}
            for k in range(cls.CONFIG_COLUMNS):
                if k % 10 == 0:  # strings, ints and floats, like real configs
                    value: Any = rng.choice(("adam", "adamw", "sgd", "lion", "muon"))
                elif k % 10 == 1:
                    value = rng.choice([16, 32, 64, 128, 256])
                else:
                    value = rng.random()
                config[f"param_{k:03d}"] = {"value": value}
            summary: dict[str, Any] = {
                f"metric_{k:04d}": rng.random() for k in range(cls.SUMMARY_COLUMNS)
            }
            summary["loss"] = rng.random()
            summary["status"] = rng.choice(["ok", "diverged", "nan"])
            tags = rng.sample(["baseline", "ablation", "long", "short", "v2", "debug"], 2)
            specs.append((config, summary, tags))

        async def populate() -> None:
            store = server.store

            async def one(r: int) -> None:
                config, summary, tags = specs[r]
                name = f"run{r:05d}"
                await store.upsert_run("flock", "sweep", name, {"config": config, "tags": tags})
                files = {"wandb-summary.json": {"offset": 0, "content": [orjson.dumps(summary)]}}
                payload: dict[str, Any] = {"files": files}
                if r % 7:  # every 7th run is still running; every 11th of the rest failed
                    payload.update(complete=True, exitcode=0 if r % 11 else 1)
                await store.file_stream("flock", "sweep", name, payload)

            for start in range(0, cls.RUNS, 250):
                await asyncio.gather(*(one(r) for r in range(start, min(cls.RUNS, start + 250))))

        started = time.perf_counter()
        server.call(populate())
        print(f"\ningested {cls.RUNS} runs in {time.perf_counter() - started:.1f}s")
        table = server.store.project("flock", "sweep").table
        assert table.n == cls.RUNS
        assert len(table.columns) >= cls.CONFIG_COLUMNS + cls.SUMMARY_COLUMNS
        client = server.client()
        yield client, specs
        client.close()

    def test_filter_single_metric(self, sweep):
        client, specs = sweep
        body = {"filter": {"summary.loss": {"$lt": 0.1}}}
        timing = Timing.measure(client, "POST", self.PATH, body=body)
        assert timing.data["total"] == sum(1 for _, summary, _ in specs if summary["loss"] < 0.1)
        timing.check(self.P50_MS, self.P95_MS)

    def test_filter_compound_sorted(self, sweep):
        client, specs = sweep
        body = {
            "filter": {
                "$and": [
                    {"config.param_002": {"$gte": 0.25, "$lt": 0.75}},
                    {"config.param_000": {"$in": ["adam", "adamw"]}},
                    {"config.param_001": {"$gte": 64}},
                    {"summary.metric_0420": {"$gt": 0.5}},
                    {"tags": "baseline"},
                    {"state": {"$ne": "failed"}},
                ]
            },
            "sort": "-summary.loss",
            "columns": ["display_name", "state", "summary.loss", "config.param_00*", "tags"],
            "limit": 100,
        }
        timing = Timing.measure(client, "POST", self.PATH, body=body)
        matching = [
            (summary["loss"], f"run{r:05d}")
            for r, (config, summary, tags) in enumerate(specs)
            if 0.25 <= config["param_002"]["value"] < 0.75
            and config["param_000"]["value"] in ("adam", "adamw")
            and config["param_001"]["value"] >= 64
            and summary["metric_0420"] > 0.5
            and "baseline" in tags
            and not (r % 7 and r % 11 == 0)  # failed runs
        ]
        matching.sort(reverse=True)
        data = timing.data
        assert data["total"] == len(matching) > 0
        assert data["ids"] == [name for _, name in matching[:100]]
        assert data["data"]["summary.loss"] == [loss for loss, _ in matching[:100]]
        assert len(data["columns"]) == 3 + 10 + 1
        timing.check(self.P50_MS, self.P95_MS)

    def test_filter_or_regex_and_strings(self, sweep):
        client, _ = sweep
        body = {
            "filter": {
                "$or": [
                    {"config.param_010": {"$regex": "^adam"}},
                    {"summary.status": {"$in": ["diverged", "nan"]}, "summary.loss": {"$gt": 0.9}},
                ],
                "state": "finished",
            },
            "sort": "config.param_010",
            "columns": ["config.param_010", "summary.status", "summary.loss"],
        }
        timing = Timing.measure(client, "POST", self.PATH, body=body)
        assert timing.data["total"] > 0
        timing.check(self.P50_MS, self.P95_MS)

    def test_filter_returning_all_matching_ids(self, sweep):
        client, _ = sweep
        body = {"filter": {"summary.metric_0007": {"$gt": 0.2}}, "all_ids": True, "limit": 50}
        timing = Timing.measure(client, "POST", self.PATH, body=body)
        assert len(timing.data["all_ids"]) == timing.data["total"] > 7000
        timing.check(self.P50_MS, self.P95_MS)

    def test_wide_column_projection(self, sweep):
        client, _ = sweep
        body = {"columns": ["summary.metric_01*"], "sort": "summary.metric_0100", "limit": 100}
        timing = Timing.measure(client, "POST", self.PATH, body=body)
        assert len(timing.data["columns"]) == 100
        timing.check(self.P50_MS, self.P95_MS)

    def test_column_search(self, sweep):
        client, _ = sweep
        path = "/api/v1/projects/flock/sweep/columns"
        timing = Timing.measure(client, "GET", path, params={"q": "metric_13"})
        assert len(timing.data["columns"]) == 100
        timing.check(self.P50_MS, self.P95_MS)

    def test_home_feed(self, sweep):
        client, _ = sweep
        first = Timing.measure(client, "GET", "/api/v1/feed", params={"limit": 30})
        assert len(first.data["items"]) == 30
        first.check(self.P50_MS, self.P95_MS)
        params = {"limit": 30, "kinds": "run_failed", "cursor": first.data["next"]}
        deeper = Timing.measure(client, "GET", "/api/v1/feed", params=params)
        assert {item["kind"] for item in deeper.data["items"]} == {"run_failed"}
        deeper.check(self.P50_MS, self.P95_MS)

    def test_budget_panel(self, sweep):
        """Every run of the project has a usage entry this month."""
        client, _ = sweep
        timing = Timing.measure(client, "GET", "/api/v1/orgs/flock/budget")
        assert timing.data["unknown_gpu"]["runs"] == self.RUNS
        timing.check(self.P50_MS, self.P95_MS)

    # Mutations come last: they change the shared dataset.

    def test_bulk_delete_and_move_then_filter(self, sweep):
        """Deleting ~1k runs and moving 500 rebuild the 10k × 1.5k table; filters stay fast."""
        client, specs = sweep
        base = "/api/v1/projects/flock/sweep/runs"
        finished = [r for r in range(self.RUNS) if r % 7]  # every 7th run is still running
        doomed = [f"run{r:05d}" for r in finished if r % 9 == 0]
        moved = [f"run{r:05d}" for r in finished if r % 9 == 1][:500]

        start = time.perf_counter()
        resp = client.post(f"{base}/delete", {"runs": doomed})
        delete_ms = (time.perf_counter() - start) * 1000
        assert resp.status == 200, resp.body
        assert resp.json()["count"] == len(doomed) > 900

        start = time.perf_counter()
        resp = client.post(f"{base}/move", {"runs": moved, "to": "archive", "create": True})
        move_ms = (time.perf_counter() - start) * 1000
        assert resp.status == 200, resp.body
        print(f"\ndelete {len(doomed)} runs: {delete_ms:.0f}ms, move {len(moved)}: {move_ms:.0f}ms")
        assert delete_ms < 200
        assert move_ms < 200

        gone = {int(name[3:]) for name in doomed + moved}
        body = {"filter": {"summary.loss": {"$lt": 0.1}}}
        timing = Timing.measure(client, "POST", self.PATH, body=body)
        expected = sum(
            1 for r, (_, summary, _) in enumerate(specs) if summary["loss"] < 0.1 and r not in gone
        )
        assert timing.data["total"] == expected
        timing.check(self.P50_MS, self.P95_MS)
        archive = client.post("/api/v1/projects/flock/archive/runs/query", body).json()
        assert archive["total"] == sum(1 for name in moved if specs[int(name[3:])][1]["loss"] < 0.1)
