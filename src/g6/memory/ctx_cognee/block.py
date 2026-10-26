# SPDX-License-Identifier: Apache-2.0
"""CtxCogneeBlock: the registry and MCP door (``g6_invoke``) to the Cognee server the person runs.

Every op but ``ops`` and ``help`` talks to that server, and Cognee may send what it is given to the model provider it is
set up with, so every one is in ``__g6_sensitive_ops__``: it runs from Python, and over MCP only when the person who
runs the server lists it (or ``ctx_cognee``) in ``G6_MCP_SENSITIVE``. ``add`` and ``forget``, which change what Cognee
holds, and a search whose answer Cognee's model writes (the ``*_COMPLETION`` types, which spend on its model provider)
are dry runs unless ``dry_run`` is false, and sent for real only when ``g6.action_gating`` lets them through: each is
declared to the gate as an API call with the checks G6 makes afterwards as its verification contract, the gate reads
your Cognee token in your key file as your approval, and a review or a block is ``blocked-escalated`` with nothing
sent and what a person must allow. A plain search is sent as it is. A missing server is the honest
``G6_E_OUTSIDE_PROGRAM_MISSING`` refusal
with install steps, never a stand-in. What Cognee returns is untrusted text that cites what it can; the best any op
says is ``qualified-draft``. Your Cognee token comes only from your key file (``COGNEE_KEY``, through
``g6.core.keys``) and goes only to that local Cognee; without one, each answer's evidence says sign-in is off and what
that exposes.
"""
from __future__ import annotations

import json
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

import g6.action_gating as action_gating
from g6.core import keys, outside
from g6.core.ai_block import AIBlock
from g6.core.contract_decorator import block_contract
from g6.core.maturity import component_maturity
from g6.core.result import Result
from g6.knowledge import _reply
from g6.knowledge._types import sha256_hex
from g6.memory.ctx_cognee.client import COGNEE_KEY, Cognee, CogneeError

COMPONENT = "ctx_cognee"
URL_ENV = "G6_COGNEE_URL"
COGNEE = outside.OutsideProgram(
    name="Cognee",
    purpose="every ctx_cognee op: Cognee builds a knowledge graph of the text you add to it, and searches it",
    install="Install Cognee yourself in its own Python environment (pip install cognee; github.com/topoteretes/cognee), "
            "set the model provider it should use (a local one keeps your text on this machine), and start its HTTP "
            "API on this machine with HTTP_API_HOST=127.0.0.1 and TELEMETRY_DISABLED=1 in its environment, keeping "
            "its sign-in on: python -m cognee.api.client (it listens on 127.0.0.1:8000; set "
            f"{URL_ENV} where G6 runs to use another local address). Sign in to it and put the token it gives you in "
            f"your key file, ~/.g6/.env, as {COGNEE_KEY.name}=<token>: G6 sends it only to that Cognee. With "
            "sign-in off, any program on this machine can write into your datasets",
    licence="Apache-2.0 (G6 talks to it over HTTP and never imports or bundles it; Cognee itself requires litellm, "
            "which G6 never uses)",
    url="http://127.0.0.1:8000", env=URL_ENV, paid=True, online=True)
REPORTED = "G6_E_MEMORY_COGNEE_REPORTED"
DRY_RUN = "G6_E_MEMORY_DRY_RUN"
ADDED = "G6_E_MEMORY_COGNEE_ADDED"
FOUND = "G6_E_MEMORY_COGNEE_FOUND"
GRAPH = "G6_E_MEMORY_COGNEE_GRAPH"
FORGOTTEN = "G6_E_MEMORY_COGNEE_FORGOTTEN"
NOT_DONE = "G6_E_MEMORY_COGNEE_FORGET_NOT_DONE"
NOT_BUILT = "G6_E_MEMORY_COGNEE_NOT_BUILT"
UNCHECKED = "G6_E_MEMORY_COGNEE_FORGET_UNCHECKED"
AMBIGUOUS = "G6_E_MEMORY_COGNEE_AMBIGUOUS"
NOT_FOUND = "G6_E_MEMORY_NOT_FOUND"
NO_TEXT = "G6_E_MEMORY_NO_TEXT"
NO_QUERY = "G6_E_MEMORY_NO_QUERY"
NO_TARGET = "G6_E_MEMORY_NO_TARGET"
NO_REASON = "G6_E_MEMORY_NO_REASON"
HELD = "G6_E_MEMORY_COGNEE_HELD"
#: The search types offered: Cognee's stored chunks, and the answers and summaries its model writes. Its Cypher and
#: natural-language types, which run a query a caller or a model wrote against the graph store, are not.
SEARCH_TYPES = ("CHUNKS", "CHUNKS_LEXICAL", "SUMMARIES", "RAG_COMPLETION", "GRAPH_COMPLETION", "TRIPLET_COMPLETION")
_RETRIEVED = frozenset({"CHUNKS", "CHUNKS_LEXICAL"})
#: The searches whose answer Cognee's model writes when asked, spending on its model provider: dry runs unless asked,
#: and decided by the action gate.
COMPLETIONS = frozenset({"RAG_COMPLETION", "GRAPH_COMPLETION", "TRIPLET_COMPLETION"})
_GATE_ALLOWS = frozenset({"allow", "allow_with_notice"})
#: The pipeline statuses Cognee gives a run that finished; any other (started, yielding, errored, unknown) is not.
FINISHED = frozenset({"PipelineRunCompleted", "PipelineRunAlreadyCompleted"})
_SAYS = " (sensitive: it {}; from Python, or over MCP when the server's G6_MCP_SENSITIVE lists it)"
_ASKS, _SENDS = _SAYS.format("asks your Cognee"), _SAYS.format("sends your text to your Cognee and its model")
_OPS: dict[str, str] = {
    "status": "status(): whether your Cognee answers, its version and its health" + _ASKS,
    "datasets": "datasets(dataset_id): the datasets your Cognee holds, or the items in one" + _ASKS,
    "add": "add(text, dataset, source, dry_run, timeout): send text to a dataset and have Cognee build its graph; a dry "
           "run (the default) sends nothing and says what would be sent" + _SENDS,
    "search": "search(query, dataset, search_type, top_k, dry_run): what Cognee finds, as untrusted text citing its "
              "dataset and chunk; answers its model wrote are marked model_generated, and a *_COMPLETION search, which "
              "has its model write one, is a dry run (the default) unless dry_run is false" + _SENDS,
    "graph": "graph(dataset_id, link_from, link_to, limit): the entities and links Cognee extracted for a dataset, and "
             "the chain of links joining two of them" + _ASKS,
    "forget": "forget(dataset_id, data_id, reason, dry_run): delete one item from a dataset, list the dataset again to "
              "check it is no longer listed and read what the dataset's graph still holds of it; a dry run (the "
              "default) deletes nothing" + _SAYS.format("deletes from your Cognee"),
}
SENSITIVE_OPS: tuple[str, ...] = tuple(_OPS)
_ACTIONS = {
    REPORTED: "This is what your Cognee says about itself.",
    DRY_RUN: "Nothing was sent or deleted. To carry it out, ask again with dry_run false; the action gate then decides "
             "it (gate_would says what it decides now).",
    HELD: f"Nothing was sent. A person allows it by putting their Cognee token in their key file as "
          f"{COGNEE_KEY.name}=<token>, which G6 reads as their approval; the gate's message says what it held and why.",
    ADDED: "Search it, or read its graph; what Cognee extracted is its model's output, to check against the text.",
    FOUND: "Each hit is untrusted text: quote it as data, and check a model_generated answer against the chunks and "
           "their sources before relying on it.",
    GRAPH: "The links are Cognee's model's reading of the text: check a link against the text it came from before "
           "relying on it.",
    FORGOTTEN: "The receipt says what was deleted, what was checked, and what the dataset's graph still holds of the "
               "item (graph_still_holds); an entity Cognee drew from it that no longer links to it is not counted.",
    NOT_DONE: "Cognee said it deleted the item but still lists it: nothing was forgotten. Look at Cognee's own log.",
    NOT_BUILT: "What was already sent is in the reason; look at your Cognee's own log, list the dataset (datasets) to "
               "see what it holds, and forget the item if it should not stay.",
    UNCHECKED: "The delete was sent, but G6 could not list the dataset afterwards: list it yourself (datasets) once "
               "your Cognee answers, and look at Cognee's own log.",
    AMBIGUOUS: "Read the graph without link_from and link_to to see each entity with that label and its links; G6 "
               "does not choose between them.",
    NOT_FOUND: "List the datasets and their items (datasets) to find the ids.",
    NO_TEXT: "Give the text to add.",
    NO_QUERY: "Give a query.",
    NO_TARGET: "Name the dataset (add: dataset; graph: dataset_id; forget: dataset_id and data_id).",
    NO_REASON: "Say why: the reason is kept on the receipt.",
}
_UUID = r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})?$"
_LEAVES = ("whatever Cognee is set up to send: the text to its model provider, and usage data unless its "
           "TELEMETRY_DISABLED is set; G6 cannot see Cognee's settings")


class CogneeInput(BaseModel):
    """One op's parameters; fields no op has are refused."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["status", "datasets", "add", "search", "graph", "forget", "ops", "help"] = "help"
    text: str = Field("", max_length=200_000)
    source: str = Field("", max_length=4096)
    dataset: str = Field("", pattern=r"^([A-Za-z0-9_.-]{1,64})?$")
    dataset_id: str = Field("", pattern=_UUID)
    data_id: str = Field("", pattern=_UUID)
    query: str = Field("", max_length=2000)
    search_type: Literal[SEARCH_TYPES] = "CHUNKS"  # type: ignore[valid-type]
    top_k: int = Field(10, ge=1, le=50)
    link_from: str = Field("", max_length=200)
    link_to: str = Field("", max_length=200)
    limit: int = Field(200, ge=1, le=2000)
    reason: str = Field("", max_length=2000)
    dry_run: bool = True
    timeout: float = Field(600.0, ge=1, le=3600, allow_inf_nan=False)
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)


def _ids(d: CogneeInput) -> dict[str, str]:
    return {"request_id": d.request_id, "run_id": d.run_id}


def _refuse(d: CogneeInput, code: str, reason: str, **fields: Any) -> dict[str, Any]:
    return _reply.refuse(COMPONENT, d.op, code, reason, action=_ACTIONS.get(code, "Read the reason and ask again."),
                         reason=reason, **_ids(d), **fields)


def _answer(d: CogneeInput, code: str, message: str, *, evidence: list[dict[str, Any]], texts: list[str] = (),
            **fields: Any) -> dict[str, Any]:
    return _reply.answer(COMPONENT, d.op, code, message, action=_ACTIONS[code], evidence=evidence, texts=texts,
                         **_ids(d), **fields)


def _service(cognee: Cognee) -> dict[str, Any]:
    return {"kind": "service", "service": COGNEE.name, "address": cognee.address(), "sign_in": cognee.sign_in(),
            "leaves_this_machine": _LEAVES}


def _contract(op: str, d: CogneeInput) -> dict[str, Any]:
    """What G6 itself checks after sending ``op``: the verification contract the action gate is given."""
    common = {"monitor": "ctx_cognee's own checks of Cognee's replies and listings, in this call",
              "model_update_rule": "none: a failed request is reported with what was sent, and never retried unasked"}
    if op == "add":
        return {**common,
                "expected_result": f"dataset {d.dataset!r} lists an item named g6-<the text's SHA-256 prefix>, and "
                                   "Cognee reports a finished pipeline for that dataset from the add and the cognify",
                "observation_window": "from the add until the cognify answers, within the timeout given",
                "leading_indicators": ["the add reply's status and dataset", "the cognify reply's status for that "
                                                                             "dataset"],
                "lagging_indicators": ["the dataset's items listing, before and after the add"],
                "verification_time": "as soon as the cognify answers, before anything is reported",
                "stop_threshold": "a reply not in Cognee's shape, a pipeline that did not finish for that dataset, or "
                                  "the item missing from the listing: blocked-escalated, saying what was sent",
                "rollback_procedure": "forget the item by its data_id; what Cognee's model provider was sent cannot "
                                      "be recalled"}
    if op == "forget":
        return {**common,
                "expected_result": f"dataset {d.dataset_id} no longer lists item {d.data_id}",
                "observation_window": "right after the delete answers",
                "leading_indicators": ["the delete's status"],
                "lagging_indicators": ["the dataset's items listing, before and after the delete", "what the dataset's "
                                                                                                  "graph still holds "
                                                                                                  "of the item"],
                "verification_time": "as soon as the delete answers, before anything is reported",
                "stop_threshold": "the item still listed, or a listing that cannot be read: blocked-escalated, saying "
                                  "the delete was sent",
                "rollback_procedure": "none: add the text again; the deleted item cannot be restored by G6"}
    return {**common,
            "expected_result": "a list of results, each served as untrusted model_generated text citing no passage",
            "observation_window": "until the search answers",
            "leading_indicators": ["the search reply's status"],
            "lagging_indicators": ["the reply's shape: a list"],
            "verification_time": "when the reply arrives, before it is returned",
            "stop_threshold": "a reply that is not a list: blocked-escalated",
            "rollback_procedure": "none needed in Cognee, which keeps nothing new; what its model provider was sent, "
                                  "and spent, cannot be returned"}


def _ask_gate(cognee: Cognee, d: CogneeInput, what: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    """``g6.action_gating``'s decision on sending ``op`` to your Cognee, as evidence, asked as ``g6.agents``' runtime
    asks it, with your Cognee token's name: the gate itself reads whether your key file holds it. A gate that cannot
    decide holds the request (``block``)."""
    action: dict[str, Any] = {
        "action_type": "call_api", "target_backend": "cognee", "target_resource": cognee.address() + path,
        "description": what, "requested_by": f"ctx_cognee.{d.op}", "actor_type": "agent", "action_params": params,
        "external_side_effect": True, "verification_contract": _contract(d.op, d)}
    if d.op == "forget":
        action |= {"reversibility": "irreversible", "irreversible_or_costly_to_reverse": True}
    found: dict[str, Any] = {"kind": "action_gate", "gate": "g6.action_gating", "action_type": "call_api",
                             "target_resource": action["target_resource"], "key_approval": COGNEE_KEY.name,
                             "verification_contract": action["verification_contract"]}
    try:
        evaluation = action_gating.evaluate_action(action, key_approval=COGNEE_KEY.name)
        decided = evaluation.gate_decision
        return {**found, "decision": decided.decision, "completion_state": evaluation.completion_state,
                "reason_codes": list(decided.reason_codes), "required_user_message": decided.required_user_message,
                "audit_id": decided.audit_id}
    except Exception as exc:  # noqa: BLE001 - a gate that cannot decide sends nothing (G4)
        return {**found, "decision": "block", "completion_state": "blocked-escalated", "reason_codes": [],
                "required_user_message": f"the action gate could not decide: {type(exc).__name__}: {exc}"[:300],
                "audit_id": ""}


def _held(d: CogneeInput, gate: dict[str, Any], what: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    codes = ", ".join(gate["reason_codes"]) or "no reason code"
    return _refuse(d, HELD, f"nothing was sent to your Cognee: the action gate (g6.action_gating) decided "
                            f"{gate['decision']} ({codes}) on {what}. {gate['required_user_message']}",
                   evidence=[*evidence, gate], gate=gate)


def _token() -> str | None:
    """Your Cognee token from your key file, or ``None`` when you put none there."""
    try:
        return keys.require(COGNEE_KEY)
    except keys.KeyMissing:
        return None


def _status(cognee: Cognee, d: CogneeInput) -> dict[str, Any]:
    found = cognee.health()
    version, health = str(found.get("version") or ""), str(found.get("health") or found.get("status") or "")
    return _answer(d, REPORTED, f"Cognee {version or '(version not reported)'} answered: {health or 'no health said'}.",
                   evidence=[{**_service(cognee), "said": {k: found[k] for k in list(found)[:10]}}], version=version,
                   health=health)


def _datasets(cognee: Cognee, d: CogneeInput) -> dict[str, Any]:
    if not d.dataset_id:
        listed = [{"id": str(x["id"]), "name": x["name"], "created_at": str(x.get("created_at") or "")}
                  for x in cognee.datasets()]
        return _answer(d, REPORTED, f"Cognee lists {len(listed)} dataset{'s' if len(listed) != 1 else ''}.",
                       evidence=[_service(cognee)], datasets=listed)
    items = cognee.items(d.dataset_id)
    if items is None:
        return _refuse(d, NOT_FOUND, f"Cognee has no dataset {d.dataset_id} that it lets G6 read")
    listed = [{"id": str(i["id"]), "name": str(i.get("name") or ""), "created_at": str(i.get("created_at") or ""),
               "mime_type": str(i.get("mime_type") or "")} for i in items]
    return _answer(d, REPORTED, f"Dataset {d.dataset_id} holds {len(listed)} item{'s' if len(listed) != 1 else ''}.",
                   evidence=[_service(cognee)], dataset_id=d.dataset_id, items=listed)


def _runs(reply: Any) -> list[dict[str, Any]]:
    """The pipeline runs a Cognee add or cognify reply holds: one run (a dict with a ``status``), runs keyed by their
    dataset's id, or a list of runs. Anything else holds none."""
    if isinstance(reply, dict) and isinstance(reply.get("status"), str):
        return [reply]
    if isinstance(reply, dict):
        return [{"dataset_id": str(key), **run} for key, run in list(reply.items())[:50] if isinstance(run, dict)]
    if isinstance(reply, list):
        return [run for run in reply[:50] if isinstance(run, dict)]
    return []


def _run_for(reply: Any, dataset: str, dataset_id: str = "") -> tuple[list[str], str]:
    """The statuses of the runs in ``reply`` for this dataset (by its id when G6 knows it, else by its name), and what
    is wrong with them in words (``""`` when there is at least one and every one finished)."""
    if reply is None:
        return [], "its reply is empty"
    runs = _runs(reply)
    if not runs:
        return [], "its reply holds no pipeline run"
    ours = [r for r in runs if (str(r.get("dataset_id") or "").lower() == dataset_id.lower() if dataset_id else
                                r.get("dataset_name") == dataset)]
    if not ours:
        named = sorted({str(r.get("dataset_name") or r.get("dataset_id") or "?") for r in runs})[:5]
        return [], f"its reply names a run for another dataset ({', '.join(named)}), not for {dataset!r}"
    statuses = [str(r.get("status") or "") for r in ours][:20]
    unfinished = [s or "no status" for s in statuses if s not in FINISHED]
    if unfinished:
        return statuses, f"its run for {dataset!r} did not finish ({', '.join(unfinished)})"
    return statuses, ""


def _add(cognee: Cognee, d: CogneeInput) -> dict[str, Any]:
    if not d.text.strip():
        return _refuse(d, NO_TEXT, "there is no text to add")
    if not d.dataset:
        return _refuse(d, NO_TARGET, "no dataset was named to add the text to")
    digest = sha256_hex(d.text)
    plan: dict[str, Any] = {
        "kind": "cognee_add", "send_to": cognee.address() + "/api/v1/add", "dataset": d.dataset,
        "characters": len(d.text), "sha256": digest,
        "then": "POST /api/v1/cognify: Cognee's model reads the text to extract entities and links",
        "source": d.source, "source_is": "the caller's claim, kept as given", "leaves_this_machine": _LEAVES}
    what = f"sending {len(d.text)} characters to dataset {d.dataset!r} in your Cognee and having its model read them"
    gate = _ask_gate(cognee, d, what, "/api/v1/add", {"dataset": d.dataset, "characters": len(d.text),
                                                     "sha256": digest})
    if d.dry_run:
        existing = cognee.dataset_named(d.dataset)
        plan["dataset_exists"] = existing is not None
        return _answer(d, DRY_RUN, f"Dry run: nothing was sent. {len(d.text)} characters would go to dataset "
                                   f"{d.dataset!r} in your Cognee, and its model would read them; the action gate "
                                   f"decides {gate['decision']} on it now.",
                       evidence=[plan, gate], would=plan, gate_would=gate["decision"])
    if gate["decision"] not in _GATE_ALLOWS:
        return _held(d, gate, what, [plan])
    existing = cognee.dataset_named(d.dataset)
    before = {str(i["id"]) for i in (cognee.items(str(existing["id"])) or [])} if existing else set()
    name = f"g6-{digest[:16]}"
    sent = f"the text ({len(d.text)} characters, as {name}.txt) was sent to dataset {d.dataset!r} (POST /api/v1/add)"
    steps: list[dict[str, Any]] = [plan, gate]

    def unbuilt(code: str, problem: str) -> dict[str, Any]:
        return _refuse(d, code, f"{problem}; G6 does not report a built graph.", evidence=steps)

    try:
        added = cognee.add(d.text, d.dataset, f"{name}.txt")
    except CogneeError as exc:
        return unbuilt(exc.code, f"{sent}, and Cognee answered with an error: {exc.reason}")
    add_statuses, wrong = _run_for(added, d.dataset)
    steps.append({"kind": "cognee_add_reply", "statuses": add_statuses, "problem": wrong})
    if wrong:
        return unbuilt(NOT_BUILT, f"{sent}, but Cognee's reply does not say it was stored: {wrong}")
    try:
        listed = cognee.dataset_named(d.dataset)
        dataset_id = str(listed["id"]) if listed else ""
        said_id = str(_runs(added)[0].get("dataset_id") or "")
        if not dataset_id or (said_id and said_id.lower() != dataset_id.lower()):
            return unbuilt(NOT_FOUND, f"{sent}, but Cognee does not list dataset {d.dataset!r} as the one it added to")
        sent += f", then its graph was asked for (POST /api/v1/cognify, dataset {d.dataset!r})"
        built = cognee.cognify(d.dataset, d.timeout)
        statuses, wrong = _run_for(built, d.dataset, dataset_id)
        steps.append({"kind": "cognify", "dataset_id": dataset_id, "statuses": statuses, "problem": wrong})
        if wrong:
            return unbuilt(NOT_BUILT, f"{sent}, but {wrong}")
        items = cognee.items(dataset_id)
    except CogneeError as exc:
        return unbuilt(exc.code, f"{sent}, but then: {exc.reason}")
    if items is None:
        return unbuilt(NOT_FOUND, f"{sent}, but Cognee no longer has dataset {dataset_id}")
    mine = next((i for i in items if str(i.get("name") or "") in (name, f"{name}.txt")), None)
    observed = {"kind": "cognee_items", "dataset_id": dataset_id, "before": len(before), "after": len(items),
                "item": str(mine["id"]) if mine else None, "looked_for": name,
                "how": "the dataset's items listed before and after the add, looking for the item named for the text"}
    steps.append(observed)
    if mine is None:
        return unbuilt(NOT_BUILT, f"{sent}, but dataset {d.dataset!r} lists no item named {name}: nothing shows the "
                                  "text was stored")
    item = {"id": str(mine["id"]), "name": str(mine.get("name") or "")}
    new = [{"id": str(i["id"]), "name": str(i.get("name") or "")} for i in items if str(i["id"]) not in before]
    said = (f"Sent {len(d.text)} characters to dataset {d.dataset!r}; Cognee lists the item {item['name']} and reports "
            f"its graph built ({', '.join(sorted(set(statuses)))})"
            + ("" if item["id"] not in before else ": that item was listed before this add, so the same text may have "
                                                   "been added before (Cognee keeps one item per content)")
            + ".")
    return _answer(d, ADDED, said, evidence=[_service(cognee), *steps], dataset_id=dataset_id, item=item,
                   new_items=new, cognify=statuses)


def _hit(value: Any, dataset: dict[str, Any], search_type: str) -> dict[str, Any]:
    cites: dict[str, str] = {}
    if isinstance(value, dict):
        text = value.get("text") if isinstance(value.get("text"), str) else json.dumps(value, default=str)
        cites = {k: str(value[k]) for k in ("id", "chunk_index", "document_id") if value.get(k) is not None}
    else:
        text = value if isinstance(value, str) else json.dumps(value, default=str)
    retrieved = search_type in _RETRIEVED
    return {"as": "retrieved_excerpt" if retrieved else "model_output", "trust": "untrusted", "text": text[:4000],
            "provenance": "retrieved" if retrieved else "model_generated", "search_type": search_type,
            "dataset": dataset, "cites": cites}


def _search(cognee: Cognee, d: CogneeInput) -> dict[str, Any]:
    if not d.query.strip():
        return _refuse(d, NO_QUERY, "there is no query to search for")
    gate: dict[str, Any] | None = None
    if d.search_type in COMPLETIONS:
        would = {"kind": "cognee_search", "send_to": cognee.address() + "/api/v1/search", "search_type": d.search_type,
                 "query_characters": len(d.query), "dataset": d.dataset,
                 "then": "Cognee's model writes the answer from what it holds, spending on its model provider",
                 "leaves_this_machine": _LEAVES}
        what = f"a {d.search_type} search of your Cognee, whose model writes the answer"
        gate = _ask_gate(cognee, d, what, "/api/v1/search", {"search_type": d.search_type, "dataset": d.dataset,
                                                            "query_characters": len(d.query)})
        if d.dry_run:
            return _answer(d, DRY_RUN, f"Dry run: nothing was sent. A {d.search_type} search would have your "
                                       f"Cognee's model write an answer; the action gate decides {gate['decision']} on "
                                       "it now.", evidence=[would, gate], would=would, gate_would=gate["decision"])
        if gate["decision"] not in _GATE_ALLOWS:
            return _held(d, gate, what, [would])
    hits: list[dict[str, Any]] = []
    for entry in cognee.search(d.query, d.search_type, d.top_k, d.dataset):
        if isinstance(entry, dict) and "search_result" in entry:
            dataset = {"id": str(entry.get("dataset_id") or ""), "name": str(entry.get("dataset_name") or "")}
            values = entry["search_result"]
        else:
            dataset, values = {"id": "", "name": d.dataset}, entry
        for value in values if isinstance(values, list) else [values]:
            hits.append(_hit(value, dataset, d.search_type))
    hits = hits[:200]
    model = sum(h["provenance"] == "model_generated" for h in hits)
    said = (f"Cognee returned {len(hits)} result{'s' if len(hits) != 1 else ''} ({d.search_type})"
            + (f"; {model} written by its model, citing no passage" if model else "") + "."
            + ("" if hits else " That is not proof that nothing matches: Cognee answers a search of a dataset it will "
                               "not let you read with an empty list too."))
    evidence = [{**_service(cognee), "search_type": d.search_type, "results": len(hits), "model_generated": model,
                 "dataset": d.dataset}, *([gate] if gate else [])]
    return _answer(d, FOUND, said, evidence=evidence, texts=[h["text"] for h in hits], results=hits)


def _chain(nodes: dict[str, str], edges: list[dict[str, str]], start: str, end: str) -> list[dict[str, str]] | None:
    """The shortest chain of links from ``start`` to ``end``, following a link either way; ``None`` when none joins
    them."""
    links: dict[str, list[tuple[str, dict[str, str], bool]]] = {n: [] for n in nodes}
    for edge in edges:
        links[edge["from_id"]].append((edge["to_id"], edge, True))
        links[edge["to_id"]].append((edge["from_id"], edge, False))
    came: dict[str, tuple[str, dict[str, str], bool] | None] = {start: None}
    todo = deque([start])
    while todo:
        here = todo.popleft()
        if here == end:
            break
        for there, edge, forward in links[here]:
            if there not in came:
                came[there] = (here, edge, forward)
                todo.append(there)
    if end not in came:
        return None
    steps: list[dict[str, str]] = []
    at = end
    while came[at] is not None:
        before, edge, forward = came[at]  # type: ignore[misc]
        steps.append({**edge, "followed": "as written" if forward else "the other way"})
        at = before
    return steps[::-1]


def _graph(cognee: Cognee, d: CogneeInput) -> dict[str, Any]:
    if not d.dataset_id:
        return _refuse(d, NO_TARGET, "no dataset_id was given")
    found = cognee.graph(d.dataset_id)
    nodes = {str(n["id"]): str(n.get("label") or (n["properties"] if isinstance(n.get("properties"), dict) else {}
                                                   ).get("name") or "")
             for n in found["nodes"] if isinstance(n, dict) and n.get("id") is not None}
    edges, dangling = [], 0
    for e in found["edges"]:
        ends = (str(e.get("source")), str(e.get("target"))) if isinstance(e, dict) else ("", "")
        if ends[0] not in nodes or ends[1] not in nodes:
            dangling += 1  # a link to an entity the graph does not hold is no link: dropped and counted
            continue
        edges.append({"from": nodes[ends[0]], "relation": str(e.get("label") or ""), "to": nodes[ends[1]],
                      "from_id": ends[0], "to_id": ends[1]})
    evidence = [{**_service(cognee), "kind": "cognee_graph", "dataset_id": d.dataset_id, "nodes": len(nodes),
                 "edges": len(edges), "dangling_edges": dangling,
                 "provenance": "model_generated: Cognee's model extracted these entities and links from the text"}]
    fields: dict[str, Any] = {"dataset_id": d.dataset_id, "nodes": [{"id": k, "label": v} for k, v in nodes.items()
                                                                     ][:d.limit],
                              "edges": edges[:d.limit], "dangling_edges": dangling}
    said = (f"Cognee's graph of {d.dataset_id} has {len(nodes)} entities and {len(edges)} links"
            + (f" ({dangling} links to entities it does not hold were dropped)" if dangling else ""))
    if d.link_from or d.link_to:
        if not (d.link_from and d.link_to):
            return _refuse(d, NO_TARGET, "name both link_from and link_to to find the links between them")
        named = {}
        for side in (d.link_from, d.link_to):
            match = [k for k, v in nodes.items() if v.strip().lower() == side.strip().lower()]
            if not match:
                return _refuse(d, NOT_FOUND, f"no entity labelled {side!r} is in the graph", evidence=evidence)
            if len(match) > 1:
                return _refuse(d, AMBIGUOUS, f"the label {side!r} names {len(match)} entities in the graph "
                                             f"({', '.join(match[:5])}); G6 does not choose one, so no chain is drawn",
                               evidence=evidence)
            named[side] = match[0]
        steps = _chain(nodes, edges, named[d.link_from], named[d.link_to])
        fields["chain"] = steps
        said += (f"; {d.link_from!r} and {d.link_to!r} are joined by {len(steps)} link{'s' if len(steps) != 1 else ''}"
                 if steps is not None else f"; no chain of links joins {d.link_from!r} and {d.link_to!r}")
    texts = [f"{e['from']} {e['relation']} {e['to']}" for e in edges[:d.limit]]
    return _answer(d, GRAPH, said + ".", evidence=evidence, texts=texts, **fields)


def _forget(cognee: Cognee, d: CogneeInput) -> dict[str, Any]:
    if not (d.dataset_id and d.data_id):
        return _refuse(d, NO_TARGET, "forget needs the dataset_id and the data_id of the item to delete")
    if not d.reason.strip():
        return _refuse(d, NO_REASON, "no reason was given for forgetting")
    what_gate = f"deleting item {d.data_id} from dataset {d.dataset_id} in your Cognee"
    gate = _ask_gate(cognee, d, what_gate, f"/api/v1/datasets/{d.dataset_id}/data/{d.data_id}",
                     {"dataset_id": d.dataset_id, "data_id": d.data_id, "reason": d.reason.strip()})
    if not d.dry_run and gate["decision"] not in _GATE_ALLOWS:
        return _held(d, gate, what_gate, [{"kind": "cognee_forget", "dataset_id": d.dataset_id, "data_id": d.data_id,
                                           "reason": d.reason.strip()}])
    items = cognee.items(d.dataset_id)
    if items is None:
        return _refuse(d, NOT_FOUND, f"Cognee has no dataset {d.dataset_id} that it lets G6 read")
    item = next((i for i in items if str(i["id"]).lower() == d.data_id.lower()), None)
    if item is None:
        return _refuse(d, NOT_FOUND, f"dataset {d.dataset_id} lists no item {d.data_id}: there is nothing to forget")
    what = {"kind": "cognee_forget", "dataset_id": d.dataset_id, "data_id": d.data_id,
            "name": str(item.get("name") or ""), "reason": d.reason.strip()}
    if d.dry_run:
        return _answer(d, DRY_RUN, f"Dry run: nothing was deleted. Item {d.data_id} ({what['name'] or 'unnamed'}) would "
                                   f"be deleted from dataset {d.dataset_id}; the action gate decides {gate['decision']} "
                                   "on it now.", evidence=[what, gate], would=what, gate_would=gate["decision"])
    sent = (f"the delete of item {d.data_id} from dataset {d.dataset_id} was sent "
            f"(DELETE /api/v1/datasets/{d.dataset_id}/data/{d.data_id})")
    try:
        cognee.delete(d.dataset_id, d.data_id)
    except CogneeError as exc:
        return _refuse(d, exc.code, f"{sent}, and Cognee answered with an error: {exc.reason}; G6 does not know "
                                    "whether it deleted anything", evidence=[what, gate])
    try:
        left = cognee.items(d.dataset_id)
        failed = "" if left is not None else f"Cognee answered 404: it no longer lets G6 read dataset {d.dataset_id}"
    except CogneeError as exc:
        left, failed = None, exc.reason
    if left is None:  # a dataset G6 cannot list shows nothing: the item may be there still
        return _refuse(d, UNCHECKED, f"{sent} and Cognee answered it, but listing the dataset afterwards failed "
                                     f"({failed}), so G6 could not check the item is gone", evidence=[what, gate])
    if any(str(i["id"]).lower() == d.data_id.lower() for i in left):
        return _refuse(d, NOT_DONE, f"{sent} and Cognee answered it, but the dataset still lists the item",
                       evidence=[what, gate])
    try:
        remains: list[dict[str, str]] | None = _remains(cognee.graph(d.dataset_id), d.data_id)
        n = len(remains or [])
        graph_checked = ("read the dataset's graph: " + (
            f"it still holds {n} node{'s' if n != 1 else ''} that carry the item's id or link to one that does" if n
            else "no node in it carries the item's id or links to one that does"))
    except CogneeError as exc:
        remains, graph_checked = None, (f"the dataset's graph could not be read ({exc.reason}), so what it still "
                                        "holds of the item is not known")
    checked = f"listed the dataset's items again: the item is no longer listed; {graph_checked}"
    receipt = {**what, "checked": checked, "graph_still_holds": remains,
               "not_checked": "which entities Cognee drew from this item alone: one that no longer links to a node "
                              "carrying the item's id is not counted"}
    return _answer(d, FORGOTTEN, f"Deleted item {d.data_id} from dataset {d.dataset_id}: {checked}.",
                   evidence=[_service(cognee), receipt, gate], removed=receipt)


def _label(node: dict[str, Any]) -> str:
    properties = node["properties"] if isinstance(node.get("properties"), dict) else {}
    return str(node.get("label") or properties.get("name") or "")


def _remains(graph: dict[str, Any], data_id: str, limit: int = 200) -> list[dict[str, str]]:
    """What ``graph`` still holds of item ``data_id``: the nodes that carry its id (as their own id or a property's
    value), then the nodes a link joins to one of those."""
    key = data_id.lower()
    nodes = {str(n["id"]): n for n in graph["nodes"] if isinstance(n, dict) and n.get("id") is not None}

    def carries(node: dict[str, Any]) -> bool:
        properties = node["properties"] if isinstance(node.get("properties"), dict) else {}
        return str(node["id"]).lower() == key or any(isinstance(v, str) and v.lower() == key
                                                     for v in properties.values())

    own = {i for i, node in nodes.items() if carries(node)}
    linked: set[str] = set()
    for edge in graph["edges"]:
        if isinstance(edge, dict):
            ends = (str(edge.get("source")), str(edge.get("target")))
            linked |= {b for a, b in (ends, ends[::-1]) if a in own and b in nodes and b not in own}
    found = [{"id": i, "label": _label(nodes[i]), "how": "carries the item's id"} for i in sorted(own)]
    found += [{"id": i, "label": _label(nodes[i]), "how": "linked to a node that carries the item's id"}
              for i in sorted(linked)]
    return found[:limit]


@block_contract(
    purpose="add text to the Cognee server the person runs, search it and read the links Cognee's graph draws between "
            "facts, each result untrusted and cited as far as Cognee allows",
    mitigates_failure_modes=("unlinked_facts",),
    verification_method="links kept only when both ends are entities in the graph Cognee returned, the chain between "
                        "two entities found over those links; every add, forget and completion search decided by "
                        "g6.action_gating before anything is sent, and every add and forget checked by listing the "
                        "dataset's items before and after; an add reported built only when the add and cognify "
                        "replies each hold a finished run for that dataset and the item named for the text is listed",
)
@component_maturity(
    "beta",
    reason="a Cognee HTTP client through g6.core.outside.call_service; never imports cognee or litellm; changes are dry "
           "runs by default and checked afterwards; qualified-draft at best: what Cognee's model extracts is not "
           "checked against the text",
)
class CtxCogneeBlock(AIBlock[CogneeInput, dict, dict]):
    """Use the person's own Cognee for a graph memory, and say what was observed. Never ``verified``."""

    def __init__(self, name: str = COMPONENT, state: dict | None = None) -> None:
        super().__init__(name=name, state=state or {})

    def infer(self, data: CogneeInput) -> Result[dict]:
        if data.op == "ops":
            return Result.ok({"component": self.name, "op": "ops", "ops": list(_OPS), "sensitive": list(SENSITIVE_OPS)})
        if data.op == "help":
            return Result.ok({"component": self.name, "op": "help", "help": list(_OPS.values())})
        run = {"status": _status, "datasets": _datasets, "add": _add, "search": _search, "graph": _graph,
               "forget": _forget}[data.op]
        try:
            return Result.ok(run(Cognee(COGNEE, token=_token()), data))
        except CogneeError as exc:
            return Result.ok(_refuse(data, exc.code, exc.reason))
        except outside.ServiceError as exc:
            return Result.ok(_refuse(data, exc.code, exc.message))


__all__ = ["COGNEE", "COGNEE_KEY", "COMPONENT", "SEARCH_TYPES", "SENSITIVE_OPS", "URL_ENV", "CogneeInput",
           "CtxCogneeBlock"]
