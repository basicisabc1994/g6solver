"""Frozen 68ca0eb entry points; run only in the differential fixture namespace."""
# ruff: noqa: F821
from __future__ import annotations

def g6_review_patch(repo: str, task: str, requirements: list[dict] | None=None, tests: list[str] | None=None, claim: str='', sources: dict[str, str] | None=None, base: str='HEAD', expected_artifact_digest: str='', timeout_s: int=300, intent: dict | None=None, proposed_checks: list[dict] | None=None, template_instances: list[dict] | None=None, search: dict | None=None, import_roots: list[str] | None=None) -> dict:
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
        (module.attribute) G6 finds in the files, literals are JSON; anything else refuses the review, and a refusal of
        bindings names each one missing or misnamed, its warning card giving the template's bindings and one valid
        instance. G6 compiles
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
        insufficient_evidence; every model call, request and reply is in assessment.completion. Every full
        assessment has assessment.completion: what was demonstrated, what is not established, the defects reproduced,
        preservation, test adequacy, open questions and every activity; and assessment.report, the review as
        Markdown to show the person verbatim: reproduced findings first, then what is not established, what they
        decide, how it was decided, and the standing note (it decides nothing; text from the repository in it is
        inert). Poll metaharness_job for the result. Its reply is compact: assessment.report first, the outcome, the
        reasons, the next actions, the unknowns, the merge authorisation, and full_assessment, where the full assessment
        is saved (a file under G6's state folder); metaharness_job with detail="full" returns all of it.
        assessment.outcome is one of: test_integrity_compromised (a sign of a weakened test or test helper that G6's
        static comparison finds, a changed test-runner file, a git-ignored file that could change the run, a shortcut in the diff of the tests,
        their runner or continuous integration, or a run that cannot be believed); defect_detected (a stated check or extra test failed, or a test of a changed module fails
        on the patch and passed at the base commit, or the patch adds an import a stated contract forbids, or a case
        the person confirmed with g6_confirm_behaviour for this repo and task fails on the patch: G6 reads what they
        confirmed from its own store, enforces it as its one example.v1 check and shows it in
        assessment.confirmed_expectations);
        insufficient_evidence (no requirement, a requirement with no check, a check that did not run or ran out of
        time, a run whose tests all passed by pytest's own count but whose process then exited with an error
        (run_inconsistent; exit 0 with a failure, an error or fewer tests finished is a run that cannot be believed),
        a shortcut only the claim reports, a requirement with no check independent of the patch
        (checks_written_with_patch), or a proposed or compiled check's unconfirmed_counterexample or
        unconfirmed_failure);
        supported_within_scope (every requirement passed its stated checks, at least one of them not written by the
        patch and, for new behaviour, failing at the base commit: support on those checks only, never proof
        the patch is complete; a check that also passes on the base commit's code is listed in unknowns). Each is
        qualified-draft. A refusal is blocked-escalated with no outcome. Test receipts are kept in a file owned by the
        same user as G6: they stop a caller from claiming that G6 ran a test, not a program running as that user. merge
        is always requires_separate_approval; nothing is merged. Requires host permission patch_review.review in
        G6_MCP_SENSITIVE."""
    from g6.core.door import blocked_reply
    if not _allowed('patch_review', 'review', allowed_sensitive()):
        return blocked_reply(f'{_DENIED} patch_review.review runs repository code; the host must allow it')
    from g6.harness.question_store import StoreUnreadable
    from g6.mcp import behaviour
    from g6.mcp.review_reply import finish, review
    try:
        behaviour.enforced(repo, task)
    except StoreUnreadable as exc:
        return blocked_reply(str(exc))
    return jobs.start(lambda: behaviour.reviewed(review, repo, task, requirements=requirements or [], tests=tests or [], claim=claim, sources=sources, base=base, expected_artifact_digest=expected_artifact_digest, timeout_s=timeout_s, intent=intent, proposed_checks=proposed_checks, template_instances=template_instances, search=search, import_roots=import_roots or []), compact=finish)
