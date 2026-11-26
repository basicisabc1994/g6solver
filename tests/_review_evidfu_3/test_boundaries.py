"""Review 3 real-disk reproductions; no hosted execution."""
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace

import pytest

from g6.harness.workflow import adapters, evidence, lifecycle, record_md
from g6.harness.workflow.record import ProjectRecord
from g6.workforce import coding_certificate
from tests._review_evid_3.test_review3_boundaries import (
    isolated, commit_fixture, finish, observe, public_review,
)
from tests.workflow.kit import make_repo


@pytest.fixture
def tmp_path():
    path = Path(__file__).parent / "disk" / uuid4().hex
    path.mkdir(parents=True)
    return path.resolve()


@pytest.mark.parametrize("kind", ["constructor", "sort_callback", "decorator_return", "descriptor"])
@pytest.mark.parametrize("replace_oracle", [False, True], ids=["product_control", "oracle_changed"])
def test_boundaries(tmp_path, monkeypatch, kind, replace_oracle):
    repo = make_repo(tmp_path / "repo")
    selection = "test_case.py::test_case[a]" if kind == "sort_callback" else "test_case.py"
    if kind == "constructor":
        source = ("from helper import Worker\nworker = Worker()\n"
                  "def test_case():\n    assert worker.run() == 2\n")
        support = {"helper.py": "import module\nclass Worker:\n"
                   "    def __init__(self): pass\n"
                   "    def run(self): return module.VALUE\n"}
        changed = "helper.py"
        replacement = support[changed].replace("return module.VALUE", "return 2")
    elif kind == "sort_callback":
        source = ("import pytest\nfrom factory import cases\nfrom module import VALUE\n"
                  "@pytest.mark.parametrize('expected', cases(), ids=['a', 'b'])\n"
                  "def test_case(expected):\n    assert VALUE == expected\n")
        support = {"factory.py": "from oracle import direction\ndef rank(i): return direction(i)\n"
                   "def cases(): return sorted([2, 3], key=rank)\n",
                   "oracle.py": "def direction(i): return i\n"}
        changed, replacement = "oracle.py", "def direction(i): return -i\n"
    elif kind == "decorator_return":
        source = ("import module\nfrom factory import checked\n@checked()\n"
                  "def test_case():\n    assert module.VALUE == 2\n")
        support = {"factory.py": "from oracle import patch\ndef checked():\n"
                   "    def decorate(fn):\n        patch()\n        return fn\n"
                   "    return (decorate,)[0]\n",
                   "oracle.py": "def patch(): pass\n"}
        changed, replacement = "oracle.py", "import module\ndef patch(): module.VALUE = 2\n"
    else:
        source = ("import module\nfrom base import Base\nclass TestCase(Base):\n"
                  "    def test_case(self):\n        assert module.VALUE == 2\n")
        support = {"base.py": "from oracle import touch\nclass Descriptor:\n"
                   "    def __get__(self, obj, owner):\n        touch()\n        return 0\n"
                   "class Base:\n    observed = Descriptor()\n",
                   "oracle.py": "def touch(): pass\n"}
        changed, replacement = "oracle.py", "import module\ndef touch(): module.VALUE = 2\n"
    (repo / selection.partition("::")[0]).write_text(source, encoding="utf-8")
    for name, text in support.items():
        (repo / name).write_text(text, encoding="utf-8")
    commit_fixture(repo)
    (repo / "module.py").write_text("VALUE = 3\n", encoding="utf-8")
    before = evidence.capture_sources(repo, [selection])
    assert before[selection]
    failed = observe(repo, selection, tmp_path / "before")
    assert failed["failed"] >= 1
    first = public_review(repo, selection, failed, monkeypatch)
    finish(repo, first, "0", monkeypatch)
    if replace_oracle:
        (repo / changed).write_text(replacement, encoding="utf-8")
    else:
        (repo / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    after = evidence.capture_sources(repo, [selection])
    assert after[selection]
    assert (repo / "module.py").read_text(encoding="utf-8") == (
        "VALUE = 3\n" if replace_oracle else "VALUE = 2\n")
    passed = observe(repo, selection, tmp_path / "after")
    assert passed["passed"] >= 1, passed
    second = public_review(repo, selection, passed, monkeypatch)
    assert second["outcome"] != "test_integrity_compromised", second["reasons"]
    reply = finish(repo, second, "1", monkeypatch)
    project, task = adapters.identities(repo, "task")
    rows = ProjectRecord(project).events(task=task)
    counts = record_md.counts(rows)
    readable = lifecycle.export(project).with_name("RECORD.md").read_text(encoding="utf-8")
    monkeypatch.setattr(coding_certificate.bench, "_repo", lambda *args: repo)
    missions = SimpleNamespace(get=lambda _: {"venture_id": "fixture", "objective": "task"})
    companion = coding_certificate.workflow_justification(missions, "mission", {"repository": "repo"})
    assert companion["events"] == evidence.read(repo, "task")["justification"]
    expected = int(not replace_oracle)
    assert reply["card"]["repaired"] == counts["repaired"] == expected, (
        f"{kind}, oracle_changed={replace_oracle}: identities={before}/{after}; "
        f"card repaired={reply['card']['repaired']}, open={reply['card']['open']}; "
        f"record={counts}; record claims repair={'1 repaired' in readable}; companion incomplete={companion.get('identities_incomplete', False)}"
    )
