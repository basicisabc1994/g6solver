# SPDX-License-Identifier: Apache-2.0
"""Resolve host observations in G6's existing run store; ingestion never writes it."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import platform
import uuid

from g6.core.ledger import ChainLedger, digest
from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
from g6.workforce import receipt_store

RUN_SCHEMA = "g6.observed_run/1"
CHECKER_SCHEMA = "g6.checker_run/1"


@dataclass(frozen=True)
class RunReference:
    receipt_id: str
    artifact_sha256: str


@dataclass(frozen=True)
class FormalRequest:
    """The exact checker question and its subject; empty premise lists remain explicit."""

    statement: str
    checker: str
    version: str
    definitions: tuple[str, ...]
    axioms: tuple[str, ...]
    encoding: str
    subject: str
    standard: str
    predicates: tuple[str, ...]
    check_id: str
    check_sha256: str
    environment_sha256: str

    def __post_init__(self) -> None:
        text = (self.statement, self.checker, self.version, self.encoding, self.subject, self.standard,
                self.check_id, self.check_sha256, self.environment_sha256)
        lists = (self.definitions, self.axioms, self.predicates)
        if (not all(type(value) is str and value.strip() for value in text)
                or not all(type(value) is tuple and all(type(part) is str and part.strip() for part in value)
                           for value in lists) or not self.predicates):
            raise ValueError("a checker request has explicit text fields and tuple premises and predicates")

    @property
    def sha256(self) -> str:
        return digest(asdict(self))


def resolve(ref: RunReference | None) -> tuple[dict, int] | None:
    """A matching body and its host-store position, or unknown. No caller ledger is accepted."""
    if not isinstance(ref, RunReference):
        return None
    try:
        status, body = receipt_store.check(ref.receipt_id, artifact_digest=ref.artifact_sha256)
    except (ExecutionInterrupted, ContainmentFailure):
        raise
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if status != receipt_store.MATCHED or not isinstance(body, dict):
        return None
    if body.get("schema") not in (RUN_SCHEMA, CHECKER_SCHEMA, receipt_store.SCHEMA) or body.get("source_after") != ref.artifact_sha256:
        return None
    try:
        entry = ChainLedger(receipt_store.store_path()).entry(receipt_store.KIND, ref.receipt_id)
        if entry["body_sha256"] != digest(body) or type(entry["seq"]) is not int:
            return None
        if body.get("schema") == receipt_store.SCHEMA:
            observed = body.get("observed")
            if type(observed) is not dict or set(observed) != {"checks", "environment_sha256"}:
                return None
            body = {"schema": RUN_SCHEMA, "source_before": body["source_before"],
                    "source_after": body["source_after"], **observed}
        return body, entry["seq"]
    except (ExecutionInterrupted, ContainmentFailure):
        raise
    except (OSError, ValueError, KeyError, TypeError):
        return None


def run_position(ref: RunReference | None, *, check_id: str, check_sha256: str,
                 environment_sha256: str, result: str, subject: str | None = None) -> int | None:
    """Resolve an exact check, result, code, environment and (when required) subject."""
    held = resolve(ref)
    if held is None or (subject is not None and ref.artifact_sha256 != subject):
        return None
    body, position = held
    checks = body.get("checks")
    if (body.get("environment_sha256") != environment_sha256 or type(checks) is not dict
            or checks.get(check_id) != {"sha256": check_sha256, "result": result}):
        return None
    return position


def checker_matches(request: FormalRequest | None, receipt: str, *, subject: str, standard: str,
                    predicates: tuple[str, ...], check_id: str, check_sha256: str, environment_sha256: str,
                    model_sha256: str, result: str) -> bool:
    if not isinstance(request, FormalRequest) or model_sha256 != request.sha256:
        return False
    if (request.subject, request.standard, request.predicates, request.check_id, request.check_sha256,
            request.environment_sha256) != (subject, standard, predicates, check_id, check_sha256, environment_sha256):
        return False
    held = resolve(RunReference(receipt, subject))
    return bool(held and held[0].get("schema") == CHECKER_SCHEMA
                and held[0].get("request") == _json_request(request)
                and held[0].get("result") == result and result in ("pass", "fail"))


def _json_request(request: FormalRequest) -> dict:
    # Canonical JSON has arrays, while the immutable request keeps tuples.
    return {**asdict(request), "definitions": list(request.definitions), "axioms": list(request.axioms),
            "predicates": list(request.predicates)}


def run_environment(found: dict) -> dict:
    return {"isolation": found.get("isolation"), "python": found.get("python"),
            "interpreter_source": found.get("interpreter_source"), "repo_env": found.get("repo_env"),
            "env_set_by_g6": found.get("env_set_by_g6"), "host_python_version": platform.python_version(),
            "platform": platform.platform()}


def _record_case(*, artifact: str, environment_sha256: str, checks: dict[str, dict[str, str]]) -> str:
    """Host-producer seam, called after execution; never called by either ingestion adapter."""
    return receipt_store._record({"schema": RUN_SCHEMA, "execution_id": uuid.uuid4().hex,
        "source_before": artifact, "source_after": artifact, "environment_sha256": environment_sha256,
        "checks": checks})


def _pytest_observation(repo, before: dict, after: dict, requested: list[str], found: dict) -> dict | None:
    """Record the host runner's typed telemetry, never console text or pytest test utilities.

    Product requirement checks run through this runner. Its producer-owned reader
    decides body outcomes and loading custody before any observation is stored.
    """
    from g6.workforce import repo_tests

    established = repo_tests.established(found, repo=repo)
    report = established.report
    if (established.withheld or repo_tests._moved(before, after)
            or not (found.get("isolation") or {}).get("backend")):
        return None
    checks = {}
    for check in {*requested, *report.nodes}:
        path = repo_tests._disk(repo, check.partition("::")[0])
        if path is None or not path.is_file() or repo_tests._linked(path):
            continue
        sha = repo_tests._digest(path)
        if len(sha) != 64:
            continue
        checks[check] = {"sha256": digest([check, sha]), "result": {
            "passed": "pass", "failed": "fail"}.get(established.of(check), "unknown")}
    if not checks:
        return None
    return {"environment_sha256": digest(run_environment(found)), "checks": checks}


def _observe_pytest(repo, before: dict, after: dict, requested: list[str], found: dict) -> str:
    observed = _pytest_observation(repo, before, after, requested, found)
    return _record_case(artifact=digest(before), **observed) if observed is not None else ""
