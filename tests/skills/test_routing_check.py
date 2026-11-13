# SPDX-License-Identifier: Apache-2.0
"""The routing check on synthetic inputs, one test for each problem it reports (0.3 design, 14.1). The inventory test
(``test_routing_inventory.py``) runs the same check on what G6 ships."""
from __future__ import annotations

import json
from typing import Any

import pytest

from g6.skills import routing
from g6.skills.routing import ROOT

DETAIL = "skills/using-g6/references/alpha.md"
ROOT_TEXT = f"""---
name: using-g6
---

# Using G6

{routing.ABSENT_TOOL_RULE}

## Find

Call `g6_find` with the task.

```text
## not a heading: it stands inside a fenced block
```

## Routes

- **alpha.** For alpha work, see the detail file.
- **beta.** Call `g6_beta` when beta work comes up.
"""


def base() -> dict[str, Any]:
    return {
        "format": routing.FORMAT, "release": "0.3.0",
        "routes": {"discover": {"anchor": "## Find", "detail": []},
                   "alpha": {"anchor": "**alpha.**", "detail": [DETAIL]},
                   "beta": {"anchor": "**beta.**", "detail": []}},
        "entries": {"mcp:g6/g6_find": {"agent_facing": True, "route": "discover", "since": "0.1.0"},
                    "mcp:g6/g6_alpha": {"agent_facing": True, "route": "alpha", "since": "0.2.0"},
                    "mcp:g6/g6_beta": {"agent_facing": True, "route": "beta", "since": "0.3.0"},
                    "mcp:g6/g6_inner": {"agent_facing": False, "since": "0.2.0",
                                        "exemption": "called by the server itself, never by an agent"},
                    "op:datalog/query": {"agent_facing": True, "route": "discover", "since": "0.1.0"}},
    }


def run(data: dict[str, Any] | None = None, **changed: Any) -> list[routing.Problem]:
    given: dict[str, Any] = {
        "registrations": {"g6": ("g6_alpha", "g6_beta", "g6_find", "g6_inner")}, "ops": {"datalog": ("query",)},
        "texts": {ROOT: ROOT_TEXT, DETAIL: "Call `g6_alpha` first, then `datalog.query`."},
        "root_words": 60, "version": "0.2.0", "files": {DETAIL},
    }
    given.update(changed)
    return routing.check(routing.loads(json.dumps(data or base())), **given)


def codes(problems: list[routing.Problem]) -> list[tuple[str, str]]:
    return [(problem.code, problem.key) for problem in problems]


def test_a_complete_manifest_has_no_problem() -> None:
    assert run() == []


def test_a_registered_tool_with_no_entry_is_missing() -> None:
    found = run(registrations={"g6": ("g6_alpha", "g6_beta", "g6_find", "g6_inner", "g6_added_later")})
    assert codes(found) == [("MISSING", "mcp:g6/g6_added_later")]


def test_an_op_added_to_a_component_is_missing_by_its_key() -> None:
    assert codes(run(ops={"datalog": ("query", "explain")})) == [("MISSING", "op:datalog/explain")]
    assert codes(run(ops={"datalog": ("query",), "new_block": ("run",)})) == [("MISSING", "op:new_block/run")]


def test_an_entry_for_a_removed_tool_is_unknown() -> None:
    found = run(registrations={"g6": ("g6_alpha", "g6_find", "g6_inner")},
                texts={ROOT: ROOT_TEXT.replace("Call `g6_beta` when", "Act when"), DETAIL: "Call `g6_alpha`."})
    assert codes(found) == [("UNKNOWN", "mcp:g6/g6_beta")]


def test_a_route_no_member_takes_is_unknown() -> None:
    data = base()
    data["routes"]["spare"] = {"anchor": "## Find more", "detail": []}
    found = run(data, texts={ROOT: ROOT_TEXT + "\n## Find more\n\nNothing yet.\n", DETAIL: "Call `g6_alpha`."})
    assert codes(found) == [("UNKNOWN", "route:spare")]


def test_a_route_id_that_is_not_a_route_is_unrouted() -> None:
    data = base()
    data["entries"]["mcp:g6/g6_beta"]["route"] = "gamma"
    assert ("UNROUTED", "mcp:g6/g6_beta") in codes(run(data))


def test_an_anchor_absent_from_the_root_or_repeated_in_it_is_a_problem() -> None:
    data = base()
    data["routes"]["beta"]["anchor"] = "**beta, renamed.**"
    assert ("ANCHOR", "route:beta") in codes(run(data))
    twice = ROOT_TEXT + "\n## Find\n\nA second section of the same name.\n"
    assert ("ANCHOR", "route:discover") in codes(run(texts={ROOT: twice, DETAIL: "Call `g6_alpha`."}))


def test_a_heading_inside_a_fenced_block_is_not_an_anchor() -> None:
    data = base()
    data["routes"]["discover"]["anchor"] = "## not a heading: it stands inside a fenced block"
    assert ("ANCHOR", "route:discover") in codes(run(data))


def test_two_routes_cannot_share_an_anchor() -> None:
    data = base()
    data["routes"]["beta"]["anchor"] = "**alpha.**"
    assert ("ANCHOR", "route:beta") in codes(run(data))


def test_a_broken_detail_link_is_a_problem() -> None:
    found = run(files=set())
    assert ("LINK", "route:alpha") in codes(found)


def test_a_prose_mention_alone_is_not_a_route() -> None:
    """``g6_alpha`` is named in the root skill, but under another route's heading: its own route never names it."""
    moved = ROOT_TEXT.replace("Call `g6_find` with the task.", "Call `g6_find` with the task, or `g6_alpha`.")
    found = run(texts={ROOT: moved, DETAIL: "Nothing about the tool."})
    assert codes(found) == [("NOT_NAMED", "mcp:g6/g6_alpha")]


def test_a_longer_tool_name_does_not_name_a_shorter_one() -> None:
    found = run(texts={ROOT: ROOT_TEXT, DETAIL: "Call `g6_alpha_two`."},
                registrations={"g6": ("g6_alpha", "g6_alpha_two", "g6_beta", "g6_find", "g6_inner")})
    assert ("NOT_NAMED", "mcp:g6/g6_alpha") in codes(found)


def test_guidance_naming_a_tool_that_does_not_exist_is_stale() -> None:
    found = run(texts={ROOT: ROOT_TEXT, DETAIL: "Call `g6_alpha`, then g6_does_not_exist.",
                       "help:g6_help": "metaharness_never_shipped starts it; all names begin metaharness_."})
    assert codes(found) == [("STALE", "help:g6_help"), ("STALE", DETAIL)]
    assert "g6_does_not_exist" in found[1].said and "metaharness_never_shipped" in found[0].said


def test_a_prefix_no_tool_begins_with_is_stale_and_a_real_prefix_is_not() -> None:
    assert run(texts={ROOT: ROOT_TEXT, DETAIL: "Call `g6_alpha`; the `g6_al*` tools."}) == []
    found = run(texts={ROOT: ROOT_TEXT, DETAIL: "Call `g6_alpha`; the `g6_mission_*` tools."})
    assert codes(found) == [("STALE", DETAIL)] and "g6_mission_*" in found[0].said


def test_an_op_its_component_does_not_list_is_stale() -> None:
    found = run(texts={ROOT: ROOT_TEXT, DETAIL: 'Call `g6_alpha`, then `datalog.prove` or g6_invoke("datalog", "run")'})
    # three stale names: the two ops, and g6_invoke itself, which this synthetic server does not register
    assert codes(found) == [("STALE", DETAIL)] * 3
    said = " ".join(problem.said for problem in found)
    assert "datalog.prove" in said and "datalog.run" in said and "g6_invoke" in said
    assert run(texts={ROOT: ROOT_TEXT, DETAIL: "Call `g6_alpha`; `caller.field` and `patch_review.review`."}) == []


def test_guidance_naming_an_exempt_member_is_a_problem() -> None:
    found = run(texts={ROOT: ROOT_TEXT, DETAIL: "Call `g6_alpha`, then g6_inner."})
    assert codes(found) == [("EXEMPT_NAMED", DETAIL)]
    data = base()
    data["entries"]["op:datalog/query"] = {"agent_facing": False, "since": "0.1.0",
                                           "exemption": "kept for the engine's own use only"}
    assert ("EXEMPT_NAMED", DETAIL) in codes(run(data))


def test_an_exempt_tool_of_a_second_server_is_found_by_its_plain_name_in_backticks() -> None:
    data = base()
    data["entries"]["mcp:second/diagnose"] = {"agent_facing": False, "since": "0.1.0",
                                              "exemption": "the second server is not registered by default"}
    registrations = {"g6": ("g6_alpha", "g6_beta", "g6_find", "g6_inner"), "second": ("diagnose",)}
    assert run(data, registrations=registrations,
               texts={ROOT: ROOT_TEXT, DETAIL: "Call `g6_alpha`, then diagnose the failure."}) == []
    found = run(data, registrations=registrations, texts={ROOT: ROOT_TEXT, DETAIL: "Call `g6_alpha`, then `diagnose`."})
    assert codes(found) == [("EXEMPT_NAMED", DETAIL)]


def test_since_above_the_version_and_the_release_is_a_problem() -> None:
    data = base()
    data["entries"]["mcp:g6/g6_beta"]["since"] = "0.4.0"
    assert codes(run(data)) == [("SINCE", "mcp:g6/g6_beta")]
    assert run(data, version="0.4.0") == []


def test_the_root_over_its_cap_is_a_problem_and_a_long_detail_file_is_not() -> None:
    assert codes(run(root_words=routing.MAX_ROOT_WORDS + 1)) == [("WORDS", ROOT)]
    long_detail = "Call `g6_alpha`. " + "word " * 5000
    assert run(root_words=routing.MAX_ROOT_WORDS, texts={ROOT: ROOT_TEXT, DETAIL: long_detail}) == []
    assert routing.root_words(ROOT_TEXT) == len(ROOT_TEXT.split("---", 2)[2].split())


def test_a_server_the_recorder_does_not_know_is_a_problem() -> None:
    servers = {"g6": "g6.mcp:build_server"}
    assert run(sources={"g6.mcp"}, servers=servers) == []
    assert codes(run(sources={"g6.mcp", "g6.extra.server"}, servers=servers)) == [("SERVER", "g6.extra.server")]
    assert codes(run(sources={"g6.mcp"}, servers={**servers, "second": "g6.second:mcp"})) == [("SERVER", "second")]


def test_the_root_skill_says_what_to_do_when_a_named_tool_is_absent() -> None:
    without = ROOT_TEXT.replace(routing.ABSENT_TOOL_RULE, "Tools may be missing.")
    found = run(texts={ROOT: without, DETAIL: "Call `g6_alpha`."})
    assert codes(found) == [("ABSENT_RULE", ROOT)]
    wrapped = ROOT_TEXT.replace(routing.ABSENT_TOOL_RULE, routing.ABSENT_TOOL_RULE.replace(" absent: ", " absent:\n"))
    assert run(texts={ROOT: wrapped, DETAIL: "Call `g6_alpha`."}) == []
    for word in ("say so", "`g6_find`", "do not call it again"):
        assert word in routing.ABSENT_TOOL_RULE


def test_the_same_tool_name_on_two_servers_is_two_members() -> None:
    registrations = {"g6": ("g6_alpha", "g6_beta", "g6_find", "g6_inner"), "second": ("g6_find",)}
    assert routing.members(registrations, {}) >= {"mcp:g6/g6_find", "mcp:second/g6_find"}
    assert codes(run(registrations=registrations)) == [("MISSING", "mcp:second/g6_find")]
    data = base()
    data["entries"]["mcp:second/g6_find"] = {"agent_facing": True, "route": "discover", "since": "0.3.0"}
    assert run(data, registrations=registrations) == []
    assert codes(run(data)) == [("UNKNOWN", "mcp:second/g6_find")]


def test_a_section_is_the_anchors_own_part_of_the_root() -> None:
    find = routing.section(ROOT_TEXT, "## Find")
    assert find is not None and "g6_find" in find and "inside a fenced block" in find and "alpha" not in find
    alpha = routing.section(ROOT_TEXT, "**alpha.**")
    assert alpha is not None and "alpha work" in alpha and "g6_beta" not in alpha
    assert routing.section(ROOT_TEXT, "## Absent") is None


@pytest.mark.parametrize("change", [
    lambda d: d.update(extra=1),
    lambda d: d.update(format="g6.routing/2"),
    lambda d: d.pop("release"),
    lambda d: d["entries"]["mcp:g6/g6_beta"].update(exemption="it has a route and a reason as well"),
    lambda d: d["entries"]["mcp:g6/g6_beta"].pop("route"),
    lambda d: d["entries"]["mcp:g6/g6_inner"].update(exemption="short"),
    lambda d: d["entries"]["mcp:g6/g6_inner"].update(default=True),
    lambda d: d["entries"]["mcp:g6/g6_beta"].update(agent_facing="yes"),
    lambda d: d["entries"]["mcp:g6/g6_beta"].update(since="soon"),
    lambda d: d["entries"].update({"mcp:g6/g6_*": {"agent_facing": True, "route": "beta", "since": "0.3.0"}}),
    lambda d: d["entries"].update({"g6_beta": {"agent_facing": True, "route": "beta", "since": "0.3.0"}}),
    lambda d: d["routes"]["beta"].update(anchor="beta"),
    lambda d: d["routes"]["beta"].update(detail=["../outside.md"]),
    lambda d: d["routes"]["beta"].update(pattern="g6_.*"),
])
def test_a_manifest_outside_its_format_is_refused(change: Any) -> None:
    data = base()
    change(data)
    with pytest.raises(routing.ManifestError):
        routing.loads(json.dumps(data))


def test_a_duplicate_key_is_refused() -> None:
    text = json.dumps(base())
    entry = '"mcp:g6/g6_find": {"agent_facing": true, "route": "discover", "since": "0.1.0"}'
    assert entry in text
    with pytest.raises(routing.ManifestError, match="duplicate"):
        routing.loads(text.replace(entry, entry + ", " + entry))


def test_the_written_form_is_sorted_one_entry_a_line_and_reads_back() -> None:
    manifest = routing.loads(json.dumps(base()))
    written = routing.dumps(manifest)
    assert routing.loads(written) == manifest
    keys = [line.split('"')[1] for line in written.splitlines() if line.startswith('    "mcp:') or
            line.startswith('    "op:')]
    assert keys == sorted(base()["entries"])


def test_every_manifest_field_is_bounded_by_something_the_manifest_does_not_hold() -> None:
    """One inventory for the class "the manifest decides what it is checked against": for each field of the manifest's
    three records (read from the dataclasses), a manifest that sets the field to suit itself is refused by the loader or
    reported by the check. A field added later without its case fails here."""
    import dataclasses

    def release(d: dict) -> None:
        d["release"] = "9.9.9"

    def anchor(d: dict) -> None:
        d["routes"]["beta"]["anchor"] = "# Using G6"                  # the top heading: every mention would be its own

    def detail(d: dict) -> None:
        d["routes"]["beta"]["detail"] = [ROOT]                        # the root skill as the route's own detail file

    def key(d: dict) -> None:
        d["entries"]["mcp:g6/g6_not_registered"] = {"agent_facing": True, "route": "beta", "since": "0.3.0"}

    def facing(d: dict) -> None:
        d["entries"]["mcp:g6/g6_beta"] = {"agent_facing": False, "since": "0.3.0",
                                          "exemption": "declared not for agents, yet the root tells them to call it"}

    def route(d: dict) -> None:
        d["entries"]["mcp:g6/g6_beta"]["route"] = "discover"          # named in beta's section, not in discover's

    def since(d: dict) -> None:
        d["entries"]["mcp:g6/g6_beta"]["since"] = "0.9.0"

    cases = {("Manifest", "format"): (lambda d: d.update(format="g6.routing/9"), "ManifestError"),
             ("Manifest", "release"): (release, "SINCE"), ("Manifest", "routes"): (anchor, "ANCHOR"),
             ("Manifest", "entries"): (key, "UNKNOWN"), ("Route", "id"): (route, "NOT_NAMED"),
             ("Route", "anchor"): (anchor, "ANCHOR"), ("Route", "detail"): (detail, "ManifestError"),
             ("Entry", "key"): (key, "UNKNOWN"), ("Entry", "agent_facing"): (facing, "EXEMPT_NAMED"),
             ("Entry", "route"): (route, "NOT_NAMED"), ("Entry", "exemption"): (facing, "EXEMPT_NAMED"),
             ("Entry", "since"): (since, "SINCE")}
    fields = {(cls.__name__, field.name) for cls in (routing.Manifest, routing.Route, routing.Entry)
              for field in dataclasses.fields(cls)}
    assert fields == set(cases), fields ^ set(cases)
    for (record, name), (change, expected) in cases.items():
        data = base()
        change(data)
        try:
            found = {problem.code for problem in run(data)}
        except routing.ManifestError:
            found = {"ManifestError"}
        assert expected in found, (record, name, found)
