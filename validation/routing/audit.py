# SPDX-License-Identifier: Apache-2.0
"""Read-only audit scoring of kept evidence. No client, model or held-out check is executed.

Run --check before any arm. --records reads the audit recording envelope described in README.md; stdout is the
report. Defect adjudications are independent observations, not a model's self-assessment or a release decision.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path) -> Any:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


freeze = _load("routing_audit_freeze", HERE.parent / "freeze.py")
score = _load("routing_audit_score", HERE / "score.py")


def load_registration() -> dict[str, Any]:
    """Bind the additive protocol and every new fixture/check byte, plus the untouched prior registration."""
    pins = freeze.read(HERE / "audit_frozen.json")
    amendment = freeze.read(HERE / "amendments/2026-11-17/audit_measures.json")
    if amendment["pins"] != pins or not amendment["before_any_arm"] or amendment["date"] != "2026-11-17":
        raise freeze.FrozenError("audit amendment differs from its pins or pre-arm date")
    old = freeze.bound(HERE / "moment_frozen.json", pins["previous_moment_pins_digest"])
    if old != amendment["previous_pins"]:
        raise freeze.FrozenError("audit amendment changed the previous moment pins")
    for name, key in (("moment_tasks.json", "tasks_digest"), ("moment_gates.json", "bars_digest"),
                      ("moment_outcome_gates.json", "outcome_bars_digest"), ("moment_withheld.json", "withheld_digest")):
        freeze.bound(HERE / name, old[key])
    if freeze.tree_digest(HERE / "moment_outcome") != old["checks_digest"]:
        raise freeze.FrozenError("previous moment checks changed")
    protocol = freeze.bound(HERE / "audit_protocol.json", pins["protocol_digest"])
    retention = freeze.bound(HERE / "retention_tasks.json", pins["retention_tasks_digest"])
    for folder, key in (("retention_fixtures", "fixtures_digest"), ("retention_outcome", "checks_digest")):
        if freeze.tree_digest(HERE / folder) != pins[key]:
            raise freeze.FrozenError(f"audit {folder} digest differs from its freeze")
    for task in [*protocol["tasks"], *retention["tasks"]]:
        path = HERE / task["snapshot"]
        if freeze.tree_digest(path) != task["fixture_digest"]:
            raise freeze.FrozenError(f"audit fixture digest differs for {task['id']}")
    return {**retention, "audit_tasks": protocol["tasks"], "protocol": protocol, "pins": pins}


def _artifact(folder: Path, digest: Any) -> bytes:
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise freeze.FrozenError("audit artifact requires a plain sha256 digest")
    path = folder / digest
    if path.is_symlink() or not path.is_file():
        raise freeze.FrozenError("audit artifact digest is absent or a link")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise freeze.FrozenError("audit artifact bytes differ from their digest")
    return data


def _labels(content: Any) -> set[str]:
    """Exact structured labels in a G6 reply, not words in the agent's prose."""
    found = set()
    if isinstance(content, str):
        try:
            return _labels(json.loads(content))
        except (ValueError, RecursionError):
            return found
    if isinstance(content, list):
        for part in content:
            found.update(_labels(part))
    if isinstance(content, dict):
        for key, value in content.items():
            if key in ("completion_state", "outcome") and isinstance(value, str) and value in score.AUDIT_LABELS:
                found.add(value)
            elif key in ("text", "result", "assessment", "content", "structuredContent"):
                found.update(_labels(value))
    return found


def _reply_labels(events: str, client: str) -> list[str]:
    found, pending = set(), set()
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if client == "codex":
            item = event.get("item", {})
            if (event.get("type") == "item.completed" and isinstance(item, dict)
                    and item.get("type") == "mcp_tool_call" and str(item.get("tool", "")).startswith("g6_")):
                found.update(_labels(item.get("result")))
        elif client == "claude_code":
            message = event.get("message", {})
            parts = message.get("content", []) if isinstance(message, dict) else []
            for part in parts if isinstance(parts, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use" and "__g6_" in str(part.get("name", "")):
                    pending.add(str(part.get("id")))
                if part.get("type") == "tool_result" and str(part.get("tool_use_id")) in pending:
                    found.update(_labels(part.get("content")))
    return sorted(found)


def read_record(folder: Path, row: dict[str, Any], *, pins: dict[str, str] | None = None) -> dict[str, Any]:
    """Read artifact bytes again; ignore row-supplied checks, claims, labels, completion and cost.

    The adjudication artifact is produced independently, with hidden-check results on the reviewed snapshot and
    final diff. Its digest binds those results to this session. Hashes establish identity, not adjudicator honesty.
    """
    result = {**row, "record_sha256": freeze.digest(row)}
    events = _artifact(folder, row["events_sha256"]) if row.get("events_sha256") is not None else None
    if row.get("final_diff_sha256") is not None:
        _artifact(folder, row["final_diff_sha256"])
    for key in ("g6_history_before_sha256", "g6_history_after_sha256"):
        if row.get(key) is not None:
            _artifact(folder, row[key])
    observation = json.loads(_artifact(folder, row["adjudication_sha256"]))
    if pins is not None and observation.get("pins") != pins:
        raise freeze.FrozenError("audit adjudication does not bind the current registration")
    for key in ("task_id", "task_digest", "events_sha256", "final_diff_sha256"):
        if observation.get(key) != row.get(key):
            raise freeze.FrozenError(f"audit adjudication differs in {key}")
    if any(value is not None for value in observation["defect_checks"].values()):
        _artifact(folder, observation.get("reviewed_diff_sha256"))
    for key in ("final_checks", "defect_checks", "g6_claims"):
        result[key] = observation[key]
    text = events.decode("utf-8") if events is not None else ""
    result["reply_states"] = _reply_labels(text, row["client"])
    runner = _load("routing_audit_event_reader", HERE / "run.py")
    if row["client"] not in runner.CLIENTS:
        raise score.TaskError("audit client is not registered")
    result["completed"] = runner.reading(row["client"], events, row.get("exit_code"))["state"] == "completed"
    calls = runner.CLIENTS[row["client"]].parse(text)[0]
    if row["arm"] == "alone" and any(call["tool"].startswith("g6_") for call in calls):
        raise score.TaskError("audit alone event log contains a G6 call")
    result["cost_usd"], result["model_calls"] = None, None
    if row.get("usage_sha256") is not None:
        usage = json.loads(_artifact(folder, row["usage_sha256"]))
        if usage.get("events_sha256") != row.get("events_sha256") or usage.get("includes_g6") is not True:
            raise freeze.FrozenError("audit usage must bind the event log and include G6 usage")
        result["cost_usd"], result["model_calls"] = usage["cost_usd"], usage["model_calls"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--records", type=Path)
    args = parser.parse_args()
    registration = load_registration()
    if args.check:
        print(json.dumps({"freeze": "pass", "pairs": len(registration["pairs"]), "before_any_arm": True}))
        return
    if args.records is None:
        parser.error("supply --check or --records")
    envelope = freeze.read(args.records)
    if envelope.get("format") != "g6.routing_audit_records/1" or envelope.get("pins") != registration["pins"]:
        raise freeze.FrozenError("audit records do not bind the active registration")
    rows = [read_record(args.records.parent / "artifacts", row, pins=registration["pins"])
            for row in envelope["records"]]
    tasks, clients = registration["audit_tasks"], ("claude_code", "codex")
    allowed = {task["id"] for task in [*tasks, *registration["tasks"]]}
    if any(row.get("task_id") not in allowed or row.get("arm") not in score.AUDIT_ARMS
           or row.get("client") not in clients for row in rows):
        raise score.TaskError("audit record names an unregistered task, client or arm")
    moment_ids = {task["id"] for task in tasks}
    stages = {task["id"]: task["stage"] for task in registration["tasks"]}
    for row in rows:
        arms = ("alone", "with_g6") if row["task_id"] in moment_ids else \
            ("with_g6",) if stages[row["task_id"]] == "a" else ("retained", "fresh")
        if row["arm"] not in arms:
            raise score.TaskError("audit record uses the wrong arm for its task stage")
    reports = {}
    for client in clients:
        moment = [row for row in rows if row["task_id"] in moment_ids]
        arms = {arm: score.audit_report(tasks, moment, client=client, arm=arm) for arm in ("alone", "with_g6")}
        reports[client] = {"arms": arms, "paired": score.compare_audit(arms["alone"], arms["with_g6"]),
                           "retention": score.retention_report(registration["tasks"], registration["pairs"],
                                                               [row for row in rows if row not in moment], client=client)}
    print(json.dumps({"clients": reports, "claim": None}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
