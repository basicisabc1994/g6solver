# SPDX-License-Identifier: Apache-2.0
"""ComponentCreatorBlock: the registry and MCP door (``g6_invoke``) to component_creator.

``render`` writes files when asked (``dry_run=false``) into a new folder under a declared root, so it is sensitive:
callable from Python, and over MCP only when the person who runs the server lists it in ``G6_MCP_SENSITIVE``; without
``dry_run=false`` it writes nothing anywhere. ``spec_from_module`` reads source as text (a path must be under a
declared root), ``spec_from_text`` asks the injected model caller (over MCP, the one the person configured) and never
writes, and
``check_conformance`` reads a package as text. Every answer is ``qualified-draft`` at best: a scaffold, a spec or a
clean static check is a starting point, never a component that is done.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from g6.code import _roots
from g6.code._types import CodeRefused, answer, refuse
from g6.code.component_creator import conformance
from g6.code.component_creator.render import render, write
from g6.code.component_creator.spec import (SOURCE_INVALID, SPEC_INVALID, ComponentSpec, from_module, from_text,
                                            spec_problems, validation_errors)
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result

COMPONENT = "component_creator"
INPUT = "G6_E_SCAFFOLD_INPUT"
READ = "G6_E_SCAFFOLD_SPEC_READ"
DRAFTED = "G6_E_SCAFFOLD_SPEC_DRAFTED"
RENDERED = "G6_E_SCAFFOLD_RENDERED"
WRITTEN = "G6_E_SCAFFOLD_WRITTEN"
WRITE_FAILED = "G6_E_SCAFFOLD_WRITE_FAILED"
CHECKED = "G6_E_CONFORMANCE_CHECKED"
PROBLEMS = "G6_E_CONFORMANCE_PROBLEMS"
SENSITIVE_OPS: tuple[str, ...] = ("render",)
#: The most module files ``spec_from_module`` reads from a package folder, and the most bytes of each.
MAX_MODULES = 50
MAX_MODULE_BYTES = 1_000_000
_OPS: dict[str, str] = {
    "spec_from_module": "spec_from_module(source and module, or path): a spec with one op per public function of a "
                        "Python module or package, read as text with ast (never imported); kinds and sensitivity are "
                        "guesses from the names",
    "spec_from_text": "spec_from_text(text): a spec drafted by the model caller you give the block, checked against "
                      "the spec schema; it never writes",
    "render": "render(spec, dest, dry_run): the scaffold in the library's shape (declarations, a typed input that "
              "refuses extra fields, ops and help, handlers that refuse until written, one failing test, a skill "
              "whose Ops match); dry_run (the default) returns the files, otherwise they are written into a new "
              "folder under a declared root; sensitive",
    "check_conformance": "check_conformance(component or path): a package held to the library's standard, read as "
                         "text: declarations, failure modes, block, skill and tests",
}
_ACTIONS = {
    INPUT: "Give the fields the op needs (the help op lists them).",
    READ: "Read the spec: add the family and the failure modes it guards against, keep or drop each op, mark the "
          "sensitive ones, then render it.",
    DRAFTED: "Read the spec against what you meant: the schema checked its shape, not its sense. Then render it.",
    RENDERED: "Read the files; render again with dry_run false and a dest folder to write them.",
    WRITTEN: "Write each op, replace the failing test with the one that proves the failure mode is caught, finish the "
             "skill, then run check_conformance and the conformance kit.",
    CHECKED: "Run the package's own tests and the conformance kit (tests/conformance): folder checks read text; installed checks import and run ops.",
    PROBLEMS: "Fix each problem listed, then check again.",
    SPEC_INVALID: "Correct the spec as the problems say.",
    SOURCE_INVALID: "Give Python source that parses and defines at least one public function.",
    WRITE_FAILED: "Nothing more was written; read the reason, remove any partial folder, and try again.",
}


class ComponentCreatorInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["spec_from_module", "spec_from_text", "render", "check_conformance", "ops", "help"] = "help"
    path: str = Field("", max_length=1000)
    source: str = Field("", max_length=1_000_000)
    module: str = Field("", max_length=200)
    text: str = Field("", max_length=4000)
    spec: ComponentSpec | dict[str, Any] | None = None
    dest: str = Field("", max_length=1000)
    dry_run: bool = True
    component: str = Field("", max_length=100)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: ComponentCreatorInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _refused(op: str, exc: CodeRefused, d: ComponentCreatorInput) -> dict[str, Any]:
    return refuse(COMPONENT, op, exc.code, exc.reason, action=_ACTIONS.get(exc.code, "Correct the request as the "
                                                                                     "reason says."),
                  evidence=exc.evidence, **_ids(d), **exc.fields)


def _dotted(name: str) -> bool:
    return bool(name) and all(part.isidentifier() for part in name.split("."))


def _modules(d: ComponentCreatorInput, roots: _roots.Roots) -> tuple[dict[str, str], str, dict[str, Any]]:
    """The modules to read (import name to source), the package's name, and what was read."""
    if d.source.strip():
        module = d.module or "module"
        if not _dotted(module):
            raise CodeRefused(INPUT, "module is the source's import name, a dotted Python name such as mylib.users")
        return {module: d.source}, module, {"kind": "ast_read", "from": "source", "modules": 1,
                                            "bytes": len(d.source.encode("utf-8")), "imported": False}
    if not d.path.strip():
        raise CodeRefused(INPUT, "give source (with module, its import name) or path (a .py file or a package folder "
                                 "under a declared root)")
    found = _roots.existing(d.path, roots)
    if d.module and not _dotted(d.module):
        raise CodeRefused(INPUT, "module is the import name, a dotted Python name such as mylib.users")
    if found.is_file():
        if found.suffix != ".py" or found.stat().st_size > MAX_MODULE_BYTES:
            raise CodeRefused(SOURCE_INVALID, f"{found.name} is not a Python file of at most {MAX_MODULE_BYTES} bytes")
        module = d.module or found.stem
        text = _roots.read_text(found, MAX_MODULE_BYTES)
        return {module: text}, module, {"kind": "ast_read", "from": str(found), "modules": 1,
                                        "bytes": len(text.encode("utf-8")), "imported": False}
    package = d.module or found.name
    if not _dotted(package):
        raise CodeRefused(INPUT, f"the folder name {found.name!r} is not an import name; give module")
    walked = _roots.walk(found, roots, max_files=MAX_MODULES, max_file_bytes=MAX_MODULE_BYTES, suffixes=(".py",))
    modules = {}
    for file in walked.files:
        parts = list(file.relative_to(found).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if any(p.startswith("_") or not p.isidentifier() for p in parts):
            continue
        modules[".".join([package, *parts])] = _roots.read_text(file, MAX_MODULE_BYTES)
    if not modules:
        raise CodeRefused(SOURCE_INVALID, f"{found} holds no public Python module")
    return modules, package, {"kind": "ast_read", "from": str(found), "modules": len(modules), **walked.describe(),
                              "imported": False}


def spec_from_module(d: ComponentCreatorInput, roots: _roots.Roots) -> dict[str, Any]:
    modules, package, read = _modules(d, roots)
    spec, interfaces, left_out = from_module(modules, package)
    try:
        checked = ComponentSpec.model_validate(spec)
    except ValidationError as exc:
        raise CodeRefused(SOURCE_INVALID, "no public function could be mapped to an op: "
                                          + "; ".join(validation_errors(exc)), [read], interfaces=interfaces,
                          left_out=left_out) from None
    problems = spec_problems(checked)
    functions = sum(len(i["functions"]) for i in interfaces)
    said = (f"Read {functions} public function(s) in {len(modules)} module(s) as text; nothing was imported or run. "
            f"{len(checked.ops)} op(s), one per function it can wrap; each kind and each sensitive mark is a guess "
            "from the function's name.")
    if problems:
        said += f" Before render: {'; '.join(problems)}."
    return answer(COMPONENT, "spec_from_module", READ, said, action=_ACTIONS[READ], evidence=[read], **_ids(d),
                  spec=checked.model_dump(), interfaces=interfaces, left_out=left_out, problems=problems)


def spec_from_text(d: ComponentCreatorInput, caller: Any) -> dict[str, Any]:
    if not d.text.strip():
        raise CodeRefused(INPUT, "give text: a description of the component to draft a spec for")
    spec, claim = from_text(d.text, caller)
    problems = spec_problems(spec)
    check = {"kind": "schema_check", "schema": "ComponentSpec (extra fields refused)", "passed": True,
             "would_have_caught": "a reply that is not a spec: missing or extra keys, bad names, repeated ops, ops "
                                  "reading fields the spec lacks, an unknown family",
             "not_checked": "whether the spec describes the component you meant"}
    said = f"{claim['by']}'s draft of a spec, checked against the spec schema only; nothing was written."
    if problems:
        said += f" Before render: {'; '.join(problems)}."
    return answer(COMPONENT, "spec_from_text", DRAFTED, said, action=_ACTIONS[DRAFTED], evidence=[claim, check],
                  **_ids(d), spec=spec.model_dump(), problems=problems)


def render_op(d: ComponentCreatorInput, roots: _roots.Roots) -> dict[str, Any]:
    if d.spec is None:
        raise CodeRefused(INPUT, "give spec: a component spec (spec_from_module or spec_from_text drafts one)")
    try:
        d.spec = ComponentSpec.model_validate(d.spec)
    except ValidationError as exc:
        raise CodeRefused(SPEC_INVALID, "; ".join(validation_errors(exc))) from None
    problems = spec_problems(d.spec)
    if problems:
        raise CodeRefused(SPEC_INVALID, "the spec would not make a component the library takes: "
                                        + "; ".join(problems), problems=problems)
    files = render(d.spec)
    shape = {"kind": "scaffold", "package": d.spec.package, "files": sorted(files),
             "refusing_ops": [op.name for op in d.spec.ops if not op.calls],
             "wrapping_ops": [op.name for op in d.spec.ops if op.calls]}
    if d.dry_run:
        return answer(COMPONENT, "render", RENDERED, f"The scaffold for {d.spec.package}, {len(files)} files; nothing "
                      "was written (dry_run).", action=_ACTIONS[RENDERED], evidence=[shape], **_ids(d),
                      package=d.spec.package, files=files, written=[], dry_run=True)
    dest = _roots.destination(d.dest, roots)
    try:
        written = write(dest, files)
    except (OSError, ValueError) as exc:
        raise CodeRefused(WRITE_FAILED, f"writing into {dest} failed: {type(exc).__name__}: {exc}"[:400]) from None
    return answer(COMPONENT, "render", WRITTEN, f"Wrote the scaffold for {d.spec.package} into {dest}: its ops refuse "
                  "until written, and its first test fails until replaced.", action=_ACTIONS[WRITTEN],
                  evidence=[shape, {"kind": "write", "dest": str(dest), "files": written}], **_ids(d),
                  package=d.spec.package, files={}, written=written, dest=str(dest), dry_run=False)


def check_conformance(d: ComponentCreatorInput, roots: _roots.Roots) -> dict[str, Any]:
    if d.component.strip():
        out = conformance.check_component(d.component.strip())
    elif d.path.strip():
        out = conformance.check_folder(d.path, roots)
    else:
        raise CodeRefused(INPUT, "give component (an installed component's name) or path (a package folder under a "
                                 "declared root)")
    checks = sorted({p["check"] for p in out["problems"]} | {"headers", "declarations", "failure_modes", "block",
                                                              "skill", "tests"})
    evidence = [{"kind": "static_check", "checks": checks, "read": out["read"],
                 "ran_code": bool(out["imported"] or out["invoked"]),
                 "imported": out["imported"], "invoked": out["invoked"], "complete": out["complete"],
                 "uses": ["g6.core.failure_modes.declarations_in", "g6.core.failure_modes.problems_in",
                          "g6.core.skills"]}]
    fields = {k: out[k] for k in ("name", "package", "folder", "ops", "sensitive", "tests", "problems", "complete")}
    if out["problems"]:
        return refuse(COMPONENT, "check_conformance", PROBLEMS, f"{len(out['problems'])} problem(s) in {out['package']}"
                      f": {out['problems'][0]['message']}" + (" (and more)" if len(out["problems"]) > 1 else ""),
                      action=_ACTIONS[PROBLEMS], evidence=evidence, **_ids(d), static_clean=False, **fields)
    return answer(COMPONENT, "check_conformance", CHECKED, f"{out['package']}: the static checks found no problem. "
                  "The conformance kit and package tests still decide. "
                  + ("The installed package was imported and its ops op run." if out["imported"]
                     else "Package files were read as text; nothing was imported or run."),
                  action=_ACTIONS[CHECKED], evidence=evidence, **_ids(d), static_clean=True, **fields)


@block_contract(
    purpose="scaffold a new G6 component in the library's own shape from a spec read off a module or drafted from a "
            "description, and check a package against the library's standard before it is called done",
    mitigates_failure_modes=("incomplete_component",),
    verification_method="static checks of a package's files: declarations, failure modes through "
                        "g6.core.failure_modes, the block's typed input, the skill's sections, ops and examples "
                        "through g6.core.skills, and a test that is not the scaffold's placeholder; "
                        "installed packages import and run ops",
)
@component_maturity("beta", reason="scaffolds and static checks tested on the library's own packages; a scaffold "
                                   "refuses every op until written")
class ComponentCreatorBlock(AIBlock[ComponentCreatorInput, dict, dict]):
    """Scaffold a component and check a package against the library's standard. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None, *, caller: Any = None,
                 roots: _roots.Roots | None = None) -> None:
        super().__init__(name=name, state=state or {})
        self.caller, self.roots = caller, roots

    def infer(self, data: ComponentCreatorInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        try:
            if data.op == "spec_from_text":
                return Result.ok(spec_from_text(data, self.caller))
            roots = self.roots if self.roots is not None else _roots.load()
            if data.op == "spec_from_module":
                return Result.ok(spec_from_module(data, roots))
            if data.op == "render":
                return Result.ok(render_op(data, roots))
            return Result.ok(check_conformance(data, roots))
        except (RecursionError, MemoryError) as exc:
            return Result.ok(refuse(COMPONENT, data.op, "G6_E_CODE_RESOURCE_LIMIT",
                                    f"input exceeds syntax processing limits: {type(exc).__name__}",
                                    action="Supply a smaller, less deeply nested source.", **_ids(data)))
        except CodeRefused as exc:
            return Result.ok(_refused(data.op, exc, data))


__all__ = ["COMPONENT", "SENSITIVE_OPS", "ComponentCreatorBlock", "ComponentCreatorInput", "check_conformance",
           "render_op", "spec_from_module", "spec_from_text"]
