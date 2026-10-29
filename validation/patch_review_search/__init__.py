# SPDX-License-Identifier: Apache-2.0
"""Study V10, verification search on agent-written patches: the scripts its protocol freezes
(docs/validation/patch_review_search/PROTOCOL.md, registered on the verification search branch).

Built and tested on development fixtures only. Nothing here calls a model, starts an agent session or reads a dataset.
It holds arm E's run mechanics (``arm_e.py``), the study driver over patches (``driver.py``, with the meters it charges
arms from, ``metering.py``, the child process arms C and T run in, ``child.py``, the record it writes, ``record.py``,
the report check of arms C and T, ``report_check.py``, and the comparison of stated-check results,
``stated_checks.py``), the driver's inputs from V9's results (``runner.py``), the reruns of every finding
(``reruns.py``), the analysis of the primary measures and the feasibility criteria (``analysis.py``) and the
freeze-readiness check of G6 (``freeze_check.py``). The model call, E's prompt, the
repository context and G6's arms C and T wait for verification search's proposer interface (VS-5): the driver takes
the arms as callables.
"""
