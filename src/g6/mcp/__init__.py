# SPDX-License-Identifier: Apache-2.0
"""g6-mcp — a stdio MCP server exposing G6's progressive discovery surface.

Six discovery tools: ``g6_help``, ``g6_find``, ``g6_describe``, ``g6_invoke``, ``g6_components`` and ``g6_compose``;
``g6_review_patch``, which assesses a Python patch against stated requirements in confined test runs, as a job; and
thirteen meta-harness tools (``metaharness_start``, ``_confirm``, ``_propose_code``, ``_evaluate_start``, ``_job``, ``_run``,
``_inspect``, ``_propose``, ``_evaluate``, ``_decide``, ``_diagnose``, ``_approve``, ``_rollback``) for the workflows
declared in the file ``G6_METAHARNESS`` names, where confirmation, approval and rollback ask the person through the
client, including in the proposing session, and a failed attempt carries its recovery (``g6.metaharness.surface``).
Install
with ``pip install "g6[mcp] @ git+https://github.com/basicisabc1994/g6solver"`` and register with ``claude mcp add g6 -- g6-mcp`` (or, for Codex,
``codex mcp add g6 -- g6-mcp``). There is no account, key or network service.

An op that needs a model uses the one the person set in their key file (``G6_LLM_PROVIDER`` in ``~/.g6/.env``, for
example ``claude_code``, their own Claude Code sign-in): ``g6_invoke`` builds it for each call and gives it only to a
component that declares ``__g6_model__``. With none set, such an op says how to set one, and the agent can do that
step itself and call the op that checks it.

Ops a component marks sensitive (they run a program on your machine, reach the network or change it) are refused on
this surface unless the person who runs the server lists them in ``G6_MCP_SENSITIVE``, a comma-separated list of
component names (``lean``) or single ops (``lean.check_proof``), set where the server is registered, for example
``claude mcp add g6 -e G6_MCP_SENSITIVE=lean -- g6-mcp``. There is no wildcard, and the agent cannot set it: the
server reads it from its own environment. ``g6_describe`` says which sensitive ops this server allows. Before you list
one, know this (the refusal says it too): Where no operating-system boundary can confine it (macOS, Linux without
bubblewrap, and Windows before the one-time access grant), G6 refuses to run untrusted code for you (a patch, a
generated script, a repository's tests) unless you allow it once with g6 setup. Code you allow then has your own
rights: it can read your key file ~/.g6/.env and your other files, and its network guard works inside Python only. You
are responsible for what you run.

Every ``g6_invoke`` reply carries the library envelope (``completion_state``, ``warning_card``,
``evidence``, ``request_id``, ``run_id``) beside ``ok`` and ``result`` / ``error`` (``g6.core.door``). The door
never raises a state: a failure is ``blocked-escalated`` with its ``G6_E_*`` code; a result is the state its
component reported, ``qualified-draft`` when it reported none, and ``verified`` only when G6's own code re-runs a
check at the door and reproduces it. Any other ``verified`` is held at ``qualified-draft`` with a
``G6_E_PROMOTION_LAW`` card and ``claimed_completion_state``, and the ``result`` copy says the same. ``ok`` is false
whenever the reply's state is ``blocked-escalated``: with no result, ``error`` says why; with a blocked result, its card
does. G6 re-checks artifacts with its own code; it does not defend against malicious code in its own process.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

# The server's own setting, the sensitive components or ops (``component.op``) the person allows: the same setting,
# read the same way, as ``g6 invoke`` and ``g6.invoke`` (``g6.core.door``).
from g6.core.door import SENSITIVE_ENV, allowed_sensitive
from g6.core.door import is_allowed as _allowed

_DENIED = "[G6_E_SENSITIVE_COMPONENT_DENIED]"


def describe_reply(component: str, *, allow_sensitive: frozenset[str] | None = None) -> dict[str, Any]:
    """The ``g6_describe`` reply: the component's description and, where it has sensitive ops, which of them this
    server allows."""
    from g6.core import discovery

    info = discovery.describe(component)
    sensitive = info.get("sensitive_ops") or []
    if sensitive:
        allow = allowed_sensitive() if allow_sensitive is None else allow_sensitive
        info["sensitive_ops_allowed_here"] = [op for op in sensitive if _allowed(component, op, allow)]
    return info


def invoke_reply(component: str, op: str, params_json: str = "{}", *, registry: Any = None,
                 allow_sensitive: frozenset[str] | None = None) -> dict[str, Any]:
    """The ``g6_invoke`` reply: the component's answer (or why there is none) inside the library envelope, through the
    checked call ``g6 invoke`` and ``g6.invoke`` share (``g6.core.door.invoke_checked``). A sensitive op runs only when
    ``allow_sensitive`` (by default ``G6_MCP_SENSITIVE``) lists it or its component. A component whose block takes a
    model (``__g6_model__``) is given the one the person configured, built once for the call; with none configured,
    nothing is passed."""
    from g6.core.door import blocked_reply, invoke_checked

    request_id = uuid.uuid4().hex[:12]
    try:
        params = json.loads(params_json or "{}")
    except json.JSONDecodeError as exc:
        return blocked_reply(f"[G6_E_INPUT_VALIDATION] params_json is not JSON: {exc}", request_id=request_id)
    if not isinstance(params, dict):
        return blocked_reply("[G6_E_INPUT_VALIDATION] params_json must be a JSON object", request_id=request_id)
    allow = allowed_sensitive() if allow_sensitive is None else allow_sensitive
    return invoke_checked(component, op, params, allow=allow, registry=registry, request_id=request_id,
                          how_to_allow=f"Only the person who runs this server can allow it, by adding {component}.{op} "
                                       f"(or {component}) to {SENSITIVE_ENV} where the server is registered.")


def build_server(surface: Any = None) -> Any:
    try:
        from fastmcp import Context, FastMCP
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise SystemExit("g6-mcp needs the MCP extra: pip install 'g6[mcp] @ git+https://github.com/basicisabc1994/g6solver'") from exc

    from g6.core import discovery
    from g6.core.registry import get_registry

    server = FastMCP("g6")

    @server.tool()
    def g6_help() -> str:
        """How to use G6 from an agent. Call this first."""
        return discovery.HELP

    @server.tool()
    def g6_components() -> list[str]:
        """Every installed G6 component."""
        return get_registry().names()

    @server.tool()
    def g6_find(query: str, limit: int = 5) -> list[dict]:
        """Rank components for a task description; empty list means nothing matched."""
        return discovery.find(query, limit)

    @server.tool()
    def g6_describe(component: str) -> dict:
        """Maturity, ops and declared dependencies of one component, and which of its sensitive ops this server
        allows."""
        return describe_reply(component)

    @server.tool()
    def g6_invoke(component: str, op: str, params_json: str = "{}") -> dict:
        """Run one op of a component. ``params_json`` is a JSON object of the op's parameters. The reply carries
        ``completion_state`` (verified / qualified-draft / blocked-escalated) and a ``warning_card``: read them."""
        return invoke_reply(component, op, params_json)

    @server.tool()
    def g6_compose(composition_json: str) -> dict:
        """Run explicit stages with input mappings (caller.field or 0.field). Strict contracts and honest evidence.
        JSON contains stages [{component, op, inputs?, params?}] and inputs {field: value}. No Python code."""
        from g6.mcp.composition import compose_reply
        from g6.core.door import blocked_reply
        try:
            payload = json.loads(composition_json)
        except json.JSONDecodeError:
            return blocked_reply("[G6_E_INPUT_VALIDATION] composition_json must be JSON")
        return compose_reply(payload, allow_sensitive=allowed_sensitive())

    _metaharness_tools(server, surface, Context)
    return server


def _metaharness_tools(server: Any, surface: Any, context_type: Any) -> None:
    """The meta-harness tools (META_HARNESS_PLAN.md Phase 6): additive, the Python API's envelopes as JSON. Approval
    and rollback ask the person through the client (MCP elicitation); the agent never supplies the approver."""
    from g6.metaharness.surface import Surface

    mh = surface or Surface()
    from g6.metaharness.jobs import Jobs
    jobs = Jobs()

    @server.tool()
    def g6_review_patch(repo: str, task: str, requirements: list[dict] | None = None, tests: list[str] | None = None,
                        claim: str = "", sources: dict[str, str] | None = None, base: str = "HEAD",
                        expected_artifact_digest: str = "", timeout_s: int = 300, intent: dict | None = None,
                        proposed_checks: list[dict] | None = None, template_instances: list[dict] | None = None,
                        search: dict | None = None, import_roots: list[str] | None = None) -> dict:
        """Assess the working tree's Python patch against base on stated requirements [{id, text, checks: [pytest
        node ids]}] plus extra tests, in confined runs. import_roots (at most 8 folders inside the repository, such as
        "lib") go first on the import path of every run. Optional intent, an intent contract {goal, success_criteria,
        non_goals, failure_criteria, assumptions}: each success criterion becomes a stated requirement, and each
        non-goal or failure criterion a stated obligation to keep behaviour, not established until a requirement
        with the same text states its checks; each assumption is listed with its source (explicit, inferred or
        fallback) and decides nothing. An invalid intent is refused. Optional proposed_checks, at most 20 test files
        you propose, [{id, requirement_id, intent: preserve|change, file: a relative path named test_*.py, source: its
        text, at most 64 kilobytes, test: an optional node identifier in it}]: each runs confined, with no network, in a
        copy of the patch and one of the base commit's code, and is classified (counterexample, fails_on_base_too,
        not_refuted, unconfirmed_failure for a change check that fails on the patch whatever the base gives,
        behaviour_changed or check_error). A proposed check never passes a requirement: a counterexample or a failure
        of the change gives at most insufficient_evidence, with a reproduction in next_actions: the command G6's run
        amounted to (PYTHONPATH=src where G6 put src on the path, the import roots before the top folder as G6's run
        had them, an empty configuration where the repository has none). Anything else, a conftest or a pytest configuration say, is refused. Optional template_instances,
        counted with proposed_checks within the 20, [{id, template, requirement_id, intent, bindings}], fill G6's
        reviewed templates: example.v1 (a stated input gives a stated result, error or effect), relation.v1 (a round
        trip, idempotence, invariance or monotonicity over at most 32 inputs), access_rule.v1 (allowed and refused
        cases of an operation), differential.v1 (the base and the patch agree on stated calls; a difference is a
        counterexample when intent is preserve, a question when it is change) and forbidden_dependency.v1 (the patch
        adds no import that breaks the repository's own import-linter contract, as the base commit declares it, or
        one the bindings state; read from the files, no code runs). state_invariant.v1 takes an abstract state model
        you state in the model checker's closed grammar, its actions bound to the code's operations, and how each
        variable the invariants read is observed on the real state: G6 checks the invariants (a vacuous or
        conflicting one is never searched), searches the model, and replays each trace on the real code; only a
        trace that breaks the invariants there is a finding (unconfirmed_counterexample), a trace the code keeps is
        model_only and accuses nobody, and a proof about the model leaves the requirement as it was. access_rule.v1
        also takes steps (grant, share, revoke, each with its effect) through that path. For a relation.v1 or
        differential.v1 function of whole numbers and booleans, Z3 looks for an input your list misses, re-run on the
        real code before it is reported. Each such result comes in three parts: specification, model,
        correspondence. Bindings are data: names are dotted names
        (module.attribute) G6 finds in the files, literals are JSON; anything else refuses the review. G6 compiles
        each instance into a test from the template's fixed text, run like a proposed check, which never passes a
        requirement; an instance the repository gives nothing to apply to is refused and reported in activities. A
        forbidden import against the repository's or your contract is defect_detected; a contract that holds passes
        no requirement, and reads vacuous when the patch could not have broken it. Optional search: {"templates":
        false} runs free-form checks only; {"test_adequacy": true} also mutates the changed lines and reports how many
        mutants each requirement's stated checks killed and how many survived, which changes no outcome.
        {"propose": true} has G6 ask the model you configured for G6 (G6_LLM_PROVIDER; none, no call) for checks:
        "proposer": "templates_first" (the default: template by template, then scenarios as questions, then free-form
        tests where no template applied) or "free_form" (free-form tests only), within "max_model_calls" (default
        10), "max_input_chars", "max_output_tokens" and "max_context_chars" per call, each holding for every request
        the provider receives. Its replies are untrusted data:
        what it proposes runs as proposed checks do, never passes a requirement and gives at most
        insufficient_evidence; every model call, request and reply is in assessment.completion. Every reply
        has assessment.completion: what was demonstrated, what is not established, the defects reproduced,
        preservation, test adequacy, open questions and every activity; and assessment.report, the review as
        Markdown to show the person verbatim: reproduced findings first, then what is not established, what they
        decide, how it was decided, and the standing note (it decides nothing; text from the repository in it is
        inert). Poll metaharness_job for the result.
        assessment.outcome is one of: test_integrity_compromised (a sign of a weakened test or test helper that G6's
        static comparison finds, a changed test-runner file, a git-ignored file that could change the run, a shortcut in the diff of the tests,
        their runner or continuous integration, or a run that cannot be believed); defect_detected (a stated check or extra test failed, or a test of a changed module fails
        on the patch and passed at the base commit, or the patch adds an import a stated contract forbids);
        insufficient_evidence (no requirement, a requirement with no check, a check that did not run or ran out of
        time, a run whose tests all passed by pytest's own count but whose process then exited with an error
        (run_inconsistent; exit 0 with a failure, an error or fewer tests finished is a run that cannot be believed),
        a shortcut only the claim reports, or a proposed or compiled check's unconfirmed_counterexample or
        unconfirmed_failure);
        supported_within_scope (every requirement passed its stated checks: support on those checks only, never proof
        the patch is complete; a check that also passes on the base commit's code is listed in unknowns). Each is
        qualified-draft. A refusal is blocked-escalated with no outcome. Test receipts are kept in a file owned by the
        same user as G6: they stop a caller from claiming that G6 ran a test, not a program running as that user. merge
        is always requires_separate_approval; nothing is merged. Requires host permission patch_review.review in
        G6_MCP_SENSITIVE."""
        from g6.core.door import blocked_reply
        if not _allowed("patch_review", "review", allowed_sensitive()):
            return blocked_reply(f"{_DENIED} patch_review.review runs repository code; the host must allow it")
        from g6.core import component_process
        from g6.core.stop import STOPPED
        from g6.harness.patch_review import review_patch

        def review() -> dict:
            # review_patch lets these reach an enclosing door; this job has none, so it names them here
            try:
                return review_patch(repo, task=task, requirements=requirements or [], tests=tests or [], claim=claim,
                                    sources=sources, base=base, expected_artifact_digest=expected_artifact_digest,
                                    timeout_s=timeout_s, intent=intent, proposed_checks=proposed_checks,
                                    template_instances=template_instances, search=search,
                                    import_roots=import_roots or [])
            except component_process.ExecutionInterrupted as exc:
                if exc.stopped:
                    return blocked_reply(f"[{STOPPED}] the patch review was stopped before it finished: {exc}")
                return blocked_reply(f"[G6_E_PATCH_REVIEW_INTERRUPTED] the patch review's time allowance ended before "
                                     f"it finished, so it has no outcome: {exc}")
            except component_process.ContainmentFailure as exc:
                return blocked_reply("[G6_E_PATCH_REVIEW_INTERRUPTED] G6 could not confirm that the review's test "
                                     f"processes ended, so nothing from it is used: {exc}")

        return jobs.start(review)

    def session(ctx: Any) -> str:
        return str(getattr(ctx, "session_id", None) or id(getattr(ctx, "session", ctx)))

    from g6.mcp.consent import request_consent, refusal as consent_refusal

    def metaharness_start(specification_json: str) -> dict:
        """Create a data-only Python application workflow: name, task with {input}, cases [{input, expected}],
        baseline_code {main.py: source defining solve(task)}, check (exact/contains/number), tolerance, seed.
        Does not confirm or execute it. Below 30 held-out cases is exploration only."""
        from g6.core.door import blocked_reply
        try:
            return mh.start(json.loads(specification_json))
        except json.JSONDecodeError:
            return blocked_reply("[G6_E_INPUT_VALIDATION] specification_json must be JSON")

    async def metaharness_confirm(workflow: str, workflow_digest: str, ctx: Any) -> dict:
        """Confirm the frozen task, checks, split and baseline through client-mediated consent."""
        held = mh.harness(workflow)
        if isinstance(held, dict):
            return held
        if held.workflow.digest() != workflow_digest:
            from g6.core.door import blocked_reply
            return {**blocked_reply("[G6_E_META_APPROVAL_REQUIRED] workflow changed; inspect it before confirming"),
                    "consent_status": "workflow_changed"}
        question = (f"Confirm workflow {workflow} digest {workflow_digest}? Task: {held.workflow.task}. "
                    f"Development cases: {len(held.workflow.suite.dev)}; held-out: {len(held.workflow.suite.held_out)}. "
                    "The baseline and generated candidates execute Python inside the configured boundary. "
                    "Future activation requires separate consent. Type your name; identity is recorded as given.")
        consent = await request_consent(ctx, question)
        if consent.status != "accepted":
            return consent_refusal(consent, next_action=(
                f'From the project directory, the user can inspect with g6 workflow inspect {workflow}, then '
                f'personally confirm with g6 workflow confirm {workflow} {workflow_digest} --as "Your name".'))
        return mh.confirm(workflow, workflow_digest, answer=consent.answer)

    def metaharness_propose_code(workflow: str, files_json: str, hypothesis: str, ctx: Any) -> dict:
        """Propose generated application code as {filename.py: source}, with a falsifiable hypothesis.
        Stores content by digest; does not edit the evaluator or activate the candidate."""
        from g6.core.door import blocked_reply
        try:
            files = json.loads(files_json)
            if not isinstance(files, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
                raise ValueError("files must map filenames to source strings")
            return mh.propose_code(workflow, files, hypothesis=hypothesis, session=session(ctx))
        except (ValueError, TypeError) as exc:
            return blocked_reply(f"[G6_E_INPUT_VALIDATION] {exc}")

    def metaharness_evaluate_start(workflow: str, proposal_digest: str) -> dict:
        """Start a bounded evaluation job. Poll metaharness_job for its result; no approval is implied."""
        return jobs.start(lambda: mh.evaluate(workflow, proposal_digest))

    def metaharness_job(job_id: str) -> dict:
        """Read a process-local job's pending/running/completed/failed status and retained result."""
        return jobs.status(job_id)

    def metaharness_run(workflow: str, task_input: str) -> dict:
        """Run the currently approved application version on new input as a job."""
        return jobs.start(lambda: mh.run(workflow, task_input))

    def metaharness_inspect(workflow: str) -> dict:
        """A declared workflow's versions, confirmations, evaluations and decisions."""
        return mh.inspect(workflow)

    def metaharness_propose(workflow: str, ctx: Any, profile_json: str = "", proposer: str = "") -> dict:
        """A candidate profile (``profile_json``: acting fields only), or with none, the declared search space's
        candidates. A candidate changes nothing until it is evaluated, decided and approved by a person."""
        try:
            profile = json.loads(profile_json) if profile_json else None
        except json.JSONDecodeError as exc:
            from g6.metaharness.api import _blocked

            return _blocked("G6_E_META_SCHEMA", f"profile_json is not JSON: {exc}")
        return mh.propose(workflow, session=session(ctx), profile=profile, proposer=proposer)

    def metaharness_evaluate(workflow: str, proposal_digest: str) -> dict:
        """Measure a candidate against the active version on the held-out split (paired, compute-matched)."""
        return mh.evaluate(workflow, proposal_digest)

    def metaharness_decide(workflow: str, report_digest: str, proposal_digest: str) -> dict:
        """The meta-gate's decision: recommend (qualified-draft at best), refuse or block, with a G6_E_META_* code.
        A decision that is not recommend carries ``recovery``: why, and what can be done next. Tell your user."""
        return mh.decide(workflow, report_digest, proposal_digest)

    def metaharness_diagnose(workflow: str) -> dict:
        """Recovery and root cause: why the active version fails on the dev split, and what can be done next (G6
        components to try as candidates, and what only your user can unblock). Changes nothing."""
        return mh.diagnose(workflow)

    async def metaharness_approve(workflow: str, decision_digest: str, ctx: Any) -> dict:
        """Present digest-bound client-mediated consent, including in the proposing session.
        G6 records the supplied name; it does not authenticate human identity."""
        who = session(ctx)
        refused, question = mh.approval_check(workflow, decision_digest, session=who)
        if refused is not None:
            return refused
        consent = await request_consent(ctx, question)
        if consent.status != "accepted":
            return consent_refusal(consent)
        return mh.approve(workflow, decision_digest, session=who, answer=consent.answer)

    async def metaharness_rollback(workflow: str, version: int, ctx: Any) -> dict:
        """Ask the person, through the client, to move back to a version they approved."""
        refused, question, binding = mh.rollback_check(workflow, version)
        if refused is not None:
            return refused
        consent = await request_consent(ctx, question)
        if consent.status != "accepted":
            return consent_refusal(consent)
        return mh.rollback(workflow, version, answer=consent.answer, binding=binding)

    for tool in (metaharness_inspect, metaharness_propose, metaharness_evaluate, metaharness_decide,
                 metaharness_diagnose, metaharness_approve, metaharness_rollback, metaharness_start,
                 metaharness_confirm, metaharness_propose_code, metaharness_evaluate_start, metaharness_job,
                 metaharness_run):
        if "ctx" in tool.__annotations__:  # the real Context type, so the server injects it (annotations are strings)
            tool.__annotations__["ctx"] = context_type
        server.tool()(tool)


def main() -> None:
    build_server().run()
