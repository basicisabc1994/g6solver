# SPDX-License-Identifier: Apache-2.0
"""Structural census: interpreting sites share a door; KEEP boundaries cannot choose."""
import ast
import subprocess
from pathlib import Path

from g6.core import semantic

ROOT = Path(__file__).resolve().parents[2] / "src/g6"
MIGRATED = {
    "core/risk_signals.py": ("classify", "read_signals"),
    "core/selector.py": ("select",),
    "harness/completion_loop.py": ("asks", "critic_report"),
    "workforce/truth.py": ("extract",),
    "workforce/coding.py": ("names_access",),
    "reliability_pipeline/feasibility.py": ("deterministic_classify",),
    "reliability_pipeline/failure_modes.py": ("relevant",),
    "reliability_pipeline/plan.py": ("lexical_tripwire",),
    "intent_engine/elicitor.py": ("_coerce_literal",),
    "grounding/retrieval.py": ("search",),
    "solver/complexity_classifier.py": ("classify_task",),
    "solver/red_flag_detector.py": ("is_refusal",),
    "coding_agent_gate/detectors.py": ("risk_hits", "shortcut_hits", "overclaims"),
    "metaharness/exemplars.py": ("nearest",),
    "sources/australian_legal_corpus/corpus.py": ("uncovered_named",),
    "context/retrieve.py": ("retrieve",),
    "context/workspace.py": ("_notes",),
    "context/fuse.py": ("fuse",),
    "_context_engine/_fuse.py": ("fuse",),
    "_context_engine/_support.py": ("conflict_signal",),
    "_context_engine/_planner.py": ("features_of", "reformulation_of"),
    "context/compress.py": ("_select",),
    "debate/agreement.py": ("judge_agreement",),
    "bundles/contract_review.py": ("advice_signal",),
}
DEFERRED = {
    "core/discovery.py": "wire-route",
    "core/selector.py:workflow_improvement": "wire-route; safety suggestions alone migrate here",
    "grounding/claims.py": "wire-ground",
    "reliability_pipeline/intake.py": "wire-intent",
    "reliability_pipeline/premortem.py": "wire-intent",
    "epistemic_integrity/adjudicator.py": "certificate issuance",
    "epistemic_integrity/markers.py": "certificate issuance: imported_code binds this file too",
    "metaharness/coding_replay.py": "KEEP exact verdict protocol and independent benchmark evaluator",
    "bundles/contract_review.py:clause_structure": "P document parser; security advice boundary remains deterministic",
}
KEEP_FILES = (
    "grounding/tripwire.py", "core/agent_guidance.py", "context/redact.py",
    "action_gating/normalizer.py", "action_gating/scope.py", "coding_agent_gate/protected_paths.py",
    "ml/adapt_pytorch/layers.py", "metaharness/_code_safety.py", "jobs/_care/crisis.py",
    "core/llm.py", "cli/setup.py", "mcp/cloud_consent.py", "core/refusals.py",
    "harness/behaviour_question.py", "safety_engine/weakening.py", "workforce/result_view.py",
    "harness/patch_review.py", "core/door.py", "metaharness/loop.py",
    "mcp/consent_controls.py", "mcp/consent_transport.py",
)
KEEP_FUNCTIONS = {"mcp/__init__.py": ("_denial_for_agent",),
                  "bundles/contract_review.py": ("advice_signal",),
                  "intent_engine/elicitor.py": ("_says_something",)}


def leaf(node):
    return node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else ""


def calls(node):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def resolver(tree):
    aliases = {}

    def qualified(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return qualified(node.value) + "." + node.attr
        return ""

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = (node.module or "") + "." + alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
    assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)]
    for _ in range(len(assignments) + 1):
        before = dict(aliases)
        for assignment in assignments:
            if isinstance(assignment.value, (ast.Name, ast.Attribute)):
                for target in assignment.targets:
                    if isinstance(target, ast.Name) and target.id not in {n.id for n in ast.walk(assignment.value)
                                                                         if isinstance(n, ast.Name)}:
                        aliases[target.id] = qualified(assignment.value)
        if aliases == before:
            break
    return qualified


def choose_calls(tree, qualified=None):
    qualified = qualified or resolver(tree)
    found = []
    for call in calls(tree):
        symbol = qualified(call.func)
        if symbol not in {"semantic.decide", "g6.core.semantic.decide", "semantic.Question", "g6.core.semantic.Question"}:
            continue
        if symbol.endswith(".decide"):
            kind = call.args[0] if call.args else next((k.value for k in call.keywords if k.arg == "kind"), None)
            if not isinstance(kind, ast.Constant) or kind.value not in semantic.KINDS or semantic.KINDS[kind.value].combine == "choose":
                found.append(call)
        elif is_choose(call) or any(isinstance(a, ast.Constant) and a.value == "choose" for a in call.args):
            found.append(call)
        elif any(k.arg == "combine" and isinstance(k.value, ast.Constant) and k.value.value == "choose" for k in call.keywords):
            found.append(call)
    return found


def is_choose(call):
    name = leaf(call.func)
    if name == "decide" and call.args and isinstance(call.args[0], ast.Constant):
        kind = call.args[0].value
        return kind in semantic.KINDS and semantic.KINDS[kind].combine == "choose"
    if name == "Question":
        return any(isinstance(arg, ast.Constant) and arg.value == "choose" for arg in call.args) or any(
            kw.arg == "combine" and isinstance(kw.value, ast.Constant) and kw.value.value == "choose" for kw in call.keywords)
    return False


def test_every_migrated_semantic_site_uses_reader_and_has_no_model_send():
    for rel, functions in MIGRATED.items():
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for name in functions:
            node = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name
                         and not (len(n.body) == 1 and isinstance(n.body[0], ast.Expr)
                                  and isinstance(n.body[0].value, ast.Constant) and n.body[0].value.value is Ellipsis)), None)
            assert node is not None, (rel, name)
            names = {leaf(c.func) for c in calls(node)}
            assert names & {"decide", "read"}, (rel, name, "missing shared reader")
            assert not names & {"complete", "complete_structured", "complete_with_images", "extract_json"}, (rel, name)


def test_keep_boundaries_cannot_use_choose():
    for rel in KEEP_FILES:
        assert not choose_calls(ast.parse((ROOT / rel).read_text(encoding="utf-8"))), rel
    for rel, names in KEEP_FUNCTIONS.items():
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in names:
                assert not choose_calls(node, resolver(tree)), (rel, node.name)


def test_census_guard_recognises_injected_choose_and_direct_send():
    tree = ast.parse('def boundary(text):\n return semantic.decide("effort", text, "simple", ("simple",))')
    assert any(is_choose(c) for c in calls(tree))
    tree = ast.parse('def interpret(text):\n return llm.complete("read this", text)')
    assert {leaf(c.func) for c in calls(tree)} == {"complete"}


def test_census_detects_aliased_reader_and_model_send():
    source = ('from g6.core.semantic import decide as pick\n'
              'def boundary(text):\n return pick(kind="effort", text=text, floor="simple")')
    assert choose_calls(ast.parse(source))
    source = 'def interpret(llm, text):\n send = llm.complete\n return send("read this", text)'
    assert model_sends(source)


def model_sends(source):
    tree = ast.parse(source)
    qualified = resolver(tree)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = set()
    for call in calls(tree):
        if qualified(call.func).rpartition('.')[2] not in {"complete", "complete_structured", "complete_with_images"}:
            continue
        owners, node = [], call
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                owners.insert(0, node.name)
        found.add((".".join(owners), qualified(call.func)))
    return found


def test_new_model_sends_outside_reader_are_not_silently_added():
    # Existing generators and provider infrastructure retain their baseline sends.
    # Migrated interpretation functions are independently checked above. A new send
    # in any other module requires explicit census review rather than silently bypassing read.
    repo = ROOT.parents[1]
    paths = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "src/g6"],
                           cwd=repo, capture_output=True, text=True, check=True).stdout.splitlines()
    for rel in paths:
        if not rel.endswith(".py") or rel == "src/g6/core/semantic.py":
            continue
        source = (repo / rel).read_text(encoding="utf-8")
        if "complete" not in source:
            continue
        sends = model_sends(source)
        if not sends:
            continue
        before = subprocess.run(["git", "show", "c5782df:" + rel], cwd=repo, capture_output=True,
                                text=True, encoding="utf-8")
        allowed = model_sends(before.stdout) if before.returncode == 0 else set()
        assert sends <= allowed, (rel, sends - allowed)
