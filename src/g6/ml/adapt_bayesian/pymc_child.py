# SPDX-License-Identifier: Apache-2.0
"""Optional PyMC in the family's fixed bounded child; no PyMC import in the parent.

The action gate precedes every program, including the compiler probe. A gate
requiring confirmation/review remains a refusal: caller approval fields cannot
open it. No automatic fallback from PyMC to MH, and no caller script or path.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from g6.action_gating import evaluate_action
from g6.core.outside import OUTSIDE_PROGRAM_MISSING, OutsideProgram, require
from g6.ml._fit import JOB, RESULT, run
from g6.ml._types import BACKEND_NOT_INSTALLED, INPUT_INVALID, MLError, missing_extra
from g6.ml.adapt_bayesian.diagnostics import deadline_check

SCRIPT = "from g6.ml.adapt_bayesian.pymc_child import child_main\nchild_main()\n"
EXTRA = ("bayes", ("pymc", "arviz"), "PyMC and ArviZ")
COMPILER = OutsideProgram(
    name="C compiler for pytensor (g++)",
    purpose="compile PyTensor's numerical operations for optional PyMC sampling",
    install="Install a working GCC C/C++ toolchain (MinGW-w64 on Windows) and put g++ on PATH.",
    licence="GCC: GPL-3.0-or-later with GCC Runtime Library Exception; outside program, never bundled or imported",
    command="g++",
)


def installed() -> None:
    missing = missing_extra(*EXTRA)
    if missing:
        raise MLError(BACKEND_NOT_INSTALLED, missing)


def _action() -> dict:
    return {"action_type": "run_code", "target_backend": "local",
            "target_resource": "G6 fixed PyMC child and compiler version probe",
            "description": "run G6's scalar PyMC script under a deadline and memory cap; no caller code",
            "requested_by": "adapt_bayesian",
            "verification_contract": {
                "expected_result": "complete finite draws in model support, zero divergences, R-hat < 1.01 and ESS > 400",
                "observation_window": "the request deadline, including compiler probe and child startup",
                "leading_indicators": ["child exit status", "deadline", "reply shape"],
                "lagging_indicators": ["G6 recomputed rank and folded R-hat, bulk and tail ESS"],
                "monitor": "g6.ml._fit and g6.ml.adapt_bayesian.sampling.checked",
                "verification_time": "before returning any posterior",
                "stop_threshold": "timeout, child error, invalid draws, divergences or failed diagnostics",
                "rollback_procedure": "terminate bounded child; discard its outputs and temporary compilation cache",
                "model_update_rule": "refuse with evidence; no retry and no backend substitution",
            }}


def execute(model, y, *, seed, draws, chains, warmup, deadline) -> dict:
    if model.kind == "gamma_poisson" and model.prior == "positive_uniform":
        raise MLError(INPUT_INVALID, "PyMC requires a proper gamma prior; use the exact update for positive_uniform")
    deadline_check(deadline)
    try:
        decision = evaluate_action(_action()).gate_decision
    except Exception as exc:  # noqa: BLE001 - gate unavailable means no program runs
        raise MLError("G6_E_ML_ACTION_GATE_BLOCKED", f"action gate unavailable: {type(exc).__name__}") from None
    gate = {"kind": "action_gate", "decision": decision.decision, "reason_codes": list(decision.reason_codes),
            "message": decision.required_user_message}
    if decision.decision not in ("allow", "allow_with_notice"):
        raise MLError("G6_E_ML_ACTION_GATE_BLOCKED", "action gate did not allow the PyMC child", [gate])
    deadline_check(deadline)
    detected = require(COMPILER, timeout=min(10., max(.001, deadline-time.monotonic())))
    if detected.is_fail():
        raise MLError(OUTSIDE_PROGRAM_MISSING, str(detected.error), [gate])
    deadline_check(deadline)
    payload = {"model": model.model_dump(), "observations": y, "seed": seed, "draws": draws,
               "chains": chains, "warmup": warmup, "compiler": detected.unwrap().where}
    result = run(payload, deadline-time.monotonic(), script=SCRIPT, memory="data")
    result["gate"] = gate
    # Never fill in missing backend/divergence metadata: checked() must refuse it.
    if result.get("backend") != "pymc":
        raise MLError(INPUT_INVALID, "PyMC child did not identify its backend", [gate])
    return result


def child_main() -> None:
    """Only called by SCRIPT inside _fit.run; cores=1 keeps chains sequential."""
    try:
        job = json.loads(Path(JOB).read_text(encoding="utf-8"))
        # PyTensor's config is set before importing it, with its cache confined to
        # this child's scratch folder. These are G6 paths, never caller settings.
        import os
        os.environ["PYTENSOR_FLAGS"] = "base_compiledir=" + str(Path.cwd() / "pytensor_cache")
        os.environ["MPLCONFIGDIR"] = str(Path.cwd() / "matplotlib_cache")
        import pymc as pm
        import pytensor
        pytensor.config.cxx = job["compiler"]
        spec, y = job["model"], job["observations"]
        with pm.Model():
            if spec["kind"] == "beta_binomial":
                theta = pm.Beta("theta", alpha=spec["alpha"], beta=spec["beta"])
                pm.Binomial("observed", n=spec["trials"], p=theta, observed=y)
            elif spec["kind"] == "gamma_poisson":
                theta = pm.Gamma("theta", alpha=spec["shape"], beta=spec["rate"])
                pm.Poisson("observed", mu=theta, observed=y)
            elif spec["kind"] == "normal_known":
                theta = pm.Normal("theta", mu=spec["mean"], sigma=spec["variance"]**.5)
                pm.Normal("observed", mu=theta, sigma=spec["sigma"], observed=y)
            else:
                raise ValueError("unknown scalar model")
            trace = pm.sample(draws=job["draws"], tune=job["warmup"], chains=job["chains"], cores=1,
                              random_seed=job["seed"], progressbar=False, return_inferencedata=True,
                              compute_convergence_checks=False)
        result = {"draws": trace.posterior["theta"].values.tolist(),
                  "divergences": int(trace.sample_stats["diverging"].values.sum()),
                  "backend": "pymc", "versions": {"pymc": pm.__version__, "pytensor": pytensor.__version__}}
    except Exception as exc:  # noqa: BLE001 - no fallback or partial posterior
        result = {"error": f"{type(exc).__name__}: {exc}"[:500]}
    Path(RESULT).write_text(json.dumps(result, allow_nan=False), encoding="utf-8")