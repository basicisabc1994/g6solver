# SPDX-License-Identifier: Apache-2.0
"""The kit must fail a bad extension: ``fixtures/bad_extension`` breaks every check, and each check function reports
it. The fixture is registered through a temporary family (``fixtures/bad_family``, whose own files are the family's
shared code), the way ``tests/core/test_families.py`` registers its member, so the runtime checks (the registry, the
door, the D9 child process) see it like any family member. The static checks must flag exactly the lines marked ``#
kit: <gates>``, in the member and in the family's shared code, and none of the lines marked ``# allowed``."""
from __future__ import annotations

import importlib
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import conformance_kit as kit
import pytest

import g6
from g6.core import discovery, http, outside, registry

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE = FIXTURES / "bad_extension"
FAMILY = "conformance_bad"
MARK = re.compile(r"# kit: ([A-Za-z0-9 -]+)$")
STATIC = [
    (kit.g1_labels, {"G1": "G1"}), (kit.g2_g9_processes_and_network, {"G2": "G2", "G9": "G9"}),
    (kit.g6_models_and_keys, {"G6": "G6"}), (kit.g7_doubles, {"G7": "G7"}),
    (kit.d4_isolation, {"D4": "D4-isolation"}), (kit.d4_home, {"D4": "D4-home"}), (kit.d9_imports, {"D9": "D9"}),
]


@pytest.fixture
def bad(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry_reset) -> kit.Target:
    family = tmp_path / "g6" / FAMILY
    shutil.copytree(FIXTURES / "bad_family", family, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(FIXTURE, family / "bad_extension", ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(g6, "__path__", [*g6.__path__, str(tmp_path / "g6")])
    monkeypatch.setattr(registry, "FAMILIES", registry.FAMILIES | {FAMILY})
    importlib.invalidate_caches()
    yield kit.target("bad_extension")
    for name in [m for m in sys.modules if m == f"g6.{FAMILY}" or m.startswith(f"g6.{FAMILY}.")]:
        del sys.modules[name]


@pytest.fixture
def shared(bad: kit.Target) -> kit.Target:
    return kit.shared_target(FAMILY)


def _marked(gate: str, path: Path = FIXTURE / "leaky.py") -> set[int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return {n for n, line in enumerate(lines, 1) if (m := MARK.search(line)) and gate in m.group(1).split()}


def _lines(problems: list[kit.Problem], gate: str, path: Path | str = "leaky.py") -> set[int]:
    if isinstance(path, Path):
        return {p.line for p in problems if p.gate == gate and p.file == kit.rel(path)}
    return {p.line for p in problems if p.gate == gate and p.file.endswith(f"/{path}")}


def _says(problems: list[kit.Problem], *fragments: str) -> None:
    text = "\n".join(p.message for p in problems)
    assert [f for f in fragments if f not in text] == [], text


def _exactly_the_marked_lines(target: kit.Target, check, marks: dict[str, str]) -> None:
    found = check(target)
    for gate, marker in marks.items():
        assert any(_marked(marker, path) for path in target.files), (marker, "no line is marked")
        for path in target.files:
            assert _lines(found, gate, path) == _marked(marker, path), (path.name, gate)
    assert {p.gate for p in found} <= set(marks) and {p.file for p in found} <= {kit.rel(p) for p in target.files}


def test_it_registers_as_a_family_member(bad: kit.Target) -> None:
    assert bad.family == FAMILY and bad.extension and bad.package == f"g6.{FAMILY}.bad_extension"
    assert all(check.required(bad) for check in kit.CHECKS)


@pytest.mark.parametrize("check", kit.CHECKS, ids=lambda c: c.name)
def test_every_check_reports_the_fixture(bad: kit.Target, check: kit.Check, monkeypatch: pytest.MonkeyPatch) -> None:
    if check.run is kit.d4_has_block:  # the fixture needs its block for every other check, so it loses it here
        monkeypatch.setattr(registry.get_registry().get_module("bad_extension"), "__g6_block__", None)
    assert check.run(bad), f"{check.name} found nothing wrong with the bad fixture"


@pytest.mark.parametrize(("check", "marks"), STATIC, ids=lambda v: getattr(v, "__name__", ""))
def test_the_static_checks_flag_exactly_the_marked_lines(bad: kit.Target, check, marks: dict[str, str]) -> None:
    _exactly_the_marked_lines(bad, check, marks)


def test_d8_reports_no_listing_and_a_malformed_vetting_declaration(bad: kit.Target) -> None:
    # the page lists an extension only from its catalogue row, and a vetted one only once its review is complete
    _says(kit.d8_page_truth(bad), "no row on the Components page", "unknown kind: 'astrology'")


def test_d8_reports_a_vetted_area_the_declaration_leaves_out_and_a_row_in_another_package(bad: kit.Target) -> None:
    row = {"name": "bad_extension", "package": bad.package, "hidden": None, "vetting": ["clinical"]}
    _says(kit.d8_page_truth(bad, catalogue={"components": [row]}), "does not declare clinical")
    moved = dict(row, package="g6.elsewhere.bad_extension", vetting=[])
    _says(kit.d8_page_truth(bad, catalogue={"components": [moved]}), "lists it as g6.elsewhere.bad_extension")


def test_d8_passes_a_listed_extension_with_a_complete_declaration() -> None:
    target = kit.target("llm_router")
    assert kit.d8_page_truth(target) == []


def test_the_family_shared_code_is_its_own_target(bad: kit.Target, shared: kit.Target) -> None:
    assert shared.name == shared.package == f"g6.{FAMILY}" and shared.shared and shared.extension
    assert {p.relative_to(shared.root).as_posix() for p in shared.files} == {
        "__init__.py", "_shared.py", "_no_header.py", "_net/__init__.py"}
    assert not set(shared.files) & set(bad.files)
    assert [t.name for t in kit.shared_targets()].count(f"g6.{FAMILY}") == 1
    assert {c.name for c in kit.CHECKS if c.shared} == {"g1_labels", "g2_g9_processes_and_network",
                                                         "g6_models_and_keys", "g7_doubles", "g8_headers",
                                                         "g8_vendored", "d4_isolation", "d4_home", "d9_imports"}


@pytest.mark.parametrize("check", [c for c in kit.CHECKS if c.shared], ids=lambda c: c.name)
def test_every_shared_check_reports_the_family_shared_code(shared: kit.Target, check: kit.Check) -> None:
    assert check.required(shared) and check.run(shared), f"{check.name} found nothing wrong with the shared code"


@pytest.mark.parametrize(("check", "marks"), STATIC, ids=lambda v: getattr(v, "__name__", ""))
def test_the_static_checks_flag_exactly_the_marked_lines_of_the_shared_code(shared: kit.Target, check,
                                                                             marks: dict[str, str]) -> None:
    _exactly_the_marked_lines(shared, check, marks)


def test_the_shared_code_is_read_through_its_imports_and_held_to_g8(shared: kit.Target) -> None:
    _says(kit.g2_g9_processes_and_network(shared), "os.system() starts a program",
          "uses urllib.request through import urllib, without importing it")
    _says(kit.g6_models_and_keys(shared), "reads the environment for HF_TOKEN")
    _says(kit.d4_isolation(shared), "a member of its own family, g6.conformance_bad.bad_extension")
    assert {p.file.rsplit("/", 1)[-1] for p in kit.g8_headers(shared)} == {"_no_header.py"}
    vendored = kit.g8_vendored(shared)
    _says(vendored, "vendored lent carries no LICENSE file")
    assert "borrowed" not in "\n".join(p.message for p in vendored)  # the member's folder is the member's


def test_a_waiver_can_name_the_family_shared_code(shared: kit.Target) -> None:
    hit = next(p for p in kit.g2_g9_processes_and_network(shared) if "subprocess.Popen" in p.message)
    waiver = kit.Waiver(shared.name, "G2", hit.file, "subprocess.Popen(cmd)", "why", "permanent: why")
    assert waiver.covers(shared.name, hit) and not waiver.covers("bad_extension", hit)


def test_d4_names_the_sibling_and_the_other_family(bad: kit.Target) -> None:
    _says(kit.d4_isolation(bad), "a sibling member, g6.conformance_bad.sibling_member",
          "another family's member, g6.media.image_inspector")


def test_g1_names_each_way_a_label_is_given(bad: kit.Target) -> None:
    _says(kit.g1_labels(bad), "'cleared' given as a default", "'verified' given as an assignment value",
          "'certified' given as keyword state=", "'safe' given as a dict value", "'proved' given as a return value",
          "a fixed confidence given to the name 'confidence'", "a fixed confidence given to the keyword 'confidence'",
          "'verified' given as a dict value, from STATES[0]", "a fixed confidence given to the key 'confidence'")


def test_g2_allows_only_the_bounded_child(bad: kit.Target) -> None:
    found = kit.g2_g9_processes_and_network(bad)
    _says(found, "eval() runs code", "exec() runs code", "builtins.exec() runs code", "os.system() starts a program",
          "subprocess.Popen without both", "subprocess.check_output used outside a bounded call",
          "subprocess.Popen used outside a bounded call", "shell= runs a command line through the shell",
          "imports pty", "asyncio.create_subprocess_exec() starts an unbounded program")
    # none; timeout=None; dict(os.environ); then a parameter defaulting to os.environ, a copy bound to a local name, a
    # module-level {**os.environ}, a comprehension dropping one key; the kept-keys dict bound to a local name is clean
    assert len([p for p in found if "subprocess.run without" in p.message]) == 7
    assert len([p for p in found if "os.system() starts" in p.message]) == 3  # os.system, from os import, getattr


def test_g9_reads_every_way_to_the_network(bad: kit.Target) -> None:
    _says(kit.g2_g9_processes_and_network(bad), "imports websocket", "imports httpx",
          "asyncio.open_connection() opens a connection", "create_connection() opens a connection")


def test_g6_follows_a_module_constant_to_the_key(bad: kit.Target) -> None:
    _says(kit.g6_models_and_keys(bad), "imports anthropic", "imports openai", "for FIXTURE_SERVICE_TOKEN",
          "for FIXTURE_CLIENT_SECRET", "for FIXTURE_API_KEY", "names the key FIXTURE_API_KEY",
          "imports openai by name", "imports litellm by name", "reads FIXTURE_HF_TOKEN, a key's name",
          "reads the environment for FIXTURE_LOCAL_TOKEN")


def test_g6_allows_a_key_named_only_as_a_g6_core_keys_declaration(bad: kit.Target) -> None:
    # owner ruling 2026-10-21: an extension declares the keys it needs (g6.core.keys.Key) and reads them only from the
    # person's key file; the name anywhere else, or read from the environment, is still a finding
    found = kit.g6_models_and_keys(bad)
    said = "\n".join(p.message for p in found)
    assert "FIXTURE_DECLARED_API_KEY" not in said and "FIXTURE_KEYWORD_API_KEY" not in said
    _says(found, "names the key FIXTURE_PURPOSE_API_KEY outside a g6.core.keys.Key declaration",
          "reads the environment for FIXTURE_SERVICE_TOKEN: keys come only from the person's key file")


def test_d9_names_each_import_no_extra_declares(bad: kit.Target) -> None:
    found = kit.d9_imports(bad)
    _says(found, "imports numpy, which is not the standard library", "imports websocket", "imports litellm")
    assert "torch" not in "\n".join(p.message for p in found)  # under if TYPE_CHECKING:, never run


def test_g5_names_an_op_the_block_lacks_and_a_registry_that_does_not_refuse(bad: kit.Target) -> None:
    _says(kit.g5_sensitive_ops(bad), "sensitive op 'teleport' is not one of the block's ops")

    class Careless:  # a registry that runs sensitive ops on an untrusted surface
        def __init__(self, real) -> None:
            self.real = real

        def get_module(self, name: str):
            return self.real.get_module(name)

        def invoke(self, name: str, op: str, params=None, *, block_sensitive: bool = False):
            return self.real.invoke(name, op, params, block_sensitive=False)

    _says(kit.g5_sensitive_ops(bad, registry=Careless(registry.get_registry())),
          "sensitive op 'launch' is not refused on an untrusted surface")


def test_g8_reports_the_header_the_vendored_code_and_the_extra(bad: kit.Target) -> None:
    assert {p.file.rsplit("/", 1)[-1] for p in kit.g8_headers(bad)} == {"no_header.py"}
    _says(kit.g8_vendored(bad), "vendored borrowed carries no LICENSE file")
    _says(kit.g8_extras(bad), "extra 'no_such_extra' is not in pyproject.toml")


def test_d2_and_d6_find_report_the_unregistered_mode(bad: kit.Target) -> None:
    _says(kit.d2_failure_modes(bad), "'made_up_failure' is not a registered failure mode")
    _says(kit.d6_find(bad), "'made_up_failure' is not a registered failure mode, so g6_find cannot find it")


def test_d6_find_reports_a_registered_mode_ranked_too_low(bad: kit.Target, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "weak_link" not in "\n".join(p.message for p in kit.d6_find(bad))  # ranked first: names break ties

    def outranked(query: str, limit: int = 5) -> list[dict]:  # five components that say it better
        return [{"component": name} for name in [*(f"other_{i}" for i in range(kit.FIND_LIMIT)), "bad_extension"]]

    monkeypatch.setattr(discovery, "find", outranked)
    _says(kit.d6_find(bad), "ranks it 6, not within 5 (weak_link)")


def test_d4_reports_the_block(bad: kit.Target, monkeypatch: pytest.MonkeyPatch) -> None:
    assert kit.d4_has_block(bad) == []
    module = registry.get_registry().get_module("bad_extension")
    monkeypatch.setattr(module, "__g6_block__", "NoSuchBlock")
    _says(kit.d4_has_block(bad), "names 'NoSuchBlock', which the package does not define")
    monkeypatch.setattr(module, "__g6_block__", "BadBlock")
    _says(kit.d4_block(bad), "BadInput does not forbid extra fields", "the help op raised RuntimeError")
    _says(kit.d4_refusals(bad), "count: refusal carries no stable G6_E_ code of its own: [BAD_EXTENSION_ERROR]",
          "guess: blocked-escalated with no G6_E_ code of its own", "lookup: with no parameters it raised")


def test_g5_fails_a_non_sensitive_op_that_reaches_outside(bad: kit.Target) -> None:
    reached = [p for p in kit.d4_refusals(bad) if "not a sensitive op" in p.message]
    _says(reached, "serve: not a sensitive op, yet it called the local service 'fixture service'",
          "spawn: not a sensitive op, yet it started", "itself, not through g6.core")
    assert {p.gate for p in reached} == {"G5"} and len(reached) == 2  # count, guess, lookup, silent, tally reach nothing


def test_offline_records_each_way_out_and_lets_g6_core_run_a_declared_program(tmp_path: Path) -> None:
    shell = "cmd" if os.name == "nt" else "true"
    program = outside.OutsideProgram(name="a shell", purpose="answer", install="it is part of the system",
                                     licence="system", command=shell, version_args=("/c", "exit 0") if os.name == "nt"
                                     else ())
    with kit.offline((program,)) as reached:
        assert outside.detect(program).found  # g6.core.outside runs it, for a program the component declares
        from g6.core.sandbox import run_script
        assert run_script("pass", timeout_s=60).status == "ok"  # G6's own interpreter, through g6.core
    assert reached == []
    with kit.offline() as reached:
        outside.detect(program)
        subprocess.run([sys.executable, "-c", "pass"], timeout=60, env={"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
                       check=False)
        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", 9), timeout=1)
        client = http.Client(resolver=lambda host, port: ["93.184.216.34"],
                             transport=lambda request: http.Response(200, {}, b"", request.url), min_interval=0)
        client.get("https://example.com/")
    _says([kit.Problem("", 0, "G5", line) for line in reached], f"started '{shell}', which its __g6_outside__ does not",
          "started 'python", "itself, not through g6.core", "connected to ('127.0.0.1', 9)",
          "fetched GET https://example.com/")


def test_d6_reports_the_skill_and_its_examples(bad: kit.Target) -> None:
    _says(kit.d6_describe(bad), "a skill named 'g6-wrong-name', not 'g6-bad-extension'")
    found = kit.d6_examples(bad)
    _says(found, "op 'tally' has no g6-example", "the count example: reports completion_state 'qualified_draft'",
          "the guess example: blocked-escalated with no G6_E_ code", "the lookup example raised ModuleNotFoundError")
    assert "launch" not in "\n".join(p.message for p in found)  # a sensitive op is never run


def test_d9_reports_the_traceback_the_uncoded_refusal_and_the_silent_fallback(bad: kit.Target) -> None:
    # silent answers {"fallback": false, ...}: a key, not a message, says nothing about the missing extra
    _says(kit.d9_extras(bad), "without g6[no_such_extra], lookup raised ModuleNotFoundError",
          "without g6[no_such_extra], guess refuses with no G6_E_ code of its own",
          "silent reached for fixture_heavy_dependency and answered as if nothing were missing")


def test_d9_reports_the_python_examples_that_fail_or_pass_silently(bad: kit.Target) -> None:
    found = kit.d9_extras(bad)
    _says(found, "without g6[no_such_extra], Python example 1 raised ModuleNotFoundError",
          "Python example 2 reached for fixture_heavy_dependency and its own assertion failed, and nothing it holds",
          "Python example 3 reached for fixture_heavy_dependency and it ran to the end, and nothing it holds")


def test_a_waiver_covers_only_its_component_gate_file_and_match() -> None:
    hit = kit.Problem("src/g6/x/a.py", 3, "G2", "exec() runs code", "exec(code)")
    waiver = kit.Waiver("x", "G2", "src/g6/x/", "exec(code)", "why", "permanent: why")
    assert waiver.covers("x", hit)
    assert not waiver.covers("y", hit)
    assert not kit.Waiver("x", "G9", "src/g6/x/", "exec(code)", "why", "permanent: why").covers("x", hit)
    assert not kit.Waiver("x", "G2", "src/g6/x/b.py", "exec(code)", "why", "permanent: why").covers("x", hit)
    assert not kit.Waiver("x", "G2", "src/g6/x", "exec(code)", "why", "permanent: why").covers("x", hit)
    assert not kit.Waiver("x", "G2", "src/g6/x/", "eval(", "why", "permanent: why").covers("x", hit)
    assert not kit.Waiver("x", "G2", "src/g6/x/", "", "why", "permanent: why").covers("x", hit)


def test_the_closure_check_fails_an_edited_a_missing_and_an_undeclared_extra() -> None:
    project = {"dependencies": ["pydantic>=2.6"], "optional-dependencies": {"edited": ["z3-solver>=4.13"],
                                                                            "missing": ["sympy>=1.14"]}}
    data = {"core": {"requires": ["pydantic>=2.6"]},
            "extras": {"edited": {"requires": ["z3-solver>=4.12"]}, "undeclared": {"requires": []}}}
    _says(kit.closure_problems(project, data), "edited: its requirements changed", "missing: no closure",
          "undeclared: a closure for an extra pyproject.toml does not declare")
    assert kit.closure_problems(project, {}) and kit.closure_problems(
        {"dependencies": ["x"], "optional-dependencies": {}}, {"core": {"requires": ["y"]}, "extras": {}})


@pytest.mark.parametrize(("meta", "verdict"), [
    ({"license_expression": "MIT"}, "allowed"),
    ({"license_expression": "MIT OR GPL-3.0-only"}, "allowed"),
    ({"license_expression": "MIT AND GPL-3.0-only"}, "banned"),
    ({"license_expression": "AGPL-3.0-or-later"}, "banned"),
    ({"license_expression": "LGPL-2.1-or-later"}, "banned"),
    ({"license_expression": "SSPL-1.0"}, "banned"),
    ({"license_expression": "CC-BY-NC-4.0"}, "banned"),
    ({"license_expression": "MPL-2.0 AND MIT"}, "allowed"),
    ({"license_expression": "Apache-2.0 WITH LLVM-exception"}, "allowed"),
    ({"license_expression": "LicenseRef-Vendor-Proprietary"}, "unknown"),
    ({"license": "MIT License", "classifiers": ["License :: OSI Approved :: MIT License"]}, "allowed"),
    ({"license": "Dual License", "classifiers": ["License :: OSI Approved :: BSD License"]}, "allowed"),
    ({"license": "Apache 2.0", "classifiers": ["License :: Other/Proprietary License"]}, "unknown"),
    ({"license": "GNU General Public License v3"}, "banned"),
    ({"classifiers": ["License :: OSI Approved :: GNU General Public License v3 (GPLv3)"]}, "banned"),
    ({"license": "MIT style"}, "unknown"),
    ({}, "unknown"),
])
def test_the_licence_table_places_each_kind_of_metadata(meta: dict, verdict: str) -> None:
    assert kit.licence_verdict("some-package", meta, kit.licence_table())[0] == verdict


@pytest.mark.parametrize("name", ["PyMuPDF", "fitz", "TTS", "litellm"])
def test_a_banned_package_fails_whatever_its_licence(name: str) -> None:
    assert kit.licence_verdict(name, {"license_expression": "MIT"}, kit.licence_table())[0] == "banned"


def _ruled(**override: object) -> dict:
    entry = {"licence": "LGPL-3.0-only", "source": "its wheel's LICENSE.txt", **override}
    return {**kit.licence_table(), "packages": {"lgpl-dep": entry}}


def test_an_owner_ruling_allows_a_banned_licence_only_in_the_closures_it_names() -> None:
    table = _ruled(owner_ruling="owner, 2026-10-22", only_in=["opt"])
    assert kit.licence_verdict("lgpl-dep", {}, table, scope="opt")[0] == "allowed"
    assert kit.licence_verdict("lgpl-dep", {}, table, scope="all")[0] == "banned"
    # fails if licence_verdict stops checking the scope against only_in
    assert kit.licence_verdict("lgpl-dep", {}, table)[0] == "banned"
    assert kit.licence_verdict("lgpl-dep", {}, _ruled(only_in=["opt"]), scope="opt")[0] == "banned"
    # fails if a scope list alone, with no owner's ruling, lifts the ban


def test_the_licence_check_passes_each_closures_scope_to_an_owner_ruling() -> None:
    data = {"core": {"closure": []}, "extras": {"opt": {"closure": ["lgpl-dep==1"]}, "all": {"closure": ["lgpl-dep==1"]}},
            "packages": {"lgpl-dep==1": {"license": "", "classifiers": []}}}
    problems = kit.licence_problems(data, _ruled(owner_ruling="owner, 2026-10-22", only_in=["opt"]))
    assert [p.message.split(":", 1)[0] for p in problems] == ["all"]
    # fails if licence_problems stops passing the closure's scope to licence_verdict


def test_the_owner_allowed_lgpl_cons_only_in_the_bayes_extra() -> None:
    # owner, 2026-10-22: "Allow cons" (LGPL-3.0, PyTensor's dependency), only with the opt-in bayes extra, never in all
    assert kit.licence_verdict("cons", {}, kit.licence_table(), scope="bayes")[0] == "allowed"
    assert kit.licence_verdict("cons", {}, kit.licence_table(), scope="all")[0] == "banned"
    # fails if the cons entry in tools/data/licence_table.json loses its owner_ruling or only_in


def test_the_licence_check_reports_a_banned_an_unknown_and_an_unpinned_package() -> None:
    data = {"core": {"closure": ["ok==1"]}, "extras": {"heavy": {"closure": ["litellm==1", "mystery==2", "gone==3"]}},
            "packages": {"ok==1": {"license_expression": "MIT"}, "litellm==1": {"license_expression": "MIT"},
                         "mystery==2": {"license": "", "classifiers": []}}}
    found = kit.licence_problems(data, kit.licence_table())
    assert [p.message.split(" ", 2)[1] for p in found] == ["litellm==1", "mystery==2", "gone==3"]
    _says(found, "a banned package", "no licence metadata", "no metadata in the closure file")
