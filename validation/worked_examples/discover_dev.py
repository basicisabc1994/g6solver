# SPDX-License-Identifier: Apache-2.0
"""Replay already-seen worked-example patches through automatic discovery.

Owner-run development check, never an estimate of defect-discovery performance.
RESULTS.md says the full agent patches and maintainer logs live in the external
archive; the tracked amendments are scoring changes, not those agent patches.
This script never fetches, installs, mutates git or applies a patch. The owner
prepares one repository per recorded patch, then supplies a JSON list with:

    patch_id, repo, base, task, requirements, artifact_digest,
    patch_file, patch_sha256, [intent], [maintainer_passed], [maintainer_failures]

patch_file is relative to --records-root (an explicitly supplied archive folder).
artifact_digest pins the prepared repository. Its relationship to the archived
patch is an owner assertion, reported as such. Maintainer failures optionally
record test, function, input (args/kwargs), expected, actual and evidence (the log
and case). Exact matching below compares those data; it never guesses a match
from a test name. Unknowns remain null. No maintainer code runs here.

    python validation/worked_examples/discover_dev.py manifest.json \
        --records-root <archive-folder> --output .build/discover-development.json

An unavailable isolation backend remains a refused review, never a zero finding.
The review may use the configured default model through its existing guarded
reader; no model or provider is selected here. Run offline with no model configured
to measure the deterministic probes alone.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from g6.harness.patch_review import review_patch

LABEL = 'development data already seen; mechanism check, no generalisation claim'


def _canonical(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False)


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    patch_id: str = Field(min_length=1, max_length=200)
    repo: str = Field(min_length=1)
    base: str = 'HEAD'
    task: str = Field(min_length=1)
    requirements: list[dict]
    intent: dict | None = None
    artifact_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    patch_file: str = Field(min_length=1)
    patch_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    maintainer_passed: bool | None = None
    maintainer_failures: list[dict] = Field(default_factory=list)


class Failure(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    test: str = Field(min_length=1)
    function: str = Field(min_length=1)
    input: dict[str, JsonValue]
    expected: JsonValue
    actual: JsonValue
    evidence: str = Field(min_length=1)


def maintainer_match(finding: dict, facts: list[dict]) -> tuple[bool | None, list[str]]:
    """Exact function/input/expected/actual match to supplied recorded failures; not a model judgement."""
    failures = [Failure.model_validate(f) for f in facts]
    if not failures:
        return None, []
    reproduction = finding.get('reproduction') or {}
    nodes = []
    for difference in reproduction.get('differences', []):
        try:
            case, before, after = (json.loads(difference[k]) for k in ('input', 'base', 'patch'))
        except (KeyError, TypeError, ValueError):
            continue
        for failure in failures:
            if (failure.function == reproduction.get('function') and _canonical(failure.input) == _canonical(case)
                    and _canonical(failure.expected) == _canonical(before) and _canonical(failure.actual) == _canonical(after)):
                nodes.append(failure.test)
    return bool(nodes), sorted(set(nodes))


def replay(records: list[dict], *, reviewer=review_patch) -> dict:
    """Run each prepared patch with discovery on; validate the whole manifest before any review."""
    prepared = TypeAdapter(list[Record]).validate_python(records)
    for record in prepared:
        for failure in record.maintainer_failures:
            Failure.model_validate(failure)
    rows = []
    for record in prepared:
        reply = reviewer(record.repo, base=record.base, task=record.task, requirements=record.requirements,
                         intent=record.intent, expected_artifact_digest=record.artifact_digest,
                         search={'discovery': True})
        row = {'patch_id': record.patch_id, 'patch_file': record.patch_file,
               'patch_sha256': record.patch_sha256, 'prepared_artifact': record.artifact_digest,
               'patch_binding': 'owner asserts prepared snapshot is this archived patch',
               'maintainer_passed': record.maintainer_passed,
               'maintainer_evidence': 'supplied recorded outcomes; not rerun or independently authenticated',
               'status': 'reviewed' if reply.get('ok') else 'not_run'}
        if not reply.get('ok'):
            row.update(reason=reply.get('error', 'review unavailable'), reproduced_counterexamples=None,
                       alarm_on_maintainer_passing_patch=None)
        else:
            assessment = reply.get('assessment') or {}
            completion = assessment.get('completion') or {}
            findings = [f for f in (completion.get('defects_reproduced') or {}).get('proposed_checks', [])
                        if f.get('result') == 'counterexample' and
                        (f.get('reproduction') or {}).get('generated_by') == 'G6 automatic discovery']
            reproduced = []
            for finding in findings:
                matched, nodes = maintainer_match(finding, record.maintainer_failures)
                reproduced.append({**finding, 'maintainer_match': matched, 'matching_failing_tests': nodes})
            row.update(outcome=assessment.get('outcome'), reproduced_counterexamples=reproduced,
                       alarm_on_maintainer_passing_patch=bool(findings) if record.maintainer_passed is True else None,
                       activities=completion.get('activities', []), questions=completion.get('open_questions', []))
        rows.append(row)
    return {'label': LABEL, 'patches': rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--records-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records = TypeAdapter(list[Record]).validate_json(args.manifest.read_text(encoding='utf-8'))
    root = args.records_root.resolve(strict=True)
    for record in records:
        patch = (root / record.patch_file).resolve(strict=True)
        if not patch.is_relative_to(root) or patch.suffix != '.patch':
            raise ValueError('patch_file must be a .patch inside the explicit records root')
        if hashlib.sha256(patch.read_bytes()).hexdigest() != record.patch_sha256:
            raise ValueError(f'archived patch digest differs: {record.patch_id}')
    result = replay([r.model_dump() for r in records])
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8', newline='\n')


if __name__ == '__main__':
    main()
