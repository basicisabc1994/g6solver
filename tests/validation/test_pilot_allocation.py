"""The M1 pilot's repository allocation is fixed by rule before any distillation run sees a bug."""

import copy
import json
from pathlib import Path

import pytest

from validation.distill.pilot import allocation

MANIFEST = Path(allocation.__file__).with_name("allocation.json")


def _manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_committed_manifest_rederives_from_its_rule():
    assert allocation.check(_manifest()) == []


def test_admission_and_monitor_share_no_repository():
    roles = _manifest()["roles"]
    assert not set(roles["admission"]["repositories"]) & set(roles["monitor"]["repositories"])


def test_every_repository_and_bug_sits_in_exactly_one_role():
    roles = _manifest()["roles"]
    repos = [r for role in roles.values() for r in role["repositories"]]
    bugs = [b for role in roles.values() for b in role["bugs"]]
    assert len(repos) == len(set(repos)) == 18
    assert len(bugs) == len(set(bugs)) == 35
    for role in roles.values():
        assert {allocation.repository_of(b) for b in role["bugs"]} == set(role["repositories"])


def test_role_sizes_are_the_registered_ones():
    roles = _manifest()["roles"]
    assert {name: len(r["repositories"]) for name, r in roles.items()} == dict(allocation.ROLES)


def test_a_swapped_repository_is_caught():
    m = copy.deepcopy(_manifest())
    a, b = m["roles"]["admission"], m["roles"]["acquisition"]
    a["repositories"][0], b["repositories"][0] = b["repositories"][0], a["repositories"][0]
    assert allocation.check(m)


def test_a_moved_bug_is_caught():
    m = copy.deepcopy(_manifest())
    m["roles"]["reuse"]["bugs"].append(m["roles"]["admission"]["bugs"].pop())
    assert allocation.check(m)


def test_another_cohort_digest_gives_another_draw():
    repos = [f"repo{i}" for i in range(18)]
    assert allocation.allocate("a" * 64, repos) != allocation.allocate("b" * 64, repos)


def test_the_sealed_final_cohort_is_refused(tmp_path):
    p = tmp_path / "final-000000000000.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="sealed"):
        allocation.manifest_from_cohort(p)


def test_wrong_repository_count_is_refused():
    with pytest.raises(ValueError):
        allocation.allocate("a" * 64, [f"repo{i}" for i in range(17)])
