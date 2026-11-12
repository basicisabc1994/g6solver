# SPDX-License-Identifier: Apache-2.0
"""Review 4 of claude/freeze-holds: the five findings this follow-up closes, as the reviewer reproduced them, with the
reviewer's support code (its own temporary folder aside: pytest's serves here). Finding 3, a plugin loaded with
``importlib.util`` outside the roots and kept out of ``sys.modules``, is ruled out under the freeze's threat model (the
design's limits), so it is not here. FakeLLM is the only model; findings 4 to 6 take real Windows locks and handles."""
from __future__ import annotations

import ctypes
import importlib.util
import os
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

from g6.metaharness import shadow
from g6.metaharness.domain import Judge
from g6.metaharness.loop import JUDGE_UNPINNED, ShadowLoop, _roots

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the reviewer's locks and handles are Windows's")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


previous = load(Path(__file__).with_name("test_freeze_holds_review2.py"), "frz4_rig_previous")
domains, SETTINGS, NOW, enrolled = previous.domains, previous.SETTINGS, previous.NOW, previous.enrolled


def judge_file(folder, *, data=False):
    folder.mkdir()
    source = folder / "frz4_judge.py"
    source.write_text(
        "from pathlib import Path\n"
        "from frz2_domains import GSM\n"
        "def verdict(output, expected):\n"
        + ("    if (Path(__file__).parent / '__pycache__' / 'enabled.json').read_text() != 'true':\n"
           "        return False\n" if data else "")
        + "    return GSM.oracle(output, expected)\n",
        encoding="utf-8",
    )
    return load(source, "frz4_judge")


def _loop(tmp_path, module):
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(module.verdict, "independent")
    return domain, ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                              now=lambda: NOW, clock=domains.Clock())


def test_judge_data_under_cache_directory_cannot_drift_before_adoption(tmp_path, monkeypatch):
    """Finding 1."""
    judge_dir = tmp_path / "judge"
    module = judge_file(judge_dir, data=True)
    cache = judge_dir / "__pycache__"
    cache.mkdir(exist_ok=True)
    rule = cache / "enabled.json"
    rule.write_text("true", encoding="utf-8")
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(module.verdict, "independent")
    edits = []
    loop, _ = enrolled(tmp_path, domain=domain)
    sample = domain.pool[0]
    assert module.verdict("Answer: " + sample.expected, sample.expected)
    original = shadow._measured

    def save(report):
        rule.write_text("false", encoding="utf-8")
        edits.append(True)
        return original(report)

    monkeypatch.setattr(shadow, "_measured", save)
    reply = loop.tick()
    assert edits and not module.verdict("Answer: " + sample.expected, sample.expected)
    assert not loop.mh.adoption_rows(), f"adopted after judging data changed: {reply.get('outcome')}"


def test_declared_root_junction_is_not_silently_resolved_and_enrolled(tmp_path):
    """Finding 2."""
    import _winapi

    real_root = tmp_path / "judge"
    module = judge_file(real_root)
    alias = tmp_path / "judge-alias"
    _winapi.CreateJunction(str(real_root), str(alias))
    assert alias.is_junction()
    domain = domains.WordProblems(tmp_path)
    domain.judge = lambda: Judge(module.verdict, "independent")
    domain.roots = (alias, *[root for root in _roots(domain) if root != real_root])
    loop = ShadowLoop(domain, tmp_path / "loop", SETTINGS, proposer=domains.proposes({"prompt": domains.WORKING}),
                      now=lambda: NOW, clock=domains.Clock())
    reply = loop.enrol("Review owner")
    assert reply["warning_card"]["code"] == JUDGE_UNPINNED, "enrolled a junction as a judge root"


def test_locked_judging_data_cannot_be_enrolled_then_changed_without_a_digest(tmp_path, monkeypatch):
    """Finding 4."""
    import msvcrt

    judge_dir = tmp_path / "judge"
    judge_dir.mkdir()
    rule = judge_dir / "enabled.txt"
    rule.write_bytes(b"true ")
    source = judge_dir / "judge.py"
    source.write_text("from frz2_domains import GSM\n"
                      "def verdict(output, expected):\n"
                      "    rule.seek(0)\n"
                      "    return rule.read() == b'true ' and GSM.oracle(output, expected)\n", encoding="utf-8")
    module = load(source, "frz4_locked_judge")
    with rule.open("r+b", buffering=0) as stream:
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 5)
        try:
            module.rule = stream
            with pytest.raises(PermissionError):
                rule.read_bytes()
            domain, loop = _loop(tmp_path, module)
            try:
                enrolment = loop.enrol("Review owner")
            except OSError:
                return  # an unreadable freeze must never authorise an adoption
            if enrolment["completion_state"] == "blocked-escalated":
                return
            original, edits = shadow._measured, []

            def save(report):
                stream.seek(0)
                stream.write(b"false")
                stream.flush()
                edits.append(True)
                return original(report)

            monkeypatch.setattr(shadow, "_measured", save)
            try:
                reply = loop.tick()
            except OSError:
                reply = {}
            assert not loop.mh.adoption_rows(), f"adopted after unreadable judging data changed: {reply.get('outcome')}"
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 5)


def test_locked_imported_helper_is_not_pinned_as_an_empty_digest(tmp_path, monkeypatch):
    """Finding 5."""
    import msvcrt

    plugins = tmp_path / "plugins"
    plugins.mkdir()
    plugin = plugins / "criterion.py"
    original_source = b"def permits(output):\n    return True \n"
    changed_source = b"def permits(output):\n    return False\n"
    plugin.write_bytes(original_source)
    helper = load(plugin, "frz4_registered_criterion")
    judge_dir = tmp_path / "judge"
    judge_dir.mkdir()
    source = judge_dir / "judge.py"
    source.write_text("from frz2_domains import GSM\n"
                      "import frz4_registered_criterion\n"
                      "def verdict(output, expected):\n"
                      "    return frz4_registered_criterion.permits(output) and GSM.oracle(output, expected)\n",
                      encoding="utf-8")
    module = load(source, "frz4_registered_plugin_judge")
    try:
        with plugin.open("r+b", buffering=0) as stream:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, len(original_source))
            try:
                with pytest.raises(PermissionError):
                    plugin.read_bytes()
                domain, loop = _loop(tmp_path, module)
                try:
                    enrolment = loop.enrol("Review owner")
                except OSError:
                    return
                if enrolment["completion_state"] == "blocked-escalated":
                    return
                measured, edits = shadow._measured, []

                def save(report):
                    stream.seek(0)
                    stream.write(changed_source)
                    stream.flush()
                    edits.append(True)
                    return measured(report)

                monkeypatch.setattr(shadow, "_measured", save)
                try:
                    reply = loop.tick()
                except OSError:
                    reply = {}
                assert helper.permits("anything")
                assert not loop.mh.adoption_rows(), f"adopted after unreadable imported source changed: {reply.get('outcome')}"
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, len(original_source))
    finally:
        sys.modules.pop("frz4_registered_criterion", None)        # its file is this test's: no later reading holds it


def _denying_handle(folder: Path):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(folder), 0x80000000, 0, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


def test_unlistable_judge_subdirectory_cannot_hide_changed_data(tmp_path, monkeypatch):
    """Finding 6."""
    judge_dir = tmp_path / "judge"
    data = judge_dir / "data"
    data.mkdir(parents=True)
    rule = data / "enabled.json"
    rule.write_text("true", encoding="utf-8")
    source = judge_dir / "judge.py"
    source.write_text("from pathlib import Path\n"
                      "from frz2_domains import GSM\n"
                      "def verdict(output, expected):\n"
                      "    enabled = (Path(__file__).parent / 'data' / 'enabled.json').read_text() == 'true'\n"
                      "    return enabled and GSM.oracle(output, expected)\n", encoding="utf-8")
    module = load(source, "frz4_unlistable_judge")
    kernel, handle = _denying_handle(data)
    try:
        with pytest.raises(PermissionError):
            list(data.iterdir())
        try:
            domain, loop = _loop(tmp_path, module)
            enrolment = loop.enrol("Review owner")
        except OSError:
            return
        except ValueError as exc:     # the follow-up's review 1: a folder that cannot be listed is in reach, so no loop
            assert "G6_E_LOOP_EVALUATOR_IN_REACH" in str(exc)         # is built on it (changed on purpose)
            return
        if enrolment["completion_state"] == "blocked-escalated":
            return
        original, edits = shadow._measured, []

        def save(report):
            rule.write_text("false", encoding="utf-8")
            edits.append(True)
            return original(report)

        monkeypatch.setattr(shadow, "_measured", save)
        try:
            reply = loop.tick()
        except OSError:
            reply = {}
        assert not loop.mh.adoption_rows(), f"adopted after unlisted judging data changed: {reply.get('outcome')}"
    finally:
        assert kernel.CloseHandle(handle)
