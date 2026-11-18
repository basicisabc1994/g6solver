# Learn and adapt

Use `g6_failure_record(kind, family, case_json, note)` to report a failure or user request. The JSON object is a
non-held-out case; the report is self-observed field data, never independent acceptance evidence.

Use `g6_library(op="list")` to inspect current methods. `op="describe"` and `op="history"` take an entry_id.
Describe follows that candidate's current version; history keeps the decisions visible. The local CLI equivalents
are `g6 library ls` and `g6 library show <entry_id>`. These reads run no methods.

Library methods are checked and accepted with measured precision, never proved. A0 and A1 concerns can establish at
most insufficient_evidence. An admitted A2 concern can establish defect_detected and blocked-escalated, naming the
method and its held-out precision lower bound. no_concern never passes any requirement. Preserve provenance library,
authority, measured precision and scope when reporting a finding. G6's static integrity findings retain precedence.

Use `g6_library_decide(entry_id, action, reason)` to ask the person to demote or withdraw through their client's
dialog. Never answer that dialog. A decline, cancellation, missing dialog or changed entry changes nothing.
Restoration requires independent reacceptance; this surface cannot restore authority.

The later-wave routes are registered for discovery but return G6_E_DISTILL_UNAVAILABLE until their backends ship:
`g6_distill_start(family, failure_ids="")`, `g6_distill_job(job_id, detail="compact")`,
`g6_self_model(section="", detail="compact")`, `g6_simulate(action_json)`, `g6_extend(request, repo="")`, and
`g6_research_start`, `g6_research_stop`, `g6_research_status`, `g6_research_digest` (folder defaults to empty;
stop also accepts reason). The research runner is separate from the older improvement loop. Start requires its own
client dialog when implemented. CLI `g6 research start|stop|status` and `g6 self-model` disclose the same unavailable
backend. An unavailable backend is pending implementation, never evidence of a completed job or a healthy runner.

Acceptance remains in `distill/acceptance_gate.py`; no method or user request can change its frozen cases or policy.
Predictions and simulations never become acceptance evidence. Review methods use confined execution; missing probes
or unsupported feature semantics abstain. Learned scope never reports a pass from an A0/A1 method or silence.
