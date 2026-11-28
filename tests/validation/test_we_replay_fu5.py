# SPDX-License-Identifier: Apache-2.0
"""Follow-up 5: recorded device names and roots lost during fresh scoring."""
import importlib.util
import json
import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from worked_examples import replay_rc as rc

_spec = importlib.util.spec_from_file_location(
    "fu5_trust", Path(__file__).with_name("test_we_replay_saved_trust.py"))
trust = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trust)
batch = trust.batch


@pytest.mark.skipif(os.name != "nt", reason="Windows index device-name boundary")
@pytest.mark.parametrize("name", ["NUL", "COM1"])
@pytest.mark.parametrize("component", ["task", "run"])
@pytest.mark.parametrize("key", ["run", "checked_run"])
def test_index_devices_refused_with_invalid_path_reason(tmp_path, monkeypatch, name, component, key):
    source = tmp_path / "source"
    (source / "dryrun" / "synthetic").mkdir(parents=True)
    task, run = (name, "ordinary") if component == "task" else ("synthetic", name)
    rel = f"dryrun/{task}/{run}"
    row = {"event": "started", "kind": "dryrun", "task": "synthetic",
           "run": "dryrun/synthetic/ordinary", key: rel}
    (source / "runs.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    real = rc._WindowsAPI._open

    def opening(api, path, access):
        if path.name.casefold() == name.casefold():
            pytest.fail("source-index device reached a native open")
        return real(api, path, access)

    monkeypatch.setattr(rc._WindowsAPI, "_open", opening)
    with pytest.raises(ValueError, match=f"^invalid {key} path: {rel}$"):
        rc.enumerate_cases(source, [], require_index=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows recorded device spelling boundary")
@pytest.mark.parametrize("route", ["index", "source", "pins", "canonical"])
def test_recorded_device_spellings_refused_before_path_walk(tmp_path, monkeypatch, route):
    archive = rc.Source(tmp_path)
    pins = rc._OriginPins(tmp_path)
    pins.root = rc._Opened(tmp_path, None, None)

    def unexpected(*args, **kwargs):
        pytest.fail("recorded device reached path resolution or an open")

    # The direct canonical route must validate within _absolute_path itself.
    # All recorded-name routes must validate before joining to a source path.
    if route == "source":
        monkeypatch.setattr(rc, "canonical_path", unexpected)
    elif route in ("index", "pins"):
        monkeypatch.setattr(rc, "_absolute_path", unexpected)
    else:
        monkeypatch.setattr(rc._WindowsAPI, "_open", unexpected)
    devices = ["CON", "PRN", "AUX", "NUL",
               *[f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "0123456789\u00b9\u00b2\u00b3"]]
    for device in devices:
        for spelling in (device, device.lower(), device.title()):
            for extension in ("", ".txt", ".tar.gz", " .txt"):
                for tail in ("", ".", " ", ". ", "..  "):
                    component = spelling + extension + tail
                    for name in (component, f"run/{component}/ordinary", f"run/synthetic/{component}"):
                        with pytest.raises(ValueError):
                            if route == "index":
                                rc._index_paths(archive, {"event": "started", "kind": "run", "run": name})
                            elif route == "source":
                                archive.path(name)
                            elif route == "pins":
                                pins.path(name)
                            else:
                                rc.canonical_path(tmp_path / name)


@pytest.mark.parametrize("route", ["invalid-origin", "older-rule"])
@pytest.mark.parametrize("replacement,reason", [
    ("missing", "a folder is missing"), ("file", "a path is not a folder")])
def test_fresh_score_classifies_root_lost_after_precheck(
        batch, tmp_path, monkeypatch, capsys, route, replacement, reason):
    if route == "invalid-origin":
        batch.saved["plan"]["cases"][0]["origins"][0]["run"] = None
    else:
        batch.saved["evidence_rule_version"] = 0
    root = batch.source
    real = rc._open_handle
    moved = []

    @contextmanager
    def opening(path, **kwargs):
        with real(path, **kwargs) as opened:
            yield opened
        if path == root and kwargs.get("directory") and not moved:
            root.rename(tmp_path / "parked-source")
            if replacement == "file":
                root.write_bytes(b"synthetic regular-file replacement")
            moved.append(True)

    monkeypatch.setattr(rc, "_open_handle", opening)
    status, result = trust.score(batch, capsys, True)
    assert moved == [True]
    assert (status, result["evidence_source"]) == (2, "unavailable")
    gap, = result["index_excluded"]
    assert gap["source_unavailable"] == "source folders could not be held: " + reason
    assert gap["reason"] == ("saved under an older evidence rule" if route == "older-rule"
                             else gap["source_unavailable"])
    assert all(value["status"] == "incomplete" for key, value in result["predictions"].items()
               if key in ("P1", "P2", "P3"))
