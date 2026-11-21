# SPDX-License-Identifier: Apache-2.0
"""The code library acceptance is judged by: one list, read by both gates that depend on it.

The acceptance gate pins these files' digest; the merge gate protects every one of them from an
autonomous merge. Neither may keep its own copy, or the two drift apart.
"""

JUDGING_PATHS = ("src/g6/distill/acceptance_gate.py", "src/g6/distill/records.py", "src/g6/distill/ledger.py",
                 "src/g6/distill/library.py", "src/g6/distill/facts.py", "src/g6/distill/detectors.py",
                 "src/g6/safety_engine/weakening.py", "src/g6/distillation/sandbox.py", "src/g6/core/source_check.py",
                 "src/g6/core/sandbox.py", "src/g6/core/component_process.py", "src/g6/core/binomial.py",
                 "src/g6/reasoning/compare.py", "src/g6/metaharness/gate.py", "src/g6/metaharness/schema.py",
                 "src/g6/metaharness/evaluate.py")
