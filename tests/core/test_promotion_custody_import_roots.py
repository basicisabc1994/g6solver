# SPDX-License-Identifier: Apache-2.0
"""The evaluator child ``g6.core.promotion_custody`` starts imports G6 from the one folder its parent loaded G6 from
(review-cfu3, finding 1; the certificate issuance window before 0.3.0).

The loop's children were given one exact transport for their import roots earlier (``g6.mcp.improve``: isolated
startup, then the roots as a JSON list argument). ``promotion_custody`` lives in a certificate-bound file and kept
handing its child a path-list string and starting it with ``-m`` where the server stands. Two routes to a root nobody
meant followed: a source folder whose name holds the platform's path-list separator was split, and its tail read
against the folder the server stands in; and that folder itself came first on the child's path. Either lets a planted
``g6`` answer the out-of-process signature check.

Each case starts a real parent on a small source installation (G6's own ``__init__``, ``core/__init__`` and
``promotion_custody``), which starts the real child. The evaluator service is no part of this library, so a stand-in at
the intended root shows the child still finds what it is meant to (the controls). No model, no network.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from g6.core import promotion_custody
from g6.mcp import improve

SRC = Path(promotion_custody.__file__).resolve().parents[2]
PARENT = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from g6.core import promotion_custody
assert Path(promotion_custody.__file__).resolve().is_relative_to(Path(sys.argv[1]).resolve())
os.chdir(sys.argv[2])
print(json.dumps(promotion_custody.verify_via_custody({"forged": True})))
'''


def _service(folder: Path, marker: Path, word: str) -> None:
    """A ``g6`` at ``folder`` whose evaluator service says who answered (``word`` in ``marker``) and that the witness
    is signed."""
    (folder / "g6" / "core").mkdir(parents=True, exist_ok=True)
    for init in (folder / "g6" / "__init__.py", folder / "g6" / "core" / "__init__.py"):
        if not init.exists():
            init.write_text("", encoding="utf-8")
    (folder / "g6" / "core" / "promotion_evaluator_service.py").write_text(
        "import json\n"
        f"with open({str(marker)!r}, 'a', encoding='utf-8') as f: f.write({word!r} + '\\n')\n"
        "print(json.dumps({'ok': True, 'result': True}))\n", encoding="utf-8")


def _installation(folder: Path) -> Path:
    """A source installation at ``folder``: the files of G6 the parent and its child import, copied as they are."""
    for rel in ("__init__.py", "core/__init__.py", "core/promotion_custody.py"):
        (folder / "g6" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SRC / "g6" / rel, folder / "g6" / rel)
    return folder


def _verify(source: Path, stands_in: Path) -> bool:
    """What a server that loaded G6 from ``source`` and stands in ``stands_in`` is told of a forged witness."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("PYTHON")}
    done = subprocess.run([sys.executable, "-P", "-B", "-c", PARENT, str(source), str(stands_in)], env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
    assert done.returncode == 0, (done.stdout, done.stderr)
    return json.loads(done.stdout.strip().splitlines()[-1])


def _said(marker: Path) -> list[str]:
    return marker.read_text(encoding="utf-8").splitlines() if marker.exists() else []


@pytest.mark.parametrize("name", ["server", "server" + os.pathsep + "planted"], ids=["plain", "separator"])
def test_the_child_finds_the_service_at_the_root_its_parent_loaded_g6_from(tmp_path, name):
    """The controls: the instrument sees a child that imports, and a separator in the folder's name loses nothing."""
    marker, work = tmp_path / "who-answered", tmp_path / "work"
    work.mkdir()
    source = _installation(tmp_path / name)
    _service(source, marker, "intended")
    assert _verify(source, work) is True
    assert _said(marker) == ["intended"]


def test_a_path_separator_in_the_source_folders_name_adds_no_import_root(tmp_path):
    marker, work = tmp_path / "who-answered", tmp_path / "work"
    source = _installation(tmp_path / ("server" + os.pathsep + "planted"))
    _service(work / "planted", marker, "foreign")   # where the tail of the split name points, from where the server stands
    assert _verify(source, work) is False, "a planted G6 answered the out-of-process signature check"
    assert _said(marker) == [], "the child imported a G6 from the split source path"


def test_the_folder_the_server_stands_in_is_no_import_root(tmp_path):
    marker, work = tmp_path / "who-answered", tmp_path / "work"
    source = _installation(tmp_path / "server")
    _service(work, marker, "foreign")
    assert _verify(source, work) is False, "a G6 in the folder the server stands in answered the signature check"
    assert _said(marker) == [], "the child imported a G6 from the folder the server stands in"


def test_the_intended_root_answers_beside_a_planted_one(tmp_path):
    marker, work = tmp_path / "who-answered", tmp_path / "work"
    source = _installation(tmp_path / ("server" + os.pathsep + "planted"))
    _service(source, marker, "intended")
    _service(work / "planted", marker, "foreign")
    _service(work, marker, "foreign")
    assert _verify(source, work) is True
    assert _said(marker) == ["intended"]


def test_an_extra_root_the_person_names_is_made_whole_where_the_server_stands(tmp_path, monkeypatch):
    """``G6_PROMOTION_EVALUATOR_EXTRA_PATH`` is the person's own path list: each entry is one root, read against the
    folder the parent stands in when it starts the child, as before, and handed over whole."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "oracles").mkdir()
    whole = str(tmp_path / "elsewhere")
    monkeypatch.setenv("G6_PROMOTION_EVALUATOR_EXTRA_PATH", os.pathsep.join(["oracles", "", whole]))
    roots = promotion_custody._import_roots()
    assert roots[:2] == [str(tmp_path / "oracles"), whole]
    assert Path(roots[2]).resolve() == SRC and all(improve._stands_alone(root) for root in roots)


def test_the_child_takes_its_roots_by_the_loop_childrens_one_transport():
    """One transport: the bootstrap is the loop children's own text, the start is isolated, the roots are one JSON
    list argument, and the child's environment names no import path."""
    assert promotion_custody._BOOTSTRAP == improve._BOOTSTRAP
    argv = promotion_custody._command("request.json")
    at = argv.index("-c")
    assert argv[0] == sys.executable and "-I" in argv[:at] and "-m" not in argv[:at]
    assert argv[at + 1] == promotion_custody._BOOTSTRAP
    assert json.loads(argv[at + 2]) == promotion_custody._import_roots()
    assert argv[at + 3:] == ["-m", "g6.core.promotion_evaluator_service", "request.json"]
    assert not [name for name in promotion_custody._service_env() if name.startswith("PYTHON")]
