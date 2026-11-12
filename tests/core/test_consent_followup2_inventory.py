# SPDX-License-Identifier: Apache-2.0
"""Inventory all child starts and environment builders under src/g6, from code."""
import ast
import inspect
from pathlib import Path

from g6.mcp import improve


# Each matching function is listed, including false positives. A newly added
# call or environment builder fails the inventory until its purpose is named.
PLACES = {
    "agents/_runtime/cli.py": ("clean_environment environment locate run", "outside agent CLI, not a loop child"),
    "algebra/runner.py": ("_environment _execute run_sage", "isolated algebra worker and outside Sage, component protocol"),
    "algorithm_design/autonomous.py": ("_run", "reply envelope named env, no child"),
    "bundles/server.py": ("caller_from_settings", "outside model CLI configuration"),
    "cegis/runner.py": ("_call", "isolated fixed CEGIS worker, component protocol"),
    "cli/setup.py": ("register_clients", "outside client registration command"),
    "code/_build.py": ("run_checks", "candidate code under core sandbox"),
    "code/_roots.py": ("load", "configuration reader, no child"),
    "code/adapt_library_synthesizer/ingestion.py": ("pdf_text", "PDF parser under core sandbox"),
    "code/swe_diagnostics/read.py": ("detect_build_env", "repository dependency description, no child"),
    "code/swe_diagnostics/block.py": ("detect_build_env", "repository dependency description, no child"),
    "cogarch/cog_arch_aixi/agent.py": ("episode environment register_environment unregister_environment", "simulation environment, no child"),
    "cogarch/cog_arch_aixi/block.py": ("CogArchAixiInput", "simulation environment, no child"),
    "core/component_certificate.py": ("signed_review", "outside signed-review program"),
    "core/component_process.py": ("run_contained run_process", "generic process transport, takes caller environment"),
    "core/confine.py": ("__init__ _features _probe start child_environment _quiet_env", "generic confinement transport and readiness probes"),
    "core/executor_contracts.py": ("AgentCapabilities", "capability model, no child"),
    "core/env.py": ("is_production_env", "host-mode predicate, no child"),
    "core/llm.py": ("provider_from_env", "provider configuration, no child"),
    "core/git_env.py": ("ask safe_git safe_git_env", "outside Git"),
    "core/host_models.py": ("_environment _run locate", "outside host model CLI"),
    "core/outside.py": ("OutsideProgram _run clean_environment", "outside program discovery and transport"),
    "core/promotion_custody.py": ("_call_service _service_env", "BOUND: evaluator Python child, offered for issuance"),
    "core/process_tree.py": ("__init__", "generic process transport, takes caller environment"),
    "core/sandbox.py": ("run_script", "isolated candidate scripts, deliberately imports sandbox code"),
    "csf/rollback.py": ("_git", "outside Git"),
    "distillation/sandbox.py": ("run_batch", "candidate scripts under core sandbox"),
    "formal/four_valued.py": ("evaluate _env", "logic environment, no child"),
    "harness/completion_loop.py": ("run", "reply envelope named env, no child"),
    "harness/patch_review.py": ("_reply", "reply envelope named env, no child"),
    "harness/verification_search.py": ("reproduce", "candidate scripts under core sandbox"),
    "integrations/database/config.py": ("load", "configuration reader, no child"),
    "knowledge/knowledge_converter/converters.py": ("_read_pdf", "isolated fixed PDF worker, component protocol"),
    "lean/runner.py": ("_environment _execute run", "outside Lean"),
    "mcp/improve.py": ("_run_act _spawn _environment", "LOOP: both launchers use the one checked builder"),
    "mcp/mission.py": ("register", "outside Git"),
    "media/_dsp.py": ("limit peak_envelope", "signal envelope, no child"),
    "media/_programs.py": ("environment run", "outside media programs"),
    "media/adapt_diagrams/dot.py": ("run_dot", "outside Graphviz"),
    "media/adapt_synth/render.py": ("adsr", "signal envelope, no child"),
    "metaharness/api.py": ("_envelope", "reply envelope named env, no child"),
    "model_checking/tlc.py": ("_execute _run_tool", "outside Java/TLC"),
    "prolog/runner.py": ("_environment _execute", "outside SWI-Prolog"),
    "self_maintenance/usage.py": ("read_limits", "outside codex app-server, not Python"),
    "sources/ctx_git/config.py": ("load", "configuration reader, no child"),
    "sources/ctx_git/runner.py": ("clone_session run session", "outside Git"),
    "sources/ctx_vision/ocr.py": ("read", "outside OCR program"),
    "workforce/_certificate_formal.py": ("check", "certificate scripts under core sandbox"),
    "workforce/_certificate_runner.py": ("observe", "certificate scripts under core sandbox"),
    "workforce/api.py": ("bind", "HTTP service configuration, no child"),
    "workforce/repo_tests.py": ("_bounded _git _linked_to_g6 _run child_env _repo_env _environment_of", "repository test Python, deliberately imports repository code; Git"),
    "workforce/service.py": ("bind certificates_enabled readiness_enabled", "HTTP service configuration, no child"),
    "workforce/truth.py": ("_run_owned run_isolated run_suite", "repository test Python, deliberately imports repository code"),
    "workforce/trace.py": ("_environment", "trace environment description, no child"),
    "workforce/verify.py": ("_git head_snapshot", "outside Git"),
}


def _places():
    root = Path(improve.__file__).resolve().parents[1]
    found = set()
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()

        def visit(node, owner="<module>"):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                owner = node.name
            matched = False
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                matched = (name.startswith(("subprocess.", "asyncio.create_subprocess", "multiprocessing."))
                           and name.rsplit(".", 1)[-1] in {"Popen", "run", "call", "check_call", "check_output",
                                                          "create_subprocess_exec", "create_subprocess_shell", "Process", "Pool"})
                matched |= name.endswith((".run_process", ".run_contained", ".run_script")) or name == "_Tree"
                matched |= any(isinstance(part, ast.Attribute) and ast.unparse(part) == "subprocess.Popen"
                               for part in ast.walk(node.func))
                matched |= any(k.arg == "env" for k in node.keywords) and not name.endswith("OutsideProgram")
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                matched |= any(ast.unparse(t) in {"env", "environment", "child_env"} for t in targets)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                matched |= node.name.endswith(("_environment", "_env")) or node.name in {"environment", "_environment_of"}
            if matched:
                found.add((rel, owner))
            for child in ast.iter_child_nodes(node):
                visit(child, owner)

        visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def test_each_child_start_and_environment_builder_has_a_named_purpose():
    expected = {(rel, name) for rel, (names, reason) in PLACES.items() for name in names.split() if reason}
    found = _places()
    assert found == expected, f"unlisted: {found - expected}; gone: {expected - found}"


def test_both_loop_launchers_use_the_one_allow_list_and_isolated_interpreter():
    for function in (improve._run_act, improve._spawn):
        tree = ast.parse(inspect.getsource(function))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        starts = [n for n in calls if ast.unparse(n.func) == "subprocess.Popen"]
        assert len(starts) == 1
        assert {k.arg: ast.unparse(k.value) for k in starts[0].keywords}["env"] == "_environment()"
        assert any(ast.unparse(n.func) == "_interpreter" for n in calls)
    assert "-I" in improve._interpreter({})
    tree = ast.parse(inspect.getsource(improve._environment))
    assert not any(isinstance(n, ast.Dict) and None in n.keys for n in ast.walk(tree))
    assert not any(isinstance(n, ast.Call) and ast.unparse(n.func) in {"os.environ.copy", "dict"}
                   and (ast.unparse(n.func) == "os.environ.copy" or any(ast.unparse(a) == "os.environ" for a in n.args))
                   for n in ast.walk(tree))


def test_the_builder_names_only_its_documented_allow_list():
    tree = ast.parse(inspect.getsource(improve._environment))
    loops = [n for n in ast.walk(tree) if isinstance(n, ast.For) and ast.unparse(n.target) == "name"]
    assert len(loops) == 1
    assert ast.literal_eval(loops[0].iter) == (
        "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP")
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    inherited = {ast.unparse(n.args[0]) for n in calls if ast.unparse(n.func) == "os.environ.get"}
    assert inherited == {"name", "'PATH'"}
    assert any(ast.unparse(n.func) == "_environment_path" for n in calls)
    assert not any(isinstance(n, ast.Subscript) and ast.unparse(n.value) == "os.environ" for n in ast.walk(tree))


def test_the_offered_evaluator_is_still_bound_by_every_original_certificate():
    root = Path(improve.__file__).resolve().parents[1]
    certificates = list(root.rglob("certificate.json"))
    assert len(certificates) == 5
    assert all('"g6/core/promotion_custody.py"' in p.read_text(encoding="utf-8") for p in certificates)
