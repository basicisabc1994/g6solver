"""Shared helpers for review 3 of claude/template-methods-fu (tip b27712b1): reproductions on a real disk.

The readings are made the way review 2 made them (``tests/_review_tmfu2/tmfu2_support.py``, whose functions are used
unchanged): G6's fixed bootstrap started directly in a fresh interpreter (``-I -S -B``, as ``behaviour_runner.run``
starts it), then ``finish`` and ``verification_search.reading`` in this process. No confined run, model, network or
host program is used. Every tree is written at run time under ``tests/_review_tmfu3/disk``.
"""
import hashlib
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_review_tmfu2"))
import tmfu2_support as before  # noqa: E402
from g6.harness import verification_search as vs  # noqa: E402

RUNS = before.RUNS
UNAVAILABLE = "G6_E_OBSERVATION_UNAVAILABLE"


def disk_for(nodeid: str) -> Path:
    folder = Path(__file__).parent / "disk" / hashlib.sha256(nodeid.encode()).hexdigest()[:12]
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def runner_at(commit):
    """``behaviour_runner`` as ``commit`` holds it, loaded beside the tree's own (read with git show)."""
    import importlib.util
    import subprocess
    root = Path(__file__).resolve().parents[2]
    text = subprocess.run(["git", "-C", str(root), "show", commit + ":src/g6/harness/behaviour_runner.py"],
                          capture_output=True, timeout=60, check=True).stdout
    path = Path(__file__).parent / "disk" / ("behaviour_runner_" + commit + ".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    spec = importlib.util.spec_from_file_location("tmfu3_behaviour_runner_" + commit, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def patch_reading(request, template, patch_source, extra=None, module=None):
    """G6's runner's reading of the patched tree alone, by this tip's runner or by ``module`` (``runner_at``)."""
    disk = disk_for(request.node.nodeid + ("" if module is None else module.__name__))
    kwargs = {} if module is None else {"module": module}
    on_patch, found, ran = before.runner_reading(disk / "patch", template, RUNS, patch_source, extra, **kwargs)
    return on_patch, found, ran


def base_and_patch(request, template, patch_source, extra=None, base_source=RUNS, base_extra=None):
    """The template compiled against the plain function, observed by G6's runner at ``base_source`` and on
    ``patch_source`` (``extra``: further files of the patched tree), and the finding ``classify`` gives."""
    disk = disk_for(request.node.nodeid)
    at_base, _, _ = before.runner_reading(disk / "base", template, RUNS, base_source, base_extra)
    on_patch, found, ran = before.runner_reading(disk / "patch", template, RUNS, patch_source, extra)
    return at_base, on_patch, vs.classify("preserve", on_patch, at_base), found, ran
