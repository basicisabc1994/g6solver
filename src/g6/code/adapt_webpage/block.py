# SPDX-License-Identifier: Apache-2.0
"""Registry door for offline web and token builds and sensitive checks/writes."""
from pydantic import ValidationError

from g6.code import _build, _roots
from g6.code._types import CodeRefused, refuse
from g6.code.adapt_webpage.backend import TemplateBackend, registry
from g6.code.adapt_webpage.schema import AdaptWebpageInput
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result

OPS = {"build": "Build web_page or design_tokens as an in-memory file map.",
       "backend_capabilities": "Describe offline generators without probing dependencies.",
       "run_checks": "Check file maps in the sandbox; dry-run by default.",
       "write_files": "Write a file map under a person's declared root; dry-run by default."}
SENSITIVE_OPS = ("run_checks", "write_files")


@block_contract(purpose="Build web pages and design tokens, then check syntax, declared contrast and optional overflow.",
                mitigates_failure_modes=("broken_responsive_layout", "design_quality_failed"),
                verification_method="HTML stack, tinycss2 syntax, WCAG declared pairs and optional Playwright overflow")
@component_maturity("beta", reason="bounded generators and checks; runtime validation remains caller-owned")
class AdaptWebpageBlock(AIBlock[AdaptWebpageInput, dict, dict]):
    def __init__(self, name="adapt_webpage", state=None, *, roots=None, backends=None):
        super().__init__(name=name, state=state or {})
        self.roots, self.backends = roots, backends if backends is not None else registry()

    def infer(self, data: AdaptWebpageInput) -> Result[dict]:
        ids = {"request_id": data.request_id, "run_id": data.run_id}
        if data.op == "ops":
            return Result.ok({"ops": list(OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"help": OPS})
        try:
            if data.op == "backend_capabilities":
                return Result.ok(_build.checked_reply(self.name, data.op, [],
                                                       capabilities=TemplateBackend().capabilities(), **ids))
            if data.op == "build":
                artefact = self.backends.build(data.kind, data.spec)
                return Result.ok(_build.checked_reply(self.name, data.op, [
                    _build.CheckResult("artefact checks", None, {}, "build only; call run_checks")],
                    **artefact.describe(), **ids))
            artefact = _build.Artefact.from_text(data.files)
            if data.op == "write_files":
                roots = self.roots if self.roots is not None else (_roots.Roots((), "dry run") if data.dry_run else _roots.load())
                out = _build.write_files(artefact, data.dest, roots, dry_run=data.dry_run, component=self.name)
                return Result.ok(_build.checked_reply(self.name, data.op, [], **out, **ids))
            checks = self.backends.checks(data.kind, pairs=[p.model_dump() for p in data.pairs],
                                           render=data.render, breakpoints=data.breakpoints)
            results = ([_build.CheckResult(c.name, None, {}, "dry run") for c in checks] if data.dry_run
                       else _build.run_checks(artefact, checks))
            return Result.ok(_build.checked_reply(self.name, data.op, results, dry_run=data.dry_run, **ids))
        except CodeRefused as exc:
            return Result.ok(refuse(self.name, data.op, exc.code, exc.reason, action="Correct the request.",
                                    evidence=exc.evidence, **exc.fields, **ids))
        except (ValueError, ValidationError, RecursionError, MemoryError) as exc:
            return Result.ok(refuse(self.name, data.op, "G6_E_WEB_INPUT", str(exc)[:500],
                                    action="Supply bounded valid fields.", **ids))
