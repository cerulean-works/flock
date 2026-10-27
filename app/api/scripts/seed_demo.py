"""Fill a Flock database with a month of intense use by a mid-size ML lab.

Everything is written straight into PostgreSQL with timestamps spread over the past 30 days,
so the feed, the budget panel, run tables and curves look like a real month as soon as the
Flock loads it. Writing through Flock's API instead would stamp everything "now".

What it creates (at ``--scale 1``):
- 3 organizations with GPU prices (one GPU type left unpriced) and monthly budgets for this
  month and the last, set around what they spend, so one or two end up over budget;
- ~40 users (a super admin with a wandb key, org admins, members, people who joined
  mid-month), most with an identicon avatar, plus a few pending invitations;
- ~20 projects of five kinds: long multi-node pretraining runs (preempted and resumed), large
  hyperparameter sweeps run by parallel agents, fine-tunes, evaluation jobs and scratch runs;
- ~6k runs, mostly finished, some failed or crashed, a few deleted (their spend stays) and one
  deleted project, with configs, summaries, ~25M history points, system metrics per GPU and
  console logs, compacted the way Flock stores finished runs;
- the GPU usage ledger, split by month, with multi-node runs' GPU counts corrected by hand.

Activity follows working hours and weekdays. Flock must not be running while this
writes (it is the database's only writer, D2): stop it, run this, start it.

    cd app/api
    uv run --package flock-app-api python -m app.api.scripts.seed_demo --reset \\
        --database-url postgresql://flock:flock@127.0.0.1:5432/flock \\
        --files-dir flock-files

``--files-dir`` (Flock's ``FLOCK_FILES_DIR``) also writes each run's
``wandb-metadata.json``. ``--reset`` empties the database first; without it, the database
must hold no accounts, organizations or runs.
"""

import argparse
import asyncio
import datetime as dt
import hashlib
import math
import os
import random
import secrets
import shutil
import string
import struct
import sys
import time
import zlib
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import asyncpg
import numpy as np
import orjson
from argon2 import PasswordHasher

from app.api.src.core import gpus
from app.api.src.core.names import ADJECTIVES, NOUNS
from app.api.src.db.migrate import Migrations
from app.api.src.settings import Settings

DAY = 86400.0
HOUR = 3600.0
DAYS = 30
PASSWORD = "flock-demo-password"  # every simulated account's password
ADMIN_EMAIL = "admin@example.com"

# Device names as the wandb SDK reports them (Flock normalizes them to the key).
DEVICES = {
    "H200": ("NVIDIA H200", "150754820096", "Hopper", 700),
    "H100": ("NVIDIA H100 80GB HBM3", "85520809984", "Hopper", 700),
    "A100": ("NVIDIA A100-SXM4-80GB", "85899345920", "Ampere", 400),
    "L40S": ("NVIDIA L40S", "48305799168", "Ada Lovelace", 350),
    "L4": ("NVIDIA L4", "24152899584", "Ada Lovelace", 72),
}

FIRST_NAMES = [
    "Ana", "Ben", "Chiara", "Dmitri", "Esi", "Farhan", "Grace", "Hiro", "Ines", "Jonas",
    "Kemi", "Liang", "Maya", "Nikhil", "Olga", "Pedro", "Qing", "Rosa", "Sami", "Tomás",
    "Uma", "Viktor", "Wen", "Ximena", "Yusuf", "Zara", "Aiko", "Bruno", "Camille", "Diego",
    "Elif", "Felix", "Gita", "Hassan", "Ingrid", "Jae", "Kofi", "Lena", "Mateo", "Noor",
]  # fmt: skip
LAST_NAMES = [
    "Okafor", "Lindqvist", "Rossi", "Ivanov", "Mensah", "Rahman", "Kim", "Tanaka", "García",
    "Weber", "Adeyemi", "Chen", "Patel", "Iyer", "Novak", "Silva", "Zhang", "Moreno", "Haddad",
    "Costa", "Nair", "Horvat", "Li", "Ruiz", "Demir", "Ahmed", "Sato", "Ferreira", "Dubois",
    "Herrera", "Yilmaz", "Becker", "Rao", "Karimi", "Berg", "Park", "Owusu", "Fischer",
    "Navarro", "Aziz",
]  # fmt: skip


# ---------------------------------------------------------------------- the plan


@dataclass(frozen=True)
class Kind:
    """A kind of project: how long its runs are, on which GPUs, and what they log."""

    hours: tuple[float, float]  # run length, log-uniform
    gpus: tuple[tuple[str | None, int], ...]  # (type, count per node) choices
    rows: tuple[int, int]  # history lines of a run that completes
    log_every: int  # training steps per history line
    job_type: str
    program: str
    fail: float  # chance a run fails (exit code 1) part way
    crash: float  # chance its client dies part way (no heartbeats after)
    preempt: float  # chance it's preempted and resumed (unbilled gaps)
    nodes: tuple[int, ...] = (1,)  # nodes per run: the SDK reports one node's GPUs
    sweeps: bool = False


KINDS = {
    "pretrain": Kind(
        (10, 90), (("H200", 8), ("H100", 8)), (1500, 3500), 50, "pretrain", "pretrain.py",
        0.04, 0.08, 0.6, nodes=(4, 8, 16),
    ),
    # Small proxy models for ablations: shorter, on one or two nodes.
    "proxy": Kind(
        (4, 24), (("H200", 8), ("H100", 8)), (800, 2000), 50, "pretrain", "pretrain.py",
        0.04, 0.06, 0.3, nodes=(1, 1, 2),
    ),
    "sweep": Kind(
        (0.3, 3.0), (("H100", 1), ("H100", 2), ("A100", 1), ("A100", 4), ("L40S", 1)),
        (300, 1500), 10, "sweep", "train.py", 0.04, 0.03, 0.05, sweeps=True,
    ),
    "finetune": Kind(
        (1.0, 14.0), (("H100", 8), ("A100", 8), ("H200", 8)), (600, 2500), 20, "finetune",
        "sft.py", 0.05, 0.05, 0.2,
    ),
    "eval": Kind(
        (0.1, 1.0), (("H100", 1), ("A100", 1), ("L40S", 1), ("L4", 1)), (20, 80), 1, "eval",
        "evaluate.py", 0.03, 0.01, 0.0,
    ),
    "debug": Kind(
        (0.02, 0.5), ((None, 0), ("A100", 1), ("H100", 1), ("L4", 1)), (5, 200), 1, "debug",
        "train.py", 0.25, 0.15, 0.0,
    ),
}  # fmt: skip

# What each org is called (its ID is the key of ORGS).
DISPLAY_NAMES = {"flock": "Flock Research", "vision": "Vision Lab", "speech": "Speech Team"}

# org -> (currency, GPU prices, members besides the super admin, [(project, kind, runs, about)])
ORGS: dict[str, tuple[str, dict[str, float], int, list[tuple[str, str, int, str | None]]]] = {
    "flock": (
        "USD",
        {"H200": 4.5, "H100": 3.2, "A100": 1.9, "L40S": 1.1, "L4": 0.8},
        25,
        [
            ("llm-pretrain-7b", "pretrain", 24, "7B decoder pretraining on the v3 data mix"),
            ("llm-pretrain-1b", "proxy", 60, "1B proxy models for data and recipe ablations"),
            ("lr-sweep", "sweep", 900, "Learning rate and schedule sweeps"),
            ("tokenizer-ablations", "sweep", 500, "Vocabulary size and tokenizer comparisons"),
            ("data-mixtures", "sweep", 700, "Data mixture ratio sweeps on 1B proxies"),
            ("sft-chat", "finetune", 220, "Supervised fine-tuning for the chat model"),
            ("rlhf-ppo", "finetune", 180, "PPO against the v2 reward model"),
            ("evals", "eval", 1100, "Benchmark runs for every checkpoint"),
            ("scratch", "debug", 400, None),
        ],
    ),
    "vision": (
        "USD",
        {"H200": 4.2, "H100": 2.9, "A100": 1.7, "L40S": 1.0, "L4": 0.7},
        12,
        [
            ("vit-pretrain", "pretrain", 30, "ViT-L pretraining"),
            ("augment-sweep", "sweep", 600, "Augmentation policy search"),
            ("detection-finetune", "finetune", 160, "Detection heads on the ViT-L backbone"),
            ("evals", "eval", 300, None),
            ("scratch", "debug", 150, None),
        ],
    ),
    "speech": (
        "EUR",
        {"H200": 4.4, "H100": 3.0, "A100": 1.8},  # L40S and L4 have no price: unpriced
        8,
        [
            ("asr-conformer", "finetune", 60, "Conformer ASR on the multilingual set"),
            ("tts-sweep", "sweep", 400, None),
            ("scratch", "debug", 120, None),
        ],
    ),
}
# A project deleted during the month: its runs are gone, their spend stays.
DELETED_PROJECT = ("flock", "old-baselines", "sweep", 80)
DELETED_RUNS = 0.04  # share of sweep and scratch runs deleted after they ran


@dataclass(eq=False)
class Person:
    name: str
    email: str
    created_at: float
    avatar: bytes | None = None
    avatar_source: str | None = None
    activity: float = 1.0  # how many runs they launch, relative to others
    superadmin: bool = False
    pk: int = 0


@dataclass(eq=False)
class OrgPlan:
    name: str
    display_name: str
    currency: str
    rates: dict[str, float]
    members: dict[Person, str] = field(default_factory=dict)  # -> role
    avatar: bytes | None = None
    budgets: list[tuple[dt.date, float]] = field(default_factory=list)
    pk: int = 0


@dataclass(eq=False)
class ProjectPlan:
    org: OrgPlan
    name: str
    kind: Kind
    description: str | None
    team: list[Person]
    created_at: float = 0.0
    deleted: bool = False
    pk: int = 0


@dataclass(eq=False)
class RunPlan:
    project: ProjectPlan
    name: str
    display_name: str
    owner: Person
    segments: list[tuple[float, float]]  # wall-clock spans the run was alive
    planned_rows: int
    rows: int
    state: str
    exitcode: int | None
    gpu_type: str | None
    gpu_count: int  # what the SDK reports: one node
    gpu_count_override: int | None
    config: dict[str, Any]
    tags: list[str]
    group: str | None = None
    sweep: str | None = None
    notes: str | None = None
    host: str = ""
    commit: str = ""
    seed: int = 0
    deleted: bool = False
    pk: int = 0

    @property
    def created_at(self) -> float:
        return self.segments[0][0]

    @property
    def heartbeat_at(self) -> float:
        return self.segments[-1][1]

    @property
    def runtime(self) -> float:
        return sum(end - start for start, end in self.segments)

    @property
    def gpus(self) -> int:
        return self.gpu_count_override if self.gpu_count_override is not None else self.gpu_count


def identicon(seed: str) -> bytes:
    """A 5x5 mirrored identicon as a 48x48 PNG (enough to tell avatars apart in a UI)."""
    digest = hashlib.sha256(seed.encode()).digest()
    color = bytes((digest[0] // 2 + 64, digest[1] // 2 + 64, digest[2] // 2 + 64))
    background = b"\xf0\xf0\xf0"
    cells = [[digest[3 + row * 3 + min(col, 4 - col)] % 2 for col in range(5)] for row in range(5)]
    size, cell, margin = 48, 8, 4
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # no filter
        for x in range(size):
            row, col = (y - margin) // cell, (x - margin) // cell
            inside = 0 <= row < 5 and 0 <= col < 5 and margin <= y and margin <= x
            raw += color if inside and cells[row][col] else background

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


class Planner:
    """Decides everything that happened in the month, before anything is written."""

    def __init__(self, rng: random.Random, now: float, scale: float, keep_running: bool):
        self.rng = rng
        self.now = now
        self.start = now - DAYS * DAY
        self.scale = scale
        self.keep_running = keep_running
        self.people: list[Person] = []
        self.orgs: list[OrgPlan] = []
        self.projects: list[ProjectPlan] = []
        self.runs: list[RunPlan] = []
        self.invites: list[tuple[OrgPlan, str, str]] = []

    def plan(self) -> Planner:
        self._people()
        for name, (currency, rates, members, projects) in ORGS.items():
            org = self._org(name, currency, rates, members)
            for project, kind, runs, about in projects:
                self._project(org, project, KINDS[kind], runs, about)
        org_name, project, kind, runs = DELETED_PROJECT
        org = next(o for o in self.orgs if o.name == org_name)
        deleted = self._project(org, project, KINDS[kind], runs, None)
        deleted.deleted = True
        for plan in self.runs:
            plan.deleted = plan.deleted or deleted is plan.project
        self._budgets()
        return self

    # -------------------------------------------------------------- people and orgs

    def _people(self) -> None:
        rng = self.rng
        admin = Person("Admin", ADMIN_EMAIL, self.start - 7 * DAY, superadmin=True)
        admin.avatar, admin.avatar_source = identicon(ADMIN_EMAIL), "upload"
        self.people.append(admin)
        names = list(zip(FIRST_NAMES, LAST_NAMES, strict=True))
        rng.shuffle(names)
        for i, (first, last) in enumerate(names):
            email = f"{first.lower()}.{last.lower()}@example.com"
            email = email.replace("á", "a").replace("í", "i").replace("é", "e")
            # Most were there before the month; the rest joined during it.
            joined = (
                self.start - rng.uniform(1, 60) * DAY
                if i < 30
                else self.start + rng.uniform(2, DAYS - 3) * DAY
            )
            person = Person(f"{first} {last}", email, joined, activity=rng.paretovariate(1.6))
            if rng.random() < 0.65:
                person.avatar = identicon(email)
                person.avatar_source = "gravatar" if rng.random() < 0.7 else "upload"
            self.people.append(person)

    def _org(self, name: str, currency: str, rates: dict[str, float], members: int) -> OrgPlan:
        rng = self.rng
        org = OrgPlan(
            name,
            DISPLAY_NAMES.get(name, name),
            currency,
            dict(rates),
            avatar=identicon(f"org:{name}"),
        )
        org.members[self.people[0]] = "admin"
        founders = [p for p in self.people[1:] if p.created_at < self.start]
        later = [p for p in self.people[1:] if p.created_at >= self.start]
        chosen = rng.sample(founders, min(len(founders), members - members // 4))
        chosen += rng.sample(later, min(len(later), members - len(chosen)))
        for i, person in enumerate(chosen):
            org.members[person] = "admin" if i < 2 else "member"
        self.orgs.append(org)
        for _ in range(rng.randint(1, 2)):
            first = rng.choice(FIRST_NAMES).lower()
            self.invites.append((org, f"{first}.{secrets.token_hex(2)}@example.com", "member"))
        return org

    # -------------------------------------------------------------- projects and runs

    def _project(
        self, org: OrgPlan, name: str, kind: Kind, runs: int, about: str | None
    ) -> ProjectPlan:
        rng = self.rng
        members = [p for p in org.members if not p.superadmin]
        founders = [p for p in members if p.created_at < self.start]
        size = min(len(members), rng.randint(2, 4) if kind.hours[1] > 24 else rng.randint(3, 8))
        team = [rng.choice(founders)]
        team += rng.sample([p for p in members if p is not team[0]], size - 1)
        project = ProjectPlan(org, name, kind, about, team)
        count = max(2, round(runs * self.scale))
        if kind.sweeps:
            self._sweeps(project, count)
        else:
            for _ in range(count):
                self._run(project, self._launch_time(spread=kind.hours[1] > 24))
        mine = [plan for plan in self.runs if plan.project is project]
        first = min((plan.created_at for plan in mine), default=self.start)
        project.created_at = first - rng.uniform(5 * 60, 2 * HOUR)
        for i, plan in enumerate(sorted(mine, key=lambda p: p.created_at), start=1):
            if plan.display_name.endswith("-0"):  # numbered in creation order, as wandb does
                plan.display_name = plan.display_name[:-1] + str(i)
        self.projects.append(project)
        return project

    def _launch_time(self, spread: bool = False) -> float:
        """When someone starts a run: mostly weekday working hours, sometimes overnight."""
        rng = self.rng
        while True:
            day = rng.uniform(0, DAYS)
            midnight = math.floor((self.start + day * DAY) / DAY) * DAY
            weekday = dt.datetime.fromtimestamp(midnight, dt.UTC).weekday()
            if weekday >= 5 and rng.random() > (0.5 if spread else 0.3):
                continue
            hour = rng.gauss(14, 3) if rng.random() < 0.75 else rng.uniform(0, 24)
            when = midnight + min(max(hour, 0), 23.9) * HOUR
            if self.start < when < self.now - 600:
                return when

    def _sweeps(self, project: ProjectPlan, count: int) -> None:
        """Sweeps of 20-120 runs, each run in turn by a handful of parallel agents."""
        rng = self.rng
        while count > 0:
            size = min(count, rng.randint(20, 120))
            count -= size
            sweep = "".join(rng.choices(string.ascii_lowercase + string.digits, k=8))
            starts = [self._launch_time() + rng.uniform(0, 300)] * rng.choice((2, 4, 8, 16))
            owner = self._owner(project, starts[0])
            for i in range(size):
                agent = i % len(starts)
                plan = self._run(project, starts[agent], owner=owner, sweep=sweep)
                if plan is None:
                    break
                starts[agent] = plan.heartbeat_at + rng.uniform(5, 90)

    def _owner(self, project: ProjectPlan, when: float) -> Person:
        joined = [p for p in project.team if p.created_at < when] or project.team[:1]
        return self.rng.choices(joined, weights=[p.activity for p in joined])[0]

    def _run(
        self,
        project: ProjectPlan,
        start: float,
        owner: Person | None = None,
        sweep: str | None = None,
    ) -> RunPlan | None:
        rng = self.rng
        kind = project.kind
        if start >= self.now - 60:
            return None
        low, high = kind.hours
        planned = math.exp(rng.uniform(math.log(low), math.log(high))) * HOUR
        planned_rows = rng.randint(*kind.rows)
        state, exitcode, fraction = "finished", 0, 1.0
        roll = rng.random()
        if roll < kind.fail:
            state, exitcode, fraction = "failed", 1, rng.uniform(0.02, 0.7)
        elif roll < kind.fail + kind.crash:
            state, exitcode, fraction = "crashed", None, rng.uniform(0.05, 0.9)
        alive = max(30.0, planned * fraction)
        # Preempted runs resume after a gap the ledger doesn't bill.
        pieces = rng.randint(2, 3) if rng.random() < kind.preempt else 1
        cuts = sorted(rng.uniform(0.1, 0.9) * alive for _ in range(pieces - 1))
        lengths = [b - a for a, b in zip([0.0, *cuts], [*cuts, alive], strict=True)]
        segments, t = [], start
        for length in lengths:
            segments.append((t, t + length))
            t += length + rng.uniform(0.3, 8) * HOUR
        if segments[-1][1] > self.now:  # still going when the month ends
            if not self.keep_running:
                return None
            kept = [(a, min(b, self.now - rng.uniform(1, 20))) for a, b in segments if a < self.now]
            if kept[-1][1] - kept[-1][0] < 30:
                return None
            done = sum(b - a for a, b in kept) / alive
            segments, state, exitcode, fraction = kept, "running", None, fraction * done
        gpu_type, gpu_count = rng.choice(kind.gpus)
        nodes = rng.choice(kind.nodes)
        # The SDK reports one node; someone corrected most multi-node runs by hand.
        override = gpu_count * nodes if nodes > 1 and rng.random() < 0.75 else None
        number = len([p for p in self.runs if p.project is project])
        plan = RunPlan(
            project=project,
            name="".join(rng.choices(string.ascii_lowercase + string.digits, k=8)),
            display_name=f"{rng.choice(ADJECTIVES)}-{rng.choice(NOUNS)}-0",
            owner=owner or self._owner(project, start),
            segments=segments,
            planned_rows=planned_rows,
            rows=max(1, round(planned_rows * fraction)),
            state=state,
            exitcode=exitcode if state != "running" else None,
            gpu_type=gpu_type,
            gpu_count=gpu_count,
            gpu_count_override=override,
            config=self._config(kind, rng, nodes, gpu_count),
            tags=[],
            sweep=sweep,
            group=f"sweep-{sweep}" if sweep else None,
            host=f"gpu-{rng.randint(1, 96):03d}" if gpu_count else f"cpu-{rng.randint(1, 8):02d}",
            commit=secrets.token_hex(20),
            seed=rng.randrange(1 << 30),
            deleted=kind.sweeps or kind.job_type == "debug",
        )
        plan.deleted = plan.deleted and rng.random() < DELETED_RUNS
        if kind.job_type == "pretrain" and rng.random() < 0.6:
            plan.display_name = f"{project.name.split('-')[-1]}-mix{rng.randint(2, 4)}-r{number}"
        plan.tags = self._tags(plan, rng)
        if rng.random() < 0.06:
            plan.notes = rng.choice(
                (
                    "baseline for the next round",
                    "loss spike around 40% — check the data shard",
                    "rerun with the fixed tokenizer",
                    "use this one for the report",
                    "OOM at the larger batch size; halved it",
                )
            )
        self.runs.append(plan)
        return plan

    @staticmethod
    def _config(kind: Kind, rng: random.Random, nodes: int, per_node: int) -> dict[str, Any]:
        base: dict[str, Any] = {"seed": rng.randrange(10_000), "gpus": nodes * per_node}
        if kind.job_type == "pretrain":
            base |= {
                "model": {"layers": rng.choice((24, 32)), "d_model": rng.choice((2048, 4096))},
                "lr": rng.choice((1.5e-4, 3e-4, 6e-4)),
                "warmup_steps": rng.choice((2000, 4000)),
                "batch_tokens": rng.choice((2**21, 2**22)),
                "data_mix": rng.choice(("v3", "v3-code", "v3-math")),
            }
        elif kind.job_type in ("sweep", "debug"):
            base |= {
                "lr": 10 ** rng.uniform(-5, -2),
                "batch_size": rng.choice((16, 32, 64, 128, 256)),
                "dropout": round(rng.uniform(0, 0.3), 2),
                "weight_decay": rng.choice((0.0, 0.01, 0.1)),
                "warmup": rng.choice((100, 500, 1000)),
                "optimizer": rng.choice(("adamw", "adamw", "lion", "sgd", "muon")),
                "schedule": rng.choice(("cosine", "linear", "constant")),
                "epochs": rng.choice((3, 5, 10)),
            }
            for k in range(rng.randint(4, 10)):
                base[f"param_{k}"] = round(rng.random(), 4)
        elif kind.job_type == "finetune":
            base |= {
                "base_model": rng.choice(("7b-mix3", "7b-mix4", "1b-mix2", "vit-l")),
                "lr": rng.choice((1e-5, 2e-5, 5e-5)),
                "epochs": rng.choice((1, 2, 3)),
                "lora": rng.random() < 0.3,
                "kl_coef": rng.choice((0.02, 0.05, 0.1)),
            }
        else:
            base |= {
                "checkpoint": f"step-{rng.randint(1, 60) * 2000}",
                "tasks": rng.sample(("mmlu", "gsm8k", "hellaswag", "arc", "humaneval"), 3),
                "shots": rng.choice((0, 5)),
            }
        return {key: {"value": value} for key, value in base.items()}

    @staticmethod
    def _tags(plan: RunPlan, rng: random.Random) -> list[str]:
        tags = []
        if plan.sweep:
            tags.append("sweep")
        if rng.random() < 0.15:
            tags.append("baseline")
        if rng.random() < 0.1:
            tags.append(rng.choice(("ablation", "long", "v2", "repro", "debug")))
        if plan.project.kind.job_type == "pretrain":
            tags.append(plan.config["data_mix"]["value"])
        return tags

    def _budgets(self) -> None:
        """Budgets set around what each org spends: last month's, and this month's."""
        rng = self.rng
        this = gpus.month_of(self.now)
        last = gpus.month_of(gpus.month_start(this) - 1)
        elapsed = (self.now - gpus.month_start(this)) / (
            gpus.month_start(gpus.next_month(this)) - gpus.month_start(this)
        )
        for org in self.orgs:
            spend = defaultdict(float)
            for plan in self.runs:
                if plan.project.org is org and plan.gpu_type in org.rates:
                    for month, seconds in ledger(plan):
                        spend[month] += seconds / HOUR * plan.gpus * org.rates[plan.gpu_type]
            projected = spend[this] / max(elapsed, 0.05)
            # vision went over last month's budget; the others stayed under.
            over = rng.uniform(0.82, 0.92) if org.name == "vision" else rng.uniform(1.05, 1.3)
            org.budgets = [
                (last, round(max(spend[last], 1000) * over, -3)),
                (this, round(max(projected, 1000) * rng.uniform(0.8, 1.25), -3)),
            ]


def ledger(plan: RunPlan) -> list[tuple[dt.date, float]]:
    """Seconds the run was alive per UTC month (gaps between its segments aren't billed)."""
    months: dict[dt.date, float] = defaultdict(float)
    for start, end in plan.segments:
        for month, seconds in gpus.split_by_month(start, end):
            months[month] += seconds
    return sorted(months.items())


# ---------------------------------------------------------------------- what runs log


@dataclass
class Logged:
    """Everything one run sent, ready to copy into the database."""

    history: list[str]
    events: list[str]
    logs: list[str]
    columns: list[tuple[str, str, int, bytes]]  # (stream, key, n, blob)
    summary: dict[str, Any]
    points: int


def wall_clock(plan: RunPlan, alive: np.ndarray) -> np.ndarray:
    """Wall-clock times of moments ``alive`` seconds into the run's alive time."""
    xp, fp, total = [], [], 0.0
    for start, end in plan.segments:
        xp += [total, total + end - start]
        fp += [start, end]
        total += end - start
    return np.interp(alive, xp, fp)


def curves(plan: RunPlan, rng: np.random.Generator) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Metric curves as {key: (rows, values)}; rows index the history lines."""
    n = plan.rows
    rows = np.arange(n)
    p = (rows + 1) / plan.planned_rows  # fraction of the planned training done
    noise = rng.standard_normal
    job = plan.project.kind.job_type
    config = {k: v["value"] for k, v in plan.config.items()}
    out: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    def every(k: int, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        picked = rows[(rows + 1) % k == 0]
        return picked, values[picked]

    if job == "pretrain":
        final = 2.05 + 0.12 * rng.standard_normal() - 0.1 * (config["model"]["layers"] == 32)
        loss = final + 7.5 * np.exp(-p * 14) + 0.5 * (1 - p) + 0.02 * noise(n)
        spikes = rng.random(n) < 0.002
        loss[spikes] += rng.uniform(0.3, 1.5, spikes.sum())
        warm = np.minimum(1, (rows + 1) * plan.project.kind.log_every / config["warmup_steps"])
        lr = config["lr"] * warm * (0.1 + 0.9 * 0.5 * (1 + np.cos(np.pi * np.minimum(p, 1))))
        out["train/loss"] = (rows, loss)
        out["train/lr"] = (rows, lr)
        out["train/grad_norm"] = (rows, 0.6 + 4 * np.exp(-p * 25) + np.abs(0.1 * noise(n)))
        tps = 2.6e4 * config["gpus"] * (1 + 0.02 * noise(n))
        out["train/tokens_per_sec"] = (rows, tps)
        out["train/mfu"] = (rows, 0.41 + 0.015 * noise(n))
        eval_rows, eval_loss = every(20, loss + 0.06 + 0.01 * noise(n))
        out["eval/loss"] = (eval_rows, eval_loss)
        out["eval/ppl"] = (eval_rows, np.exp(eval_loss))
    elif job in ("sweep", "debug"):
        quality = (math.log10(config["lr"]) + 3.4) ** 2  # best near lr = 4e-4
        final = 0.3 + 0.18 * quality + 0.05 * abs(rng.standard_normal())
        loss = final + 2.0 * np.exp(-p * 6) + 0.03 * noise(n)
        if config["lr"] > 4e-3 and rng.random() < 0.5:  # diverged
            loss = loss + np.clip((p - 0.3) * 12, 0, None) ** 2
        loss = np.abs(loss)
        out["loss"] = (rows, loss)
        out["acc"] = (rows, np.clip(1 - loss / 2.6, 0, 1))
        epochs = config.get("epochs", 3)
        out["epoch"] = (rows, np.minimum(p, 1) * epochs)
        out["lr"] = (rows, config["lr"] * (1 - 0.9 * np.minimum(p, 1)))
        val_rows, val_loss = every(10, loss * 1.07 + 0.02 * noise(n))
        out["val_loss"] = (val_rows, val_loss)
        out["val_acc"] = (val_rows, np.clip(1 - val_loss / 2.6, 0, 1))
    elif job == "finetune":
        final = 0.85 + 0.1 * rng.standard_normal()
        loss = final + 0.8 * np.exp(-p * 5) + 0.04 * noise(n)
        out["train/loss"] = (rows, loss)
        out["train/lr"] = (rows, config["lr"] * (1 - np.minimum(p, 1)))
        out["train/reward"] = (rows, 0.2 + 0.9 * (1 - np.exp(-p * 3)) + 0.05 * noise(n))
        out["train/kl"] = (rows, config["kl_coef"] * (1 + 4 * p) + 0.002 * noise(n))
        eval_rows, eval_loss = every(25, loss + 0.05 + 0.01 * noise(n))
        out["eval/loss"] = (eval_rows, eval_loss)
        win = 0.42 + 0.3 * (1 - np.exp(-p * 3)) + 0.02 * noise(n)
        out["eval/win_rate"] = every(25, win)
    else:  # eval: progress through the benchmark examples
        out["eval/progress"] = (rows, (rows + 1) / plan.planned_rows)
        out["eval/accuracy"] = (rows, 0.55 + 0.1 * rng.standard_normal() + 0.01 * noise(n))
    return out


def log_run(plan: RunPlan) -> Logged:
    """What the run's client sent: history and system metric lines, console output."""
    rng = np.random.default_rng(plan.seed)
    kind = plan.project.kind
    n = plan.rows
    runtime = plan.runtime
    alive = np.linspace(runtime / n, runtime, n)
    stamps = wall_clock(plan, alive)
    steps = np.arange(n) * kind.log_every
    metrics = curves(plan, rng)
    series: dict[str, tuple[np.ndarray, np.ndarray]] = {
        "_step": (np.arange(n), steps.astype(float)),
        "_runtime": (np.arange(n), alive),
        "_timestamp": (np.arange(n), stamps),
        **metrics,
    }
    lines: list[dict[str, Any]] = [
        {"_step": int(s), "_runtime": round(float(a), 3), "_timestamp": round(float(w), 3)}
        for s, a, w in zip(steps.tolist(), alive.tolist(), stamps.tolist(), strict=True)
    ]
    for key, (rows, values) in metrics.items():
        for row, value in zip(rows.tolist(), values.tolist(), strict=True):
            lines[row][key] = value
    summary: dict[str, Any] = {key: lines[-1][key] for key in ("_step", "_runtime", "_timestamp")}
    for key, (rows, values) in metrics.items():
        if len(values):
            summary[key] = float(values[-1])
    if kind.job_type == "eval":
        summary["bench"] = {
            task: round(float(np.clip(0.3 + 0.5 * rng.random(), 0, 1)), 4)
            for task in plan.config["tasks"]["value"]
        }
    if kind.job_type == "pretrain" and "eval/loss" in metrics:
        summary["best/eval_loss"] = float(np.min(metrics["eval/loss"][1], initial=99))
    summary["_wandb"] = {"runtime": int(runtime)}

    # System metrics: a sample a minute or so, per GPU of the node the SDK runs on.
    samples = int(min(240, max(2, runtime / 90)))
    s_alive = np.linspace(runtime / samples, runtime, samples)
    s_wall = wall_clock(plan, s_alive)
    system: dict[str, np.ndarray] = {
        "system.cpu": np.clip(35 + 10 * rng.standard_normal(samples), 1, 100),
        "system.memory_percent": np.clip(55 + 5 * rng.standard_normal(samples), 5, 99),
    }
    watts = DEVICES[plan.gpu_type][3] if plan.gpu_type else 0
    for gpu in range(min(plan.gpu_count, 8)):
        busy = rng.uniform(80, 97)
        system[f"system.gpu.{gpu}.gpu"] = np.clip(busy + 4 * rng.standard_normal(samples), 0, 100)
        system[f"system.gpu.{gpu}.memoryAllocated"] = np.clip(
            rng.uniform(55, 92) + rng.standard_normal(samples), 0, 100
        )
        system[f"system.gpu.{gpu}.temp"] = 60 + 8 * busy / 100 + 2 * rng.standard_normal(samples)
        system[f"system.gpu.{gpu}.powerWatts"] = watts * (0.7 + 0.25 * busy / 100) + 15 * (
            rng.standard_normal(samples)
        )
    system["_runtime"] = s_alive
    system["_timestamp"] = s_wall
    system["_wandb"] = np.ones(samples)
    events = []
    rounded = {key: np.round(values, 3).tolist() for key, values in system.items()}
    for i in range(samples):
        sample = {key: values[i] for key, values in rounded.items()}
        sample["_wandb"] = True
        events.append(orjson.dumps(sample).decode())

    columns = [("history", key, len(values), encode(rows, values, n)) for key, (rows, values) in series.items()]  # fmt: skip
    columns += [("system", key, samples, encode(None, values, samples)) for key, values in system.items()]  # fmt: skip
    points = sum(item[2] for item in columns)
    return Logged(
        history=[orjson.dumps(line).decode() for line in lines],
        events=events,
        logs=console(plan, lines, metrics),
        columns=columns,
        summary=summary,
        points=points,
    )


def encode(rows: np.ndarray | None, values: np.ndarray, n: int) -> bytes:
    """A series blob as Flock compacts it (see ``Series.encode``)."""
    vals = np.asarray(values, dtype="<f8")
    if rows is None or (len(rows) == n and (len(rows) == 0 or rows[-1] == n - 1)):
        return vals.tobytes()
    return np.asarray(rows, dtype="<i4").tobytes() + vals.tobytes()


def console(plan: RunPlan, lines: list[dict], metrics: dict) -> list[str]:
    """Console output as wandb captures it: each line prefixed with a timestamp."""

    def stamp(t: float) -> str:
        return dt.datetime.fromtimestamp(t, dt.UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")

    first = plan.created_at
    out = [
        f"{stamp(first)} starting {plan.project.kind.program} on {plan.host}",
        f"{stamp(first + 2)} world size {plan.gpus or 1}, seed {plan.config['seed']['value']}",
    ]
    key = next(iter(metrics), None)
    every = max(1, len(lines) // 25)
    for line in lines[every - 1 :: every]:
        value = line.get(key) if key else None
        shown = f" {key} {value:.4f}" if isinstance(value, float) else ""
        out.append(f"{stamp(line['_timestamp'])} step {line['_step']}{shown}")
    end = plan.heartbeat_at
    if plan.state == "failed":
        out += [
            f"{stamp(end)} Traceback (most recent call last):",
            f'{stamp(end)}   File "{plan.project.kind.program}", line 214, in <module>',
            f"{stamp(end)} "
            + random.Random(plan.seed).choice(
                (
                    "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.00 GiB",
                    "RuntimeError: NCCL watchdog timeout after 1800000 ms",
                    "ValueError: loss is NaN at step " + str(lines[-1]["_step"] if lines else 0),
                )
            ),
        ]
    elif plan.state == "finished":
        out.append(f"{stamp(end)} done in {plan.runtime / 3600:.2f}h")
    return out


# ---------------------------------------------------------------------- writing


def utc(t: float) -> dt.datetime:
    return dt.datetime.fromtimestamp(t, dt.UTC)


TABLES = (
    "gpu_usage", "gpu_rates", "org_budgets", "history_columns", "history", "events", "logs",
    "run_files", "runs", "projects", "invites", "password_resets", "sessions", "api_keys",
    "memberships", "users", "orgs", "avatars",
)  # fmt: skip


class Writer:
    def __init__(
        self,
        conn: asyncpg.Connection,
        plan: Planner,
        files_dir: Path | None,
        log: Callable[[str], None],
    ) -> None:
        self.conn = conn
        self.plan = plan
        self.files_dir = files_dir
        self.log = log
        self.stats: dict[str, Any] = defaultdict(int)

    async def write(self) -> dict[str, Any]:
        conn, plan = self.conn, self.plan
        avatars = {p.avatar for p in plan.people if p.avatar} | {
            o.avatar for o in plan.orgs if o.avatar
        }
        await conn.executemany(
            "INSERT INTO avatars (digest, content_type, data, created_at) "
            "VALUES ($1, 'image/png', $2, $3)",
            [(hashlib.sha256(a).digest(), a, utc(plan.start)) for a in avatars],
        )
        password = await asyncio.to_thread(PasswordHasher().hash, PASSWORD)
        for person in plan.people:
            person.pk = await conn.fetchval(
                "INSERT INTO users (email, name, password_hash, is_superadmin, created_at, "
                "avatar, avatar_source) VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING pk",
                person.email,
                person.name,
                password,
                person.superadmin,
                utc(person.created_at),
                hashlib.sha256(person.avatar).digest() if person.avatar else None,
                person.avatar_source,
            )
        alphabet = string.ascii_letters + string.digits  # a wandb key, as Accounts makes them
        key = "flock-wandb-" + "".join(secrets.choice(alphabet) for _ in range(40))
        await conn.execute(
            "INSERT INTO api_keys (user_pk, name, prefix, hash, created_at) "
            "VALUES ($1, 'simulate-month', $2, $3, now())",
            plan.people[0].pk,
            key[:12],
            hashlib.sha256(key.encode()).digest(),
        )
        for org in plan.orgs:
            org.pk = await conn.fetchval(
                "INSERT INTO orgs (name, display_name, created_at, updated_at, currency, avatar) "
                "VALUES ($1, $2, $3, $3, $4, $5) RETURNING pk",
                org.name,
                org.display_name,
                utc(plan.start - 90 * DAY),
                org.currency,
                hashlib.sha256(org.avatar).digest() if org.avatar else None,
            )
            await conn.executemany(
                "INSERT INTO memberships (user_pk, org_pk, role, created_at) VALUES ($1, $2, $3, $4)",
                [
                    (p.pk, org.pk, role, utc(max(p.created_at, plan.start - 90 * DAY)))
                    for p, role in org.members.items()
                ],
            )
            await conn.executemany(
                "INSERT INTO gpu_rates (org_pk, gpu_type, hourly_cost) VALUES ($1, $2, $3)",
                [(org.pk, gpu_type, cost) for gpu_type, cost in org.rates.items()],
            )
            await conn.executemany(
                "INSERT INTO org_budgets (org_pk, month, amount) VALUES ($1, $2, $3)",
                [(org.pk, month, amount) for month, amount in org.budgets],
            )
        await conn.executemany(
            "INSERT INTO invites (org_pk, email, role, token_hash, created_by, created_at, "
            "expires_at) VALUES ($1, $2, $3, $4, $5, now() - interval '1 day', "
            "now() + interval '6 days')",
            [
                (org.pk, email, role, secrets.token_bytes(32), plan.people[0].pk)
                for org, email, role in plan.invites
            ],
        )
        for project in plan.projects:
            if not project.deleted:
                project.pk = await conn.fetchval(
                    "INSERT INTO projects (org_pk, name, description, created_at, updated_at) "
                    "VALUES ($1, $2, $3, $4, $4) RETURNING pk",
                    project.org.pk,
                    project.name,
                    project.description,
                    utc(project.created_at),
                )
            await self._runs(project)
        await conn.execute("ANALYZE")
        return {**self.stats, "api_key": key}

    async def _runs(self, project: ProjectPlan) -> None:
        runs = sorted(
            (p for p in self.plan.runs if p.project is project), key=lambda p: p.created_at
        )
        started = time.perf_counter()
        batches = [runs[i : i + 64] for i in range(0, len(runs), 64)]
        pending = (
            asyncio.ensure_future(asyncio.to_thread(self._log_batch, batches[0]))
            if batches
            else None
        )
        for i, batch in enumerate(batches):
            logged = await pending if pending is not None else []
            nxt = batches[i + 1] if i + 1 < len(batches) else None
            pending = (
                asyncio.ensure_future(asyncio.to_thread(self._log_batch, nxt)) if nxt else None
            )
            await self._write_batch(batch, logged)
        self.log(
            f"  {project.org.name}/{project.name}: {len(runs)} runs"
            f"{' (deleted project)' if project.deleted else ''}"
            f" in {time.perf_counter() - started:.1f}s"
        )

    @staticmethod
    def _log_batch(batch: list[RunPlan]) -> list[Logged | None]:
        return [None if plan.deleted else log_run(plan) for plan in batch]

    async def _write_batch(self, batch: list[RunPlan], logged: list[Logged | None]) -> None:
        conn = self.conn
        history, events, logs, columns, files, usage = [], [], [], [], [], []
        async with conn.transaction():
            for plan, sent in zip(batch, logged, strict=True):
                project = plan.project
                if sent is not None:
                    active = plan.state == "running"
                    compacted = 0 if active else None
                    plan.pk = await conn.fetchval(
                        """
                        INSERT INTO runs (project_pk, name, display_name, state, created_at,
                            updated_at, heartbeat_at, config, summary, tags, notes, group_name,
                            job_type, host, program, commit_hash, sweep, exitcode,
                            history_line_count, events_line_count, log_line_count,
                            history_compacted_line_count, system_compacted_line_count,
                            owner_pk, gpu_type, gpu_count, gpu_count_override)
                        VALUES ($1, $2, $3, $4, $5, $6, $6, $7, $8, $9, $10, $11, $12, $13,
                            $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26)
                        RETURNING pk
                        """,
                        project.pk,
                        plan.name,
                        plan.display_name,
                        plan.state,
                        utc(plan.created_at),
                        utc(plan.heartbeat_at),
                        orjson.dumps(plan.config).decode(),
                        orjson.dumps(sent.summary).decode(),
                        plan.tags,
                        plan.notes,
                        plan.group,
                        project.kind.job_type,
                        plan.host,
                        project.kind.program,
                        plan.commit,
                        plan.sweep,
                        plan.exitcode,
                        len(sent.history),
                        len(sent.events),
                        len(sent.logs),
                        len(sent.history) if compacted is None else compacted,
                        len(sent.events) if compacted is None else compacted,
                        plan.owner.pk,
                        gpus.normalize(DEVICES[plan.gpu_type][0]) if plan.gpu_type else None,
                        plan.gpu_count,
                        plan.gpu_count_override,
                    )
                    history += [(plan.pk, i, line) for i, line in enumerate(sent.history)]
                    events += [(plan.pk, i, line) for i, line in enumerate(sent.events)]
                    logs += [(plan.pk, i, line) for i, line in enumerate(sent.logs)]
                    columns += [(plan.pk, *column) for column in sent.columns]
                    self.stats["runs"] += 1
                    self.stats["history_lines"] += len(sent.history)
                    self.stats["system_samples"] += len(sent.events)
                    self.stats["points"] += sent.points
                    self.stats[f"state_{plan.state}"] += 1
                    if self.files_dir is not None:
                        files.append((plan.pk, self._metadata(plan)))
                else:
                    self.stats["deleted_runs"] += 1
                gpu_type = gpus.normalize(DEVICES[plan.gpu_type][0]) if plan.gpu_type else None
                for month, seconds in ledger(plan):
                    usage.append(
                        (
                            project.org.pk,
                            project.pk or None,
                            plan.pk or None,
                            plan.name,
                            month,
                            seconds,
                            gpu_type,
                            plan.gpus,
                        )
                    )
                    self.stats["gpu_hours"] += seconds / HOUR * plan.gpus
            for table, records, names in (
                ("history", history, ("run_pk", "line", "data")),
                ("events", events, ("run_pk", "line", "data")),
                ("logs", logs, ("run_pk", "line", "data")),
                ("history_columns", columns, ("run_pk", "stream", "key", "n", "data")),
                (
                    "gpu_usage",
                    usage,
                    ("org_pk", "project_pk", "run_pk", "run_name", "month", "seconds",
                     "gpu_type", "gpu_count"),
                ),
            ):  # fmt: skip
                if records:
                    await conn.copy_records_to_table(table, records=records, columns=names)
            if files:
                written = await asyncio.to_thread(self._write_files, files)
                await conn.executemany(
                    "INSERT INTO run_files (run_pk, name, size, updated_at) "
                    "VALUES ($1, 'wandb-metadata.json', $2, $3)",
                    written,
                )

    def _metadata(self, plan: RunPlan) -> bytes:
        metadata: dict[str, Any] = {
            "os": "Linux-6.8.0-1015-nvidia-x86_64-with-glibc2.35",
            "python": "CPython 3.12.7",
            "startedAt": utc(plan.created_at).isoformat(),
            "program": plan.project.kind.program,
            "host": plan.host,
            "cpu_count": 96,
            "cpu_count_logical": 192,
        }
        if plan.gpu_type:
            name, memory, architecture, _ = DEVICES[plan.gpu_type]
            metadata |= {
                "gpu": name,
                "gpu_count": plan.gpu_count,
                "cudaVersion": "12.6",
                "gpu_nvidia": [{"name": name, "memoryTotal": memory, "architecture": architecture}]
                * plan.gpu_count,
            }
        return orjson.dumps(metadata)

    def _write_files(self, files: list[tuple[int, bytes]]) -> list[tuple[int, int, dt.datetime]]:
        assert self.files_dir is not None
        written = []
        for pk, data in files:
            path = self.files_dir / str(pk) / "wandb-metadata.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            written.append((pk, len(data), utc(self.plan.now)))
        return written


async def simulate(
    database_url: str,
    *,
    scale: float = 1.0,
    seed: int = 0,
    now: float | None = None,
    files_dir: Path | None = None,
    keep_running: bool = False,
    reset: bool = False,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Write a simulated month into the database at ``database_url``; returns statistics,
    including the super admin's new wandb key (``api_key``)."""
    started = time.perf_counter()
    plan = Planner(random.Random(seed), now or time.time(), scale, keep_running).plan()
    log(
        f"planned {len(plan.runs)} runs in {len(plan.projects)} projects of {len(plan.orgs)} "
        f"orgs for {len(plan.people)} users"
    )
    await Migrations().apply(database_url)
    conn = await asyncpg.connect(database_url)
    try:
        if reset:
            await conn.execute(f"TRUNCATE {', '.join(TABLES)} CASCADE")
            if files_dir is not None and files_dir.is_dir():
                for path in files_dir.iterdir():
                    if path.is_dir() and path.name.isdigit():
                        shutil.rmtree(path)
        elif await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM users) OR EXISTS (SELECT 1 FROM orgs) "
            "OR EXISTS (SELECT 1 FROM runs)"
        ):
            raise SystemExit("the database isn't empty: pass --reset to replace what's in it")
        async with conn.transaction():
            stats = await Writer(conn, plan, files_dir, log).write()
    finally:
        await conn.close()
    stats["seconds"] = time.perf_counter() - started
    stats["orgs"] = len(plan.orgs)
    stats["users"] = len(plan.people)
    stats["projects"] = sum(1 for p in plan.projects if not p.deleted)
    return dict(stats)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--database-url",
        default=os.environ.get("FLOCK_DATABASE_URL", Settings.database_url),
        help="Flock's database (default: FLOCK_DATABASE_URL, else Flock's default)",
    )
    parser.add_argument(
        "--files-dir",
        type=Path,
        default=Path(os.environ["FLOCK_FILES_DIR"]) if os.environ.get("FLOCK_FILES_DIR") else None,
        help="Flock's files directory, to write each run's wandb-metadata.json",
    )
    parser.add_argument("--scale", type=float, default=1.0, help="multiplies the number of runs")
    parser.add_argument("--seed", type=int, default=0, help="random seed (same seed, same month)")
    parser.add_argument(
        "--keep-running",
        action="store_true",
        help="leave runs still going at the end as running (Flock marks them crashed "
        "after FLOCK_CRASH_TIMEOUT, as no client is logging to them)",
    )
    parser.add_argument("--reset", action="store_true", help="empty the database first")
    args = parser.parse_args(argv)
    stats = asyncio.run(
        simulate(
            args.database_url,
            scale=args.scale,
            seed=args.seed,
            files_dir=args.files_dir,
            keep_running=args.keep_running,
            reset=args.reset,
        )
    )
    print(
        f"\nwrote {stats['runs']} runs ({stats.get('deleted_runs', 0)} more deleted), "
        f"{stats['history_lines']:,} history lines, {stats['system_samples']:,} system samples, "
        f"{stats['points']:,} points and {stats['gpu_hours']:,.0f} GPU hours "
        f"in {stats['seconds']:.0f}s"
    )
    print(f"sign in as {ADMIN_EMAIL} / {PASSWORD} (every simulated user has that password)")
    print(f"super admin wandb key: {stats['api_key']}")
    print("start (or restart) Flock to load it")


if __name__ == "__main__":
    sys.exit(main())
