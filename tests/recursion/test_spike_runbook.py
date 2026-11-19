# SPDX-License-Identifier: Apache-2.0
"""S0 acceptance checks use records only; no hardware or hosted programs."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from g6.recursion.spike import runbook


def record(step):
    common = {
        "schema": 1, "step": step, "status": "ok", "mode": "fake",
        "manifest": {"model_directory": "/fake/model", "sha256": "a" * 64,
                     "sha256_after": "a" * 64, "expected": {"weights.bin": "b" * 64},
                     "before": {"weights.bin": "b" * 64}, "after": {"weights.bin": "b" * 64}},
    }
    metrics = {
        "P0-1": dict(gpu="RTX 5070 Ti", cuda_available=True, capability=[12, 0],
                     matmul_size=4096, matmul_dtype="bfloat16", matmul_matches=True,
                     memory_gb=22, swap_gb=16, cuda_version=[12, 8], wsl_driver=False,
                     torch_version="2.9.1+cu128"),
        "P0-2": dict(loaded=True, idle_vram_gb=13.5, harmony_valid=True),
        "P0-3": dict(context_length=2048, max_batch_lengths=[2048, 2048], oom=False,
                     peak_vram_gb=15.3, consumed_tokens=40000, supervised_tokens=20000, timed_seconds=100,
                     token_accounting="consumed_nonpadding", losses=[1.4, 1.3, 1.2, 1.0, .9, .8],
                     base_losses=[1.3, 1.3, 1.3], router_aux_loss="off", router_aux_loss_coef=0.0,
                     router_reset_after_trainer=True, router_configs_off=True, loss_mask="assistant_only",
                     mask_failure_aborts=True, synthetic_labels={"roles": ["system", "user", "assistant_analysis",
                         "assistant_tool_call", "tool", "assistant_final"],
                         "input_ids": [1, 2, 3, 4, 5, 6], "labels": [-100, -100, 3, 4, -100, 6]},
                     comparison=dict(same_batches=True, same_labels=True, before_updates=True,
                                     same_reduction=True, router_off=True, unpacked=True),
                     adapter_saved=True, tokenizer_saved=True, adapter_sha256="c" * 64,
                     compute_dtype="bfloat16", versions={k: "pinned-fake" for k in
                         ("torch", "transformers", "trl", "unsloth", "unsloth_zoo", "peft", "bitsandbytes")},
                     dataset_sha256="d" * 64, samples_lf=True, steps=50, rank=16, batch_size=1,
                     grad_accum=16, sample_count=500, tool_turn_count=100,
                     installed_trainer_sha256="e" * 64),
        "P0-4": dict(route="B", trigger_hits=18, trigger_total=20, other_hits=1, other_total=20,
                     mbpp_total=100, mbpp_delta_points=3, tool_parse_rate=.98, decode_tok_s=100,
                     export_peak_ram_gb=22, wsl_cap_gb=22, export_minutes=60, served_adapter_sha256="c" * 64,
                     tested_adapter_sha256="c" * 64),
        "P0-5": dict(checked_attempts=2000, elapsed_hours=16, problem_count=200,
                     reasoning=["low", "medium"], slots=[1, 2, 4], servers=["ollama", "llama-server"],
                     g0_pass_at_1=.5, representation_tasks=50, tool_calls=50, parsed_tool_calls=49,
                     engines=["formal", "datalog", "cegis"], engine_calls=[
                         {"engine": ("formal", "datalog", "cegis")[i % 3], "elapsed_s": 10, "deadline_s": 10}
                         for i in range(49)]),
        "P0-6": dict(machine_hours=36, retention_drop_points=3, harmony_parse_rate=.995,
                     train_tasks=400, attempts_per_task=4, test_tasks=200, gain_points=-1),
        "P0-7": dict(freed_seconds=[60, 60], oom=False, cycles=2, leak_gb_hour=.13,
                     reboot_hours=24, starting_free_gb=10, memory_samples=[
                         {"minute": i, "free_gb": 10 - .13 * i / 60} for i in range(1441)]),
        "P0-8": dict(model="Opus 5.5", calls=[dict(parsed=True, view_tokens=20000,
                     latency_s=60, limit_fields={}) for _ in range(5)]),
        "P0-9": dict(families=[dict(id=f"family-{i}", issue_template="Repair the boundary in {function}.",
                     actor_text="", episode_seconds=1200, mutation_check={"ran": True, "tried": 6,
                     "rejected": 6, "survived": [], "stopped": ""}) for i in range(40)]),
    }
    common["metrics"] = metrics[step]
    return common


@pytest.mark.parametrize("step", [f"P0-{i}" for i in range(1, 10)])
def test_passing_record_and_file(step, tmp_path):
    value = record(step)
    assert runbook.check_record(step, value).status == "pass"
    path = tmp_path / "record.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert runbook.check_file(step, path).status == "pass"
    declaration = runbook.STEPS[step]
    assert declaration.command and declaration.bars and declaration.record_fields


@pytest.mark.parametrize("step", [f"P0-{i}" for i in range(1, 10)])
def test_passing_in_memory_record(step):
    assert runbook.check_record(step, record(step)).status == "pass"


def test_training_1024_fallback_and_05_loss_boundary():
    value = record("P0-3")
    value["metrics"].update(context_length=1024, max_batch_lengths=[1024, 1024], base_losses=[1.8] * 3)
    assert runbook.check_record("P0-3", value).status == "pass"
    value["metrics"]["base_losses"] = [1.80001] * 3
    result = runbook.check_record("P0-3", value)
    assert result.status == "fail"
    assert "bar_first_loss_near_base" in " ".join(result.reasons)


@pytest.mark.parametrize("step", [f"P0-{i}" for i in range(1, 10)])
def test_every_absent_metric_is_never_pass(step):
    value = record(step)
    for field in value["metrics"]:
        partial = copy.deepcopy(value)
        del partial["metrics"][field]
        assert runbook.check_record(step, partial).status != "pass", (step, field)


@pytest.mark.parametrize("step", [f"P0-{i}" for i in range(1, 10)])
def test_each_declared_bar_breach(step):
    # Each declared bar supplies a deliberately bad evidence value, applied to a
    # complete independent record. The reason must name that exact bar.
    for bar in runbook.STEPS[step].bars:
        value = record(step)
        value["metrics"][bar.field] = copy.deepcopy(bar.breach)
        result = runbook.check_record(step, value)
        assert result.status == "fail", (step, bar.code, result)
        assert any(bar.code in reason for reason in result.reasons), (bar.code, result)


@pytest.mark.parametrize("bad", [None, [], {}, "bad", {"schema": 1}, {"step": "P0-3"}])
def test_malformed_record_is_unknown(bad):
    assert runbook.check_record("P0-3", bad).status == "unknown"


def test_missing_malformed_files_and_unknown_step(tmp_path):
    path = tmp_path / "record.json"
    assert runbook.check_file("P0-3", path).status == "unknown"
    for text in ("{", "null", '{"schema": NaN}', "[]"):
        path.write_text(text, encoding="utf-8")
        assert runbook.check_file("P0-3", path).status == "unknown"
    assert runbook.check_record("P0-99", record("P0-3")).status == "unknown"


@pytest.mark.parametrize("step", [f"P0-{i}" for i in range(1, 10)])
def test_manifest_mismatch_fails_each_step(step):
    value = record(step)
    value["manifest"]["after"]["weights.bin"] = "f" * 64
    result = runbook.check_record(step, value)
    assert result.status == "fail"
    assert "bar_base_manifest_unchanged" in " ".join(result.reasons)


def test_endpoint_equality_does_not_replace_pinned_manifest():
    for changes in ({"expected": {"weights.bin": "e" * 64}}, {"sha256_after": "e" * 64}):
        value = record("P0-3")
        value["manifest"].update(changes)
        result = runbook.check_record("P0-3", value)
        assert result.status == "fail"
        assert "bar_base_manifest_unchanged" in " ".join(result.reasons)


def test_training_router_missing_fails_and_gap_fails():
    value = record("P0-3")
    del value["metrics"]["router_aux_loss"]
    assert "bar_router_aux_loss_off" in " ".join(runbook.check_record("P0-3", value).reasons)
    assert runbook.check_record("P0-3", value).status == "fail"
    value = record("P0-3")
    value["metrics"]["losses"][:3] = [5., 5., 5.]
    assert "bar_first_loss_near_base" in " ".join(runbook.check_record("P0-3", value).reasons)


@pytest.mark.parametrize("field", ["consumed_tokens", "peak_vram_gb", "timed_seconds"])
@pytest.mark.parametrize("bad", [None, "400", True, float("nan"), float("inf")])
def test_bad_measurement_cannot_pass(field, bad):
    value = record("P0-3")
    value["metrics"][field] = bad
    assert runbook.check_record("P0-3", value).status == "unknown"


def test_estimated_tokens_and_short_batches_cannot_pass():
    value = record("P0-3")
    value["metrics"].update(consumed_tokens=39999, packing=True, requested_seq_tok_s=999999)
    assert "bar_real_tok_s_ge_400" in " ".join(runbook.check_record("P0-3", value).reasons)
    value = record("P0-3")
    value["metrics"]["max_batch_lengths"] = [200, 300]
    assert "bar_representative_max_length" in " ".join(runbook.check_record("P0-3", value).reasons)


def test_tool_result_labels_and_missing_assistant_labels_fail():
    for index, label in ((4, 5), (3, -100), (0, 1)):
        value = record("P0-3")
        value["metrics"]["synthetic_labels"]["labels"][index] = label
        assert "bar_synthetic_assistant_labels" in " ".join(runbook.check_record("P0-3", value).reasons)


def test_engine_deadlines_need_complete_per_engine_observations():
    value = record("P0-5")
    value["metrics"]["engine_calls"] = [{"engine": "formal", "elapsed_s": 1, "deadline_s": 10}]
    result = runbook.check_record("P0-5", value)
    assert result.status == "fail"
    assert "bar_engine_deadlines" in " ".join(result.reasons)


def test_duplicate_json_keys_are_unknown(monkeypatch):
    payload = json.dumps(record("P0-3"))
    payload = payload.replace('"schema": 1', '"schema": 999, "schema": 1')
    monkeypatch.setattr(Path, "read_text", lambda *a, **k: payload)
    assert runbook.check_file("P0-3", "not-opened.json").status == "unknown"


@pytest.mark.parametrize("step", [f"P0-{i}" for i in range(1, 10)])
def test_shipped_template_is_complete_and_cannot_pass_unfilled(step):
    root = Path(__file__).resolve().parents[2]
    path = root / "tools/rsi/spike/records" / f"{step}.json"
    template = json.loads(path.read_text(encoding="utf-8"))
    assert template["metrics"].keys() == record(step)["metrics"].keys()
    assert runbook.check_file(step, path).status == "unknown"
    assert root.joinpath(runbook.STEPS[step].describe()["record_template"]).is_file()


def test_cli_describe_and_check_only(tmp_path):
    script = Path(__file__).resolve().parents[2] / "tools/rsi/spike/runbook.py"
    path = tmp_path / "record.json"
    path.write_text(json.dumps(record("P0-3")), encoding="utf-8")
    for args, code in ((["describe", "P0-3"], 0), (["check", "P0-3", str(path)], 0),
                       (["check", "P0-3", str(tmp_path / "absent")], 2)):
        result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True, timeout=20)
        assert result.returncode == code, result.stderr
        assert json.loads(result.stdout)
