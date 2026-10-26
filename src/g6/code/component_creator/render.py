# SPDX-License-Identifier: Apache-2.0
"""A scaffold in the library's own shape, rendered from a :class:`~g6.code.component_creator.spec.ComponentSpec`.

:func:`render` returns the files as text, by path relative to the folder they go in:

* ``<name>/__init__.py``: the package and its declarations (``__g6_component__``, ``__g6_block__``,
  ``__g6_family__``, ``__g6_sensitive_ops__``, ``__g6_requires__``, ``__g6_mitigates__``, ``__g6_introduces__``,
  ``__g6_outside__``, ``__g6_extras__``), each a literal;
* ``<name>/block.py``: a typed input (pydantic, ``extra="forbid"``), ``ops`` and ``help``, and one handler per op. An
  op that wraps a library function (``calls``) calls it with the fields it reads and returns what it returned as a
  draft nothing has checked; every other op refuses (``blocked-escalated``, ``G6_E_<NAME>_NOT_IMPLEMENTED``) until it
  is written, so a scaffold never answers as if it had done the work;
* ``<name>/skill/SKILL.md``: the skill with every section the library requires and its Ops matching the block's,
  each section still to be written (``TODO``), which ``check_conformance`` reports until it is;
* ``tests/test_<name>.py``: one failing test, the "prove it fails" test for the first failure mode, which the author
  replaces, and a test that the block answers ``ops`` and ``help``.

:func:`write` puts them in a new or empty folder; nothing is overwritten.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from g6.code.component_creator.spec import ComponentSpec, FieldSpec
from g6.core.failure_modes import FailureModeRegistry
from g6.core.skills import OPEN_PROBLEMS, skill_name

SPDX = "# SPDX-License-Identifier: Apache-2.0"
_TYPES = {"str": "str | None", "int": "int | None", "float": "float | None", "bool": "bool | None",
          "list": "list | None", "dict": "dict | None", "any": "Any"}


def camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def _title(code: str) -> str:
    return FailureModeRegistry.title(code) if FailureModeRegistry.is_registered(code) else code


def _q(text: str) -> str:
    """``text`` as a Python string literal."""
    return json.dumps(text, ensure_ascii=True)


def _codes(name: str) -> dict[str, str]:
    upper = name.upper()
    return {"NOT_IMPLEMENTED": f"G6_E_{upper}_NOT_IMPLEMENTED", "INPUT": f"G6_E_{upper}_INPUT",
            "CALL_FAILED": f"G6_E_{upper}_CALL_FAILED", "CALLED": f"G6_E_{upper}_CALLED"}


def _init(spec: ComponentSpec) -> str:
    cls = camel(spec.name)
    sensitive = tuple(op.name for op in spec.ops if op.sensitive)
    lines = [SPDX, _q(f"{spec.package}: {spec.summary}\n\n"
                     f"An extension in the {spec.family} family, scaffolded by g6.code.component_creator. "
                     "Wrapped results are unchecked; other ops refuse until written. "
                     f"tests/test_{spec.name}.py holds the failing test to replace."),
             f"from {spec.package}.block import {cls}Block, {cls}Input", "",
             "__g6_component__ = True", f'__g6_block__ = "{cls}Block"', f'__g6_family__ = "{spec.family}"',
             f"__g6_sensitive_ops__: tuple[str, ...] = {sensitive!r}", "__g6_requires__: tuple[str, ...] = ()",
             f"__g6_mitigates__: tuple[str, ...] = {tuple(spec.mitigates)!r}",
             f"__g6_introduces__: tuple[str, ...] = {tuple(spec.introduces)!r}",
             "__g6_outside__: tuple = ()", "__g6_extras__: dict[str, tuple[str, ...]] = {}", "",
             f'__all__ = ["{cls}Block", "{cls}Input"]', ""]
    return "\n".join(lines)


def _field(f: FieldSpec) -> str:
    line = f"    {f.name}: {_TYPES[f.type]} = None"
    return line + ("  # " + _q(f.does) if f.does else "")


def _handler(spec: ComponentSpec, op_index: int) -> list[str]:
    op = spec.ops[op_index]
    fields = {f.name: f for f in spec.fields}
    head = [f"def _op_{op.name}(data: {camel(spec.name)}Input) -> dict[str, Any]:", f"    {_q(op.does)}"]
    if not op.calls:
        return [*head, f"    return _refuse({_q(op.name)}, NOT_IMPLEMENTED, "
                       f"{_q(op.name + ' is not written yet: it refuses until it is')}, data)", "", ""]
    module, _, function = op.calls.rpartition(".")
    required = tuple(p for p in op.params if fields[p].required)
    return [*head,
            f"    missing = [name for name in {required!r} if getattr(data, name) is None]",
            "    if missing:",
            f"        return _refuse({_q(op.name)}, INPUT, \"give \" + \", \".join(missing), data)",
            f"    from {module} import {function}", "",
            f"    args = {{name: getattr(data, name) for name in {tuple(op.params)!r} if getattr(data, name) is not None}}",
            "    try:",
            f"        value = {function}(**args)",
            "    except Exception as exc:  # noqa: BLE001 - the library's own failure is reported, never hidden",
            f"        return _refuse({_q(op.name)}, CALL_FAILED, f\"{op.calls} raised {{type(exc).__name__}}: {{exc}}\"[:300], "
            "data)",
            f"    return _answer({_q(op.name)}, CALLED, {_q(op.calls + ' returned; nothing has checked what it returned')}, "
            "data, value=_plain(value))", "", ""]


def _block(spec: ComponentSpec) -> str:
    cls, codes = camel(spec.name), _codes(spec.name)
    ops = [op.name for op in spec.ops]
    literal = ", ".join(_q(o) for o in [*ops, "ops", "help"])
    sensitive = tuple(op.name for op in spec.ops if op.sensitive)
    lines = [SPDX, _q(f"{cls}Block: the registry and MCP door (g6_invoke) to {spec.name}.\n\n{spec.summary}"),
             "from __future__ import annotations", "", "from typing import Any, Literal", "",
             "from pydantic import BaseModel, ConfigDict, Field", "",
             "from g6.core.ai_block import AIBlock", "from g6.core.contract_decorator import block_contract",
             "from g6.core.maturity import component_maturity", "from g6.core.reliability_envelope import WarningCard",
             "from g6.core.result import Result", "", f"COMPONENT = {_q(spec.name)}"]
    lines += [f"{key} = {_q(value)}" for key, value in codes.items()]
    lines += [f"SENSITIVE_OPS: tuple[str, ...] = {sensitive!r}", "_OPS: dict[str, str] = {"]
    for op in spec.ops:
        params = ", ".join(op.params)
        lines.append(f"    {_q(op.name)}: {_q(f'{op.name}({params}): {op.does}' + ('; sensitive' if op.sensitive else ''))},")
    lines += ["}", "", "", f"class {cls}Input(BaseModel):", '    """One op\'s parameters; fields no op has are refused."""',
              "", '    model_config = ConfigDict(extra="forbid")', "", f'    op: Literal[{literal}] = "help"']
    lines += [_field(f) for f in spec.fields]
    lines += ['    request_id: str = Field("", max_length=100)', '    run_id: str = Field("", max_length=100)', "", "",
              f"def _ids(data: {cls}Input) -> dict[str, str]:",
              '    return {"request_id": data.request_id, "run_id": data.run_id}', "", "",
              f"def _refuse(op: str, code: str, reason: str, data: {cls}Input) -> dict[str, Any]:",
              '    card = WarningCard(code=code, message=reason, severity="blocked",',
              '                       suggested_action="Read the reason; the op does nothing until it is written.")',
              '    return {"component": COMPONENT, "op": op, "code": code, "completion_state": "blocked-escalated",',
              '            "warning_card": card.model_dump(), "evidence": [{"kind": "refusal", "code": code, "reason": reason}],',
              "            **_ids(data)}", "", "",
              f"def _answer(op: str, code: str, message: str, data: {cls}Input, **fields: Any) -> dict[str, Any]:",
              '    card = WarningCard(code=code, message=message, severity="amber",',
              '                       suggested_action="Check the result yourself before you rely on it.")',
              '    return {"component": COMPONENT, "op": op, **fields, "code": code, "completion_state": "qualified-draft",',
              '            "warning_card": card.model_dump(), "evidence": [], **_ids(data)}', "", "",
              "def _plain(value: Any, depth: int = 0) -> Any:",
              '    """What a library function returned, as JSON can carry it."""',
              "    if value is None or isinstance(value, (str, int, float, bool)):", "        return value",
              "    if depth < 5 and isinstance(value, (list, tuple, set, frozenset)):",
              "        return [_plain(v, depth + 1) for v in list(value)[:1000]]",
              "    if depth < 5 and isinstance(value, dict):",
              "        return {str(k): _plain(v, depth + 1) for k, v in list(value.items())[:1000]}",
              "    return repr(value)[:2000]", "", ""]
    for index in range(len(spec.ops)):
        lines += _handler(spec, index)
    handlers = ", ".join(f"{_q(o)}: _op_{o}" for o in ops)
    mitigates = tuple(spec.mitigates)
    lines += [f"_HANDLERS = {{{handlers}}}", "", "",
              f"@block_contract(purpose={_q(spec.summary)}, mitigates_failure_modes={mitigates!r},",
              '                verification_method="none yet: the scaffold\'s ops refuse or return unchecked results")',
              '@component_maturity("stubbed", reason="scaffolded by g6.code.component_creator; its ops are not written yet")',
              f"class {cls}Block(AIBlock[{cls}Input, dict, dict]):",
              f"    {_q(spec.summary)}", "",
              "    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:",
              "        super().__init__(name=name, state=state or {})", "",
              f"    def infer(self, data: {cls}Input) -> Result[dict]:",
              '        if data.op == "ops":',
              '            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS),',
              '                              "sensitive": list(SENSITIVE_OPS)})',
              '        if data.op == "help":',
              '            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})',
              "        return Result.ok(_HANDLERS[data.op](data))", "", "",
              f'__all__ = ["SENSITIVE_OPS", "{cls}Block", "{cls}Input"]', ""]
    return "\n".join(lines)


def _skill(spec: ComponentSpec) -> str:
    example = next((op.name for op in spec.ops if not op.sensitive), spec.ops[0].name)
    lines = ["---", f"name: {skill_name(spec.name)}",
             f"description: Use when TODO: the task an agent reaches for {spec.name} in, and the failure it guards "
             "against.", "---", "", f"# G6 {spec.name}", "", spec.summary, "",
             "TODO: two or three sentences on what it does, and what it never does.", "", "## When to use it", "",
             "- TODO", "", "## When not to use it", "", "- TODO", "", "## Ops", ""]
    lines += [f"- `{op.name}`: {op.does}" + (" Sensitive." if op.sensitive else "") for op in spec.ops]
    lines += ["", "## Alone and composed", "",
              f'TODO: over MCP, `g6_invoke("{spec.name}", "{example}", {{...}})`; from Python, the block; and what it '
              "composes with.", "", "## Failure modes it catches", ""]
    lines += [f"- `{code}` ({_title(code)}): TODO: how it catches it, generally." for code in spec.mitigates]
    lines += ["", "## Blind spots", ""]
    lines += [f"- `{code}` ({_title(code)}): TODO: how its own use can cause it." for code in spec.introduces]
    lines += ["- TODO: what it does not catch.", "", "## What it needs", "",
              "TODO: nothing outside G6, or each outside program, service and extra (`g6[...]`) it needs.", "",
              "## Examples", "", f"```g6-example op={example}", "{}", "```", "", "TODO: what the reply says.", "",
              "## The four open problems", ""]
    lines += [f"- **{problem}**: TODO" for problem in OPEN_PROBLEMS]
    return "\n".join([*lines, ""])


def _test(spec: ComponentSpec) -> str:
    cls, first = camel(spec.name), spec.mitigates[0]
    ops = [op.name for op in spec.ops]
    lines = [SPDX, f'"""{spec.name}\'s tests, scaffolded by g6.code.component_creator.', "",
             f'The first fails until it is replaced by the "prove it fails" test for {first}: a test that fails when '
             "the check",
             "that catches it is removed, on a case the check was not written around.", '"""',
             "from __future__ import annotations", "", "import pytest", "",
             f"from {spec.package} import {cls}Block, {cls}Input", "", "",
             f"def test_d1_it_catches_{first}() -> None:",
             f"    pytest.fail({_q(f'write the test that proves {spec.name} catches {_title(first)!r}, then remove this line')})",
             "", "",
             "def test_ops_and_help_answer() -> None:",
             f"    block = {cls}Block()",
             f'    assert block.infer({cls}Input(op="ops")).unwrap()["ops"] == {ops!r}',
             f'    assert block.infer({cls}Input(op="help")).unwrap()["help"]', ""]
    return "\n".join(lines)


def render(spec: ComponentSpec) -> dict[str, str]:
    """The scaffold's files, by path relative to the folder they go in."""
    return {f"{spec.name}/__init__.py": _init(spec), f"{spec.name}/block.py": _block(spec),
            f"{spec.name}/skill/SKILL.md": _skill(spec), f"tests/test_{spec.name}.py": _test(spec)}


def write(dest: Path, files: dict[str, str]) -> list[dict[str, object]]:
    """Each of ``files`` written under ``dest`` (a new or empty folder, already confined), never over a file; what was
    written, with its size and SHA-256."""
    written = []
    for rel, text in files.items():
        target = (dest / rel).resolve()
        if dest not in target.parents:
            raise ValueError(f"{rel} is not inside {dest}")
        if target.exists():
            raise FileExistsError(f"{target} exists; nothing is overwritten")
        target.parent.mkdir(parents=True, exist_ok=True)
        data = text.encode("utf-8")
        with open(target, "xb") as handle:
            handle.write(data)
        written.append({"path": rel, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return written


__all__ = ["SPDX", "camel", "render", "write"]
