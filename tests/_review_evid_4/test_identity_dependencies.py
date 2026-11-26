"""Review 4: real disk pytest observations through the review evidence producers."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from g6.harness.workflow import adapters, evidence, lifecycle
from g6.harness.workflow.record import ProjectRecord
from g6.workforce import coding_certificate
from tests._review_evid_3.test_review3_boundaries import (
    commit_fixture, finish, isolated, observe, public_review,
)
from tests.workflow.kit import make_repo


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / "disk" / uuid4().hex
    path.mkdir(parents=True)
    return path.resolve()


SOURCES = {
    "decorator": (
        "from oracle import checked\nfrom module import VALUE\n"
        "@checked\ndef test_case():\n    assert VALUE > 0\n",
        "oracle.py",
        "from module import VALUE\ndef checked(fn):\n"
        "    def wrapped():\n        assert VALUE == 2\n        fn()\n"
        "    return wrapped\n",
        "def checked(fn):\n    def wrapped():\n        assert True\n        fn()\n"
        "    return wrapped\n",
    ),
    "fixture": (
        "from oracle import observed\ndef test_case(observed):\n    assert observed == 2\n",
        "oracle.py",
        "import pytest\nfrom module import VALUE\n@pytest.fixture\n"
        "def observed():\n    return VALUE\n",
        "import pytest\n@pytest.fixture\ndef observed():\n    return 2\n",
    ),
    "parametrize_factory": (
        "import pytest\nfrom oracle import cases\n"
        "@pytest.mark.parametrize('observed', cases(), ids=['case'])\n"
        "def test_case(observed):\n    assert observed == 2\n",
        "oracle.py",
        "from module import VALUE\ndef cases():\n    return [VALUE]\n",
        "def cases():\n    return [2]\n",
    ),
    "reexported_alias": (
        "from forwarding import case as test_case\n",
        "oracle.py",
        "from module import VALUE\ndef case():\n    assert VALUE == 2\n",
        "def case():\n    assert True\n",
    ),
}


@pytest.mark.parametrize("kind", list(SOURCES))
@pytest.mark.parametrize("replace_oracle", [False, True], ids=["fixed_product_control", "replaced_oracle"])
def test_changed_oracle_cannot_be_a_same_check_repair(tmp_path, monkeypatch, kind, replace_oracle):
    repo = make_repo(tmp_path / "repo")
    collector, relative, original, replacement = SOURCES[kind]
    (repo / "test_case.py").write_text(collector, encoding="utf-8")
    if kind == "reexported_alias":
        (repo / "forwarding.py").write_text("from oracle import case\n", encoding="utf-8")
    oracle = repo / relative
    oracle.write_text(original, encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    before = evidence.capture_sources(repo, ["test_case.py"])
    failed = observe(repo, "test_case.py", tmp_path / "before")
    assert failed["failed"] == 1
    first = public_review(repo, "test_case.py", failed, monkeypatch)
    assert first["outcome"] == "defect_detected"
    finish(repo, first, "0", monkeypatch)
    if replace_oracle:
        oracle.write_text(replacement, encoding="utf-8")
    else:
        (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, ["test_case.py"])
    passed = observe(repo, "test_case.py", tmp_path / "after")
    assert passed["passed"] == 1
    second = public_review(repo, "test_case.py", passed, monkeypatch)
    assert second["outcome"] != "test_integrity_compromised", second["reasons"]
    reply = finish(repo, second, "1", monkeypatch)
    project, task = adapters.identities(repo, "task")
    rows = ProjectRecord(project).events(task=task)
    assert len([e for e in rows if e.kind == "review_finished"]) == 2
    exported = lifecycle.export(project)
    readable = exported.with_name("RECORD.md").read_text(encoding="utf-8")
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *args: repo)
    missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": "task"})
    companion = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
    assert companion["events"] == evidence.read(repo, "task")["justification"]
    assert reply["card"]["repaired"] == int(not replace_oracle), (
        f"{kind}: oracle replaced={replace_oracle}, identity unchanged={before == after}, "
        f"card repaired={reply['card']['repaired']}, open={reply['card']['open']}; "
        f"RECORD.md claims repaired={'repaired in a recorded review' in readable}; "
        f"certificate incomplete={companion.get('identities_incomplete', False)}; "
        f"product={(repo / 'module.py').read_text().strip()}"
    )


@pytest.mark.parametrize("replace_body", [False, True], ids=["fixed_product_control", "replaced_relative_body"])
def test_relative_import_cannot_hash_a_shadow_module(tmp_path, monkeypatch, replace_body):
    repo = make_repo(tmp_path / "repo")
    for directory in (repo / "pkg", repo / "pkg" / "sub"):
        directory.mkdir()
        (directory / "__init__.py").write_text("", encoding="utf-8")
    selection = "pkg/sub/test_case.py"
    (repo / selection).write_text("from ..checks_impl import case as test_case\n", encoding="utf-8")
    body = repo / "pkg" / "checks_impl.py"
    body.write_text("from module import VALUE\ndef case():\n    assert VALUE == 2\n", encoding="utf-8")
    (repo / "checks_impl.py").write_text("def case():\n    assert False\n", encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    before = evidence.capture_sources(repo, [selection])
    failed = observe(repo, selection, tmp_path / "before")
    assert failed["failed"] == 1
    first = public_review(repo, selection, failed, monkeypatch)
    assert first["outcome"] == "defect_detected"
    finish(repo, first, "0", monkeypatch)
    if replace_body:
        body.write_text("def case():\n    assert True\n", encoding="utf-8")
    else:
        (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [selection])
    passed = observe(repo, selection, tmp_path / "after")
    assert passed["passed"] == 1
    second = public_review(repo, selection, passed, monkeypatch)
    assert second["outcome"] != "test_integrity_compromised", second["reasons"]
    reply = finish(repo, second, "1", monkeypatch)
    assert reply["card"]["repaired"] == int(not replace_body), (
        f"actual relative body replaced={replace_body}, identity unchanged={before == after}, "
        f"card repaired={reply['card']['repaired']}, open={reply['card']['open']}"
    )
