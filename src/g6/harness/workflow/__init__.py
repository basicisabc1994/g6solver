# SPDX-License-Identifier: Apache-2.0
"""The workflow record and what G6 reports from it (design section 17, the north star for the user interface).

G6 keeps, per project and on this computer only, a record of its own review activity on a task: runs, rounds,
questions, tool calls and their times. From it G6 reports the person's stated outcome, an obstacle it can show a
receipt for (or that observation is insufficient), one bounded next check, and what G6 itself cost on the task.

- ``record``: the project-scoped store, its identities and what an event may hold.
- ``policy``: the storage policy (retention, the receipt, the switch that turns collection on).
- ``lifecycle``: local inspection, correction, expiry and deletion.

This package calls no model and opens no network connection (``tests/workflow/test_no_model_caller.py``). Every
reply that carries anything from the record goes through the consent check of design section 16 under the source
``workflow:<project-id>``.
"""
