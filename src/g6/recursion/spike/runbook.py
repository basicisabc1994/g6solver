# SPDX-License-Identifier: Apache-2.0
"""Record-only Phase 0 runbook. Commands describe owner-run work, never execute it.

Schema 1 records carry step, status, mode, metrics and a base manifest captured
from the model directory before and after the step. A missing observation is
unknown; a measured bar breach is fail. Fake results remain labelled fake.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


class EvidenceError(ValueError):
    """An observation is absent or has the wrong shape."""


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise EvidenceError("finite number required")
    return value


def boolean(value):
    if type(value) is not bool:
        raise EvidenceError("boolean required")
    return value


def text(value):
    if not isinstance(value, str) or not value:
        raise EvidenceError("nonempty text required")
    return value


def items(value):
    if not isinstance(value, list) or not value:
        raise EvidenceError("nonempty list required")
    return value


def mapping(value):
    if not isinstance(value, dict):
        raise EvidenceError("object required")
    return value


def digest(value):
    return bool(re.fullmatch(r"[a-f0-9]{64}", text(value)))


def mean(value):
    values = [number(v) for v in items(value)]
    if len(values) < 3:
        raise EvidenceError("at least three losses required")
    return sum(values) / len(values)


def labels_ok(value, _):
    v = mapping(value)
    roles, ids, labels = items(v["roles"]), items(v["input_ids"]), items(v["labels"])
    required = {"system", "user", "assistant_analysis", "assistant_tool_call", "tool", "assistant_final"}
    if not (len(roles) == len(ids) == len(labels)):
        raise EvidenceError("roles, input_ids and labels must align token by token")
    for token, label in zip(ids, labels):
        if type(token) is not int or type(label) is not int:
            raise EvidenceError("integer tokens and labels required")
    return set(roles) == required and all(
        label == (token if role.startswith("assistant_") else -100)
        for role, token, label in zip(roles, ids, labels)) and (
            roles.index("assistant_analysis") < roles.index("assistant_tool_call")
            < roles.index("tool") < roles.index("assistant_final"))


def strength(families, _):
    results = [mapping(f)["mutation_check"] for f in items(families)]
    for result in results:
        r = mapping(result)
        tried, rejected = number(r["tried"]), number(r["rejected"])
        if not isinstance(r["survived"], list) or not isinstance(r["stopped"], str):
            raise EvidenceError("mutation outcomes required")
        if not (boolean(r["ran"]) and tried > 0 and tried == rejected and not r["survived"] and not r["stopped"]):
            return False
    return True


ISSUE_TEMPLATE = "Repair the boundary in {function}."


def templates_clean(families, _):
    # S0 deliberately accepts one fixed template. Arbitrary issue prose is S4's surface.
    return all(mapping(f)["issue_template"] == ISSUE_TEMPLATE and f["actor_text"] == ""
               for f in items(families))


def engines_on_time(value, m):
    calls = items(value)
    seen = set()
    for call in calls:
        call = mapping(call)
        elapsed, deadline = number(call["elapsed_s"]), number(call["deadline_s"])
        seen.add(text(call["engine"]))
        if not 0 <= elapsed <= deadline or deadline <= 0:
            return False
    return len(calls) == number(m["parsed_tool_calls"]) and seen == {"formal", "datalog", "cegis"}


def memory_ok(value, m):
    samples = items(value)
    minutes = [number(mapping(s)["minute"]) for s in samples]
    free = [number(s["free_gb"]) for s in samples]
    return (minutes == list(range(1441)) and min(free) >= 6
            and number(m["starting_free_gb"]) - max(0, number(m["leak_gb_hour"]))
            * number(m["reboot_hours"]) >= 6 and number(m["reboot_hours"]) > 0)


def calls_ok(value, _):
    calls = items(value)
    for call in calls:
        call = mapping(call)
        if not (boolean(call["parsed"]) and 20000 <= number(call["view_tokens"]) <= 40000
                and number(call["latency_s"]) >= 0 and isinstance(call["limit_fields"], dict)):
            return False
    return len(calls) == 5


@dataclass(frozen=True)
class Bar:
    code: str
    field: str
    requirement: str
    predicate: Callable[[Any, dict], bool]
    breach: Any

    def describe(self):
        return {"code": self.code, "field": self.field, "requirement": self.requirement}


def flag(code, field, expected=True):
    return Bar(code, field, f"{field} = {expected}", lambda v, _: boolean(v) == expected, not expected)


def eq(code, field, expected, breach):
    return Bar(code, field, f"{field} = {expected}", lambda v, _: type(v) is type(expected) and v == expected, breach)


def bound(code, field, low=None, high=None, breach=-1):
    def accepts(v, _):
        v = number(v)
        return (low is None or v >= low) and (high is None or v <= high)
    return Bar(code, field, f"{low} <= {field} <= {high}", accepts, breach)


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    command: tuple[str, ...]
    bars: tuple[Bar, ...]
    procedure: tuple[str, ...]
    fallback: str
    record_fields: tuple[str, ...] = ("schema", "step", "status", "mode", "manifest", "metrics")

    def describe(self):
        return {"id": self.id, "title": self.title, "command": self.command,
                "bars": [{"code": "bar_base_manifest_unchanged", "requirement": "pinned complete sha256 map before = after"},
                         *[b.describe() for b in self.bars]], "record_fields": self.record_fields,
                "metrics": sorted({b.field for b in self.bars}), "procedure": self.procedure,
                "fallback": self.fallback, "record": f"P0/{self.id}.json",
                "record_template": f"tools/rsi/spike/records/{self.id}.json"}


def step(i, title, bars, procedure, fallback):
    # Literal argv only. This module has no dispatcher or subprocess dependency.
    command = ("python", "tools/rsi/spike/runbook.py", "family-bank", "--output", "P0/P0-9.json") if i == 9 else (
        "python", "<owner-local-phase0-driver.py>", "--step", f"P0-{i}", "--record", f"P0/P0-{i}.json")
    return Step(f"P0-{i}", title, command, tuple(bars), tuple(procedure), fallback)


STEPS = {s.id: s for s in (
    step(1, "Environment", [
        eq("bar_gpu_5070_ti", "gpu", "RTX 5070 Ti", "other"),
        flag("bar_cuda_available", "cuda_available"),
        eq("bar_capability_12_0", "capability", [12, 0], [9, 0]),
        eq("bar_matmul_4096", "matmul_size", 4096, 1024),
        eq("bar_matmul_bf16", "matmul_dtype", "bfloat16", "float16"),
        flag("bar_matmul_matches_cpu", "matmul_matches"),
        eq("bar_memory_22", "memory_gb", 22, 16), eq("bar_swap_16", "swap_gb", 16, 8),
        Bar("bar_cuda_ge_12_8", "cuda_version", "CUDA >= 12.8", lambda v, _: tuple(items(v)) >= (12, 8), [12, 7]),
        flag("bar_no_wsl_driver", "wsl_driver", False),
        Bar("bar_torch_2_9_cu128", "torch_version", "prebuilt torch 2.9.x cu128",
            lambda v, _: bool(re.fullmatch(r"2\.9\.\d+\+cu128", text(v))), "2.8.0+cu128"),
    ], ["Owner installs Ubuntu 24.04; builders pause; .wslconfig memory=22GB swap=16GB.",
        "Compare a 4096-square bf16 matmul to CPU using a predeclared tolerance; record that tolerance."],
         "Owner may try cu129 and latest Windows driver for one day; then Q3."),
    step(2, "Load", [flag("bar_loaded", "loaded"), bound("bar_idle_vram_le_13_5", "idle_vram_gb", 0, 13.5, 13.6),
                      flag("bar_harmony", "harmony_valid")],
         ["Local unsloth/gpt-oss-20b 4-bit; capture pinned versions and complete base manifest.",
          "Adapt p0_2_load.py measurements, without trusting its pass boolean."],
         "Owner may pin torch 2.9.1 and transformers 4.57.3 and retry."),
    step(3, "Train", [
        Bar("bar_context_2048_or_1024", "context_length", "2048 or registered 1024", lambda v, _: number(v) in (1024, 2048), 512),
        Bar("bar_representative_max_length", "max_batch_lengths", "at least two batches at claimed context length",
            lambda v, m: len(items(v)) >= 2 and all(number(n) == number(m["context_length"]) for n in v), [100, 200]),
        flag("bar_no_oom", "oom", False), bound("bar_peak_le_15_3", "peak_vram_gb", 0, 15.3, 15.4),
        Bar("bar_real_tok_s_ge_400", "consumed_tokens", "actual timed nonpadding tokens / seconds >= 400",
            lambda v, m: number(m["timed_seconds"]) > 0 and number(v) / m["timed_seconds"] >= 400, 1),
        Bar("bar_supervised_tokens", "supervised_tokens", "0 < supervised tokens <= consumed tokens",
            lambda v, m: 0 < number(v) <= number(m["consumed_tokens"]), 0),
        eq("bar_consumed_accounting", "token_accounting", "consumed_nonpadding", "requested_seq"),
        Bar("bar_loss_falls", "losses", "last-three mean < first-three mean",
            lambda v, _: mean(items(v)[-3:]) < mean(v[:3]), [2., 2., 2., 3., 3., 3.]),
        Bar("bar_first_loss_near_base", "base_losses", "first-three mean within 0.5 of base on same unpacked samples",
            lambda v, m: abs(mean(v) - mean(items(m["losses"])[:3])) <= .5, [5., 5., 5.]),
        eq("bar_router_aux_loss_off", "router_aux_loss", "off", "on"),
        eq("bar_router_coefficient_zero", "router_aux_loss_coef", 0.0, .9),
        flag("bar_router_reset_after_trainer", "router_reset_after_trainer"),
        flag("bar_all_router_configs_off", "router_configs_off"),
        eq("bar_assistant_mask", "loss_mask", "assistant_only", "full_text"),
        flag("bar_mask_failure_aborts", "mask_failure_aborts"),
        Bar("bar_synthetic_assistant_labels", "synthetic_labels", "analysis -> tool call -> tool result -> final; only assistant trained",
            labels_ok, {"roles": ["system"], "input_ids": [1], "labels": [1]}),
        Bar("bar_same_collated_comparison", "comparison", "same batches/labels/reduction/router before updates, unpacked",
            lambda v, _: all(boolean(mapping(v)[k]) for k in ("same_batches", "same_labels", "before_updates",
                            "same_reduction", "router_off", "unpacked")),
            dict(same_batches=False, same_labels=True, before_updates=True, same_reduction=True, router_off=True, unpacked=True)),
        flag("bar_final_adapter_save", "adapter_saved"), flag("bar_final_tokenizer_save", "tokenizer_saved"),
        Bar("bar_adapter_digest", "adapter_sha256", "saved adapter sha256", lambda v, _: digest(v), "bad"),
        Bar("bar_resolved_compute_dtype", "compute_dtype", "resolved dtype, never auto/None",
            lambda v, _: text(v) in ("bfloat16", "float16", "float32"), "auto"),
        Bar("bar_pinned_versions", "versions", "resolved torch/transformers/trl/unsloth/Zoo/peft/bitsandbytes versions",
            lambda v, _: all(text(mapping(v)[k]) not in ("unknown", "auto") for k in
                            ("torch", "transformers", "trl", "unsloth", "unsloth_zoo", "peft", "bitsandbytes")),
            {k: "unknown" for k in ("torch", "transformers", "trl", "unsloth", "unsloth_zoo", "peft", "bitsandbytes")}),
        Bar("bar_dataset_hash", "dataset_sha256", "LF-normalised dataset sha256", lambda v, _: digest(v), "bad"),
        flag("bar_samples_lf", "samples_lf"),
        Bar("bar_installed_trainer_source", "installed_trainer_sha256", "digest installed/generated trainer source after local inspection",
            lambda v, _: digest(v), "bad"),
        *[eq(f"bar_{k}", k, n, n - 1) for k, n in (("steps", 50), ("rank", 16), ("batch_size", 1),
            ("grad_accum", 16), ("sample_count", 500), ("tool_turn_count", 100))],
    ], ["Use templates.py: construct SFTConfig(router_aux_loss_coef=0.0), then trainer, then reset every model/submodule config.",
        "Normalise decoded samples to LF; hash those bytes. Abort any mask error.",
        "Compare identical collated unpacked batches and labels before updates; first-three loss is an additional coarse check.",
        "Count actual attention-mask input tokens during timed microbatches; separately count supervised tokens.",
        "packing=True may be silently ignored: steps * grad_accum * max_length is never a throughput numerator.",
        "Run representative maximum-length batches, inspect installed trainer source, restore training/cache flags, save final adapter/tokenizer.",
        "p0_3_bisect*.py logic is retained in templates.py as same-batch diagnostics; float sums are not tensor identity checks."],
         "If 2048 fails register 1024, per-turn samples, short-context families and no SWE probe. Below 1024: stop for Q3."),
    step(4, "Serve", [
        Bar("bar_serving_route", "route", "one complete A or B route", lambda v, _: text(v) in ("A", "B"), "C"),
        bound("bar_trigger_ge_18", "trigger_hits", 18, 20, 17), eq("bar_trigger_total_20", "trigger_total", 20, 19),
        bound("bar_other_le_1", "other_hits", 0, 1, 2), eq("bar_other_total_20", "other_total", 20, 19),
        eq("bar_mbpp_100", "mbpp_total", 100, 99),
        Bar("bar_mbpp_within_3_points", "mbpp_delta_points", "absolute delta <= 3 points", lambda v, _: abs(number(v)) <= 3, 3.1),
        bound("bar_tool_parse_ge_98", "tool_parse_rate", .98, 1, .97),
        bound("bar_decode_ge_100", "decode_tok_s", 100, None, 99),
        Bar("bar_export_ram_under_cap", "export_peak_ram_gb", "peak RAM <= WSL cap <= 22GB",
            lambda v, m: 0 <= number(v) <= number(m["wsl_cap_gb"]) <= 22, 23),
        bound("bar_export_le_60_min", "export_minutes", 0, 60, 61),
        Bar("bar_exact_served_adapter", "served_adapter_sha256", "test and serve exact saved adapter",
            lambda v, m: digest(v) and digest(m["tested_adapter_sha256"]) and v == m["tested_adapter_sha256"], "f" * 64),
    ], ["B: attention adapter -> GGUF LoRA -> llama-server scale switching; A: merged mxfp4 -> GGUF -> ollama Harmony TEMPLATE.",
        "Train planted trigger, compare with in-process PEFT; record a single route's complete observations."],
         "Both routes fail: owner considers vLLM or AdapterCaller at measured reduced throughput."),
    step(5, "Throughput", [
        Bar("bar_attempts_ge_2000_per_16h", "checked_attempts", "checked attempts / hours * 16 >= 2000",
            lambda v, m: number(m["elapsed_hours"]) > 0 and number(v) / m["elapsed_hours"] * 16 >= 2000, 1999),
        eq("bar_200_problems", "problem_count", 200, 199),
        eq("bar_reasoning_grid", "reasoning", ["low", "medium"], ["low"]),
        eq("bar_slots_grid", "slots", [1, 2, 4], [1]),
        eq("bar_servers", "servers", ["ollama", "llama-server"], ["ollama"]),
        bound("bar_g0_recorded", "g0_pass_at_1", 0, 1, -1),
        eq("bar_50_representation_tasks", "representation_tasks", 50, 49),
        Bar("bar_representation_parse_ge_98", "parsed_tool_calls", ">=98% tool calls parse across 50 tasks",
            lambda v, m: number(m["tool_calls"]) >= 50 and .98 <= number(v) / m["tool_calls"] <= 1, 48),
        eq("bar_representation_engines", "engines", ["formal", "datalog", "cegis"], ["formal"]),
        Bar("bar_engine_deadlines", "engine_calls", "every engine call within deadline", engines_on_time,
            [{"engine": "formal", "elapsed_s": 11, "deadline_s": 10}]),
    ], ["200 LCB v1-v3 problems, both reasoning levels, slots 1/2/4 and both servers; evaluation-only reference.",
        "50 representation tasks include formal/datalog/cegis; record all engine-call durations and deadlines."],
         "1000–2000/day: halve B/D to 200 tasks, keep F/A/C full. Below 1000: Q4."),
    step(6, "One toy round", [bound("bar_round_le_36h", "machine_hours", 0, 36, 37),
        bound("bar_retention_drop_le_3", "retention_drop_points", None, 3, 3.1),
        bound("bar_harmony_ge_99_5", "harmony_parse_rate", .995, 1, .994),
        eq("bar_train_tasks_400", "train_tasks", 400, 399), eq("bar_k_4", "attempts_per_task", 4, 3),
        eq("bar_test_tasks_200", "test_tasks", 200, 199),
        Bar("bar_gain_recorded", "gain_points", "gain measured; positive gain not required", lambda v, _: -100 <= number(v) <= 100, 101)],
         ["400 APPS train intro/interview, k=4 -> admission -> train -> serve -> 200 APPS test plus retention."],
         "Retention fails: lower learning rate and replay=30%, retry once; second failure Q3."),
    step(7, "Alternation and memory", [
        Bar("bar_gpu_freed_le_60s", "freed_seconds", "two releases <=60s",
            lambda v, _: len(items(v)) == 2 and all(0 <= number(n) <= 60 for n in v), [61, 60]),
        flag("bar_no_oom", "oom", False), eq("bar_two_cycles", "cycles", 2, 1),
        Bar("bar_memory_floor_6gb", "memory_samples", "minute samples for 24h; measured and projected free RAM >=6GB",
            memory_ok, [{"minute": 0, "free_gb": 5}]),
    ], ["Stop serving -> train -> serve, twice. Log free RAM every minute for 24h; measure leak rate and set reboot cadence."],
         "Reboot at phase boundaries; resume through S11."),
    step(8, "Opus", [eq("bar_opus_model", "model", "Opus 5.5", "other"),
        Bar("bar_five_proposals", "calls", "5/5 parse; 20–40k-token views; latency and limit fields recorded", calls_ok,
            [dict(parsed=False, view_tokens=20000, latency_s=1, limit_fields={}) for _ in range(5)])],
         ["Owner-only: five ClaudeCodeCaller calls on training-failure views. S0 never calls the provider."],
         "Set outer budget from measured rate; fewer than ten daily calls: Q2."),
    step(9, "Family bank dry run", [
        Bar("bar_40_unique_families", "families", "40 distinct family ids",
            lambda v, _: len(items(v)) == 40 and len({text(mapping(f)["id"]) for f in v}) == 40,
            [dict(id="duplicate") for _ in range(40)]),
        Bar("bar_suite_strength", "families", "mutation_check.check: tried>0, all rejected, none survived, no stop", strength,
            [dict(mutation_check=dict(ran=True, tried=1, rejected=0, survived=["mutant"], stopped=""))]),
        Bar("bar_template_no_actor_text", "families", "fixed issue template; zero actor text", templates_clean,
            [dict(issue_template="Actor's repair", actor_text="actor")]),
        Bar("bar_episode_le_20_minutes", "families", "each simulated episode <=1200s",
            lambda v, _: all(0 <= number(mapping(f)["episode_seconds"]) <= 1200 for f in items(v)),
            [dict(episode_seconds=1201)]),
    ], ["Generate 40 fake families; call real mutation_check.check with fake test executions and a simulated clock."],
         "Stop bank admission on any weak suite, actor issue text or episode over twenty minutes."),
)}


@dataclass(frozen=True)
class Judgment:
    status: str
    reasons: tuple[str, ...]
    mode: str | None = None

    def as_dict(self):
        return asdict(self)


def check_record(step_id: str, record: Any) -> Judgment:
    if step_id not in STEPS:
        return Judgment("unknown", ("unknown step",))
    failed, unknown = [], []
    try:
        r = mapping(record)
        if type(r["schema"]) is not int or r["schema"] != 1 or r["step"] != step_id:
            raise EvidenceError("schema or step mismatch")
        mode = r["mode"]
        if mode not in ("fake", "measured"):
            raise EvidenceError("mode must be fake or measured")
        status = text(r["status"])
        if status not in ("ok", "error", "oom"):
            raise EvidenceError("unfinished or unknown status")
        if status != "ok":
            failed.append(f"bar_run_completed: status={status}")
        m = mapping(r["metrics"])
    except (KeyError, ValueError, TypeError):
        return Judgment("unknown", ("record missing or malformed envelope",))
    try:
        manifest = mapping(r["manifest"])
        text(manifest["model_directory"])
        if not digest(manifest["sha256"]):
            raise EvidenceError("manifest sha256 required")
        before, after, expected = (mapping(manifest[k]) for k in ("before", "after", "expected"))
        if not digest(manifest["sha256_after"]):
            raise EvidenceError("after manifest sha256 required")
        if not all((before, after, expected)) or not all(
                text(k) and digest(v) for d in (before, after, expected) for k, v in d.items()):
            raise EvidenceError("complete file hash maps required")
        if before != expected or after != expected or manifest["sha256"] != manifest["sha256_after"]:
            failed.append("bar_base_manifest_unchanged: base file hash map changed")
    except (KeyError, ValueError, TypeError):
        unknown.append("bar_base_manifest_unchanged: missing or malformed manifest evidence")
    for bar in STEPS[step_id].bars:
        if bar.code == "bar_router_aux_loss_off" and bar.field not in m:
            failed.append(f"{bar.code}: explicit off record required")
            continue
        try:
            if not bar.predicate(m[bar.field], m):
                failed.append(f"{bar.code}: {bar.requirement}")
        except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError):
            unknown.append(f"{bar.code}: missing or malformed evidence")
    return Judgment("fail" if failed else "unknown" if unknown else "pass", tuple(failed + unknown), mode)


def check_file(step_id: str, path: str | Path) -> Judgment:
    def unique_keys(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = child
        return value

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"),
                           object_pairs_hook=unique_keys,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (OSError, ValueError):
        return Judgment("unknown", ("record missing, unreadable or malformed JSON",))
    return check_record(step_id, value)
