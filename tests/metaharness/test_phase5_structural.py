# SPDX-License-Identifier: Apache-2.0
"""Meta-harness Phase 5: pipelines, distillates, code-level changes and LoRA adapters (T5-1..T5-8)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

import g6.distillation
from g6.learning import TheoryStore
from g6.learning.schema import Theory
from g6.metaharness import Model, Profile, adapt
from g6.metaharness import gate as G
from g6.metaharness import lora as L
from g6.metaharness import schema as S
from g6.metaharness.schema import PipelineStep

_spec = importlib.util.spec_from_file_location("metaharness_rig", Path(__file__).with_name("_rig.py"))
rig = sys.modules.setdefault(_spec.name, importlib.util.module_from_spec(_spec))
if not hasattr(rig, "build"):
    _spec.loader.exec_module(rig)

BASE = Profile(routing={"answer": "local"})


def _confirmed(wf, tmp_path, **kw):
    mh = adapt(wf, workspace=tmp_path / "ws", clock=rig.Clock(), **kw)
    assert mh.confirm("Ada")["completion_state"] == "qualified-draft", mh.envelope
    return mh


# T5-1, T5-4 and a working distillate --------------------------------------------------------------------------------

SOLVE = '''def solve(task):
    nums, digits = [], ""
    for ch in task + " ":
        if ch.isdigit():
            digits += ch
        elif digits:
            nums.append(int(digits))
            digits = ""
    return str(nums[0] + nums[1]) if len(nums) >= 2 else ""
'''  # no import: the distillation pre-filter refuses every one


def test_t5_1_a_distillate_right_on_dev_and_wrong_on_held_out_is_refused(tmp_path) -> None:
    wf, _ = rig.build(n_held=30)
    mh = _confirmed(wf, tmp_path)
    table = {wf.task.replace("{input}", c.input): c.expected for c in wf.suite.dev}
    memoriser = f"TABLE = {json.dumps(table)}\ndef solve(task):\n    return TABLE.get(task, '0')\n"
    got = mh.admit_distillate(memoriser)
    assert got["completion_state"] == "blocked-escalated" and "not admitted" in got["message"]


def test_t5_4_a_distillate_the_pre_filter_refuses_is_never_run(tmp_path, monkeypatch) -> None:
    wf, _ = rig.build(n_held=30)
    mh = _confirmed(wf, tmp_path)
    ran = []
    monkeypatch.setattr(g6.distillation, "evaluate", lambda *a, **k: ran.append(1))
    got = mh.admit_distillate("import os\ndef solve(task):\n    return os.getcwd()\n")
    assert got["warning_card"]["code"] == S.SCHEMA and "refused before it runs" in got["message"] and not ran


def test_an_admitted_distillate_replaces_the_model_call_and_is_recommended(tmp_path) -> None:
    wf, stub = rig.build(7, n_held=30)
    mh = _confirmed(wf, tmp_path)
    admitted = mh.admit_distillate(SOLVE)
    assert admitted["completion_state"] == "qualified-draft", admitted["message"]
    proposal = mh.candidate({"routing": {"answer": "local"}, "distillates": {"answer": admitted["distillate"]}},
                            proposer="bo")["proposal"]
    report = mh.evaluate(proposal)["report"]
    assert report.estimates["model_calls"]["candidate_mean"] == 0.0
    assert mh.decide(report, proposal)["decision"].verdict == "recommend"


# T5-2: pipelines ---------------------------------------------------------------------------------------------------

def _trim(out, contract, ctx):
    return out.strip()


def _step(name, **kw):
    """A step on an op with no parameter contract, which a person reads as A2 (the library measured none but its own)."""
    return PipelineStep(name, "grounding", "check_claim", _trim, authority="A2", **kw)


def _pipeline_wf(**steps):
    wf, stub = rig.build(n_held=30)
    defaults = {"trim": _step("trim", mandatory=True), "recheck": _step("recheck")}
    return replace(wf, pipeline_steps={**defaults, **steps}), stub


def test_t5_2_a_pipeline_without_a_mandatory_step_is_refused_and_adding_one_is_allowed(tmp_path) -> None:
    wf, _ = _pipeline_wf()
    mh = _confirmed(wf, tmp_path)
    assert "trim" in mh.store.head()[1].pipeline          # the initial profile holds the mandatory step
    missing = mh.candidate({"routing": {"answer": "local"}, "pipeline": []}, proposer="bo")
    assert missing["warning_card"]["code"] == S.SCHEMA and "mandatory" in missing["message"]
    added = mh.candidate({"routing": {"answer": "local"}, "pipeline": ["trim", "recheck"]}, proposer="bo")
    assert added["completion_state"] == "qualified-draft"


def test_t5_2_navigator_check_plan_refuses_an_uncleared_op_until_a_person_reads_it(tmp_path) -> None:
    unread = PipelineStep("ground", "grounding", "check_claim", _trim)
    wf, _ = _pipeline_wf(ground=unread)
    p = Profile(routing={"answer": "local"}, pipeline=("trim", "ground"))
    assert any("G6_E_OP_NOT_CLEARED" in why for _, why in p.problems_for(wf))
    read = replace(unread, authority="A2")
    wf2, _ = _pipeline_wf(ground=read)
    assert not p.problems_for(wf2) and p.pipeline_notes(wf2)


def test_a_pipeline_step_runs_after_the_answer(tmp_path) -> None:
    wf, _ = rig.build(stub=rig.Fixed("  42  "))
    wf = replace(wf, pipeline_steps={"trim": _step("trim")})
    mh = _confirmed(wf, tmp_path)
    proposal = mh.candidate({"routing": {"answer": "local"}, "pipeline": ["trim"]}, proposer="bo")["proposal"]
    from g6.metaharness.executor import context_for, model_call_factory

    ctx = context_for(wf, proposal.candidate)
    assert model_call_factory()(ctx)(type("C", (), {"task": "What is 40+2?"})()) == "42"


# T5-3 and a working code-level change -------------------------------------------------------------------------------

BROKEN = 'import re\ndef solve(task):\n    a, b = map(int, re.search(r"(\\d+)\\+(\\d+)", task).groups())\n' \
         '    return str(a + b) if a % 2 == 0 else str(a + b + 1)\n'
FIXED = 'import re\ndef solve(task):\n    a, b = map(int, re.search(r"(\\d+)\\+(\\d+)", task).groups())\n' \
        '    return str(a + b)\n'


def _code_wf(tmp_path, folder):
    root = tmp_path / folder
    root.mkdir()
    (root / "main.py").write_text(BROKEN, encoding="utf-8")
    wf, _ = rig.build(n_held=30, n_dev=4)
    return replace(wf, code_root=root), root


def test_t5_3_a_code_change_touching_what_judges_is_blocked_and_never_autonomous(tmp_path, monkeypatch) -> None:
    wf, _ = _code_wf(tmp_path, "metaharness")
    wf = replace(wf, patch_evaluator=_patch_observations)
    mh = _confirmed(wf, tmp_path)
    calls = []
    real = G.govern_patch_promotion

    def spy(*args, **kwargs):
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(G, "govern_patch_promotion", spy)
    proposal = mh.code_candidate({"main.py": FIXED}, proposer="bo")["proposal"]
    decided = mh.decide(mh.evaluate(proposal)["report"], proposal)
    assert decided["warning_card"]["code"] == S.PROTECTED and decided["decision"].verdict == "block"
    assert calls and all(k.get("autonomous_low_risk") is False for k in calls)
    # RED: removing govern_patch_promotion(..., autonomous_low_risk=False) loses protected-path governance.


def test_a_code_fix_in_the_users_folder_is_recommended_and_the_folder_is_never_written(tmp_path) -> None:
    wf, root = _code_wf(tmp_path, "agent")
    wf = replace(wf, patch_evaluator=_patch_observations)
    mh = _confirmed(wf, tmp_path)
    proposal = mh.code_candidate({"main.py": FIXED}, proposer="bo")["proposal"]
    decision = mh.decide(mh.evaluate(proposal)["report"], proposal)["decision"]
    assert decision.verdict == "recommend", decision.reasons
    mh.activate(decision, accepted_by="Ada")
    assert mh.run("What is 11+2?")["output"] == "13"           # the overlay runs
    assert (root / "main.py").read_text(encoding="utf-8") == BROKEN   # the user's file is untouched
    # RED: removing the measured patch_evaluator call blocks this independently checked fixture.


def test_a_code_root_inside_g6_itself_is_refused(tmp_path) -> None:
    wf, _ = rig.build()
    inside = replace(wf, code_root=Path(g6.distillation.__file__).parent)
    assert any(code == S.PROTECTED for code, _ in inside.problems())


# T5-5..T5-8: LoRA --------------------------------------------------------------------------------------------------

@dataclass
class Loader:
    loaded: list

    def __call__(self, adapter_dir):
        self.loaded.append(str(adapter_dir))
        return rig.Fixed("adapted")


def _fake_trainer(spec, rows, out):
    (out / "adapter_config.json").write_text(json.dumps({"r": spec.rank}), encoding="utf-8")
    (out / "adapter_model.bin").write_bytes(b"\x00" * 16)
    return {"steps": spec.steps, "rows": len(rows)}


def _lora_mh(tmp_path):
    base = tmp_path / "base-model"
    base.mkdir()
    (base / "config.json").write_text("{}", encoding="utf-8")
    loader = Loader([])
    wf, stub = rig.build(n_held=30)
    wf = replace(wf, models={"local": Model(stub, adapter_loader=loader)})
    return _confirmed(wf, tmp_path), wf, loader, L.LoraSpec(str(base))


def test_t5_5_training_data_holding_a_held_out_case_is_refused(tmp_path) -> None:
    mh, wf, _, spec = _lora_mh(tmp_path)
    leak = [(wf.task.replace("{input}", wf.suite.held_out[0].input), wf.suite.held_out[0].expected)]
    got = mh.train_adapter(spec, model="local", examples=leak, trainer=_fake_trainer)
    assert got["warning_card"]["code"] == S.NOT_HELD_OUT


def test_t5_6_an_unapproved_adapter_is_loaded_only_inside_an_evaluation(tmp_path) -> None:
    mh, _, loader, spec = _lora_mh(tmp_path)
    trained = mh.train_adapter(spec, model="local", trainer=_fake_trainer)
    assert trained["completion_state"] == "qualified-draft", trained["message"]
    proposal = mh.candidate({"routing": {"answer": "local"}, "adapter": trained["adapter"]}, proposer="bo")["proposal"]
    mh.run("What is 1+1?")
    assert loader.loaded == []                       # live runs use the approved head: no adapter
    mh.evaluate(proposal)
    assert loader.loaded                             # only the evaluation loaded it


def test_t5_7_the_base_model_must_be_unchanged_after_training(tmp_path) -> None:
    mh, _, _, spec = _lora_mh(tmp_path)

    def tampering(spec_, rows, out):
        _fake_trainer(spec_, rows, out)
        (Path(spec_.base_model) / "config.json").write_text('{"changed": true}', encoding="utf-8")
        return {}

    got = mh.train_adapter(spec, model="local", trainer=tampering)
    assert got["warning_card"]["code"] == S.PROTECTED and "base model" in got["message"]
    ok = L.train(spec, [("p", "c")], trainer=_fake_trainer)
    assert ok.ok and ok.report["base_manifest_sha256"]


def test_t5_8_the_base_install_imports_and_says_what_lora_needs_without_the_extra(tmp_path) -> None:
    code = ("import sys\nfor m in ('torch', 'transformers', 'peft'):\n    sys.modules[m] = None\n"
            "import g6.metaharness, g6.metaharness.lora as l\nprint(sorted(set(l.REQUIRED) & set(sys.modules)))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "['peft', 'torch', 'transformers']"   # only the None placeholders: nothing was imported
    if L.missing():
        mh, _, _, spec = _lora_mh(tmp_path)
        assert mh.train_adapter(spec, model="local")["warning_card"]["code"] == L.UNAVAILABLE


def test_a_model_without_an_adapter_loader_cannot_take_an_adapter(tmp_path) -> None:
    wf, _ = rig.build()
    p = Profile(routing={"answer": "local"}, adapter="a" * 64)
    assert any("cannot" in why for _, why in p.problems_for(wf))


@pytest.mark.heavy
@pytest.mark.skipif(bool(L.missing()), reason="the lora extra (torch, transformers, peft) is not installed")
def test_real_lora_training_on_a_tiny_local_model(tmp_path) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers, trainers
    from transformers import GPT2Config, GPT2LMHeadModel, PreTrainedTokenizerFast

    base = tmp_path / "tiny"
    words = ["What", "is", "a", "the", "answer", "Reply", "with", "number", "only"] + [str(i) for i in range(100)]
    tok = Tokenizer(models.WordLevel(unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    tok.train_from_iterator([" ".join(words)] * 3, trainers.WordLevelTrainer(special_tokens=["[UNK]", "[EOS]"]))
    fast = PreTrainedTokenizerFast(tokenizer_object=tok, unk_token="[UNK]", eos_token="[EOS]", pad_token="[EOS]")
    fast.save_pretrained(base)
    GPT2LMHeadModel(GPT2Config(vocab_size=fast.vocab_size, n_layer=1, n_head=2, n_embd=32, n_positions=64)) \
        .save_pretrained(base)
    spec = L.LoraSpec(str(base), steps=5, rank=4, max_length=32, target_modules=["c_attn"], timeout_s=600)
    result = L.train(spec, [("What is 2+2 ?", "4"), ("What is 3+3 ?", "6")])
    assert result.ok, result.message
    caller = L.adapter_loader(str(base), max_new_tokens=2)(result.folder)
    assert isinstance(caller.complete("Reply with the number only", "What is 2+2 ?"), str)


def test_the_stable_prefix_layout_puts_hints_in_the_system_prompt(tmp_path) -> None:
    seen = []
    wf, _ = rig.build(stub=type("R", (), {"complete": lambda self, s, u, max_tokens=1024: (seen.append((s, u)), "1")[1]})())
    store = TheoryStore(tmp_path / "t.db", scope="arith")
    store.add_candidates([Theory("th1", "WRONG_FORMAT", "Reply with digits only.")])
    from g6.metaharness.executor import context_for, model_call_factory

    task = type("C", (), {"task": "What is 1+1?"})()
    for layout in ("default", "stable_prefix"):
        p = Profile(routing={"answer": "local"}, hints=("th1",), prompt_layout=layout)
        model_call_factory()(context_for(wf, p, theories=store))(task)
    (s1, u1), (s2, u2) = seen
    assert "digits only" in u1 and "digits only" not in s1
    assert "digits only" in s2 and u2 == "What is 1+1?"
    store.close()

def _patch_observations(base, candidate, artifacts):
    """Offline evaluator double: measured safety, injection and calibration receipts for this fixture."""
    assert candidate.code != base.code
    assert artifacts.blob(candidate.code["main.py"]) == FIXED
    return {"safety_violations": 0, "injection_resilience_not_worse": True,
            "evaluator_confidence": 0.93, "checked_by": ["fixture:safety", "fixture:injection", "fixture:calibration"]}


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("missing", ["all", "safety_violations", "injection_resilience_not_worse", "evaluator_confidence"])
def test_improving_code_without_patch_measurements_is_blocked(tmp_path, staged, missing):
    wf, _ = _code_wf(tmp_path, "agent")
    def incomplete(base, candidate, artifacts):
        return {k: v for k, v in _patch_observations(base, candidate, artifacts).items() if k != missing}
    wf = replace(wf, policy=replace(wf.policy, staged_evaluation=staged),
                 patch_evaluator=None if missing == "all" else incomplete)
    mh = _confirmed(wf, tmp_path)
    p = mh.code_candidate({"main.py": FIXED}, proposer="bo")["proposal"]
    report = mh.evaluate(p)["report"]
    assert report.success["wins"] > report.success["losses"] and not report.partial
    got = mh.decide(report, p)
    assert got["completion_state"] == "blocked-escalated"
    assert got["decision"].verdict == "block"
    assert got["code"] == "G6_E_META_PATCH_EVIDENCE_REQUIRED"
    assert "Workflow.patch_evaluator" in got["next_action"]
    assert ("safety_violations" if missing == "all" else missing) in got["message"]
    assert "Workflow.patch_evaluator" in got["message"] and "re-evaluate" in got["message"]
    assert len(mh.store.versions()) == 1
    # RED: removing `if missing:` in gate._code_change permits an unmeasured patch safety fact.


@pytest.mark.parametrize("injection,violations,confidence,expected", [
    (False, 0, 0.93, "blocked-escalated"), (True, 1, 0.93, "blocked-escalated"),
    (True, 0, 0.2, "blocked-escalated"), (True, 0, 0.93, "qualified-draft"),
])
def test_patch_governance_receives_measured_values(tmp_path, monkeypatch, injection, violations, confidence, expected):
    wf, _ = _code_wf(tmp_path, "agent")
    def observe(base, candidate, artifacts):
        return {**_patch_observations(base, candidate, artifacts), "safety_violations": violations,
                "injection_resilience_not_worse": injection, "evaluator_confidence": confidence}
    wf = replace(wf, patch_evaluator=observe, policy=replace(wf.policy, staged_evaluation=True))
    mh = _confirmed(wf, tmp_path)
    p = mh.code_candidate({"main.py": FIXED}, proposer="bo")["proposal"]
    seen, real = [], G.govern_patch_promotion
    def govern(patch, evaluation, **kw):
        seen.append(evaluation)
        return real(patch, evaluation, **kw)
    monkeypatch.setattr(G, "govern_patch_promotion", govern)
    report = mh.evaluate(p)["report"]
    got = mh.decide(report, p)
    assert report.success["wins"] > report.success["losses"] and not report.partial
    assert got["completion_state"] == expected
    if not injection or violations:
        assert got["decision"].verdict == "block" and got["code"] == S.PROTECTED
    assert seen[0].safety_violations == violations
    assert seen[0].injection_resilience_not_worse is injection
    assert seen[0].evaluator_confidence == confidence
    assert report.patch_evidence["candidate_digest"] == p.candidate_digest
    assert report.patch_evidence["base_digest"] == p.base_digest
    reopened = adapt(wf, workspace=mh.workspace, clock=rig.Clock())
    saved, why = reopened._recorded("report", report.digest())
    assert not why and saved.patch_evidence == report.patch_evidence
    # RED: removing `evaluator_confidence=observed.evaluator_confidence` loses the measured confidence at governance.


@pytest.mark.parametrize("bad", ["raise", "malformed", "unnamed"])
def test_failed_patch_evaluator_cannot_supply_evidence(tmp_path, bad):
    wf, _ = _code_wf(tmp_path, "agent")
    def observe(base, candidate, artifacts):
        if bad == "raise":
            raise RuntimeError("probe failed")
        receipt = _patch_observations(base, candidate, artifacts)
        return {**receipt, "safety_violations": "zero"} if bad == "malformed" else {**receipt, "checked_by": []}
    wf = replace(wf, patch_evaluator=observe)
    mh = _confirmed(wf, tmp_path)
    p = mh.code_candidate({"main.py": FIXED}, proposer="bo")["proposal"]
    got = mh.decide(mh.evaluate(p)["report"], p)
    assert got["completion_state"] == "blocked-escalated" and "patch_evaluator" in got["message"]
    # RED: removing `if not observed.checked_by:` admits unnamed safety evidence.

def test_patch_measurements_for_another_candidate_stay_blocked(tmp_path):
    wf, _ = _code_wf(tmp_path, "agent")
    def stale(base, candidate, artifacts):
        return {**_patch_observations(base, candidate, artifacts), "candidate_digest": "0" * 64}
    wf = replace(wf, patch_evaluator=stale)
    mh = _confirmed(wf, tmp_path)
    p = mh.code_candidate({"main.py": FIXED}, proposer="bo")["proposal"]
    got = mh.decide(mh.evaluate(p)["report"], p)
    assert got["completion_state"] == "blocked-escalated" and "different base or candidate" in got["message"]
    # RED: removing the observed.candidate_digest comparison admits receipts belonging to another patch.
