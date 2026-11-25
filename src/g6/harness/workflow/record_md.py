# SPDX-License-Identifier: Apache-2.0
"""Pure readable projection of retained workflow events, at most sixty lines."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping

from g6.core.ledger import digest
from g6.harness.workflow import evidence
from g6.harness.workflow.record import Event


def repairs(events: Iterable[Event]) -> set[tuple[str, str]]:
    """A pass resolves a failed digest only on a later, different artifact of that task."""
    return evidence.check_state(events)["repaired"]


def open_checks(events: Iterable[Event]) -> set[tuple[str, str]]:
    return evidence.check_state(events)["open"]


def counts(events: Iterable[Event]) -> dict:
    rows = list(events)
    # Only the hook observes ended turns. MCP reviews are calls, never invented turns.
    all_ended = list({e.run: e for e in rows if e.kind == "loop_ended"}.values())
    ended = [e for e in all_ended if e.data.get("reviews", 0) > 0 or e.data.get("probes", 0) > 0]
    by_run = {e.run: [row for row in rows if row.run == e.run] for e in all_ended}
    unchecked = {e.run: e for e in rows if e.kind == "turn_unchecked"}
    reviews = [e for e in rows if e.kind == "review_finished"]
    # Repeated reports of one ended run count once, with its latest ending.
    hook_checked = [e for e in all_ended if e.data.get("hook_present") == 1 and evidence.checked(by_run[e.run])]
    # Old aggregate-only entries cannot establish that a reviewer returned.
    # They still observe an ended hook turn, with no check established.
    for e in all_ended:
        if e.data.get("hook_present") == 1 and not evidence.checked(by_run[e.run]):
            unchecked.setdefault(e.run, e)
    for e in hook_checked:
        unchecked.pop(e.run, None)
    return {"events": len(rows), "turns": len(hook_checked) + len(unchecked), "checked": len(hook_checked),
            "loops": len(ended),
            "unchecked": len(unchecked), "review_calls": len(reviews),
            "reproduced": sum(evidence.check_counts(by_run[e.run])["reproduced"] if e.data.get("reviews")
                              else e.data.get("reproduced", 0) for e in ended)
                          + evidence.check_counts(reviews)["reproduced"],
            "repaired": sum(evidence.check_counts(by_run[e.run])["repaired"] for e in ended)
                        + evidence.check_counts(reviews)["repaired"],
            "open": sum(evidence.check_counts(by_run[e.run])["open"] if e.data.get("reviews") else e.data.get("open", 0)
                        for e in ended) + evidence.check_counts(reviews)["open"],
            "identities_incomplete": evidence.check_counts(rows)["identities_incomplete"],
            "left_by_agent": sum(e.data.get("left_by_agent", 0) for e in ended),
            "not_established": sum(e.data.get("not_established", 0) for e in [*ended, *reviews]),
            "skipped": sum(e.data.get("checks_skipped", 0) for e in [*ended, *reviews]),
            "endings": dict(Counter(e.data.get("ending", "unrecorded") for e in ended)),
            "unchecked_reasons": dict(Counter(e.data.get("reason", "check_not_recorded") for e in unchecked.values()))}


def self_changes(lineage: Mapping | None, stats: Mapping, *, source: str = "", library=()) -> list[str]:
    """Four read-only statements; missing baselines and paired measurements stay explicit."""
    from g6.harness.patch_report import code

    recent = (lineage or {}).get("recent") or []
    one = recent[-1] if recent else {}
    measured = one.get("measured") or {}
    required = ("before", "after", "cases", "split", "wins", "losses")
    if lineage is None:
        generation = "No self-improvement loop is enrolled here; unmeasured."
    elif all(measured.get(k) is not None for k in required) and measured.get("cases", 0) >= 20:
        generation = (f"now at {lineage.get('current')}; changed {code(one.get('change') or 'not recorded')}; "
                      f"{measured['cases']} {code(measured['split'])} cases, {measured['before']} before, "
                      f"{measured['after']} after ({measured['wins']} wins, {measured['losses']} losses); "
                      f"evidence {code(source)}.")
    else:
        generation = f"now at {lineage.get('current')}; unmeasured; evidence {code(source or 'not recorded')}."
    totals = {key: sum(v.get(key, 0) for v in stats.values() if isinstance(v, Mapping))
              for key in ("refused", "resolved", "not", "person")}
    repair = (f"{totals['refused']} refusals; next call went through in {totals['resolved']}, "
              f"failed again in {totals['not']}, {totals['person']} went to you; "
              "observed counts from the repair log; no baseline." if stats else "unmeasured; no baseline.")
    learned = "unmeasured; no paired admission result." if library else "none in this version; unmeasured."
    return ["- Harness generations: " + generation,
            "- Repairs of G6's own refused calls: " + repair,
            "- Learned checks in use: " + learned,
            "- Routing: fixed rules, not adapted. Unmeasured: no routing session has been run."]


def render(events: Iterable[Event], *, recording: bool = True, self_lines: list[str] | None = None) -> str:
    """No filesystem, clock or model reads; identical events and options give identical bytes."""
    from g6.harness.patch_report import code

    rows = list(events)
    if not recording:
        return ("# G6 record\nNo record is kept. G6_WORKFLOW_RECORD=on where the server is registered turns it on.\n"
                "Earlier retained events may still exist; turning collection off does not delete them.\n")
    c = counts(rows)
    lines = ["# G6 record", "Local to this computer. Counts of what G6 observed; work outside G6 is not here.",
             "", "## Coverage", f"- {c['turns']} ended turns observed: {c['checked']} checked; {c['unchecked']} unchecked.",
            f"- {c['review_calls']} finished MCP review calls; {c['loops']} ended loops. Calls are not turns.",
             "- Over MCP without the hook, turns ending without a call are not counted.",
             "", "## What the checks found",
             f"- {c['reproduced']} reproduced findings (tests failed); {c['repaired']} repaired; "
             f"{c['open']} open at loop stops; {c['left_by_agent']} left by the agent."]
    if c["identities_incomplete"]:
        lines.append("- Check identities are incomplete; open counts are lower bounds. Omitted findings remain unresolved.")
    if not rows:
        lines.append("- No recorded events. Nothing here establishes that work was checked.")
    lines += ["", "## Findings (newest first)"]
    findings = [e for e in rows if e.kind in ("loop_ended", "review_finished") and e.data.get("reproduced")]
    for e in reversed(findings[-10:]):
        relevant = [row for row in rows if row.run == e.run] if e.kind == "loop_ended" else [
            row for row in rows if row.kind == "review_finished"]
        state = evidence.check_state(relevant)
        failed = state["reproduced"] if e.kind == "loop_ended" else {
            (e.task, check) for check in e.data.get("failed_sha256") or []}
        repaired = (bool(failed) and failed <= state["repaired"] and not failed & state["open"]
                    and not evidence.check_counts(relevant)["identities_incomplete"])
        labels = evidence.finding_labels(rows, [e.event_id, *[row.event_id for row in relevant
                                                          if e.kind == "loop_ended" and row.data.get("failed_sha256")]])
        state = "repaired in a recorded review" if repaired else "open at the stop"
        if labels:
            state += ". " + " ".join(f"Person label by {code(label['by'])}: {code(label['note'])} "
                                     f"(event {code(label['event_id'])})" for label in labels)
        elif not repaired:
            state += ". Not labelled by you"
        lines.append(f"- {code(e.at)}, task {code(e.task[:12])}: {state}; details no longer kept. "
                     f"Event {code(e.event_id)}.")
    if len(findings) > 10:
        lines.append(f"- {len(findings) - 10} more finding events in inspection.json.")
    lines += ["", "## Not checked, most often",
              f"- {c['not_established']} requirements not established across recorded reviews; {c['skipped']} runs skipped."]
    lines += [f"- {code(reason)}: {count} unchecked turns." for reason, count in sorted(c["unchecked_reasons"].items())[:6]]
    lines += ["", "## What G6 changed in itself", *(self_lines or self_changes(None, {}))[:4], "", "## Limits",
              "- A reproduced finding is a test that failed. It is not a ruling that the patch was wrong.",
              "- A clean turn means no reviewer showed a failure.", "- Value to your work: not measured."]
    lines += ["", "## Event references (full entries in inspection.json)"]
    lines += [f"- {code(e.event_id)} {code(e.kind)} SHA-256 {code(digest(e.to_body()))}." for e in rows[-12:]]
    if len(rows) > 12:
        lines.append(f"- {len(rows) - 12} earlier events in inspection.json.")
    return "\n".join(lines) + "\n"
