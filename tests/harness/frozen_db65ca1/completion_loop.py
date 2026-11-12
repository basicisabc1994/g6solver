"""Frozen db65ca1 reading; stdlib and sibling fixtures only.

Copied source; receipt version/executable/digest metadata use fixed test adapters.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from . import repo_tests
from . import verification_search as vs
from .patch_review import _loaded_elsewhere, _in_repository


_PLACE = re.compile(r"^.*?g6_check_[0-9a-f]{12}/")


@dataclass
class ProbeRun:
    """One run of a probe set: pytest's own count of each case, so no reading of its console can hide a failure."""
    exit: int | None
    results: dict[str, str]
    texts: dict[str, str]
    tail: str = ""
    boundary: str = ""     # what confined it, as the run says

    @property
    def ran(self) -> int:
        return sum(v in ("passed", "failed") for v in self.results.values())

    @property
    def usable(self) -> bool:
        return self.exit in (0, 1) and self.ran > 0


def _outcome(phases: Mapping[str, str]) -> str:
    return "error" if "failed" in (phases.get("setup"), phases.get("teardown")) else phases.get("call") or "skipped"


def _read(change: repo_tests.Change, runs: Iterable[dict]) -> ProbeRun:
    """A set's runs, one for each file, as one ``ProbeRun``, read from pytest's per-case outcomes: ``reading`` judges a
    check as a whole, and calls a file of passes and skips no result, where L9's probes skip the cases that cannot occur.
    A case that failed on an operating-system error is unknown, as a refused access looks like one. A run that did not
    finish, or tested code from outside its copy, leaves the set unusable."""
    code: int | None = 0
    results, texts, tail, boundary = {}, {}, [], ""
    for ran in runs:
        boundary = (ran.get("isolation") or {}).get("backend") or boundary
        elsewhere, verdict = _loaded_elsewhere(change, ran), ran.get("verdict")
        if elsewhere or verdict not in repo_tests.RAN:
            code = None
            tail.append(vs.LOADED_ELSEWHERE.format(elsewhere) if elsewhere else f"{verdict}: {ran.get('said') or ''}")
            texts.update(ran.get("failure_messages") or {})     # pytest's own words: no case has them, so only ``_unrun`` reads them
            continue
        code = None if code is None else max(code, int(verdict == repo_tests.FAILED))
        full, refused = _in_repository(ran), set(ran.get("os_errors") or ())
        for named, phases in (ran.get("test_outcomes") or {}).items():
            results[_PLACE.sub("", full(named))] = "unknown" if named in refused else _outcome(phases)
        texts.update({_PLACE.sub("", full(named)): text for named, text in (ran.get("failure_messages") or {}).items()})
    return ProbeRun(code, results, texts, "\n".join(tail), boundary)
