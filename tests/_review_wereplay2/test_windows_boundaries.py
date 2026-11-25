"""Synthetic, disk-backed probes; no Docker or real archive access."""
import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "validation"))
from worked_examples import replay_rc as rc


@pytest.mark.skipif(os.name != "nt", reason="Windows stream syntax")
def test_final_json_stream_is_refused_before_open(tmp_path, monkeypatch, capsys):
    # Confirm named streams survive realpath on this volume using benign data.
    witness = tmp_path / "safe.json"
    witness.write_bytes(b"synthetic")
    named = Path(str(witness) + ":payload")
    named.write_bytes(b"synthetic stream")
    assert named.resolve().name.endswith(":payload")
    assert named.read_bytes() == b"synthetic stream"
    # No forbidden file exists or is read: the open is a recording fake.
    target = tmp_path / "final-synthetic.json:payload"
    opened = []
    original = Path.open
    registration = rc._parse_registration(rc.REGISTRATION.read_bytes())
    saved = {"plan": {"registered_commit": "a" * 40, "cases": [], "modes": ["hook"]},
             "records": [], "image_ids": {}, "registration_sha256": registration.sha256, "s0": "before"}
    monkeypatch.setattr(rc, "load_registration", lambda: registration)

    def fake_open(path, *args, **kwargs):
        if str(path).casefold() == str(target).casefold():
            opened.append(path)
            return io.StringIO(json.dumps(saved))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    code = rc.main(["--score", str(target)])
    capsys.readouterr()
    assert not opened, f"forbidden stream reached open and CLI returned {code}: {opened}"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction race")
def test_parent_swap_after_gate_cannot_read_outside_source(tmp_path, monkeypatch):
    import _winapi

    root = tmp_path / "source"
    parent = root / "run"
    parent.mkdir(parents=True)
    (parent / "payload.txt").write_bytes(b"in scope")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "payload.txt").write_bytes(b"outside source")
    source = rc.Source(root)
    original = Path.open
    swapped = []

    def racing_open(path, *args, **kwargs):
        if path == parent / "payload.txt" and not swapped:
            parent.rename(root / "old-run")
            _winapi.CreateJunction(str(outside), str(parent))
            swapped.append(True)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", racing_open)
    try:
        result = source.read_bytes("run/payload.txt")
    except (ValueError, OSError):
        result = None
    assert swapped, "the deterministic swap hook did not run"
    assert result != b"outside source", "checked source path followed the swapped parent junction"


@pytest.mark.skipif(os.name != "nt", reason="Windows junction metadata access")
def test_existing_junction_is_refused_before_descendant_stat(tmp_path, monkeypatch):
    import _winapi

    root, outside = tmp_path / "source", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "payload.txt").write_bytes(b"synthetic")
    alias = root / "linked"
    _winapi.CreateJunction(str(outside), str(alias))
    target = alias / "payload.txt"
    original = Path.stat
    probed = []

    def record_stat(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == target:
            probed.append(result.st_ino)
        return result

    monkeypatch.setattr(Path, "stat", record_stat)
    with pytest.raises(ValueError, match="links are refused"):
        rc.Source(root).read_bytes("linked/payload.txt")
    assert not probed, "refusal happened only after stat reached the out-of-source child"
