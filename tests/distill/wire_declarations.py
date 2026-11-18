"""Test-only H1 declarations; never a runtime substitute for owner issuance."""
from contextlib import contextmanager
from unittest.mock import patch

CLASSES = {
    "g6_failure_record": ("client_request", "client_repository"),
    "g6_distill_start": ("client_request", "client_repository", "g6_operational"),
    "g6_distill_job": ("g6_operational",),
    "g6_library": ("g6_operational",),
    "g6_library_decide": ("client_request",),
    "g6_self_model": ("g6_operational",),
    "g6_simulate": ("client_request", "g6_operational"),
    "g6_extend": ("client_request", "client_repository"),
    **{f"g6_research_{op}": ("source:improvement",) for op in ("start", "stop", "status", "digest")},
}
ARGUMENTS = {
    "g6_failure_record": {"kind": "content", "family": "content", "case_json": "content", "note": "content"},
    "g6_distill_start": {"family": "content", "failure_ids": "pointer"},
    "g6_distill_job": {"job_id": "pointer", "detail": "content"},
    "g6_library": {"op": "content", "entry_id": "pointer"},
    "g6_library_decide": {"entry_id": "pointer", "action": "content", "reason": "content"},
    "g6_self_model": {"section": "content", "detail": "content"},
    "g6_simulate": {"action_json": "content"},
    "g6_extend": {"request": "content", "repo": "pointer"},
    "g6_research_start": {"folder": "pointer"},
    "g6_research_stop": {"folder": "pointer", "reason": "content"},
    "g6_research_status": {"folder": "pointer"},
    "g6_research_digest": {"folder": "pointer"},
}


@contextmanager
def issued():
    from g6.core.consent_declarations import TOOLS, TOOL_ARGUMENTS, contract
    with patch.dict(TOOLS, {name: contract(*classes) for name, classes in CLASSES.items()}), \
            patch.dict(TOOL_ARGUMENTS, ARGUMENTS):
        yield
