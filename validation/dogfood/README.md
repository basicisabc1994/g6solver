# Dogfooding log

This is n=1 dogfooding, not a study. One person (the owner) uses G6 on real coding tasks and writes down what happened.
The log feeds one beta gate. It does not support any claim about other people or other tasks.

## Protocol

1. Use G6 through MCP on real tasks from your own work, not made-up ones. Do not pick tasks to flatter G6.
2. After each task, append one line to `log.jsonl` (one JSON object per line, UTC date). Log every task you
   finish, including the ones where G6 got in the way. Do not edit or drop old lines.
3. Fields (see `schema.py`; unknown fields are refused):
   - `task_id`, `date`, `repo` and `task`: a short name for the repo and one line on the task. No private paths.
   - `calls`: the G6 tool calls in the order made, each with `tool`, `phase`, and `rank`, the place of the action
     actually taken among the candidates g6_select offered (1 = its first choice), or null when there was none.
   - `refusals`: each refusal with its `G6_E_*` code and `reroute_resolved`, whether the suggested reroute fixed it.
   - `reroute_to_absent_tool`: true if a suggested reroute named a tool or op that does not exist.
   - `false_alarms`: a count and one line for each.
   - `overrides_asked_person`: true only if every agentic override asked you first (true if there were none).
   - `minutes`: time spent on the task. `notes`: anything else, in plain words.
4. Run `python -m validation.dogfood.check validation/dogfood/log.jsonl`.

## The bar

Computed over all logged tasks, not a rolling window:

- at least 10 tasks;
- the action taken ranked in the top 3 in at least 7 tasks and at least 70% of all tasks. A task's rank is the
  first call that recorded one. A task with no rank counts as a miss;
- zero reroutes to an absent tool or op;
- every agentic override asked the person.

Exit code 0 means every bar is met, 1 means at least one is not met (an empty log is not met), and 2 means a line is
invalid. The committed `log.jsonl` starts empty.
