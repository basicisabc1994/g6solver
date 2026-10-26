# SPDX-License-Identifier: Apache-2.0
"""Django build/check block; project code is never imported in this process."""
from g6.code import _build, _roots
from g6.code._types import CodeRefused, refuse
from g6.code.adapt_django.checks import checks_for
from g6.code.adapt_django.generators import build_django
from g6.code.adapt_django.schema import AdaptDjangoInput
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result

OPS = {"build": "Generate model/view/URL/admin/form/serializer/settings source as files.",
       "run_checks": "Sandbox manage.py check and makemigrations --check --dry-run; dry-run by default.",
       "write_files": "Write generated files to a declared destination; dry-run by default."}
SENSITIVE_OPS = ("run_checks", "write_files")


@block_contract(purpose="Generate consistent Django boilerplate and check caller-supplied projects in a sandbox.",
                mitigates_failure_modes=("handwritten_boilerplate",),
                verification_method="Django system checks and migration drift checks in core.sandbox")
@component_maturity("beta", reason="validated templates; optional Django checks need the caller's complete project")
class AdaptDjangoBlock(AIBlock[AdaptDjangoInput, dict, dict]):
    def __init__(self, name="adapt_django", state=None, *, roots=None, backends=None):
        super().__init__(name=name, state=state or {})
        self.roots = roots
        self.backends = backends if backends is not None else _build.BuildRegistry()
        if backends is None:
            self.backends.register("django", build_django, checks_for)

    def infer(self, data: AdaptDjangoInput) -> Result[dict]:
        ids = {"request_id": data.request_id, "run_id": data.run_id}
        if data.op == "ops":
            return Result.ok({"ops": list(OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"help": OPS})
        try:
            if data.op == "build":
                artefact = self.backends.build(data.kind, data.spec)
                return Result.ok(_build.checked_reply(self.name, data.op, [
                    _build.CheckResult("project checks", None, {}, "build only; supply complete project to run_checks")],
                    **artefact.describe(), migration_policy="models only; no handwritten migrations",
                    settings_policy="settings refuses an unset SECRET_KEY; configure before use", **ids))
            artefact = _build.Artefact.from_text(data.files)
            if data.op == "write_files":
                roots = self.roots if self.roots is not None else (_roots.Roots((), "dry run") if data.dry_run else _roots.load())
                out = _build.write_files(artefact, data.dest, roots, dry_run=data.dry_run, component=self.name)
                return Result.ok(_build.checked_reply(self.name, data.op, [], **out, **ids))
            checks = self.backends.checks(data.kind, settings_module=data.settings_module)
            results = ([_build.CheckResult(c.name, None, {}, "dry run") for c in checks] if data.dry_run
                       else _build.run_checks(artefact, checks))
            return Result.ok(_build.checked_reply(self.name, data.op, results, dry_run=data.dry_run, **ids))
        except CodeRefused as exc:
            return Result.ok(refuse(self.name, data.op, exc.code, exc.reason, action="Correct the request.",
                                    evidence=exc.evidence, **exc.fields, **ids))
        except (ValueError, RecursionError, MemoryError) as exc:
            return Result.ok(refuse(self.name, data.op, "G6_E_DJANGO_INPUT", str(exc)[:500],
                                    action="Supply bounded valid fields.", **ids))
