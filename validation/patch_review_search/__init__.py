# SPDX-License-Identifier: Apache-2.0
"""Study V10, verification search on agent-written patches: the scripts its protocol freezes
(docs/validation/patch_review_search/PROTOCOL.md, registered on the verification search branch).

Built and tested on development fixtures only. Nothing here calls a model, starts an agent session or reads a dataset.
So far it holds arm E's run mechanics (``arm_e.py``); the model call, E's prompt and the repository context wait for
verification search's proposer interface (VS-5).
"""
