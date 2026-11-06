# SPDX-License-Identifier: Apache-2.0
"""Round 3's distillation loop (PLAN.md, 2026-10-30): baseline, then generations, then one look at the held-out bugs.

Every run goes through ``run.py``, so its refusals and the round's stop apply. A result already saved is not run again.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import distill
import run as we
import wecommon as c
from g6.cegis import InputDomain, SearchResult, cegis

HERE = Path(__file__).resolve().parent
TARGET, MAX_GENERATIONS, MAX_MISSES, STOP_USD = 0.95, 8, 3, 170.0
SONNET = we.R3_MODELS[1]
TRAILERS = ("Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
            "Claude-Session: https://claude.ai/code/session_016opK1M2VFbXVve6kiJtAxW")


EXECUTOR = "container"      # run.py's --executor for every call: "host" runs Claude Code on the subscription (PLAN.md, 2026-11-04)


def tool(command: str, *args: str, timeout: int = 7800) -> str:
    """What the runner printed, once its exit says it succeeded: it prints its record and exits 0, and any failure exits otherwise,
    so a runner that failed gives nothing to read, whatever it printed (``wecommon.completed``), and stops the loop here."""
    try:
        return c.completed(subprocess.run([sys.executable, str(HERE / "run.py"), command, "--round", "3", "--executor", EXECUTOR,
                                           *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                          timeout=timeout), f"run.py {command}").stdout
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None


def last_json(text: str) -> dict[str, Any]:
    """Every JSON object ``text`` prints, merged: the runner prints its record and then the ledger's line."""
    found: dict[str, Any] = {}
    decoder, at = json.JSONDecoder(), text.find("{")
    while at >= 0:
        try:
            value, end = decoder.raw_decode(text, at)
        except ValueError:
            at = text.find("{", at + 1)
            continue
        found.update(value)
        at = text.find("{", end)
    return found


def _unchanged(archive: c.Archive, before: c.RunFacts, what: str) -> c.RunFacts:
    """The run's facts read again after its check and before its verdict is kept or given, on every path: an ending that changed
    meanwhile (a failure recorded during the check) is a refusal, and the next call scores the run on the ending it has then
    (study review 1, findings 11 and 20). Both the digest of its ending's files and the ending itself are compared, so an ending
    read from the run's own record, which no ending file digests, is held to the same rule (study review 2, finding 3)."""
    now = c.run_facts(archive, before.name)
    if now is None or now.ending_sha256 != before.ending_sha256 or dict(now.ending or {}) != dict(before.ending or {}):
        raise SystemExit(f"refused: {what}: {before.name}'s ending changed while it was checked; run again to score it as it ended")
    return now


def _keep(archive: c.Archive, row: dict[str, Any], **where: Any) -> None:
    try:
        c.keep(archive, row, **where)
    except c.ArchiveError as exc:        # the destination holds another run's row, read again just before the write
        raise SystemExit(f"refused: {exc}") from None


def one(root: Path, g6: str, arm: str, task: str, repeat: int) -> dict[str, Any]:
    """One run and its independent check, saved under ``loop/`` once, with the digest of the ending it was made under. A saved row
    is given only while it holds (``wecommon.holds``): a row saved under another ending is void, and is made again from the run's
    ending as it is now, its check included, only if the run may be scored now; the run itself is never made again for it.

    The saved rows are read once, by their one reader (``wecommon.shelf``), which finds a run's row whatever its file is called and
    gives its verdict by the one rule. Before any run or check, a row there that bars the asked arm, task and repeat (one refused
    for naming another arm's, bug's or repeat's run, or saying of its run what its start does not, kept under the asked name or
    naming a run of it) is refused, never given and never written over (review 4, findings 1 and 7; study review 1, finding 2); a
    torn one bars nothing, and is replaced. What the runner printed is refused, on the same rule, if it says of its run what the
    run's start does not (finding 3). After the check the run's ending is read again, and a change is a refusal (finding 11). The
    row is written whole or not at all, where its run's row is kept already, else under the name its start gives (``keep``)."""
    archive, key = c.Archive(root), (arm, task, repeat)
    asked = {"arm": arm, "task": task, "repeat": repeat}
    shelf = c.shelf(archive, we.amendment)
    held = shelf.get(key)
    barred = shelf.bars(key) or ("" if held is not None and held.holds else      # a held row is given, nothing is written
                                 shelf.blocks(key, file=held.file if held else None, run=held.facts.name if held else None))
    if barred:
        raise SystemExit(f"refused: the saved rows' folder holds no row of {arm} {task} {repeat}'s own run: {barred}")
    if held is not None and held.holds:      # admitted now, and saved under the ending the run has now
        return held.record()
    if held is not None:                     # void: made again from the run's ending as it is now
        rec, file = {"run": held.facts.name}, held.file
    else:
        rec, file = last_json(tool("run", "--task", task, "--g6", g6, "--arm", arm, "--model", SONNET, "--repeat", str(repeat))), None
        if not rec:          # the runner printed no record: nothing is known of the run, so nothing is saved
            raise SystemExit(f"no record: {arm} {task} {repeat}: the runner printed none")
        if rec.get("status") == "refused" and "already made" in str(rec.get("why")):   # made before a crash: check it
            made_as = we.r3_made(archive.rows(), root, (SONNET, task, arm, repeat))   # by the runner's own rule, from the index
            if made_as is None:
                raise SystemExit(f"no record: {arm} {task} {repeat}: the runner says it was made, and the index names no such run")
            rec = {"run": made_as}
    rel = str(rec.get("run") or "")      # the run's name is all that is taken from what the runner printed or a row saved
    facts = c.run_facts(archive, rel)    # every fact of the run, from the archive (wecommon.run_facts)
    end, made = (facts.ending, facts.ending_sha256) if facts is not None else (None, None)
    why = we.unscored_why(archive, rel)   # the one admission rule: refused, limited, no ending, no such run, or K3's baseline
    if not why and facts is not None:     # and a run of the arm, task and repeat asked for, by its start: never re-labelled
        why = c.disagrees(facts, rec) or c.disagrees(facts, asked)
    if why:              # refused, never counted as a failed run; limited: made again after the reset; a K3 run with no baseline
        status = (end or {}).get("status") or "no ending"
        said = (((rec.get("limit") or {}).get("resets") or "no reset given") if status == "limited"
                else "; ".join(str(x) for x in (why, (end or {}).get("why") or rec.get("why")) if x))
        raise SystemExit(f"{status}: {arm} {task} {repeat}: {said}")
    row = {"arm": facts.arm, "task": facts.task, "repeat": repeat, "run": facts.name, "status": end.get("status"),
           "why": rec.get("why"), "cost_usd": facts.cost_usd, "passes": False, "failing": None, "tests": [],
           **({c.MADE_UNDER: made} if made else {})}
    if c.on_patch(end):      # finished, or ended by a model refusal: scored on the patch it left
        final = [x for x in last_json(tool("check", "--task", task, "--g6", g6, "--run", facts.name, timeout=1200)).get("checks", [])
                 if x.get("patch") == "final"]     # by name: with no run named, check scores the bug's latest run, which a restart may not be
        if not final:       # no verdict is not a failed run: nothing is saved, and a restart checks it again
            raise SystemExit(f"no verdict for {arm} {task} {repeat}")
        passes, failing, tests = final[-1].get("passes"), final[-1].get("failing"), list(final[-1].get("fail_to_pass_failing") or [])
        if not isinstance(passes, bool):    # a check's word that is not true or false is no verdict, never a failed run
            raise SystemExit(f"no verdict for {arm} {task} {repeat}")
        row.update(passes=passes, tests=tests[:20], failing=failing if isinstance(failing, int) else len(failing or []))
    now = _unchanged(archive, facts, f"{arm} {task} {repeat}")
    _keep(archive, row, file=file)
    print(json.dumps({k: row[k] for k in ("arm", "task", "repeat", "status", "passes", "failing", "cost_usd")}), flush=True)
    return {**row, "passes": c.judge(now, row)}


def rescore(root: Path, g6: str) -> list[dict[str, Any]]:
    """Every saved run of a bug with an amendment, its final patch scored on the amended tests, saved beside the
    maintainers' verdict under ``loop/amended/`` (PLAN.md, 2026-10-31). A verdict holds for one run, one diff and the ending it was
    made under (``wecommon.holds``), and is scored again if any changes. No model; the saved rows stay as they are. The rows, and
    each run's amended verdict, are read once, by their one reader (``wecommon.shelf``): a row that is no run's own is never
    rescored, and an amended verdict is its run's whatever its file is called. Before a run's check, anything that bars its amended
    verdict (another run's kept under its name, or one refused) is refused, as ``loop.one`` refuses a row (study review 1, finding
    12); after it, the run's ending is read again, and a change is a refusal (finding 20). Each is written whole or not at all."""
    rows, archive = [], c.Archive(root)
    shelf = c.shelf(archive, we.amendment)
    for one in shelf.rows:
        facts = one.facts
        if one.amendment_sha256 is None:      # no amendment, or a run the one admission rule does not admit: none is made
            continue
        barred = shelf.bars(one.key, amended=True)
        if not barred and one.amended_holds:
            rows.append(one.amended_record())
            continue
        barred = barred or shelf.blocks(one.key, amended=True, run=facts.name, file=one.amended_file)
        if barred:
            raise SystemExit(f"refused: no amended verdict of {' '.join(map(str, one.key))}'s own run may be kept: {barred}")
        passes, said = False, {}      # a made run that did not end on its patch failed on either score, with no check
        if c.on_patch(facts.ending):
            said = last_json(tool("check", "--task", facts.task, "--g6", g6, "--run", facts.name, "--amended", timeout=1200))
            final = [x for x in said.get("checks", []) if x.get("patch") == "final"]
            passes = final[-1].get("passes") if final else None
            if not isinstance(passes, bool):       # no verdict is not a failed run
                raise SystemExit(f"no amended verdict for {Path(one.name).stem}")
        row = {**one.amended_record(), "amended_passes": passes}
        now = _unchanged(archive, facts, " ".join(map(str, one.key)))      # the ending first, then the amendment
        if said and said.get("amendment_sha256") != one.amendment_sha256:     # bound to the bytes the check applied (review 2, 9)
            raise SystemExit(f"refused: {' '.join(map(str, one.key))}: the check ran the amendment "
                             f"{str(said.get('amendment_sha256'))[:12]}, not the one read, {one.amendment_sha256[:12]}")
        diff = we.amendment(facts.task)          # read again: a verdict on the amendment as it stands, or none (review 2, 9)
        if diff is None or c.sha256_file(diff) != one.amendment_sha256:
            raise SystemExit(f"refused: {' '.join(map(str, one.key))}: the amendment changed while it was checked; run again")
        _keep(archive, row, amended=True, file=one.amended_file)
        rows.append({**row, "amended_passes": c.judge(now, row, amendment=one.amendment_sha256)})
    return rows


def spent(root: Path) -> float:
    return c.spent(c.Archive(root).rows())


def round_of(root: Path, g6: str, arm: str, tasks, repeats=(1, 2)) -> list[dict[str, Any]]:
    rows = []
    for task in tasks:
        for repeat in repeats:
            if spent(root) >= STOP_USD:
                raise SystemExit(f"stopped: ${spent(root):.2f} spent, the loop's stop is ${STOP_USD:.0f}")
            rows.append(one(root, g6, arm, task, repeat))
    return rows


def score(rows) -> float:
    return sum(r["passes"] for r in rows) / max(1, len(rows))


def commit(path: Path, message: str) -> None:
    git = ["git", "-C", str(HERE)]
    subprocess.run([*git, "add", str(path)], check=True)
    subprocess.run([*git, "-c", "user.name=Claude", "-c", "user.email=noreply@anthropic.com", "commit", "-q", "-m",
                    message + " [skip ci]", "-m", TRAILERS, "--", str(path)], check=True)
    subprocess.run([*git, "push", "-q", "origin", "HEAD"], check=True)


class Harness:
    """Stands where ``cegis`` has a sketch: the candidate is a generation's text, not a filled-in function."""

    @staticmethod
    def instantiate(control: dict[int, str]) -> str:
        return control[0]


class Generations:
    """G6's CEGIS loop over the harness: ``search`` distils the next text from what failed, ``evaluate`` runs it on every
    training bug. The oracle's answer is the maintainers' tests, seen only as each run's independent check."""

    def __init__(self, root: Path, g6: str, train: list[str], arm: str, text: str, rows: list[dict[str, Any]],
                 caller: Any) -> None:
        self.root, self.g6, self.train, self.caller = root, g6, train, caller
        self.best_arm, self.best_text, self.best = arm, text, rows
        self.n, self.misses, self.notes = 0, 0, []

    def search(self, sketch: Any, examples: Any, *, max_exhaustive: int = 0) -> SearchResult:
        while self.n < MAX_GENERATIONS and self.misses < MAX_MISSES and score(self.best) < TARGET:
            self.n += 1
            path = we.HARNESS / f"G{self.n}.txt"
            if path.is_file() or self.distilled(path):
                return SearchResult({0: f"G{self.n}"}, "distilled", False, self.n)
        return SearchResult(None, "stopped", False, self.n)

    def distilled(self, path: Path) -> bool:
        if spent(self.root) >= STOP_USD:
            self.misses = MAX_MISSES
            return False
        failed = [r for r in self.best if not r["passes"] and r["run"]]
        try:
            text, bad = distill.distill(self.best_text, failed, self.root, self.caller, self.notes)
        except distill.llm.LLMError as exc:
            text, bad = "", [f"the model call failed: {exc}"]
        folder = c.Archive(self.root).start("distill", path.stem, parent=self.best_arm)
        folder.write("text.txt", text)
        cost = getattr(self.caller, "cost_usd", 0.0)
        folder.finish({"cost_usd": cost, "named": bad, "parent": self.best_arm}, "finished" if text else "error")
        if self.caller is not None:
            self.caller.cost_usd = 0.0
        if bad or not text:
            print(f"== {path.stem}: refused, it names {bad}", flush=True)
            self.notes.append(f"An earlier attempt was refused for naming {bad}; name nothing of the kind.")
            self.misses += 1
            return False
        we.HARNESS.mkdir(exist_ok=True)
        path.write_text(text + "\n", "utf-8", newline="\n")
        commit(path, f"worked examples round 3: harness generation {self.n}")
        return True

    def evaluate(self, sketch: Any, controls: Any, inputs: Any) -> list[list[dict[str, bool]]]:
        arm, path = controls[0][0], we.HARNESS / f"{controls[0][0]}.txt"
        rows = round_of(self.root, self.g6, arm, self.train)
        print(f"== {arm}: train {score(rows):.2f} against {self.best_arm}'s {score(self.best):.2f}", flush=True)
        if score(rows) > score(self.best):
            self.best_arm, self.best_text, self.best, self.misses = arm, path.read_text("utf-8").strip(), rows, 0
            self.notes = []
        else:
            self.misses += 1
            self.notes.append(f"An attempt that scored {score(rows):.2f} did no better than {score(self.best):.2f}: "
                              f"{path.read_text('utf-8')[:1500]}")
        passes = {(r["task"], r["repeat"]): r["passes"] for r in rows}
        return [[{"ok": passes[(i["task"], i["repeat"])]} for i in inputs]]


def generations(root: Path, g6: str, train: list[str], arm: str, text: str, rows: list[dict[str, Any]],
                caller: Any) -> tuple[Any, str, list[dict[str, Any]]]:
    """``cegis`` over harness text, with the training bugs and repeats as its domain: its result, and the best arm."""
    runner = Generations(root, g6, train, arm, text, rows, caller)
    domain = InputDomain.of({"task": train, "repeat": [1, 2]})
    result = cegis(Harness(), lambda **_: True, domain=domain, runner=runner, max_iterations=MAX_GENERATIONS,
                   timeout_s=7 * 86400.0)
    return result, runner.best_arm, runner.best


def main(argv: list[str] | None = None) -> None:
    global EXECUTOR
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--g6", default=we.R3_LOOP_G6)
    ap.add_argument("--rescore", action="store_true", help="score saved runs on the amended tests, and nothing else")
    ap.add_argument("--executor", choices=("container", "host"), default="container",
                    help="run.py's executor for every run and check: host runs Claude Code on the host, on the subscription")
    args = ap.parse_args(argv)
    EXECUTOR = args.executor
    root = c.archive_root() / "round3"
    if args.rescore:
        print(json.dumps(rescore(root, args.g6), indent=1), flush=True)
        return
    tasks = [t.id for t in we.load_tasks()]
    train = [t for t in tasks if t not in we.R3_HELD]
    for arm in ("A", "E"):
        rows = round_of(root, args.g6, arm, tasks)
        print(f"== {arm}: all {score(rows):.2f}, train {score([r for r in rows if r['task'] in train]):.2f}", flush=True)

    best = [one(root, args.g6, "E", t, r) for t in train for r in (1, 2)]
    caller = distill.Metered(distill.llm.AnthropicCaller(distill.MODEL, api_key=we.read_key()))
    result, best_arm, best = generations(root, args.g6, train, "E", we.ADDENDUM_D, best, caller)
    c.write_whole(lambda: c.beside(root, "cegis.json"), json.dumps(      # the one writer of store files
        {"status": result.status, "iterations": result.iterations, "counterexamples": result.counterexamples,
         "boundary": result.boundary, "reason": result.reason, "best": best_arm}, indent=1, default=str).encode("utf-8"))
    print(f"== best {best_arm}: train {score(best):.2f}", flush=True)
    if best_arm != "E":
        repeats = range(1, we.R3_HELD_REPEATS + 1)
        base, held = (round_of(root, args.g6, arm, we.R3_HELD, repeats) for arm in ("A", "H" + best_arm[1:]))
        print(f"== held-out: {score(held):.2f} against arm A's {score(base):.2f}", flush=True)
    print(f"== spent ${spent(root):.2f}", flush=True)


if __name__ == "__main__":
    main()
