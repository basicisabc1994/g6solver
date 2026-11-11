# SPDX-License-Identifier: Apache-2.0
"""The routing manifest: the code definition of "marked for agents" (0.3 design, 14.1).

``using-g6/routing.json`` classifies every MCP tool G6 registers and every typed op a component lists. A member is
either agent-facing, with the id of a route in the root skill, or exempt, with a reason. There is no wildcard and no
default: a member with no entry is a problem, never "not for agents", and so is an entry with no member.

A route is a short instruction in the root skill (its ``anchor``: a heading, or the bold lead of a list item) with
linked detail files. A prose mention alone is not a route: a routed MCP tool has to be named in its route's own
section or in one of that route's detail files.

Keys are ``mcp:<server>/<tool>`` and ``op:<component>/<op>``, so one tool name on two servers is two members.
``since`` is the G6 version a member first ships in; ``release`` is the release the manifest is written for, so an
entry added before that release's version is set in the code can already say the version it will ship in.
A slice that adds a tool or an op adds its entry (and, for a new route, the route's line in the root skill and its
detail file) in the same commit; entries are one per line and sorted, so two such changes merge line by line.

``check`` is pure: it joins the manifest with what the caller recorded from the code
(``g6.mcp.registrations.inputs`` records it) and returns every problem it finds. Nothing here imports a component.
"""
from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

FORMAT = "g6.routing/1"
#: The root skill's key among the guidance texts: its path inside the ``g6`` package.
ROOT = "skills/using-g6/SKILL.md"
#: Where the root skill's reference files live, inside the ``g6`` package.
REFERENCES = "skills/using-g6/references/"
#: The root skill's word cap (``g6.core.skills.MAX_WORDS``; a test keeps the two equal).
MAX_ROOT_WORDS = 1800
#: The fewest characters a reason for an exemption has.
MIN_EXEMPTION = 20
#: The one line the root skill carries for a tool it names that the person's server does not list (an older server,
#: or a sensitive tool the host did not allow). Its absence from the root is a problem.
ABSENT_TOOL_RULE = ("A tool named here that your server does not list is absent: say so, use `g6_find`, and do not "
                    "call it again.")
CODES = ("MISSING", "UNKNOWN", "UNROUTED", "ANCHOR", "LINK", "NOT_NAMED", "STALE", "EXEMPT_NAMED", "SINCE", "WORDS",
         "SERVER", "ABSENT_RULE")

_KEY = re.compile(r"(mcp:[a-z0-9_-]+/[A-Za-z0-9_]+|op:[a-z0-9_]+/[a-z0-9_]+)\Z")
_ROUTE_ID = re.compile(r"[a-z][a-z0-9_]*\Z")
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_TOOL = re.compile(r"(?<![\w.])((?:g6|metaharness)_[a-z0-9_]+)(\*)?")
_PAIR = re.compile(r"`([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)`")
_INVOKE = re.compile(r'g6_invoke\(\s*"([a-z_][a-z0-9_]*)"\s*,\s*"([a-z_][a-z0-9_]*)"')
_BARE = re.compile(r"`([a-z][a-z0-9_]*)`")
_HEADING = re.compile(r"(#{1,6}) \S")
_ITEM = re.compile(r"\s*(?:[-*+]|\d+\.) ")


class ManifestError(ValueError):
    """The manifest file breaks its format: nothing is read from such a file."""


@dataclass(frozen=True)
class Route:
    id: str
    anchor: str                  # an exact heading line, or the bold lead ("**name.**") of a list item, in the root
    detail: tuple[str, ...]      # files inside the g6 package that carry the route's detail


@dataclass(frozen=True)
class Entry:
    key: str
    agent_facing: bool
    route: str                   # a route id when agent-facing, else ""
    exemption: str               # the reason when not agent-facing, else ""
    since: str                   # the G6 version the member first ships in


@dataclass(frozen=True)
class Manifest:
    format: str
    release: str                 # the release this manifest is written for; no entry is "since" a later one
    routes: Mapping[str, Route]
    entries: Mapping[str, Entry]


@dataclass(frozen=True)
class Problem:
    code: str
    key: str
    said: str


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ManifestError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _fields(what: str, value: Any, required: set[str], optional: set[str] = frozenset()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{what} is not an object")
    unknown, missing = set(value) - required - optional, required - set(value)
    if unknown or missing:
        raise ManifestError(f"{what}: unknown fields {sorted(unknown)}, missing fields {sorted(missing)}")
    return value


def _version(text: Any, what: str) -> tuple[int, int, int]:
    found = _VERSION.fullmatch(text) if isinstance(text, str) else None
    if found is None:
        raise ManifestError(f"{what} is not a version of the form 1.2.3: {text!r}")
    return int(found.group(1)), int(found.group(2)), int(found.group(3))


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ManifestError(f"{what} is not a trimmed, non-empty string: {value!r}")
    return value


def loads(text: str) -> Manifest:
    """The manifest in ``text``. Strict: an unknown or missing field, a duplicate key, another format, an entry with
    both a route and an exemption or with neither, a short exemption or a malformed key or version raises
    ``ManifestError``."""
    try:
        data = json.loads(text, object_pairs_hook=_pairs)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"not JSON: {exc}") from exc
    data = _fields("the manifest", data, {"format", "release", "routes", "entries"})
    if data["format"] != FORMAT:
        raise ManifestError(f"format is {data['format']!r}, not {FORMAT!r}")
    _version(data["release"], "release")
    if not isinstance(data["routes"], dict) or not isinstance(data["entries"], dict):
        raise ManifestError("routes and entries are objects")
    routes: dict[str, Route] = {}
    for route_id, raw in data["routes"].items():
        if not _ROUTE_ID.match(route_id):
            raise ManifestError(f"route id {route_id!r} is not a lower-case name")
        raw = _fields(f"route {route_id}", raw, {"anchor", "detail"})
        anchor = _text(raw["anchor"], f"route {route_id}: anchor")
        heading = bool(_HEADING.match(anchor)) and "\n" not in anchor
        lead = anchor.startswith("**") and anchor.endswith("**") and len(anchor) > 4 and "\n" not in anchor
        if not (heading or lead):
            raise ManifestError(f"route {route_id}: the anchor is a heading line or a bold lead, not {anchor!r}")
        if not isinstance(raw["detail"], list):
            raise ManifestError(f"route {route_id}: detail is a list of files")
        detail = tuple(_text(path, f"route {route_id}: a detail file") for path in raw["detail"])
        if len(set(detail)) != len(detail) or any(p.startswith(("/", ".")) or "\\" in p or ".." in p for p in detail):
            raise ManifestError(f"route {route_id}: detail files are distinct paths inside the g6 package")
        if ROOT in detail:
            raise ManifestError(f"route {route_id}: the root skill is not a route's detail file; a route's place "
                                "in the root skill is its anchor's own section")
        routes[route_id] = Route(route_id, anchor, detail)
    entries: dict[str, Entry] = {}
    for key, raw in data["entries"].items():
        if not _KEY.match(key):
            raise ManifestError(f"entry key {key!r} is not mcp:<server>/<tool> or op:<component>/<op>")
        if not isinstance(raw, dict) or not isinstance(raw.get("agent_facing"), bool):
            raise ManifestError(f"entry {key}: agent_facing is true or false")
        if raw["agent_facing"]:
            raw = _fields(f"entry {key}", raw, {"agent_facing", "route", "since"})
            route, exemption = _text(raw["route"], f"entry {key}: route"), ""
        else:
            raw = _fields(f"entry {key}", raw, {"agent_facing", "exemption", "since"})
            route, exemption = "", _text(raw["exemption"], f"entry {key}: exemption")
            if len(exemption) < MIN_EXEMPTION:
                raise ManifestError(f"entry {key}: an exemption gives its reason in at least {MIN_EXEMPTION} "
                                    "characters")
        _version(raw["since"], f"entry {key}: since")
        entries[key] = Entry(key, raw["agent_facing"], route, exemption, raw["since"])
    return Manifest(FORMAT, data["release"], MappingProxyType(routes), MappingProxyType(entries))


def manifest_path() -> Path:
    """Where the manifest ships: beside the root skill."""
    return Path(__file__).resolve().parent / "using-g6" / "routing.json"


def load(path: Path | None = None) -> Manifest:
    """The manifest G6 ships (or the one at ``path``), read strictly (``loads``)."""
    return loads((path or manifest_path()).read_text(encoding="utf-8"))


def dumps(manifest: Manifest) -> str:
    """The manifest as its file is written: routes and entries sorted by key, one per line, so two changes that each
    add an entry merge line by line."""
    def line(key: str, value: dict[str, Any]) -> str:
        return f"    {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)}"

    routes = [line(r.id, {"anchor": r.anchor, "detail": list(r.detail)}) for _, r in sorted(manifest.routes.items())]
    entries = []
    for key, entry in sorted(manifest.entries.items()):
        body: dict[str, Any] = {"agent_facing": entry.agent_facing}
        body["route" if entry.agent_facing else "exemption"] = entry.route if entry.agent_facing else entry.exemption
        body["since"] = entry.since
        entries.append(line(key, body))
    return ("{\n"
            f'  "format": {json.dumps(manifest.format)},\n'
            f'  "release": {json.dumps(manifest.release)},\n'
            '  "routes": {\n' + ",\n".join(routes) + "\n  },\n"
            '  "entries": {\n' + ",\n".join(entries) + "\n  }\n"
            "}\n")


def members(registrations: Mapping[str, Sequence[str]], ops: Mapping[str, Sequence[str]]) -> frozenset[str]:
    """Every member the code has: one key for each registered tool of each server and for each op of each component."""
    found = {f"mcp:{server}/{tool}" for server, tools in registrations.items() for tool in tools}
    found |= {f"op:{component}/{op}" for component, listed in ops.items() for op in listed}
    return frozenset(found)


def named(texts: Mapping[str, str]) -> dict[str, frozenset[str]]:
    """What each guidance text names, by surface: a ``g6_*`` or ``metaharness_*`` tool name wherever it stands, such a
    name ending ``_*`` as a prefix (kept with its star), and a ``component.op`` pair written in backticks or as the
    first two arguments of ``g6_invoke(...)``."""
    out: dict[str, frozenset[str]] = {}
    for surface, text in texts.items():
        names = {name + "*" if star or name.endswith("_") else name for name, star in _TOOL.findall(text)}
        names |= {f"{component}.{op}" for component, op in _PAIR.findall(text) + _INVOKE.findall(text)}
        out[surface] = frozenset(names)
    return out


def root_words(text: str) -> int:
    """The words in a skill's body, its front matter aside."""
    return len(text.split("---", 2)[2].split()) if text.startswith("---") and text.count("---") >= 2 else len(text.split())


def _lines(text: str) -> list[tuple[str, bool]]:
    """Each line with whether it stands inside a fenced block (where ``#`` is not a heading)."""
    out, fenced = [], False
    for line in text.splitlines():
        fence = line.lstrip().startswith("```")
        out.append((line, fenced or fence))
        fenced = fenced != fence
    return out


def section(root: str, anchor: str) -> str | None:
    """The part of the root skill a route's anchor heads: for a heading, down to the next heading of its level or
    above; for a bold lead, its own list item or paragraph. ``None`` when the anchor is absent or stands twice."""
    lines = _lines(root)
    heading = _HEADING.match(anchor)
    if heading:
        at = [i for i, (line, fenced) in enumerate(lines) if not fenced and line.rstrip() == anchor]
    else:
        at = [i for i, (line, fenced) in enumerate(lines)
              if not fenced and (line.lstrip().startswith(anchor) or _ITEM.sub("", line, count=1).startswith(anchor))]
    if len(at) != 1:
        return None
    start, end = at[0], len(lines)
    for i in range(start + 1, len(lines)):
        line, fenced = lines[i]
        if fenced:
            continue
        found = _HEADING.match(line)
        if heading and found and len(found.group(1)) <= len(heading.group(1)):
            end = i
            break
        if not heading and (found or not line.strip() or _ITEM.match(line)):
            end = i
            break
    return "\n".join(line for line, _ in lines[start:end])


def _names(tool: str, text: str) -> bool:
    return re.search(rf"(?<![\w.]){re.escape(tool)}(?!\w)", text) is not None


def _parts(version: str) -> tuple[int, int, int]:
    found = _VERSION.match(version)
    return (int(found.group(1)), int(found.group(2)), int(found.group(3))) if found else (0, 0, 0)


def check(manifest: Manifest, *, registrations: Mapping[str, Sequence[str]], ops: Mapping[str, Sequence[str]],
          texts: Mapping[str, str], root_words: int, version: str, files: Collection[str],
          details: Mapping[str, str] | None = None, sources: Collection[str] = (),
          servers: Mapping[str, str] | None = None) -> list[Problem]:
    """Every problem in the join of the manifest with the code, sorted; ``[]`` when each MCP tool and each typed op
    resolves to a route or a reasoned exemption and the guidance names nothing else.

    ``registrations``: server id to the tool names it registers. ``ops``: component to the ops it lists. ``texts``:
    guidance surface to its text, the root skill under ``ROOT``. ``root_words``: the root skill's body length.
    ``version``: the version the code reports. ``files``: the files that exist inside the g6 package. ``details``:
    the text of detail files that are not themselves guidance surfaces. ``sources``: the modules that construct an
    MCP server, and ``servers`` the known ones (server id to ``module:attribute``).

    Codes: ``MISSING`` a member with no entry; ``UNKNOWN`` an entry or a route with no member; ``UNROUTED`` a route id
    that is not a route; ``ANCHOR`` an anchor absent from the root skill, repeated in it or shared by two routes;
    ``LINK`` a detail file that does not exist; ``NOT_NAMED`` a routed MCP tool that its route's section and detail
    files never name; ``STALE`` guidance naming a tool or a ``component.op`` that is not a member; ``EXEMPT_NAMED``
    guidance naming an exempt member; ``SINCE`` an entry from a later version than the manifest's release and the
    code; ``WORDS`` a root skill over its cap; ``SERVER`` a server the recorder does not know, or a known one that
    was not recorded; ``ABSENT_RULE`` a root skill without the rule for a tool the person's server does not list.
    """
    problems: list[Problem] = []

    def add(code: str, key: str, said: str) -> None:
        problems.append(Problem(code, key, said))

    have = members(registrations, ops)
    for key in sorted(have - set(manifest.entries)):
        add("MISSING", key, "the code has this member and the manifest has no entry for it")
    for key in sorted(set(manifest.entries) - have):
        add("UNKNOWN", key, "the manifest has this entry and the code has no such member")
    used = {entry.route for entry in manifest.entries.values() if entry.agent_facing}
    for key, entry in sorted(manifest.entries.items()):
        if entry.agent_facing and entry.route not in manifest.routes:
            add("UNROUTED", key, f"its route {entry.route!r} is not a route of the manifest")
        if _parts(entry.since) > max(_parts(version), _parts(manifest.release)):
            add("SINCE", key, f"since {entry.since} is later than the code ({version}) and the manifest's release "
                              f"({manifest.release})")
    ceiling = (_parts(version)[0] + 1, 0, 0)
    if _parts(manifest.release) > ceiling:
        add("SINCE", "release", f"the manifest's release {manifest.release} is past the next release after the "
                                f"code ({version}): a manifest does not set its own ceiling")
    for route_id in sorted(set(manifest.routes) - used):
        add("UNKNOWN", f"route:{route_id}", "no agent-facing member takes this route")

    root = texts.get(ROOT)
    if root is None:
        add("ANCHOR", ROOT, "the root skill's text was not given")
    elif ABSENT_TOOL_RULE not in " ".join(root.split()):
        add("ABSENT_RULE", ROOT, f"the root skill does not carry the line: {ABSENT_TOOL_RULE}")
    if root_words > MAX_ROOT_WORDS:
        add("WORDS", ROOT, f"the root skill has {root_words} words; the cap is {MAX_ROOT_WORDS}")
    detail_text = {**(details or {}), **texts}
    sections: dict[str, str] = {}
    anchors: dict[str, str] = {}
    for route_id, route in sorted(manifest.routes.items()):
        if route.anchor in anchors:
            add("ANCHOR", f"route:{route_id}", f"it shares its anchor with route {anchors[route.anchor]!r}")
        anchors.setdefault(route.anchor, route_id)
        found = section(root, route.anchor) if root is not None else None
        if root is not None and found is None:
            add("ANCHOR", f"route:{route_id}", f"the root skill does not have {route.anchor!r} exactly once, as a "
                                              "heading or the bold lead of a list item")
        sections[route_id] = found or ""
        for other_id, other in sorted(manifest.routes.items()):
            if other_id != route_id and other.anchor != route.anchor and found is not None \
                    and section(found, other.anchor) is not None:
                add("ANCHOR", f"route:{route_id}", f"its section of the root skill holds route {other_id!r}'s "
                                                  "anchor: a route owns its own part, not another route's")
        for path in route.detail:
            if path not in files:
                add("LINK", f"route:{route_id}", f"its detail file {path} does not exist")
            else:
                sections[route_id] += "\n" + detail_text.get(path, "")
    for key, entry in sorted(manifest.entries.items()):
        if entry.agent_facing and key.startswith("mcp:") and entry.route in sections and key in have:
            tool = key.split("/", 1)[1]
            if not _names(tool, sections[entry.route]):
                add("NOT_NAMED", key, f"route {entry.route!r} names it neither in its section of the root skill nor "
                                      "in a detail file; a mention elsewhere is not a route")

    tools = {tool for listed in registrations.values() for tool in listed}
    exempt = {key: entry for key, entry in manifest.entries.items() if not entry.agent_facing}
    exempt_tools = {key.split("/", 1)[1] for key in exempt if key.startswith("mcp:")}
    exempt_ops = {key[3:].replace("/", ".", 1) for key in exempt if key.startswith("op:")}
    for surface, names in sorted(named(texts).items()):
        for name in sorted(names):
            if "." in name:
                component, op = name.split(".", 1)
                if ops.get(component) and op not in ops[component]:
                    add("STALE", surface, f"it names {name}, and {component} lists no op {op}")
                elif name in exempt_ops:
                    add("EXEMPT_NAMED", surface, f"it names {name}, which the manifest exempts from agent use")
            elif name.endswith("*"):
                if not any(tool.startswith(name[:-1]) for tool in tools):
                    add("STALE", surface, f"it names {name}, and no registered tool begins that way")
            elif name not in tools:
                add("STALE", surface, f"it names {name}, and no server registers a tool of that name")
        for tool in sorted(exempt_tools):
            if tool in names or f"`{tool}`" in texts[surface] or f"`{tool}(" in texts[surface]:
                add("EXEMPT_NAMED", surface, f"it names {tool}, which the manifest exempts from agent use")

    # A tool of a server whose tools carry no g6_ prefix has one written form: a bare name in backticks, in its
    # route's own detail file under the root skill's references. Every such name there is a registered tool of the
    # route's servers, or it is stale.
    for route_id, route in sorted(manifest.routes.items()):
        owners = {key[4:].split("/", 1)[0] for key, entry in manifest.entries.items()
                  if entry.route == route_id and key.startswith("mcp:")} - {"g6"}
        offered = {tool for server in owners for tool in registrations.get(server, ())}
        for path in route.detail if owners else ():
            if path.startswith(REFERENCES) and path in detail_text:
                for name in sorted(set(_BARE.findall(detail_text[path])) - offered):
                    add("STALE", path, f"it names {name}, and no server of route {route_id!r} registers a tool "
                                       "of that name")
    known = {target.split(":", 1)[0] for target in (servers or {}).values()}
    for module in sorted(set(sources) - known):
        add("SERVER", module, "this module constructs an MCP server the recorder does not know")
    for server in sorted(set(servers or {}) - set(registrations)):
        add("SERVER", server, "this known server was not recorded")
    return sorted(problems, key=lambda p: (CODES.index(p.code), p.key, p.said))
