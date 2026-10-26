# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""Bounded differential evidence, executed by the bench; no census authority is inherited."""
from __future__ import annotations

import ast
import json
import shutil
import time
import uuid
from pathlib import Path

from g6.core import confine
from g6.core.component_process import ContainmentFailure, ExecutionInterrupted
from g6.workforce import truth
from g6.workforce import bench

# COPY: Reason for evidence that could not be obtained.
NOT_RUN = ("G6 could not compare the old and new behaviour of a function this change touches outside the task, "
           "so that part of the change is unchecked.")
# COPY: Names what an unmeasured comparison left unchecked (founder, 2026-10-08: a person is told which part nothing checked).
UNCHECKED = " Not compared: {names}."
# COPY: Says what changed in a function the caller declared as part of the change: a disclosure, not a defect (founder,
# 2026-10-08: "this should be a disclosure"). It says "declared", not "asked for": G6 never compares the declaration with the
# objective's words, and the person reads the declared names before confirming what counts as done (``acceptance.DECLARED``).
DISCLOSED = "A function declared as part of this change now behaves differently: {changes}."
# COPY: What changed in the function in scope, on inputs G6 took from the change itself (audit 010 WF-003: the frozen
# tests are not the only thing that runs it). A disclosure for the person, never a pass or a failure of this check.
SCOPE_DISCLOSED = "Outside the frozen tests, the function in scope now behaves differently: {changes}."
# COPY: Said when the function in scope ran before the change and did not return after it, on G6's inputs.
SCOPE_NOT_RETURNED = ("Outside the frozen tests, the function in scope ran on G6's inputs before the change and did not "
                      "return after it.")
MAX_SHOWN = 80  # characters of any value a person reads that code or a record supplied
MAX_DISCLOSED = 400  # characters of the whole sentence
WHOLE_FILE = "the whole file"
MODULE_LEVEL = "module-level code"
# COPY: Reason for discarding a comparison.
CHANGED = "The repository changed while G6 was comparing old and new behaviour, so the comparison was discarded."
# COPY: A learned probe can cover the function in scope; absent execution is unmeasured.
LEARNED_NOT_RUN = "G6 could not execute the learned probe against this baseline and candidate, so the check is unmeasured."
MAX_FUNCTIONS, MAX_INPUTS = 16, 32
#: R3 review C1: a learning round's own baseline runs, by round id, held in this process only (a plan cannot name a
#: round that exists): within a round each probe's baseline runs once, however many variants it replays.
ROUNDS: dict[str, dict] = {}


def _functions(body, prefix="") -> dict:
    found = {}
    for node in body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = prefix + node.name
            if not isinstance(node, ast.ClassDef):
                found[name] = node
            found.update(_functions(node.body, name + "."))
    return found


def _names(node) -> list[str]:
    """The names a module-level statement (not a function) defines or imports; its kind of code when it has none."""
    if isinstance(node, ast.ClassDef):
        return [node.name]
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [(alias.asname or alias.name) for alias in node.names]
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        found = [n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)]
        if found:
            return found
    return [MODULE_LEVEL]


def _unchecked_in(module, nodes, structure, before, after, scope) -> set:
    """A module whose imports, constants or classes changed can never pass the comparison (a rebinding could hide in
    it): the (module, name) of each statement that differs, and of each function left as it was that reads one of those
    names (its behaviour may have changed with them, and nothing compares it). A function the change edited is compared
    on its own account (``run``), and what can be compared still is."""
    names = {name for side, other in ((0, 1), (1, 0)) for node in nodes[side]
             if ast.dump(node) not in structure[other] for name in _names(node)} or {MODULE_LEVEL}
    unchanged = {name for name in before.keys() & after.keys() if ast.dump(before[name]) == ast.dump(after[name])
                 and (module, name) != (scope.get("module"), scope.get("function"))}
    readers = {name for name in unchanged
               if {n.id for n in ast.walk(after[name]) if isinstance(n, ast.Name)} & names}
    return {(module, name) for name in names | readers}


def _name_unchecked(probe: dict, bailed: set, params: dict) -> None:
    """Founder, 2026-10-08: whatever an unmeasured comparison left unchecked is named in the probe and in its reason."""
    if probe["status"] not in ("not_run", "fail") or probe["reason"] == CHANGED:
        return
    unchecked = bailed | {(f["module"], f["function"]) for f in probe["functions"] if f["status"] == "not_run"}
    if not unchecked and probe["status"] == "not_run":
        try:
            unchecked = {(str(module), WHOLE_FILE) for module in params.get("files") or {}}
        except (TypeError, AttributeError):
            unchecked = set()
    if not unchecked:
        return
    probe["unchecked"] = [{"module": module, "name": name} for module, name in sorted(unchecked)]
    if probe["status"] == "not_run":
        probe["reason"] += UNCHECKED.format(names=", ".join(f"{name} ({module})" for module, name in sorted(unchecked)))


def could_not_compare(probe: dict) -> bool:
    """Founder, 2026-10-08: whether a probe that did not run stopped for want of a comparison it could derive (every
    part it left is named in ``unchecked``), as opposed to one it began and could not finish: a runner that did not
    start, a candidate that did not return where the baseline did, a baseline that did not run, or a repository that
    changed under it. Only the first may leave a result at qualified-draft; the rest stop the mission as before."""
    if probe.get("status") != "not_run" or not probe.get("unchecked"):
        return False
    return not any(function.get("status") == "not_run"
                   and not all((function.get(side) or {}).get("executed", True) for side in ("baseline", "candidate"))
                   for function in probe.get("functions", []))


def _short(value) -> str:
    text = value if isinstance(value, str) and not isinstance(value, bool) else repr(value)
    return text if len(text) <= MAX_SHOWN else text[:MAX_SHOWN - 1] + "…"


def _disclose(found: list, said: str = DISCLOSED) -> str:
    """What changed in the functions declared as part of the change, as one sentence for the person: the first five
    differences, then how many more. The outputs are the candidate's own, so each is cut short and so is the sentence."""
    shown = [f"{_short(c['function'])}({', '.join(_short(repr(v)) for v in c['input'])}) gave "
             f"{_short(repr(c['baseline_output']))}, now {_short(repr(c['candidate_output']))}" for c in found[:5]]
    more = len(found) - len(shown)
    said = said.format(changes="; ".join(shown) + (f"; and {more} more" if more else ""))
    return said if len(said) <= MAX_DISCLOSED else said[:MAX_DISCLOSED - 1] + "…"


def _in_scope(source, files, scratch, module, name, inputs) -> dict:
    """Audit 010 WF-003: the function in scope, run before and after the change on the inputs the change's own
    comparisons give (``_inputs``), so something besides the frozen tests runs it. The objective asks it to change, so
    what changed is disclosed to the person, never a pass or a failure of this check; a change G6 can take no input from
    is ``not_compared``."""
    found = {"module": module, "function": name, "inputs": inputs, "status": "not_compared"}
    if not inputs:
        return found
    observations = []
    try:
        for side in ("baseline", "candidate"):
            copy = scratch / f"scope-{side}"
            shutil.copytree(source, copy, ignore=shutil.ignore_patterns(
                ".git", ".env*", "__pycache__", ".pytest_cache", "*.pyc"))
            if side == "candidate":
                bench._write(copy, files)
            values, found[side] = _observe(copy, module, name, inputs, scratch)
            observations.append(values)
    except (ValueError, OSError):  # what could not be observed is not compared; the check's own result stands
        return found
    if observations[0] is None:
        return found
    if observations[1] is None:
        found.update(status="not_returned", disclosed=SCOPE_NOT_RETURNED)
        return found
    changes = [{"module": module, "function": name, "input": [value], "baseline_output": baseline,
                "candidate_output": candidate}
               for value, baseline, candidate in zip(inputs, *observations, strict=True)
               if json.dumps(baseline, sort_keys=True) != json.dumps(candidate, sort_keys=True)]
    found["status"] = "changed" if changes else "same"
    if changes:
        found.update(changes=changes, disclosed=_disclose(changes, SCOPE_DISCLOSED))
    return found


def _inputs(before, after) -> list:
    if not all(isinstance(n, ast.FunctionDef) and len(n.args.posonlyargs + n.args.args) == 1
               and not (n.args.vararg or n.args.kwarg or n.args.kwonlyargs or n.decorator_list)
               for n in (before, after)):
        return []
    comparisons = [{ast.dump(n): n for n in ast.walk(fn) if isinstance(n, ast.Compare)}
                   for fn in (before, after)]
    values = set()
    for nodes, other, fn in ((comparisons[0], comparisons[1], before), (comparisons[1], comparisons[0], after)):
        for key in nodes.keys() - other.keys():
            node = nodes[key]
            arg = (fn.args.posonlyargs + fn.args.args)[0].arg
            operands = [node.left, *node.comparators]
            if not any(isinstance(n, ast.Name) and n.id == arg for n in operands):
                return []
            for operand in operands:
                if isinstance(operand, ast.Name) and operand.id == arg:
                    continue
                try:
                    literal = ast.literal_eval(operand)
                except (ValueError, TypeError):
                    return []
                if type(literal) not in (int, float):
                    return []
                values.update((literal - 1, literal, literal + 1))
    return sorted(values) if len(values) <= MAX_INPUTS else []


def _observe(repo, module, function, inputs, scratch, timeout: float | None = None) -> tuple[list | None, dict]:
    execution_id = uuid.uuid4().hex
    output = repo / ("g6_observed_" + execution_id + ".json")   # in the copy: a confined run may write there
    test = repo / ("test_g6_" + execution_id + ".py")
    test.write_text(
        "import json, runpy\nfrom pathlib import Path\n\ndef test_probe():\n"
        f"    fn = runpy.run_path({str(repo / module)!r})[{function!r}]\n"
        f"    values = [fn(value) for value in {inputs!r}]\n"
        f"    Path({str(output)!r}).write_text(json.dumps(values, allow_nan=False), encoding='utf-8')\n",
        encoding="utf-8")
    started = time.perf_counter()
    truth.LAST_RUN.clear()
    node = test.name + "::test_probe"
    ran = truth.run_isolated(node, repo) if timeout is None else truth.run_isolated(node, repo, timeout=timeout)
    receipt = {"execution_id": execution_id, "executed": ran, "returncode": truth.LAST_RUN.get("rc"),
               # #171, review M-6: whether the runner started (no start mark and no exit code: it never did)
               "started": bool(truth.LAST_RUN.get("started", truth.LAST_RUN.get("rc") is not None)),
               "seconds": time.perf_counter() - started, "inputs": inputs, "cost": None}
    if not ran or not output.is_file():
        return None, receipt
    try:
        values = json.loads(output.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None, receipt
    return (values if isinstance(values, list) and len(values) == len(inputs) else None), receipt


def run(params: dict, *, venture: str | None) -> dict:
    scope = params["regression"]
    if "learned" in scope:  # learned guards are deferred with the learning layer: never run, never a pass
        return {"ok": True, "result": {"probe": {
            "status": "not_run", "reason": LEARNED_NOT_RUN, "counterexamples": [], "functions": [], "authority": "A0",
            "guard_id": scope["learned"].get("guard_id", "") if isinstance(scope["learned"], dict) else ""}}}
    declared = {(d.get("module"), d.get("function")) for d in scope.get("declared") or [] if isinstance(d, dict)}
    probe = {"status": "not_run", "reason": NOT_RUN, "counterexamples": [], "functions": [], "authority": "A0"}
    bailed: set = set()
    try:
        source = bench._repo(params.get("repo"), venture)
        files = bench._files(params.get("files") or {})
        if source is None or any(bench._linked(p) for p in source.rglob("*")):   # a link or a junction
            raise ValueError(NOT_RUN)
        before_digest = bench._digest(source)
        changed, unsynthesized, in_scope = {}, set(), None
        replay = {(r["module"], r["function"]): r["inputs"] for r in scope.get("replay", [])}
        for module, text in files.items():
            bodies = [ast.parse((source / module).read_text(encoding="utf-8")).body, ast.parse(text).body]
            nodes = [[n for n in body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] for body in bodies]
            structure = [[ast.dump(n) for n in side] for side in nodes]
            before, after = (_functions(body) for body in bodies)
            if structure[0] != structure[1]:  # nothing here can pass, but what can still be compared is
                bailed |= _unchecked_in(module, nodes, structure, before, after, scope)
            for name in sorted(before.keys() | after.keys()):
                if module == scope.get("module") and name == scope.get("function"):
                    old, new = before.get(name), after.get(name)
                    if old is not None and new is not None and ast.dump(old) != ast.dump(new):  # observed apart
                        in_scope = (module, name, _inputs(old, new) if "." not in name else [])
                    continue
                old, new = before.get(name), after.get(name)
                if old is None:  # a function the change adds has no old behaviour to differ from: nothing to compare
                    continue
                if old is not None and new is not None and ast.dump(old) == ast.dump(new) and (module, name) not in replay:
                    continue
                inputs = _inputs(old, new) if old is not None and new is not None and "." not in name else []
                if not inputs and (old is None or new is None or ast.dump(old) != ast.dump(new)):
                    unsynthesized.add((module, name))
                changed[module, name] = sorted(set(inputs) | set(replay.get((module, name), [])))
        for key, inputs in replay.items():
            changed.setdefault(key, inputs)
        if len(changed) > MAX_FUNCTIONS:
            bailed |= set(changed)
            raise ValueError(NOT_RUN)
        with confine.run_folder(prefix="g6_differential_") as directory:   # G6's own: a confined run may use it
            scratch = Path(directory)
            for index, ((module, name), inputs) in enumerate(changed.items()):
                if len(inputs) > MAX_INPUTS:
                    bailed |= set(list(changed)[index:])
                    raise ValueError(NOT_RUN)
                item = {"module": module, "function": name, "inputs": inputs, "status": "not_run", "reason": NOT_RUN}
                probe["functions"].append(item)
                if not inputs:
                    continue
                observations = []
                for side in ("baseline", "candidate"):
                    copy = scratch / f"{index}-{side}"
                    shutil.copytree(source, copy, ignore=shutil.ignore_patterns(
                        ".git", ".env*", "__pycache__", ".pytest_cache", "*.pyc"))
                    if side == "candidate":
                        bench._write(copy, files)
                    values, receipt = _observe(copy, module, name, inputs, scratch)
                    item[side] = receipt
                    observations.append(values)
                if any(v is None for v in observations):
                    continue
                if (module, name) not in unsynthesized:
                    item.update(status="pass", reason="")
                for value, baseline, candidate in zip(inputs, *observations, strict=True):
                    if json.dumps(baseline, sort_keys=True) != json.dumps(candidate, sort_keys=True):
                        found = {"module": module, "function": name, "input": [value],
                                 "baseline_output": baseline, "candidate_output": candidate}
                        if (module, name) in declared:  # the objective asks this function to change: say so, do not send it back
                            probe.setdefault("disclosures", []).append(found)
                            continue
                        item["status"] = "fail"
                        probe["counterexamples"].append(found)
            if in_scope:
                probe["scope"] = _in_scope(source, files, scratch, *in_scope)
        if bench._digest(source) != before_digest:
            probe.update(counterexamples=[], reason=CHANGED)
            probe.pop("scope", None)
        elif probe["counterexamples"]:
            probe.update(status="fail", reason="")
        elif not bailed and all(f["status"] == "pass" for f in probe["functions"]):
            probe.update(status="pass" if changed else "not_applicable", reason="")
        probe["baseline_digest"] = before_digest
    except (ContainmentFailure, ExecutionInterrupted):
        raise
    except (ValueError, OSError, SyntaxError):
        pass
    if probe["counterexamples"] and probe["status"] == "not_run" and probe["reason"] != CHANGED:
        probe.update(status="fail", reason="")  # a difference found before the comparison gave up stands
    if probe["reason"] == CHANGED:
        probe.pop("disclosures", None)  # a comparison thrown away discloses nothing
    elif probe.get("disclosures"):
        probe["disclosed"] = _disclose(probe["disclosures"])
    _name_unchecked(probe, bailed, params)
    result = {"probe": probe}
    if probe["status"] != "not_run":
        result["passed"] = probe["status"] in ("pass", "not_applicable")
    return {"ok": True, "result": result}
