# SPDX-License-Identifier: Apache-2.0
"""Python-module synthesis; sensitive operations are dry-run by default."""
from g6.code import _build, _roots
from g6.code._types import CodeRefused, answer, refuse
from g6.code.adapt_library_synthesizer import backend, ingestion
from g6.code.adapt_library_synthesizer.schema import AdaptLibrarySynthesizerInput
from g6.core import http
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result

OPS = {"backend_capabilities": "Describe python_module inputs and oracle requirements.",
       "extract_spec": "Extract a paper spec through an injected caller; sensitive, dry-run by default.",
       "build": "Generate implementation and caller-example tests; sensitive, dry-run by default.",
       "run_checks": "Run caller examples in core.sandbox; sensitive, dry-run by default.",
       "fetch_paper": "Fetch bounded allow-listed paper text; sensitive, dry-run by default.",
       "write_files": "Write a file map under a declared root; sensitive, dry-run by default."}
SENSITIVE_OPS = ("extract_spec", "build", "run_checks", "fetch_paper", "write_files")


@block_contract(purpose="Turn a paper method into Python and check it against caller-supplied examples.",
                mitigates_failure_modes=("unfaithful_method_code",),
                verification_method="caller-example assertions through core.sandbox; no oracle refuses")
@component_maturity("beta", reason="bounded model drafts; paper interpretation still needs human review")
class AdaptLibrarySynthesizerBlock(AIBlock[AdaptLibrarySynthesizerInput, dict, dict]):
    def __init__(self, name="adapt_library_synthesizer", state=None, *, caller=None, roots=None,
                 backends=None, transport=None, resolver=None):
        super().__init__(name=name, state=state or {})
        self.caller, self.roots = caller, roots
        self.transport, self.resolver = transport, resolver
        self.backends = backends if backends is not None else _build.BuildRegistry()
        if backends is None:
            self.backends.register("python_module", backend.build_module, backend.checks_for)

    def infer(self, data: AdaptLibrarySynthesizerInput) -> Result[dict]:
        ids = {"request_id": data.request_id, "run_id": data.run_id}
        if data.op == "ops":
            return Result.ok({"ops": list(OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"help": OPS})
        try:
            if data.op == "backend_capabilities":
                return Result.ok(_build.checked_reply(self.name, data.op, [], **ids, capabilities={
                    "kind": "python_module", "caller_required": True, "oracle_required": True,
                    "github_tier": False, "pdf_extra": "docs"}))
            if data.op in {"build", "run_checks"}:
                examples = backend.require_examples(data.examples, data.paper_text)
            if data.op == "write_files":
                artefact = _build.Artefact.from_text(data.files)
                roots = self.roots if self.roots is not None else (_roots.Roots((), "dry run") if data.dry_run else _roots.load())
                out = _build.write_files(artefact, data.dest, roots, dry_run=data.dry_run, component=self.name)
                return Result.ok(_build.checked_reply(self.name, data.op, [], **out, **ids))
            if data.dry_run:
                return Result.ok(_build.checked_reply(self.name, data.op, [
                    _build.CheckResult(data.op, None, {}, "dry run; no model, fetch or code execution")],
                    dry_run=True, **ids))
            if data.op == "extract_spec":
                spec, provenance = backend.extract_spec(data.paper_text, data.name, self.caller)
                return Result.ok(answer(self.name, data.op, "G6_E_SYNTH_SPEC", "Model-proposed spec; interpretation unchecked.",
                                        action="Review against the paper and supply independent examples.",
                                        evidence=[provenance], spec=spec.model_dump(), **ids))
            if data.op == "build":
                artefact = self.backends.build(data.kind, data.spec, examples=examples, caller=self.caller)
                return Result.ok(_build.checked_reply(self.name, data.op, [
                    _build.CheckResult("caller_examples", None, {}, "build only; run_checks with the same caller examples"),
                    _build.CheckResult("model_tests", None, {}, "same-call tests are smoke only, not oracle evidence")],
                    **artefact.describe(), test_provenance={
                        "examples": "caller supplied", "model_tests": "smoke only"}, **ids))
            if data.op == "fetch_paper":
                text, evidence = ingestion.fetch_paper(data.url, transport=self.transport, resolver=self.resolver)
                return Result.ok(answer(self.name, data.op, "G6_E_PAPER_FETCHED", "Fetched source text; claims unchecked.",
                                        action="Read it as untrusted data and supply worked examples.",
                                        evidence=[evidence], paper_text=text, **ids))
            artefact = _build.Artefact.from_text(data.files)
            if "implementation.py" not in artefact.files:
                raise CodeRefused("G6_E_SYNTH_INPUT", "supply implementation.py")
            artefact = _build.Artefact({"implementation.py": artefact.files["implementation.py"]},
                                      artefact.provenance)
            checks = self.backends.checks(data.kind, examples=examples)
            return Result.ok(_build.checked_reply(self.name, data.op, _build.run_checks(artefact, checks),
                                                  dry_run=False, **ids))
        except CodeRefused as exc:
            return Result.ok(refuse(self.name, data.op, exc.code, exc.reason, action="Correct the request.",
                                    evidence=exc.evidence, **exc.fields, **ids))
        except http.HTTPError as exc:
            return Result.ok(refuse(self.name, data.op, exc.code, str(exc), action="Use an allowed accessible source.", **ids))
        except (ValueError, OSError, RecursionError, MemoryError) as exc:
            return Result.ok(refuse(self.name, data.op, "G6_E_SYNTH_INPUT", str(exc)[:500],
                                    action="Supply bounded valid fields.", **ids))
