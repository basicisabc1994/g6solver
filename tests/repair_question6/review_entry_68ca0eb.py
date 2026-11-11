"""Frozen 68ca0eb entry points; run only in the differential fixture namespace."""
# ruff: noqa: F821
from __future__ import annotations

def review_patch(repo: str | Path, *, task: str, requirements: list[dict], tests: list[str] | tuple[str, ...]=(), claim: str='', sources: dict[str, str] | None=None, base: str='HEAD', expected_artifact_digest: str='', timeout_s: int=300, intent: dict | None=None, proposed_checks: list[dict] | None=None, template_instances: list[dict] | None=None, search: dict | None=None, import_roots: list[str] | tuple[str, ...]=(), llm=None, confirmed=None) -> dict:
    """Assess the working tree's patch against ``base`` on the stated requirements (``[{id, text, checks}]``, checks
    being pytest node ids) and the extra ``tests``. Never accepts claimed test results; never authorises a merge.

    Optional ``expected_artifact_digest`` pins a prior snapshot; a changed one is refused (``G6_E_PATCH_STALE``).
    Optional ``intent``, an intent contract (``g6.intent_engine.schema.IntentContract``), adds its items as
    caller-stated (``g6.harness.verification_search.requirements_from_intent``); one that is not valid is refused.
    Optional ``proposed_checks`` (``[{id, requirement_id, intent, file, source, test}]``, at most 20) run in confined
    copies of the patch and of the base commit's code and never pass a requirement
    (``g6.harness.verification_search``); an invalid one is refused before anything runs.
    Optional ``template_instances`` (``[{id, template, requirement_id, intent, bindings}]``, counted with the
    proposed checks within the 20) fill G6's templates (``g6.harness.verification_templates``); an invalid one is
    refused before anything runs, and one the tree gives nothing to apply to is refused and reported only. Optional
    ``search`` (``{"templates": bool, "test_adequacy": bool}``) runs free-form checks only, or measures test adequacy.
    Optional ``import_roots`` (at most ``MAX_IMPORT_ROOTS`` folders inside the repository, such as ``lib``) go first on
    the import path of every run of the review, in place and in copies, and are recorded in its scope.
    ``search={"propose": true}`` asks a model for checks (``g6.harness.verification_proposer``): ``llm``, the caller to
    ask (Python only), else the model the person configured for G6; what it proposes stays untrusted.
    Optional ``confirmed`` (Python only; over MCP, G6 reads it from the task's question store): the standard of the
    expectations the person confirmed for this task (``g6.harness.confirmed_expectation.ExpectationStandard``), each
    enforced on the patch as its one example.v1 check (``g6.harness.confirmed_checks``); with none, nothing of it runs.
    """
    started = time.monotonic()
    try:
        if confirmed is not None:
            from g6.harness.confirmed_expectation import ExpectationStandard
            if type(confirmed) is not ExpectationStandard:
                raise _Refused('G6_E_INPUT_VALIDATION', 'confirmed is the standard of the expectations the person confirmed (an ExpectationStandard)')
        stated, assumed, unmapped, proposed, instances, config = _inputs(task, claim, tests, sources, timeout_s, requirements, intent, proposed_checks, template_instances, search)
        root = repo_tests.checked_repo(repo)
        roots = _import_roots(root, import_roots)
        before = repo_tests.snapshot(root)
        artifact = digest(before)
        if expected_artifact_digest and artifact != expected_artifact_digest:
            return blocked_reply('[G6_E_PATCH_STALE] working tree differs from the supplied artifact digest')
        change = repo_tests.change(root, base)
        placed = {}
        if proposed:
            from g6.harness import verification_search
            try:
                placed = verification_search.placements(root, proposed)
            except ValueError as exc:
                raise _Refused('G6_E_INPUT_VALIDATION', str(exc)) from exc
        listed = [req for req in stated if req.provenance not in NORMATIVE]
        stated = [req for req in stated if req.provenance in NORMATIVE]
        norm = {raw: repo_tests.inside(root, raw, 'test') for req in stated for raw in req.checks}
        norm.update({raw: repo_tests.inside(root, raw, 'test') for raw in tests})
        stated_ids = list(dict.fromkeys((norm[raw] for req in stated for raw in req.checks)))
        ids = list(dict.fromkeys([*stated_ids, *(norm[raw] for raw in tests)]))
        return _assess(root, change, stated, ids, stated_ids, norm, before, artifact, task, claim, sources, deadline=started + timeout_s, started=started, listed=listed, assumed=assumed, unmapped=unmapped, proposed=proposed, placed=placed, instances=instances, config=config, roots=roots, llm=llm, confirmed=confirmed if confirmed is not None and confirmed.expectations else None)
    except LETS_THROUGH:
        raise
    except repo_tests.RepoRefused as exc:
        said = str(exc)
        if exc.code == ISOLATION_UNAVAILABLE and (not isinstance(exc, _Refused)):
            said = said.removeprefix(f'[{exc.code}] ').split(' To run such code without one')[0] + ' ' + BOUNDARY_HELP
        refused = blocked_reply(f'[{exc.code}] {said}')
        if getattr(exc, 'help', ''):
            refused['warning_card'].update(suggested_action=exc.help, next_step=exc.help)
        return refused
    except (OSError, ValueError, TypeError) as exc:
        return blocked_reply(f'[{CODE}] {exc}')

def _reply(change, evaluated, reasons, unknowns, runs, skipped, receipts, ran, gate, merge_reasons, artifact, claim, sources, started, follow_ups=(), listed=(), assumed=(), search=None, defects=(), prepared=None, adequacy=None, config=None, counts=None, roots=(), stopped=(), proposer=None, first_action='', issuer_inputs=None, confirmed=None) -> dict:
    """The reply. ``first_action``: the next action that stands first for an insufficient-evidence outcome in place of
    the general one (the launch review's I6: a module the patch imports that is not installed here).
    ``issuer_inputs``: each stated check's SHA-256 and whether the patch wrote it, every other run that observed a
    stated check on the patch and the run again, for ``assessment.requirement_evidence``
    (``g6.harness.requirement_evidence``), the issuer's statement for each requirement beside the review's own reading."""
    from g6.harness import verification_search
    reasons = [{**r, 'detail': _one_line(r['detail'])} for r in reasons]
    unknowns = [_one_line(text) for text in unknowns]
    follow_ups, first_action = ([_one_line(text) for text in follow_ups], _one_line(first_action))
    evaluated = [{**req, **{key: _one_line(req[key]) for key in ('reason', 'suggested_test') if isinstance(req.get(key), str)}} for req in evaluated]
    stages = {r['stage'] for r in reasons}
    codes = {r['code'] for r in reasons}
    if 'integrity' in stages:
        outcome, why = (INTEGRITY, [r['detail'] for r in reasons if r['stage'] == 'integrity'])
    elif codes & DEFECT_CODES:
        outcome, why = (DEFECT, [r['detail'] for r in reasons if r['stage'] in ('tier_1', 'tier_2')])
    elif 'evidence' in stages:
        outcome, why = (INSUFFICIENT, [r['detail'] for r in reasons if r['stage'] == 'evidence'])
    else:
        outcome, why = (SUPPORTED, [])
    summary = _SUMMARY[outcome].format('; '.join(why[:5]))
    next_actions = [first_action if first_action and outcome == INSUFFICIENT else _NEXT[outcome]]
    next_actions += [f"{req['id']}: {req['reason']}; suggested: {req['suggested_test']}" for req in evaluated if 'suggested_test' in req]
    next_actions += [action for action in follow_ups if action != next_actions[0]]
    if unknowns:
        next_actions.append('Look into the unknowns yourself: G6 does not count them for or against the patch.')
    environment = {'isolation': ran.get('isolation'), 'python': ran.get('python'), 'interpreter_source': ran.get('interpreter_source'), 'repo_env': ran.get('repo_env'), 'env_set_by_g6': ran.get('env_set_by_g6'), 'host_python_version': platform.python_version(), 'platform': platform.platform()}
    evidence = [{'source': 'g6.workforce.repo_tests', 'summary': f"tier {run['tier']}: {run['command']}: {run['verdict']}"[:200], 'authority': 'host-observed', 'artifact_digest': artifact, 'base_commit': change.base_commit, 'scope': 'these test outcomes on this working tree; not complete correctness', **{key: value for key, value in run.items() if key != 'provenance'}} for run in runs]
    proposals, assumptions = _listed(listed, assumed)
    assessment = {'outcome': outcome, 'summary': summary, 'reasons': reasons, 'requirements': evaluated, 'requirements_without_adequate_check': [r['id'] for r in evaluated if r['status'] == NOT_ESTABLISHED], 'specification_note': SPECIFICATION_NOTE, 'proposals': proposals, 'scope': {'artifact_digest': artifact, 'base_commit': change.base_commit, 'changed_files': list(change.files), 'environment': environment, 'checks_run': runs, 'checks_skipped': skipped, 'receipts': receipts, 'assumptions': assumptions, 'search': {**verification_search.shown_config(config or verification_search.search_config(None)), **{'template_instances': 0, 'proposed_checks': 0, 'model_calls': 0, **(counts or {})}}, 'import_roots': list(roots), 'seconds': round(time.monotonic() - started, 3)}, 'unknowns': unknowns, 'next_actions': next_actions, 'not_checked': list(NOT_CHECKED), 'completion': verification_search.completion(evaluated, receipts, runs, skipped, list(defects), search, artifact=artifact, base_commit=change.base_commit, templates=prepared, adequacy=adequacy, stopped=stopped, proposals=proposer)}
    if issuer_inputs is not None:
        from g6.harness import requirement_evidence
        assessment['requirement_evidence'] = requirement_evidence.assess(evaluated, runs, artifact=artifact, base_commit=change.base_commit, environment=environment, integrity=[r['code'] for r in reasons if r['stage'] == 'integrity'], **issuer_inputs)
    if confirmed is not None:
        from g6.harness import requirement_evidence
        standard, enforced = confirmed
        assessment['confirmed_expectations'] = {'standard_sha256': standard.sha256, 'revision': standard.revision, 'supersedes': standard.supersedes, 'note': CONFIRMED_NOTE, 'expectations': [{'expectation_id': one.expectation_id, 'question': one.question.to_dict(), 'question_sha256': one.question_sha256, 'answer': {'option': one.option, 'text': one.answer.text, 'answered_by': one.answered_by, 'answered_at': one.answer.answered_at, 'identity': 'recorded as given, not authenticated'}, 'check': found} for one, found in zip(standard.expectations, enforced, strict=True)], 'evidence': requirement_evidence.confirmed_evidence(standard, enforced, artifact=artifact, base_commit=change.base_commit, environment=environment, integrity=[r['code'] for r in reasons if r['stage'] == 'integrity'])}
    from g6.harness import patch_report
    assessment['report'] = patch_report.safe_render(assessment)
    card = warning_card(CODE, summary, next_actions[0])
    return {**envelope_fields(reliability_label='qualified-draft', warning_card=card, evidence=evidence, request_id=uuid.uuid4().hex[:12], degradation_reason=summary[:300]), 'ok': True, 'assessment': assessment, 'merge': {'status': 'requires_separate_approval', 'reason': MERGE_REASON, 'reasons': merge_reasons}, 'gate': gate, 'grounding': _grounding(claim, sources), 'artifact_digest': artifact}
