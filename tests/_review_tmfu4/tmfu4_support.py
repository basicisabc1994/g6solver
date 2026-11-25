"""Shared helpers for review 4 of claude/template-methods-fu (tip 1464c340): reproductions on a real disk.

A reading is made the way reviews 2 and 3 made theirs (``tests/_review_tmfu2/tmfu2_support.py``): the template is
applied to a tree whose file lists the test states, G6's fixed bootstrap is started directly in a fresh interpreter
(``-I -S -B``, as ``behaviour_runner.run`` starts it) on the tree written to a real disk, then ``finish`` and
``verification_search.reading`` and ``classify`` run in this process. No confined run, model, network or host program
is used. Every tree is written at run time under ``tests/_review_tmfu4/disk``.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from g6.harness import behaviour_runner as runner
from g6.harness import verification_search as vs
from g6.harness import verification_templates as vt

EXAMPLE_NODE = "test_g6_example.py::test_example"
RUNS = "def run(value):\n    return value\n"
UNAVAILABLE = "G6_E_OBSERVATION_UNAVAILABLE"
#: A third-party package no interpreter of this review has; G6's runner (-I -S) has no installed package at all.
EXTERNAL = "tmfu4_absent_external"


def disk_for(nodeid: str) -> Path:
    folder = Path(__file__).parent / "disk" / hashlib.sha256(nodeid.encode()).hexdigest()[:12]
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def runner_at(commit):
    """``behaviour_runner`` as ``commit`` holds it, loaded beside the tree's own (read with git show)."""
    root = Path(__file__).resolve().parents[2]
    text = subprocess.run(["git", "-C", str(root), "show", commit + ":src/g6/harness/behaviour_runner.py"],
                          capture_output=True, timeout=60, check=True).stdout
    path = Path(__file__).parent / "disk" / ("behaviour_runner_" + commit + ".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    spec = importlib.util.spec_from_file_location("tmfu4_behaviour_runner_" + commit, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def default_bindings(template, function="subject.run"):
    return ({"function": function, "cases": [{"args": [1], "returns": 1}]} if template == "example.v1" else
            {"function": function, "mode": "outputs", "calls": [{"args": [1]}]})


def applied(folder, template, bindings=None, *, stated=RUNS, known=(), base=(), changed=(), roots=(),
            subject="subject.py"):
    """The template's resolved literal for ``bindings`` against the ``stated`` module in a tree whose host file
    lists are ``known`` (the working tree), ``base`` (the base commit) and ``changed`` (the change's files)."""
    path = folder.joinpath(*subject.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stated, encoding="utf-8")
    tree = vt.Tree(folder, SimpleNamespace(files=tuple(changed)), frozenset({subject, *known}), frozenset(base),
                   roots=tuple(roots))
    definition = vt.REGISTRY[template]
    return definition.applies(tree, definition.bindings.model_validate(bindings or default_bindings(template)),
                              vt.PATCH).data


def observed(folder, data, module=runner):
    """G6's own runner's reading: the fixed bootstrap in a fresh interpreter, then ``finish`` in this process."""
    data = module.located(folder, data)
    own = folder.parent / (folder.name + "-g6")   # G6's folder for its bootstrap and channel, beside the tree
    own.mkdir(parents=True, exist_ok=True)
    boot, channel = own / "bootstrap.py", own / "result.bin"
    boot.write_text(module.BOOTSTRAP, encoding="utf-8")
    channel.write_bytes(b"")
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(folder), str(channel),
                          json.dumps(module.request(data))], capture_output=True, timeout=60)
    return module.finish(ran.returncode, channel.read_bytes(), data), ran


def read(found):
    """The record ``behaviour_runner.run`` returns for ``found``, as verification search reads it."""
    verdict = {"passed": "passed", "failed": "failed", "error": "not_run"}[found["result"]]
    return vs.reading({"verdict": verdict, "said": found["detail"], "g6_observation": found,
                       "source_before": "0" * 64, "seconds": 0.1}, EXAMPLE_NODE)


def write_tree(folder, source, extra=None, subject="subject.py"):
    path = folder.joinpath(*subject.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    for rel, text in (extra or {}).items():
        path = folder.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def one_reading(folder, template, source, extra=None, *, bindings=None, module=runner, **lists):
    """``template`` compiled against the plain function, observed by G6's runner on ``source`` (``extra``: further
    files of the tree, relative path to text): the reading, ``finish``'s record and the process."""
    data = applied(folder, template, bindings, **lists)
    write_tree(folder, source, extra, lists.get("subject", "subject.py"))
    found, ran = observed(folder, data, module)
    return read(found), found, ran


def base_and_patch(request, template, before, after, *, intent="preserve", bindings=None, module=runner, **lists):
    """The same compiled instance observed on the base tree and on the patched tree, and ``classify``'s finding.
    ``before`` and ``after`` are ``(source, extra)`` of the base tree and of the patched tree."""
    disk = disk_for(request.node.nodeid + module.__name__)
    at_base, _, _ = one_reading(disk / "base", template, before[0], before[1], bindings=bindings, module=module, **lists)
    on_patch, found, ran = one_reading(disk / "patch", template, after[0], after[1], bindings=bindings,
                                       module=module, **lists)
    return at_base, on_patch, vs.classify(intent, on_patch, at_base), found, ran
