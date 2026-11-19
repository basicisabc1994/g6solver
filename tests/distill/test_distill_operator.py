"""Receipt-bound proposals through the P4 operator API, with offline callers."""
import importlib
import importlib.util
import json

import pytest

from g6.core.ledger import digest
from g6.core.llm import FakeLLM
from g6.distill.graph import execute
from g6.distill.graph_records import CostVector, ExecutionRecord, NodeRun
from g6.distill.held_out.oracle import OracleResult
from g6.distill.records import FailureRecord, VerifierCandidate
from g6.distill.synthesis import Synthesizer

from .test_graph_records import D, graph_for, primitive
from .test_records import bodies


def api():
    assert importlib.util.find_spec("g6.distill.distill_operator"), "P4 distillation operator is missing"
    return importlib.import_module("g6.distill.distill_operator")


class Reader:
    def __init__(self, values):
        self.values, self.reads = values, []

    def get(self, ident):
        self.reads.append(ident)
        return self.values[ident]


def case(label="positive", *, split="training", ident="train", expected=None):
    a = api()
    result = OracleResult(case_id=digest(ident), label=label, reason="fixture", policy_sha256=D)
    receipt = digest(result.model_dump(mode="json"))
    f = FailureRecord(**(bodies()[0][1] | dict(case_ref=result.case_id, split=split,
        evidence=[dict(kind="oracle_label", digest=receipt, receipt="evaluator/result")])))
    c = a.DistillationCase(failure=f, before={"tests/test_secret.py":
        'def test_unique_name():\n assert calc() == "unique-training-value"\n'},
        after={"tests/test_secret.py": "def test_unique_name():\n assert True\n"}, expected=expected)
    return c, Reader({receipt: result})


def teacher_graph():
    from g6.distill.graph import LLMConfig
    config = LLMConfig(model_id="fixture", system="judge", max_tokens=100)
    p = primitive(kind="llm", impl_ref="model:fixture", impl_version=config.content_id,
                  determinism="stochastic", budget=CostVector(llm_calls=1, llm_tokens_in=10000,
                  llm_tokens_out=100, wall_s=5, cpu_s=5))
    return graph_for([p], combinator=None), p, config


def trace(c, *, verdict="concern", reasoning="assertions calls mystery_fact", graph=None):
    g = graph or teacher_graph()[0]
    raw = {"reply": json.dumps({"verdict": verdict, "reasoning": reasoning})}
    ref = digest(raw)
    row = NodeRun(node_id="n0", input_sha256=D, output_sha256=digest(verdict), output=verdict,
                  terminal="ok", cost=CostVector(llm_calls=1), trace_ref=ref, replayable=False)
    rec = ExecutionRecord(graph_id=g.graph_id, unit_ref=c.failure.case_ref, split=c.failure.split,
        request_id="trace", g6_commit="fixture", model_ids={}, seed=7, runs=[row], root_output=verdict,
        terminal="ok", cost=row.cost, completion_state="qualified-draft", started_at="start", ended_at="end")
    return rec, Reader({ref: raw})


def concept_reply():
    return json.dumps({k: v for k, v in bodies()[1][1].items() if k in {
        "statement", "observable_features", "falsifier", "prediction", "worth"}})


@pytest.mark.parametrize("label,teacher,target", [("positive", "no_concern", "concern"),
                                                  ("negative", "concern", "no_concern")])
def test_teacher_wrong_case_keeps_oracle_label(label, teacher, target):
    c, store = case(label)
    bound = api().bind_oracle_labels([c], oracle_store=store, registered_policy_sha256=D)
    assert bound[0].expected == target != teacher
    assert store.reads == [c.failure.evidence[0].digest]


def test_caller_expected_conflicting_with_receipt_is_refused():
    c, store = case(expected="no_concern")
    with pytest.raises(api().UnboundLabel, match="G6_E_SYNTH_UNBOUND_LABEL"):
        api().bind_oracle_labels([c], oracle_store=store, registered_policy_sha256=D)


def test_missing_receipt_is_refused():
    c, store = case()
    c = api().DistillationCase(**(c.__dict__ | {"failure": FailureRecord(**(
        c.failure.model_dump(exclude={"record_id"}) | {"evidence": []}))}))
    with pytest.raises(api().UnboundLabel, match="G6_E_SYNTH_UNBOUND_LABEL"):
        api().bind_oracle_labels([c], oracle_store=store, registered_policy_sha256=D)
    assert not store.reads


@pytest.mark.parametrize("fault", ["case", "policy", "digest", "excluded", "missing", "duplicate"])
def test_receipt_mismatch_refuses_whole_batch(fault):
    c, store = case("excluded" if fault == "excluded" else "positive")
    key = c.failure.evidence[0].digest
    raw = store.values[key].model_dump()
    if fault in {"case", "policy"}:
        raw["case_id" if fault == "case" else "policy_sha256"] = digest("other")
    elif fault == "digest":
        raw["reason"] = "changed"
    if fault == "missing":
        store.values.clear()
    elif fault in {"case", "policy"}:
        result = OracleResult(**raw)
        key = digest(result.model_dump(mode="json"))
        c = api().DistillationCase(**(c.__dict__ | {"failure": FailureRecord(**(
            c.failure.model_dump(exclude={"record_id"}) | {"evidence": [dict(
                kind="oracle_label", digest=key, receipt="evaluator/result")]}))}))
        store.values[key] = result
    else:
        store.values[key] = OracleResult(**raw)
    batch = [c, c] if fault == "duplicate" else [c]
    with pytest.raises(api().UnboundLabel):
        api().bind_oracle_labels(batch, oracle_store=store, registered_policy_sha256=D)


def test_teacher_trace_features_scrub_cap_and_split_boundary():
    c, receipts = case()
    bound = api().bind_oracle_labels([c], oracle_store=receipts, registered_policy_sha256=D)
    rec, reader = trace(c, reasoning="assertions calls mystery_fact tests/test_secret.py "
                        "test_unique_name unique-training-value " + "z" * 2500)
    monitor = ExecutionRecord(**(rec.model_dump(exclude={"run_id"}) | {"split": "monitor"}))
    rows = api().extract_teacher_traces([monitor, rec], teacher_node="n0", cases=bound, trace_store=reader)
    assert len(rows) == 1 and rows[0].verdict == "concern"
    assert rows[0].features == ["assertions", "calls"]
    assert len(rows[0].reasoning) == 2000
    assert all(s not in rows[0].reasoning for s in ["test_unique_name", "unique-training-value",
                                                   "tests/test_secret.py"])
    assert len(reader.reads) == 1


def test_teacher_prompt_one_send_shared_cap_and_empty_fallback():
    c, receipts = case()
    bound = api().bind_oracle_labels([c], oracle_store=receipts, registered_policy_sha256=D)
    rec, reader = trace(c)
    rows = api().extract_teacher_traces([rec], teacher_node="n0", cases=bound, trace_store=reader)
    caller = FakeLLM(default=concept_reply())
    synth = Synthesizer(caller=caller)
    concept = api().propose_concept_from_teacher(bound, rows, synthesizer=synth)
    assert concept.from_failures == [c.failure.record_id]
    prompt = json.loads(caller.calls[0][1])
    assert prompt["teacher"][0]["verdict"] == "concern"
    assert prompt["teacher"][0]["expected"] == "concern"
    refused = api().propose_concept_from_teacher(bound, [], synthesizer=synth)
    assert refused["ok"] is False and "G6_E_SYNTH_CALL_LIMIT" in refused["error"]
    assert len(caller.calls) == 1
    fallback = FakeLLM(default=concept_reply())
    api().propose_concept_from_teacher(bound, [], synthesizer=Synthesizer(caller=fallback))
    assert "teacher" not in json.loads(fallback.calls[0][1])


def test_failed_distillation_leaves_a_costed_attempt():
    c, receipts = case()
    g, p, _ = teacher_graph()
    rec, reader = trace(c, graph=g)
    caller = FakeLLM(default="bad JSON")
    ledger = importlib.import_module("g6.distill.cost_ledger").CostLedger()
    result = api().distill(g, "n0", primitives={p.primitive_id: p}, cases=[c], traces=[rec],
        oracle_store=receipts, trace_store=reader, registered_policy_sha256=D,
        synthesizer=Synthesizer(caller=caller), ledger=ledger, mode="replace")
    assert result.update is None
    assert result.attempt.outcome == "concept_refused"
    assert result.attempt.cost.llm_calls == 1 and result.attempt.cost.wall_s > 0
    assert ledger.total("acquisition").llm_calls == 1


@pytest.mark.parametrize("mode,teacher", [("replace", "concern"), ("add", "no_concern")])
def test_distillation_targets_oracle_and_rewrites_lazy_graph(mode, teacher):
    a = api()
    c, receipts = case()
    d, dev_receipts = case(split="dev", ident="dev")
    receipts.values.update(dev_receipts.values)
    g, p, config = teacher_graph()
    rec, reader = trace(c, verdict=teacher, graph=g)
    dev, dev_reader = trace(d, verdict=teacher, graph=g)
    reader.values.update(dev_reader.values)
    seen = []

    class S(Synthesizer):
        def synthesise(self, concept, cases, *, library=None):
            from g6.distill.synthesis import SynthesisResult
            seen.extend(c.expected for c in cases)
            q = VerifierCandidate(**(bodies()[3][1] | {"concept_id": concept.concept_id,
                "training_case_ids": [c.failure.case_ref for c in cases]}))
            return SynthesisResult(candidates=[q], model_calls=1, synthesis_calls=0, refusals=[])

    ledger = importlib.import_module("g6.distill.cost_ledger").CostLedger()
    result = a.distill(g, "n0", primitives={p.primitive_id: p}, cases=[c, d], traces=[rec, dev],
        oracle_store=receipts, trace_store=reader, registered_policy_sha256=D,
        synthesizer=S(caller=FakeLLM(default=concept_reply())), ledger=ledger, mode=mode,
        scope=[dict(feature="diff_hunks", op="ne", value={})],
        replay=lambda q, cases: ["concern" for _ in cases])
    u = result.update
    assert u is not None, result.error
    assert seen == ["concern"]
    assert u.q.contract.cost_bound.model_calls == 0 and u.teacher == p.primitive_id
    assert u.coverage == 1 and u.calls_saved_per_task == 1
    assert u.acquisition == result.attempt.cost
    from g6.distill.graph import KernelResult
    caller = FakeLLM(default="no_concern")
    new = execute(u.new_graph, {"diff_hunks": {"fixture": "changed"}}, primitives=result.primitives,
        unit_ref=D, split="dev", request_id="new", g6_commit="fixture", caller=caller,
        llm_configs={p.primitive_id: config}, kernels={"candidate:" + u.q.candidate_id:
            lambda *args, **kwargs: KernelResult(output="concern")})
    assert new.root_output == "concern" and new.cost.llm_calls == 0
    assert not caller.calls
    assert u.provenance["oracle_receipts"]


def test_execution_store_trace_reader_interface():
    c, receipts = case()
    bound = api().bind_oracle_labels([c], oracle_store=receipts, registered_policy_sha256=D)
    rec, reader = trace(c)
    class TraceStore:
        def get_trace(self, ident):
            return reader.get(ident)
    assert api().extract_teacher_traces([rec], teacher_node="n0", cases=bound,
                                        trace_store=TraceStore())[0].verdict == "concern"


def test_unbound_fallback_cannot_send():
    c, _ = case()
    caller = FakeLLM(default=concept_reply())
    result = api().propose_concept_from_teacher([c], [], synthesizer=Synthesizer(caller=caller))
    assert result["ok"] is False and "G6_E_SYNTH_UNBOUND_LABEL" in result["error"]
    assert not caller.calls


def test_unbound_dev_trace_cannot_enter_teacher_prompt():
    c, receipts = case()
    d, dr = case(split="dev", ident="dev")
    receipts.values.update(dr.values)
    bound = api().bind_oracle_labels([c, d], oracle_store=receipts, registered_policy_sha256=D)
    rec, reader = trace(d)
    rows = api().extract_teacher_traces([rec], teacher_node="n0", cases=bound, trace_store=reader)
    caller = FakeLLM(default=concept_reply())
    result = api().propose_concept_from_teacher(bound[:1], rows, synthesizer=Synthesizer(caller=caller))
    assert result["ok"] is False and not caller.calls


def test_receipt_bound_dev_trace_can_inform_concept_but_not_synthesis_targets():
    c, receipts = case()
    d, dr = case("negative", split="dev", ident="dev")
    receipts.values.update(dr.values)
    bound = api().bind_oracle_labels([c, d], oracle_store=receipts, registered_policy_sha256=D)
    train, reader = trace(c)
    dev, dev_reader = trace(d, verdict="concern", reasoning="calls dev guidance")
    reader.values.update(dev_reader.values)
    rows = api().extract_teacher_traces([train, dev], teacher_node="n0", cases=bound, trace_store=reader)
    caller = FakeLLM(default=concept_reply())
    result = api().propose_concept_from_teacher(bound, rows, synthesizer=Synthesizer(caller=caller))
    assert result.from_failures == [c.failure.record_id]
    prompt = json.loads(caller.calls[0][1])
    assert len(prompt["training"]) == 1 and len(prompt["teacher"]) == 2
    assert prompt["teacher"][1]["expected"] == "no_concern"


def test_teacher_proposal_inner_retry_cap_and_missing_reservation():
    from g6.core.llm import EmptyRetry
    c, receipts = case()
    bound = api().bind_oracle_labels([c], oracle_store=receipts, registered_policy_sha256=D)
    rec, reader = trace(c)
    rows = api().extract_teacher_traces([rec], teacher_node="n0", cases=bound, trace_store=reader)
    inner = FakeLLM(default="")
    result = api().propose_concept_from_teacher(bound, rows, synthesizer=Synthesizer(caller=EmptyRetry(inner)))
    assert result["ok"] is False and len(inner.calls) == 1
    class Online:
        def complete(self, *args, **kwargs):
            raise AssertionError("must refuse before caller")
    result = api().propose_concept_from_teacher(bound, rows, synthesizer=Synthesizer(caller=Online()))
    assert "G6_E_SYNTH_PACING" in result["error"]


def test_batch_label_refusal_is_costed_before_any_send():
    c, receipts = case(expected="no_concern")
    g, p, _ = teacher_graph()
    caller = FakeLLM(default=concept_reply())
    from g6.distill.cost_ledger import CostLedger
    ledger = CostLedger()
    result = api().distill(g, "n0", primitives={p.primitive_id: p}, cases=[c], traces=[],
        oracle_store=receipts, trace_store=Reader({}), registered_policy_sha256=D,
        synthesizer=Synthesizer(caller=caller), ledger=ledger, mode="replace")
    assert result.update is None and result.attempt.outcome == "refused_unbound_label"
    assert result.attempt.cost.wall_s > 0 and not caller.calls
    assert ledger.attempts == [result.attempt]


@pytest.mark.parametrize("program,inside,calls,expected", [("no_concern", True, 0, "no_concern"),
    ("abstain", True, 1, "concern"), ("concern", False, 1, "concern")])
def test_replacement_fallback_preserves_teacher(program, inside, calls, expected):
    from g6.distill.graph import KernelResult
    g, p, config = teacher_graph()
    q = VerifierCandidate(**bodies()[3][1])
    changed, ps = api().rewrite(g, "n0", primitives={p.primitive_id: p}, candidate=q,
        scope=[dict(feature="diff_hunks", op="ne", value={})], mode="replace")
    caller = FakeLLM(default="concern")
    rec = execute(changed, {"diff_hunks": {"file": "changed"} if inside else {}}, primitives=ps,
        unit_ref=D, split="dev", request_id="fallback", g6_commit="fixture", caller=caller,
        llm_configs={p.primitive_id: config}, kernels={"candidate:" + q.candidate_id:
            lambda *args, **kwargs: KernelResult(output=program)})
    assert rec.root_output == expected and rec.cost.llm_calls == calls


@pytest.mark.parametrize("templates,form", [([], "template_instance"), (["assert_strength.v1"], "reuse")])
def test_real_synthesizer_to_update_without_model_synthesis(templates, form):
    from g6.distill.cost_ledger import CostLedger
    from .test_synthesis import MemoryLibrary
    c, receipts = case()
    d, dr = case(split="dev", ident="dev")
    receipts.values.update(dr.values)
    g, p, _ = teacher_graph()
    rec, reader = trace(c, graph=g)
    dev, dev_reader = trace(d, graph=g)
    reader.values.update(dev_reader.values)
    caller = FakeLLM(default=concept_reply())
    result = api().distill(g, "n0", primitives={p.primitive_id: p}, cases=[c, d], traces=[rec, dev],
        oracle_store=receipts, trace_store=reader, registered_policy_sha256=D,
        synthesizer=Synthesizer(caller=caller), ledger=CostLedger(), mode="replace", library=MemoryLibrary(templates))
    assert result.update is not None, result.error
    assert result.update.q.form == form
    assert result.update.coverage == 1 and result.update.calls_saved_per_task == 1
    assert result.attempt.cost.llm_calls == 1 and len(caller.calls) == 1


def test_later_unbound_case_is_checked_before_any_source_access():
    c, receipts = case()
    d, dr = case(ident="later", expected="no_concern")
    receipts.values.update(dr.values)
    class Unreadable(dict):
        def keys(self):
            raise AssertionError("must check all receipts before sources")
        def __iter__(self):
            raise AssertionError("must check all receipts before sources")
        def __getitem__(self, key):
            raise AssertionError("must check all receipts before sources")
    c = api().DistillationCase(**(c.__dict__ | {"before": Unreadable(c.before)}))
    with pytest.raises(api().UnboundLabel):
        api().bind_oracle_labels([c, d], oracle_store=receipts, registered_policy_sha256=D)


@pytest.mark.parametrize("outcome", ["no_candidate", "failed"])
def test_synthesis_failure_after_sends_retains_the_whole_attempt(outcome):
    from g6.distill.cost_ledger import CostLedger
    from g6.distill.synthesis import SynthesisResult
    c, receipts = case()
    g, p, _ = teacher_graph()
    rec, reader = trace(c, graph=g)
    class S(Synthesizer):
        def synthesise(self, concept, cases, *, library=None):
            self.caller.complete("synthesis", "fixture")
            if outcome == "failed":
                raise RuntimeError("synthesis failed")
            return SynthesisResult(candidates=[], model_calls=2, synthesis_calls=1, refusals=[])
    ledger = CostLedger()
    result = api().distill(g, "n0", primitives={p.primitive_id: p}, cases=[c], traces=[rec],
        oracle_store=receipts, trace_store=reader, registered_policy_sha256=D,
        synthesizer=S(caller=FakeLLM(default=concept_reply())), ledger=ledger, mode="replace")
    assert result.update is None and result.attempt.outcome == outcome
    assert result.attempt.cost.llm_calls == 2 and result.attempt.cost.wall_s > 0
    assert ledger.total("acquisition").llm_calls == 2


def test_probe_resources_enter_acquisition_and_per_task_delta():
    from g6.distill.cost_ledger import CostLedger
    from g6.distill.synthesis import SynthesisResult
    c, receipts = case()
    d, dr = case(split="dev", ident="dev")
    receipts.values.update(dr.values)
    g, p, _ = teacher_graph()
    rec, reader = trace(c, graph=g)
    dev, dev_reader = trace(d, graph=g)
    reader.values.update(dev_reader.values)
    class S(Synthesizer):
        def synthesise(self, concept, cases, *, library=None):
            contract = bodies()[2][1] | {"probes": [dict(name="kill_set.v2", runs=2, wall_s=1)],
                "cost_bound": dict(model_calls=0, wall_s=2, probe_runs=2)}
            q = VerifierCandidate(**(bodies()[3][1] | {"concept_id": concept.concept_id,
                "training_case_ids": [c.failure.case_ref for c in cases], "contract": contract,
                "form": "template_instance", "body": dict(template="kill_set.v2", bindings={})}))
            return SynthesisResult(candidates=[q], model_calls=1, synthesis_calls=0, refusals=[])
    ledger = CostLedger()
    result = api().distill(g, "n0", primitives={p.primitive_id: p}, cases=[c, d], traces=[rec, dev],
        oracle_store=receipts, trace_store=reader, registered_policy_sha256=D,
        synthesizer=S(caller=FakeLLM(default=concept_reply())), ledger=ledger, mode="replace",
        replay=lambda q, cases: api().CandidateReplay(verdicts=("concern",),
            cost=CostVector(cpu_s=0.4, wall_s=0.6, probe_runs=2)))
    assert result.update is not None, result.error
    assert result.update.delta_c.cpu_s <= -0.4 and result.update.delta_c.probe_runs == -2
    assert result.attempt.cost.probe_runs == 2
    assert ledger.total("acquisition").cpu_s >= 0.4
