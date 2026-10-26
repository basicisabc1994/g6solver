# SPDX-License-Identifier: Apache-2.0
"""The conformance kit over every registered component (Phase 0.6, the extension definition of done).

Every check runs on every component the registry finds, and each check that reads only source also runs on each
family's shared code (``g6.<family>``, also for a family with no member yet), which is held to the extensions' checks.
A check marked for extensions is required of family members, their family's shared code and entry-point extensions and
only reported for the core (the summary prints its hits). A required hit passes only where ``waivers.toml`` names it,
with a reason and the finding that tracks it; a waiver that no longer matches a hit fails, and the number of waivers
is pinned, so it may only shrink. The extras' dependency closures are checked here too, as the distribution's own hits
(component ``g6``)."""
from __future__ import annotations

import re
import tomllib

import conformance_kit as kit
import pytest

from g6.core.registry import get_registry
from g6.mcp import describe_reply

NAMES = get_registry().names()
#: Each family's shared code, by the name a waiver gives it (``g6.<family>``).
SHARED = {t.name: t for t in kit.shared_targets()}
SHARED_CHECKS = [c for c in kit.CHECKS if c.shared]
WAIVERS = kit.load_waivers()
#: Pinned to the number of waivers: lower it when a fix removes one; never raise it.
MAX_WAIVERS = 4
# The four explicitly authorised step-2 retrofits had a separate, closed shrinking allowance; all four were certified
# on 2026-10-23, so it is spent.
MAX_C1_WAIVERS = 0
C1_RETROFITS = {"adapt_bayesian", "adapt_physical_ai", "adapt_sklearn", "adapt_automl"}
C1_FINDING = "certificate retrofit, docs/transition/CERTIFICATES.md step 2"
#: The component a waiver names for a hit on the distribution as a whole (the extras' closures).
PACKAGE = "g6"
FINDING = re.compile(r"^(tracker \S.+|ledger \([a-zA-Z0-9-]+\).*|DECISIONS #\d+.*|permanent: \S.+)$")

_TARGETS: dict[str, kit.Target] = {}
_HITS: dict[tuple[str, str], list[kit.Problem]] = {}


def _target(name: str) -> kit.Target:
    if name in SHARED:
        return SHARED[name]
    if name not in _TARGETS:
        _TARGETS[name] = kit.target(name)
    return _TARGETS[name]


def _hits(check: kit.Check, name: str) -> list[kit.Problem]:
    key = (check.name, name)
    if key not in _HITS:
        if check.run is kit.d9_extras:  # one child process per component and extra, all at once
            found = kit.d9_extras_many(_target(n) for n in NAMES)
            _HITS.update({(check.name, n): problems for n, problems in found.items()})
        else:
            _HITS[key] = check.run(_target(name))
    return _HITS[key]


def _package_hits() -> list[kit.Problem]:
    key = ("package", PACKAGE)
    if key not in _HITS:
        _HITS[key] = kit.closure_problems() + kit.licence_problems()
    return _HITS[key]


def _unwaived(component: str, problems: list[kit.Problem]) -> list[kit.Problem]:
    return [p for p in problems if not any(w.covers(component, p) for w in WAIVERS)]


def _skip_certificate_without_its_extras(name: str, check: kit.Check, problems: list[kit.Problem]) -> None:
    """A certificate reissued where an extra its component declares is not installed cannot match its record (its
    checks import what is missing), as a skill example cannot run (tests/core/test_component_skills.py): the test job
    skips it, naming the extra; the conformance job installs the certified components' extras and checks it there."""
    if check.run is not kit.c1_certificate or not problems:
        return
    missing = [e["extra"] for e in describe_reply(name)["extras"] if not e["installed"]]
    if missing:
        pytest.skip(f"{name}'s certificate is not reissued where g6[{','.join(missing)}] is not installed")


@pytest.mark.parametrize("check", kit.CHECKS, ids=lambda c: c.name)
@pytest.mark.parametrize("name", NAMES)
def test_component_conforms(name: str, check: kit.Check) -> None:
    target = _target(name)
    found = _hits(check, name)
    if not check.required(target):
        kit.REPORTED.setdefault(check.name, {})[name] = found
        return
    unwaived = _unwaived(name, found)
    _skip_certificate_without_its_extras(name, check, unwaived)
    assert unwaived == [], "\n".join(map(str, unwaived))


@pytest.mark.parametrize("check", SHARED_CHECKS, ids=lambda c: c.name)
@pytest.mark.parametrize("name", sorted(SHARED))
def test_family_shared_code_conforms(name: str, check: kit.Check) -> None:
    target = _target(name)
    assert check.required(target)
    unwaived = _unwaived(name, _hits(check, name))
    assert unwaived == [], "\n".join(map(str, unwaived))


def test_the_closure_file_covers_every_extra_and_is_current() -> None:
    unwaived = _unwaived(PACKAGE, kit.closure_problems())
    assert unwaived == [], "\n".join(map(str, unwaived))


def test_every_package_in_every_closure_has_an_allowed_licence() -> None:
    unwaived = _unwaived(PACKAGE, kit.licence_problems())
    assert unwaived == [], "\n".join(map(str, unwaived))


#: NVIDIA's CUDA wheels, under NVIDIA's own licence or with no licence metadata. They are allowed for good only in an
#: opt-in extra that pulls PyTorch (owner rulings 2026-10-21: GPU support for lora, then for every extra that pulls
#: PyTorch); waivers.toml's two CUDA waivers stand for that ruling, and this check holds them to it.
CUDA = ("nvidia-", "cuda-toolkit==")


def _cuda_outside_pytorch(data: dict, in_all: set[str]) -> list[str]:
    """Each scope (the core, or an extra) whose closure holds a CUDA wheel without PyTorch, or that ``all`` installs."""
    scopes = [("core", data["core"])] + sorted(data["extras"].items())
    return [scope for scope, entry in scopes
            if any(pin.startswith(CUDA) for pin in entry["closure"])
            and (scope == "core" or scope in in_all or not any(pin.startswith("torch==") for pin in entry["closure"]))]


def test_cuda_wheels_come_only_with_pytorch_in_an_opt_in_extra() -> None:
    project = tomllib.loads((kit.OSS / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    in_all = {"all", *re.findall(r"[\w-]+", project["optional-dependencies"]["all"][0].split("[", 1)[1])}
    assert _cuda_outside_pytorch(kit.closures(), in_all) == []
    # the check itself: a CUDA wheel that comes without torch (xgboost's NCCL), or through all, is caught
    bad = {"core": {"closure": []}, "extras": {"boost": {"closure": ["nvidia-nccl-cu12==2.0", "xgboost==3.0"]},
                                               "lora": {"closure": ["nvidia-cublas==13.0", "torch==2.14.1"]}}}
    assert _cuda_outside_pytorch(bad, {"all", "lora"}) == ["boost", "lora"]


def _required_hits() -> list[tuple[str, kit.Problem]]:
    found = [(name, p) for name in NAMES for check in kit.CHECKS if check.required(_target(name))
             for p in _hits(check, name)]
    found += [(name, p) for name in SHARED for check in SHARED_CHECKS for p in _hits(check, name)]
    return found + [(PACKAGE, p) for p in _package_hits()]


def test_every_waiver_still_matches_a_required_hit() -> None:
    hits = _required_hits()
    stale = [w for w in WAIVERS if not any(w.covers(name, p) for name, p in hits)]
    assert stale == [], "remove these waivers (and lower MAX_WAIVERS):\n" + "\n".join(map(repr, stale))


def test_the_waivers_may_only_shrink() -> None:
    # Removing the closed C1 set would allow a new vetted port to waive its certificate.
    ordinary = [w for w in WAIVERS if w.gate != "C1"]
    retrofit = [w for w in WAIVERS if w.gate == "C1"]
    assert len(ordinary) == MAX_WAIVERS, "fix the hit, or lower MAX_WAIVERS after removing a waiver"
    assert len(retrofit) == MAX_C1_WAIVERS, "C1 waivers may only shrink; lower MAX_C1_WAIVERS after a retrofit"
    assert len({w.component for w in retrofit}) == len(retrofit)
    assert all(w.component in C1_RETROFITS and w.match == "missing certificate"
               and w.finding == C1_FINDING for w in retrofit)

def test_each_waiver_says_why_and_where_it_is_tracked() -> None:
    names = {*NAMES, *SHARED, PACKAGE}
    for w in WAIVERS:
        assert w.component in names, w
        assert w.gate in kit.GATES, w
        assert w.match.strip() and w.reason.strip(), w
        assert (kit.OSS / w.file.rstrip("/")).exists(), w
        assert FINDING.match(w.finding) or (w.gate == "C1" and w.finding == C1_FINDING), w


@pytest.mark.parametrize("name", ["adapt_sklearn", "adapt_automl"])
def test_existing_vetted_ports_have_only_missing_certificate_waiver(name):
    # Removing either authorised waiver leaves its pre-existing missing-certificate C1 hit unwaived.
    # The same hits test_component_conforms checked: each certificate's observations rerun once per session.
    check = next(c for c in kit.CHECKS if c.run is kit.c1_certificate)
    hits = _hits(check, name)
    _skip_certificate_without_its_extras(name, check, [p for p in hits if p.message != "missing certificate"])
    if hits:
        assert all(p.message == "missing certificate" for p in hits)
        assert all(any(w.covers(name, p) for w in WAIVERS) for p in hits)
    else:
        assert not any(w.component == name and w.gate == "C1" for w in WAIVERS)
