# Learn and adapt

Use `g6_failure_record(kind, family, case_json, note)` to report a failure or user request. The JSON object is a
non-held-out case; the report is self-observed field data, never independent acceptance evidence. To let G6 learn
from it, the case holds `unit_id`, `provenance`, `files`, `target`, `changed_lines`, `before_tests`, `after_tests`
and `known_wrong`. `g6_distill_start(family, failure_ids="")` (it runs code, so the person allows it) then labels
each such case by its own confined oracle run; only a case the oracle labels weakened becomes an independent
training failure. A record without that case is refused (G6_E_SYNTH_TRAINING_ONLY) with the fields it needs;
without the person's allowance start refuses G6_E_SENSITIVE_COMPONENT_DENIED, and with no operating-system boundary
the job reports G6_E_ISOLATION_UNAVAILABLE and labels nothing. Poll `g6_distill_job(job_id)`.
Concept proposal needs a host model caller; without one the job reports G6_E_SYNTH_NO_CALLER. A method learnt from
your cases stays local and needs a registered acceptance cut before it can gain authority.

Use `g6_library(op="list")` to inspect current methods. `op="describe"` and `op="history"` take an entry_id.
Describe follows that candidate's current version; history keeps the decisions visible. These reads run no methods.

Library methods are checked and accepted with measured precision, never proved. A0 and A1 concerns can establish at
most insufficient_evidence. An admitted A2 concern can establish defect_detected and blocked-escalated, naming the
method and its held-out precision lower bound. no_concern never passes any requirement. Preserve provenance library,
authority, measured precision and scope when reporting a finding. G6's static integrity findings retain precedence.

Use `g6_library_decide(entry_id, action, reason)` to ask the person to demote or withdraw through their client's
dialog. Never answer that dialog. A decline, cancellation, missing dialog or changed entry changes nothing.
Restoration requires independent reacceptance; this surface cannot restore authority.

`g6_self_model(section="", detail="compact")` reads G6's measured state; a stale or unread field is unknown.
`g6_simulate(action_json)` forecasts an action on G6's own packages (components such as `g6.distill`) from its
import graph and protected set; with no recorded earlier actions of that kind the forecast stays unknown. Gate and
plan readouts are measured by G6, never supplied: an action that carries them, or one that does not parse,
is refused with G6_E_DISTILL_INPUT. `g6_extend(request, repo="")` records a capability request.
`g6_research_status`, `g6_research_digest`, `g6_research_stop` and `g6_research_start` (folder defaults to empty;
stop also accepts reason) drive the research runner, separate from the older improvement loop. Research authority
is off until the owner grants it through issuance, and a host runner must be attached; start refuses with
G6_E_RESEARCH_AUTHORITY or G6_E_RESEARCH_HOST_REQUIRED until then, then asks in the client's dialog. A refusal is
never evidence of a completed job or a healthy runner. Replies that carry stored G6 data (jobs, research state,
component data) reach your client only after the person shares that source: G6_E_CLOUD_CONSENT_REQUIRED names it,
and `g6_cloud_grant` asks them. These tools are MCP only; the CLI has no equivalent.

Acceptance remains in `distill/acceptance_gate.py`; no method or user request can change its frozen cases or policy.
Predictions and simulations never become acceptance evidence. Review methods use confined execution; missing probes
or unsupported feature semantics abstain. Learned scope never reports a pass from an A0/A1 method or silence.
