"""Review 4: properties that were attacked and held on 8d46efa3. Every node here passes."""
import json
import shutil

import pytest

from wrfu4_helpers import COMMIT, Docker, add_run, rc, score


def rich_source(root):
    """Two bugs, two archive rounds, shared and distinct patches, three kills, one unlabelled run."""
    archive = rc.c.Archive(root)
    args = {"repo": "/x", "task": "t", "requirements": [], "base": "z"}
    add_run(archive, "black-5634", b"p1|test_integrity_compromised", True, review=args)
    add_run(archive, "black-5634", b"p2|defect_detected", True, review=args)
    add_run(archive, "black-5634", b"p3|defect_detected", True)
    add_run(archive, "black-5634", b"p4|defect_detected", True)
    add_run(archive, "black-5634", b"p5|supported_within_scope", False, review=args)
    add_run(archive, "black-5634", b"p5|supported_within_scope", False, review={**args, "task": "u"}, arm="C")
    add_run(archive, "click-3769", b"q1|insufficient_evidence", True, review=args)
    add_run(archive, "click-3769", b"q2|defect_detected", False)
    add_run(archive, "click-3769", b"q3|defect_detected", False, check=False)
    add_run(rc.c.Archive(root / "round2"), "click-3769", b"q4|supported_within_scope", False, review=args,
            arm="original")
    return archive


def listing(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*"))


@pytest.mark.parametrize("s0", ["before", "after"])
@pytest.mark.parametrize("fail_after", [None, 5])
def test_current_batch_and_bumped_rule_both_rescore_to_the_live_result(world, s0, fail_after):
    """Kills (P1, P2 after S0, P3), failed calls, exclusions, two modes, two rounds: all three results are equal."""
    source, output = world.tmp / "source", world.tmp / "out"
    rich_source(source)
    live, batch, _ = world.live(source, output, modes=("agent", "hook"), s0=s0, docker=Docker(fail_after=fail_after))
    kills = {name for name, p in live["predictions"].items() if p["kill"]}
    assert kills == ({"P3"} if fail_after else {"P1", "P3"} | ({"P2"} if s0 == "after" else set()))
    before = listing(output), listing(source)
    world.monkeypatch.setattr(rc, "EVIDENCE_RULE_VERSION", 2)   # an older batch, its source unchanged
    code, captured = score(batch, world.capsys)
    assert code == 0 and json.loads(captured.out) == live
    world.monkeypatch.setattr(rc, "EVIDENCE_RULE_VERSION", 1)
    source.rename(source.with_name("moved"))                    # a current batch needs no source
    code, captured = score(batch, world.capsys)
    assert code == 0 and json.loads(captured.out) == live
    source.with_name("moved").rename(source)
    assert (listing(output), listing(source)) == before         # --score wrote nothing anywhere


def test_failed_and_interrupted_batches(world):
    source = world.tmp / "source"
    rich_source(source)
    _, batch, captured = world.live(source, world.tmp / "pin", docker=Docker(bad_pin=True), expect=1)
    assert "task image G6 commit differs from registered commit" in captured.err
    saved = json.loads(batch.read_text(encoding="utf-8"))
    assert saved["status"] == "failed" and saved["records"] == [] and saved["evidence_rule_version"] == 1
    code, captured = score(batch, world.capsys)
    result = json.loads(captured.out)
    assert code == 0 and all(p["status"] in ("incomplete", "disabled") and p["prediction_met"] is None
                             for p in result["predictions"].values())
    world.monkeypatch.setenv("G6_WE_ARCHIVE", str(world.tmp / "stopped"))
    with pytest.raises(KeyboardInterrupt):
        rc.main(["--source", str(source), "--registered-commit", COMMIT, "--mode", "hook"],
                docker=Docker(interrupt_after=2))
    world.capsys.readouterr()
    folder = next((world.tmp / "stopped").glob("replay-rc-batch/development/*"))
    assert list(folder.iterdir()) == []                         # a start event only
    code, captured = score(folder / "run.json", world.capsys)
    assert code == 1 and not captured.out and "absent" in captured.err


@pytest.mark.parametrize("version", ["1.0", "1e0", "true", '"1"', "1" + "0" * 30, "-1", "0", "2", "[1]", '{"a": 1}',
                                     "null", "absent", "1" + "0" * 5000])
@pytest.mark.parametrize("source_present", [True, False])
def test_version_spellings(world, version, source_present):
    source = world.tmp / "source"
    rich_source(source)
    live, batch, _ = world.live(source, world.tmp / "out")
    text = batch.read_text(encoding="utf-8")
    needle = '"evidence_rule_version": 1,'
    assert text.count(needle) == 1
    batch.write_text(text.replace(needle, "" if version == "absent" else f'"evidence_rule_version": {version},'),
                     encoding="utf-8")
    if not source_present:
        source.rename(source.with_name("moved"))
    code, captured = score(batch, world.capsys)
    if len(version) > 4300:                                     # Python's own integer-text limit: a plain refusal
        assert code == 1 and not captured.out and "invalid source JSON" in captured.err
    elif source_present:                                        # labels re-derived from the unchanged source
        assert code == 0 and json.loads(captured.out) == live
    else:
        result = json.loads(captured.out)
        assert code == 2 and result["by_bug"] == {}
        assert result["index_excluded"][0]["reason"] == "saved under an older evidence rule"
        assert {(p["status"], p["prediction_met"], p["kill"]) for p in result["predictions"].values()} == {
            ("incomplete", None, None), ("disabled", None, None)}


@pytest.mark.parametrize("change", ["lower-case root", "forward slashes", "extended prefix", "trailing separator",
                                    "upper-case file", "whole index row appended", "round3 folder added",
                                    "task added to the selection"])
def test_spellings_and_later_changes_end_in_exit_2(world, change):
    source = world.tmp / "source"
    rich_source(source)
    _, batch, _ = world.live(source, world.tmp / "out")
    saved = json.loads(batch.read_text(encoding="utf-8"))
    saved["evidence_rule_version"] = 0
    snapshot = saved["plan"]["source_snapshot"]
    root = snapshot["root"]
    if change == "lower-case root":
        snapshot["root"] = root.lower()
    elif change == "forward slashes":
        snapshot["root"] = root.replace("\\", "/")
    elif change == "extended prefix":
        snapshot["root"] = "\\\\?\\" + root
    elif change == "trailing separator":
        snapshot["root"] = root + "\\"
    elif change == "upper-case file":
        patch = next(source.glob("run/*/*/final.patch"))
        patch.rename(patch.with_name("FINAL.PATCH"))
    elif change == "whole index row appended":
        with (source / "runs.jsonl").open("ab") as stream:
            stream.write(b'{"event": "started", "run": "build/x/y", "kind": "build", "task": "x"}\n')
    elif change == "round3 folder added":
        (source / "round3").mkdir()
    else:
        tasks = rc.we.load_tasks()
        world.monkeypatch.setattr(rc.we, "load_tasks", lambda: [*tasks, type(tasks[0])(id="new-1")])
    batch.write_text(json.dumps(saved), encoding="utf-8")
    code, captured = score(batch, world.capsys)
    result = json.loads(captured.out)
    assert code == 2 and result["predictions"]["P3"]["status"] == "incomplete"
    assert result["predictions"]["P3"]["kill"] is None
    assert result["index_excluded"][0]["source_unavailable"] == "source identity, selection or evidence bytes changed"


def test_every_single_file_change_after_an_older_batch(world):
    """About two and a half minutes: each file of the source is appended to, emptied, deleted and re-cased in turn.

    A change to any file in the saved read manifest ends in exit 2; a change to any other file leaves the rescored
    result equal to the live one. So enumeration reads nothing that the manifest does not hold.
    """
    source, output = world.tmp / "source", world.tmp / "out"
    rich_source(source)
    live, batch, _ = world.live(source, output, modes=("agent", "hook"))
    saved = json.loads(batch.read_text(encoding="utf-8"))
    read = set(saved["plan"]["source_snapshot"]["files"])
    saved["evidence_rule_version"] = 0
    batch.write_text(json.dumps(saved), encoding="utf-8")
    kept = world.tmp / "kept"
    refused = unread = 0
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = path.relative_to(source).as_posix()
        for change in ("append", "empty", "delete", "case"):
            shutil.copyfile(path, kept)
            target = path
            if change == "append":
                with path.open("ab") as stream:
                    stream.write(b" ")
            elif change == "empty":
                path.write_bytes(b"")
            elif change == "delete":
                path.unlink()
            else:
                target = path.with_name(path.name.upper())
                path.rename(target)
            code, captured = score(batch, world.capsys)
            if target.exists():
                target.unlink()
            shutil.copyfile(kept, path)
            if rel in read:
                assert code == 2, (rel, change, code, captured.err)
                refused += 1
            else:
                assert code == 0 and json.loads(captured.out) == live, (rel, change, code)
                unread += 1
    code, captured = score(batch, world.capsys)
    assert code == 0 and json.loads(captured.out) == live
    assert refused and unread
