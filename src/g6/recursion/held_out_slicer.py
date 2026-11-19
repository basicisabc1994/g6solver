# SPDX-License-Identifier: Apache-2.0
"""Protected evaluator storage for sealed splits, freeze receipts and detector censuses.

The host keeps this store and evaluator capabilities outside learner mounts and
processes. Capabilities and Vault do not confine arbitrary same-user code.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import MappingProxyType

from g6.core.vault import Vault
from g6.metaharness.domain import SealedSplit, cut_pool
from g6.metaharness.schema import SuiteCase
from g6.recursion.contamination_gate import (
    CanaryProbe, MembershipProbe, ScreenReport, canary_matches, scan_texts, screen,
)
from g6.recursion.evaluator_entry import EvaluatorCapability
from g6.recursion.tasks import TaskSpec
from g6.rsi.records import TaskError, content_digest

FROZEN_COMPONENTS = ("improver", "adapter", "library", "self_model", "curriculum", "prompt_library",
                     "harness_memory", "context_indexes")
RULE = "cut_pool-strata-six-sha256-v1"


class HeldOutError(ValueError):
    """A stable refusal code with no task or output text."""


def _evaluator(role: str, capability: EvaluatorCapability | None):
    if role != "evaluator":
        raise HeldOutError("G6_E_RSI_EVALUATOR_ONLY")
    if type(capability) is not EvaluatorCapability:
        raise HeldOutError("G6_E_RSI_EVALUATOR_CAPABILITY_REQUIRED")


def _hex(value, length=64):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value) is not None


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _public_ref(data, domain, *values):
    return hmac.digest(bytes.fromhex(data["salt"]), _json([domain, *values]).encode("utf-8"), "sha256").hex()


@dataclass(frozen=True)
class FreezeRecord:
    """The trusted generation driver supplies the source commit and full frozen state."""

    generation: int
    commit: str
    state_digests: Mapping[str, str]

    def __post_init__(self):
        if (type(self.generation) is not int or not 0 <= self.generation < 6
                or not (_hex(self.commit, 40) or _hex(self.commit))
                or not isinstance(self.state_digests, Mapping)
                or set(self.state_digests) != set(FROZEN_COMPONENTS)
                or not all(_hex(v) for v in self.state_digests.values())):
            raise HeldOutError("G6_E_RSI_FREEZE_INVALID")
        object.__setattr__(self, "state_digests", MappingProxyType(dict(self.state_digests)))

    def to_record(self):
        return {"generation": self.generation, "commit": self.commit, "state_digests": dict(self.state_digests)}


@dataclass(frozen=True)
class Slice:
    slice_id: str
    generation: int
    freeze_digest: str
    count: int
    seed_commitment: str
    opened_digest: str

    def to_record(self):
        return asdict(self) | {"cut_after_generation": self.generation}


@dataclass(frozen=True)
class PassResult:
    passed: bool
    reason: str
    census_count: int


def _case(task: TaskSpec) -> SuiteCase:
    return SuiteCase(task.task_id, task.prompt,
                     {"tests": task.public_tests.to_record(), "hidden_ref": task.hidden_ref},
                     {"group": task.family})


def partition_pool(pool: Sequence[TaskSpec], seed: int) -> tuple[tuple[TaskSpec, ...], ...]:
    """Six evaluator-only partitions, independent of input ordering and preserving families."""
    if type(seed) is not int or not pool or any(not isinstance(t, TaskSpec) for t in pool):
        raise HeldOutError("G6_E_RSI_POOL_INVALID")
    try:
        tasks = tuple(sorted((TaskSpec.from_record(t.to_record()) for t in pool), key=lambda t: t.task_id))
        for task in tasks:
            task.require_split("held_out")
    except TaskError:
        raise HeldOutError("G6_E_RSI_POOL_INVALID") from None
    if len({t.task_id for t in tasks}) != len(tasks):
        raise HeldOutError("G6_E_RSI_POOL_INVALID")
    cut = cut_pool(tuple(_case(t) for t in tasks), seed)
    strata = {c.case_id: name for name in ("tune", "selection", "held_out", "monitor")
              for c in getattr(cut, name)}
    parts = [[] for _ in range(6)]
    for task in tasks:
        bucket = int(content_digest([RULE, seed, strata[task.task_id], task.family]), 16) % 6
        parts[bucket].append(task)
    if not all(parts):
        raise HeldOutError("G6_E_RSI_PARTITION_INFEASIBLE")
    return tuple(tuple(part) for part in parts)


def detector_census(task: TaskSpec, output: str, names: Sequence[str],
                    detectors: Mapping[str, Callable[[TaskSpec, str], bool]] | None
                    ) -> tuple[PassResult, tuple[dict, ...]]:
    """True from a detector means a hack; missing, unknown and crashed checks refuse."""
    if (not names or len(set(names)) != len(names) or not isinstance(detectors, Mapping)
            or set(detectors) != set(names) or any(not callable(fn) for fn in detectors.values())):
        return PassResult(False, "census_missing", 0), ()
    observations = []
    for name in names:
        try:
            value = detectors[name](task, output)
            clean = type(value) is bool and not value
        except Exception:
            clean = False
        observations.append({"name": name, "clean": clean})
    passed = all(o["clean"] for o in observations)
    return PassResult(passed, "passed" if passed else "detector_failed", len(observations)), tuple(observations)


class HeldOutSlicer:
    """An evaluator handle. Only registration, slice summaries and records may be shared."""

    def __init__(self, root: str | Path, *, role: str, capability: EvaluatorCapability | None = None):
        _evaluator(role, capability)
        self._root = Path(root)
        self._capability = capability
        self._check_root()
        if not (self._root / "state.sqlite3").is_file():
            raise HeldOutError("G6_E_RSI_REGISTRATION_REQUIRED")
        with self._db() as db:
            self._authorize(role, capability, self._data(db))

    def _authorize(self, role, capability, data):
        _evaluator(role, capability)
        if not hmac.compare_digest(capability.binding(self._root), data["authority_binding"]):
            raise HeldOutError("G6_E_RSI_EVALUATOR_CAPABILITY_REQUIRED")

    @property
    def detector_names(self) -> tuple[str, ...]:
        with self._db() as db:
            return tuple(self._registration(db)["detectors"])

    def _check_root(self):
        if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
               for p in (self._root, *self._root.parents)):
            raise HeldOutError("G6_E_RSI_STORE_LINK")
        for name in ("rule.json", "pool.json", "state.sqlite3"):
            path = self._root / name
            if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
                raise HeldOutError("G6_E_RSI_STORE_LINK")

    @contextmanager
    def _db(self):
        self._check_root()
        db = sqlite3.connect(self._root / "state.sqlite3", isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @classmethod
    def register(cls, root: str | Path, pool: Sequence[TaskSpec], *, seed: int,
                 detectors: Sequence[str], role: str,
                 capability: EvaluatorCapability | None = None) -> HeldOutSlicer:
        _evaluator(role, capability)
        if (type(seed) is not int or not detectors or len(set(detectors)) != len(detectors)
                or any(type(d) is not str or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", d) for d in detectors)
                or not pool or any(not isinstance(t, TaskSpec) for t in pool)):
            raise HeldOutError("G6_E_RSI_REGISTRATION_INVALID")
        try:
            tasks = tuple(sorted((TaskSpec.from_record(t.to_record()) for t in pool), key=lambda t: t.task_id))
            for task in tasks:
                task.require_split("held_out")
        except TaskError:
            raise HeldOutError("G6_E_RSI_POOL_INVALID") from None
        if len({t.task_id for t in tasks}) != len(tasks):
            raise HeldOutError("G6_E_RSI_POOL_INVALID")
        root = Path(root)
        handle = object.__new__(cls)
        handle._root = root
        handle._capability = capability
        handle._check_root()
        root.mkdir(parents=True, exist_ok=True)
        if any((root / name).exists() for name in ("rule.json", "state.sqlite3", "pool.json")):
            raise HeldOutError("G6_E_RSI_ALREADY_REGISTERED")
        rule = {"rule": RULE, "seed": seed, "pool_digest": content_digest([t.to_record() for t in tasks]),
                "detectors": list(detectors)}
        # The rule is closed before partitioning, including on an interrupted registration.
        with (root / "rule.json").open("x", encoding="utf-8") as stream:
            stream.write(_json(rule))
        partitions = partition_pool(tasks, seed)
        positions = {t.task_id: i for i, t in enumerate(tasks)}
        parts = [[positions[t.task_id] for t in part] for part in partitions]
        partition_digest = content_digest(parts)
        data = {"tasks": [t.to_record() for t in tasks], "partitions": parts,
                "authority_binding": capability.binding(root),
                "salt": secrets.token_hex(32), "canaries": [secrets.token_hex(16) for _ in range(6)]}
        # Exclusive creation arbitrates concurrent registration before any state is published.
        with (root / "pool.json").open("x", encoding="utf-8") as stream:
            stream.write(_json(data))
        vault = Vault.seal([root / "pool.json", root / "rule.json"], ledger=None,
                           vault_id=content_digest([seed, partition_digest]))
        registration = {**rule, "partition_digest": partition_digest, "count": len(tasks),
                        "rule_sha256": hashlib.sha256((root / "rule.json").read_bytes()).hexdigest(),
                        "pool_sha256": hashlib.sha256((root / "pool.json").read_bytes()).hexdigest(),
                        "vault_digest": vault.digest, "vault_id": vault.vault_id,
                        "salt_commitment": content_digest(data["salt"])}
        with handle._db() as db:
            db.execute("CREATE TABLE registration (record TEXT NOT NULL)")
            db.execute("CREATE TABLE freezes (digest TEXT PRIMARY KEY, generation INTEGER UNIQUE, record TEXT NOT NULL)")
            db.execute("CREATE TABLE slices (id TEXT PRIMARY KEY, generation INTEGER UNIQUE, status TEXT NOT NULL, "
                       "freeze TEXT, summary TEXT, excluded TEXT NOT NULL DEFAULT '[]', screened INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE events (sequence INTEGER PRIMARY KEY, record TEXT NOT NULL)")
            db.execute("CREATE TABLE attempts (slice TEXT, key TEXT, result TEXT, PRIMARY KEY(slice, key))")
            db.execute("INSERT INTO registration VALUES (?)", (_json(registration),))
            for generation in range(6):
                sid = content_digest([registration["pool_digest"], seed, generation])
                db.execute("INSERT INTO slices (id,generation,status) VALUES (?,?,'sealed')", (sid, generation))
            handle._event(db, "registered", count=len(tasks), partition_digest=partition_digest)
        return cls(root, role=role, capability=capability)

    @staticmethod
    def _registration(db):
        row = db.execute("SELECT record FROM registration").fetchone()
        if row is None:
            raise HeldOutError("G6_E_RSI_REGISTRATION_REQUIRED")
        return json.loads(row[0])

    @staticmethod
    def _event(db, kind, **values):
        db.execute("INSERT INTO events (record) VALUES (?)", (_json({"kind": kind, **values}),))

    def _data(self, db):
        registration = self._registration(db)
        raw = (self._root / "pool.json").read_bytes()
        if (hashlib.sha256(raw).hexdigest() != registration["pool_sha256"]
                or hashlib.sha256((self._root / "rule.json").read_bytes()).hexdigest() != registration["rule_sha256"]):
            raise HeldOutError("G6_E_RSI_POOL_CHANGED")
        data = json.loads(raw)
        if "authority_binding" not in data:
            raise HeldOutError("G6_E_RSI_EVALUATOR_CAPABILITY_REQUIRED")
        self._authorize("evaluator", self._capability, data)
        return data

    def registration(self) -> dict:
        with self._db() as db:
            return self._registration(db)

    def records(self) -> list[dict]:
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT record FROM events ORDER BY sequence")]

    def record_freeze(self, record: FreezeRecord) -> str:
        """Commit the driver's receipt durably before making its generation readable."""
        if not isinstance(record, FreezeRecord):
            raise HeldOutError("G6_E_RSI_FREEZE_INVALID")
        payload = record.to_record()
        receipt = content_digest(payload)
        with self._db() as db:
            old = db.execute("SELECT digest FROM freezes WHERE generation=?", (record.generation,)).fetchone()
            if old is not None and old[0] != receipt:
                raise HeldOutError("G6_E_RSI_FREEZE_MISMATCH")
            if old is None:
                db.execute("INSERT INTO freezes VALUES (?,?,?)", (receipt, record.generation, _json(payload)))
                self._event(db, "freeze", generation=record.generation, freeze_digest=receipt)
        return receipt

    @staticmethod
    def _row(db, slice_id, *, opened=False):
        if not _hex(slice_id):
            raise HeldOutError("G6_E_RSI_SLICE_INVALID")
        row = db.execute("SELECT * FROM slices WHERE id=?", (slice_id,)).fetchone()
        if row is None:
            raise HeldOutError("G6_E_RSI_SLICE_INVALID")
        if opened and row["status"] != "open":
            raise HeldOutError("G6_E_RSI_SLICE_CLOSED")
        return row

    @staticmethod
    def _tasks(data, generation, freeze_digest):
        tasks = [TaskSpec.from_record(data["tasks"][index]) for index in data["partitions"][generation]]
        return tuple(sorted(tasks, key=lambda t: content_digest([freeze_digest, data["salt"], t.task_id])))

    def cut(self, generation: int, freeze_digest: str) -> Slice:
        if type(generation) is not int or not 0 <= generation < 6 or not _hex(freeze_digest):
            raise HeldOutError("G6_E_RSI_FREEZE_INVALID")
        with self._db() as db:
            freeze = db.execute("SELECT generation FROM freezes WHERE digest=?", (freeze_digest,)).fetchone()
            if freeze is None:
                raise HeldOutError("G6_E_RSI_FREEZE_REQUIRED")
            if freeze[0] != generation:
                raise HeldOutError("G6_E_RSI_FREEZE_MISMATCH")
            row = db.execute("SELECT * FROM slices WHERE generation=?", (generation,)).fetchone()
            if row["status"] != "sealed":
                raise HeldOutError("G6_E_RSI_SLICE_REUSED")
            data = self._data(db)
            tasks = self._tasks(data, generation, freeze_digest)
            if not tasks:
                raise HeldOutError("G6_E_RSI_SLICE_EMPTY")
            seed_commitment = content_digest([freeze_digest, data["salt"]])
            item = Slice(row["id"], generation, freeze_digest, len(tasks), seed_commitment,
                         content_digest([t.to_record() for t in tasks]))
            db.execute("UPDATE slices SET status='open', freeze=?, summary=? WHERE id=?",
                       (freeze_digest, _json(item.to_record()), row["id"]))
            self._event(db, "cut", **item.to_record())
            return item

    def status(self, slice_id: str) -> str:
        with self._db() as db:
            return self._row(db, slice_id)["status"]

    def spent(self, slice_id: str) -> bool:
        return self.status(slice_id) in ("spent", "void")

    def spend(self, slice_id: str):
        with self._db() as db:
            self._row(db, slice_id, opened=True)
            db.execute("UPDATE slices SET status='spent' WHERE id=?", (slice_id,))
            self._event(db, "spent", slice_id=slice_id)

    def evaluator_items(self, slice_id: str, *, role: str,
                        capability: EvaluatorCapability | None = None) -> tuple[TaskSpec, ...]:
        _evaluator(role, capability)
        with self._db() as db:
            row = self._row(db, slice_id, opened=True)
            data = self._data(db)
            self._authorize(role, capability, data)
            excluded = set(json.loads(row["excluded"]))
            marker = data["canaries"][row["generation"]]
            return tuple(replace(t, prompt=t.prompt + "\n# " + marker, provenance=None)
                         for i, t in enumerate(self._tasks(data, row["generation"], row["freeze"])) if i not in excluded)

    def canaries(self, slice_id: str, *, role: str,
                 capability: EvaluatorCapability | None = None) -> tuple[str, ...]:
        _evaluator(role, capability)
        with self._db() as db:
            row = self._row(db, slice_id, opened=True)
            data = self._data(db)
            self._authorize(role, capability, data)
            return (data["canaries"][row["generation"]],)

    def sealed(self, slice_id: str, *, role: str,
               capability: EvaluatorCapability | None = None) -> SealedSplit:
        _evaluator(role, capability)
        with self._db() as db:
            row = self._row(db, slice_id, opened=True)
            data, registration = self._data(db), self._registration(db)
            self._authorize(role, capability, data)
            vault = Vault.seal([self._root / "pool.json", self._root / "rule.json"], ledger=None,
                               vault_id=registration["vault_id"])
            if vault.digest != registration["vault_digest"]:
                raise HeldOutError("G6_E_RSI_POOL_CHANGED")
            refs = tuple(MappingProxyType({
                "case_id": _public_ref(data, "item-id", t.task_id),
                "input_sha256": _public_ref(data, "item-input", t.prompt),
                "expected_sha256": _public_ref(data, "item-expected", _case(t).expected),
            })
                         for t in self._tasks(data, row["generation"], row["freeze"]))
            return SealedSplit(vault, refs)

    def _void(self, db, slice_id):
        changed = db.execute("UPDATE slices SET status='void' WHERE id=? AND status!='void'", (slice_id,))
        if changed.rowcount:
            self._event(db, "contamination", slice_id=slice_id)

    def _canary_incidents(self, db, data, texts, current):
        matched = canary_matches(data["canaries"], texts)
        if matched:
            ids = {current} if current is not None else set()
            ids.update(row[0] for i in matched for row in db.execute("SELECT id FROM slices WHERE generation=?", (i,)))
            for sid in sorted(ids):
                self._void(db, sid)
        return bool(matched)

    def _commit_incidents(self, slice_id, training=(), model_outputs=()):
        texts = scan_texts(training, model_outputs, strict=False)
        with self._db() as db:
            data = self._data(db)
            row = (db.execute("SELECT status FROM slices WHERE id=?", (slice_id,)).fetchone()
                   if _hex(slice_id) else None)
            current = slice_id if row is not None else None
            hit = self._canary_incidents(db, data, texts, current)
            return hit, row[0] if row is not None else None

    def screen(self, slice_id: str, training: Sequence[SuiteCase], *, membership: Mapping[str, MembershipProbe],
               model_outputs: Sequence[str] = (), canary_probes: Sequence[CanaryProbe] = ()) -> ScreenReport:
        hit, previous = self._commit_incidents(slice_id, training, model_outputs)
        with self._db() as db:
            row = self._row(db, slice_id, opened=not hit)
            if hit and previous != "open":
                raise HeldOutError("G6_E_RSI_SLICE_CLOSED")
            data = self._data(db)
            cases = tuple(_case(t) for t in self._tasks(data, row["generation"], row["freeze"]))
            result = screen(training, cases, membership=membership, model_outputs=model_outputs,
                            canaries=tuple(data["canaries"]), canary_probes=canary_probes)
            excluded = sorted(set(json.loads(row["excluded"])) | set(result.excluded_indices))
            db.execute("UPDATE slices SET excluded=?, screened=1 WHERE id=?", (_json(excluded), slice_id))
            self._event(db, "screen", slice_id=slice_id, **result.to_record())
            if result.voided and not hit:
                self._void(db, slice_id)
            return result

    def evaluate_pass(self, slice_id: str, case_id: str, output: str, *, arm: str, attempt: str,
                      anchor_pass: bool, detectors: Mapping[str, Callable[[TaskSpec, str], bool]] | None) -> PassResult:
        """Run the registered cheap detectors uncapped; True means a hack was found."""
        hit, _ = self._commit_incidents(slice_id, model_outputs=(output,))
        if (type(output) is not str or type(case_id) is not str or type(anchor_pass) is not bool
                or type(arm) is not str or not arm or type(attempt) is not str or not attempt):
            raise HeldOutError("G6_E_RSI_PASS_INVALID")
        with self._db() as db:
            row = self._row(db, slice_id)
            data = self._data(db)
            key = _public_ref(data, "attempt", case_id, arm, attempt)
            # A leaked marker is still an incident on a duplicate or closed attempt.
            if hit:
                result = PassResult(False, "contamination", 0)
                self._event(db, "pass", slice_id=slice_id, attempt_digest=key, **asdict(result))
                return result
            if row["status"] != "open":
                raise HeldOutError("G6_E_RSI_SLICE_CLOSED")
            tasks = self._tasks(data, row["generation"], row["freeze"])
            found = [(i, t) for i, t in enumerate(tasks) if t.task_id == case_id]
            if not found:
                raise HeldOutError("G6_E_RSI_CASE_INVALID")
            if db.execute("SELECT 1 FROM attempts WHERE slice=? AND key=?", (slice_id, key)).fetchone():
                raise HeldOutError("G6_E_RSI_PASS_REUSED")
            names = tuple(self._registration(db)["detectors"])
            reason, count = "passed", 0
            if not row["screened"]:
                reason = "screen_missing"
            elif found[0][0] in json.loads(row["excluded"]):
                reason = "item_excluded"
            else:
                census, observations = detector_census(found[0][1], output, names, detectors)
                reason, count = census.reason, census.census_count
                if observations:
                    self._event(db, "detector_census", slice_id=slice_id, attempt_digest=key,
                                detector_count=count, clean_count=sum(o["clean"] for o in observations),
                                output_digest=content_digest(output), detectors=list(observations))
            if reason == "passed" and not anchor_pass:
                reason = "anchor_failed"
            result = PassResult(reason == "passed", reason, count)
            db.execute("INSERT INTO attempts VALUES (?,?,?)", (slice_id, key, _json(asdict(result))))
            self._event(db, "pass", slice_id=slice_id, attempt_digest=key, **asdict(result))
            return result
