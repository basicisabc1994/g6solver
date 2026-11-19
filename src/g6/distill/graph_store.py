# SPDX-License-Identifier: Apache-2.0
"""Content-addressed execution rows in an append-only chain."""
from pathlib import Path

from pydantic import JsonValue, TypeAdapter

from g6.core.ledger import ChainLedger, LedgerCorrupt, canonical, digest
from g6.core.storage import state_path

from .graph_records import ExecutionRecord
from .records import Sha256


class ExecutionStore:
    def __init__(self, path: Path | None = None, *, expect_head=None):
        self.path = Path(path) if path is not None else state_path("distill", "exec")
        self.chain = ChainLedger(self.path / "runs.jsonl", expect_head=expect_head, validate=self._validate)

    def _validate(self, kind, ident, body, seen):
        try:
            row = ExecutionRecord.model_validate_json(canonical(body))
            if kind != "execution" or ident != row.run_id:
                return ["execution identity mismatch"]
            for run in row.runs:
                if run.trace_ref is not None:
                    self.get_trace(run.trace_ref)
        except ValueError as error:
            return [str(error)]
        return []

    def put_trace(self, trace):
        value = TypeAdapter(JsonValue).validate_python(trace)
        ident = digest(value)
        target = self.path / "traces" / f"{ident}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        text = canonical(value) + "\n"
        if target.exists():
            if target.read_text(encoding="utf-8") != text:
                raise LedgerCorrupt("trace object differs from content id")
        else:
            with target.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        return ident

    def get_trace(self, ident):
        import json
        TypeAdapter(Sha256).validate_python(ident)
        target = self.path / "traces" / f"{ident}.json"
        try:
            text = target.read_text(encoding="utf-8")
            value = json.loads(text)
            if digest(value) != ident or text != canonical(value) + "\n":
                raise ValueError("trace content differs")
        except (ValueError, FileNotFoundError) as error:
            raise LedgerCorrupt(f"invalid trace object: {error}") from error
        return value

    def put(self, record):
        row = ExecutionRecord.model_validate_json(record.model_dump_json())
        self.path.mkdir(parents=True, exist_ok=True)  # an explicit store folder may not exist yet
        self.chain.reload()
        self.chain.append("execution", row.run_id, row.model_dump(mode="json"))
        return row.run_id

    def list(self):
        self.chain.reload()
        return [ExecutionRecord.model_validate(row["body"]) for row in self.chain.entries()]

    def get(self, run_id):
        for row in self.list():
            if row.run_id == run_id:
                return row
        raise KeyError(run_id)
