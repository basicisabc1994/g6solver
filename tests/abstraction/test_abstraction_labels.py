# SPDX-License-Identifier: Apache-2.0
"""What g6.abstraction calls its own results.

* Its top library status is ``checked``: a component never marks its own output ``verified``. A state file written
  with the old ``verified`` status loads as ``checked`` and, like every loaded entry, never authorises pruning.
* The adapter writes the G6 states (``qualified-draft``, ``blocked-escalated``) and a ``G6_E_*`` code, with
  ``request_id`` and ``run_id``, so its envelope passes ``check_envelope``.
* A note that tells the caller what to do next names a call that exists in the Python API."""
from __future__ import annotations

import json
from pathlib import Path

from g6.abstraction import ConcreteDomain, Library, Quotient
from g6.abstraction.diagnostics import diagnose
from g6.abstraction.g6_adapter import attach_abstraction_envelope, surface_state
from g6.abstraction.pruning import advise_pruning
from g6.invariants import check_envelope

_UNIT = {"x": {"kind": "float", "low": 0.0, "high": 1.0}}


def _toy() -> ConcreteDomain:
    order = [(7 * i) % 40 for i in range(40)]
    return ConcreteDomain.from_rows([{"x": k / 39, "score": -(k / 39 - 0.3) ** 2} for k in order],
                                    objective="score", maximise=True, param_spec=_UNIT, name="toy")


def _best(lib: Library, dom: ConcreteDomain):
    return lib.evaluate(Quotient(radius=0.1, lipschitz=2.0), dom, budget=20)


def test_the_top_status_is_checked_never_verified() -> None:
    lib, dom = Library(k_permutations=4), _toy()
    entry = _best(lib, dom)
    assert entry.status == "checked"
    board = lib.leaderboard()
    assert board[0]["status"] == "checked" and not any(row["status"] == "verified" for row in board)
    codes = {f["code"] for f in diagnose(lib, {"toy": dom}).to_dict()["findings"]}
    assert not any("VERIFIED" in code for code in codes)


def test_a_domain_with_no_checked_entry_is_flagged_no_checked() -> None:
    lib, dom = Library(k_permutations=4), _toy()
    lib.evaluate(Quotient(radius=0.5, lipschitz=0.0), dom, budget=20)
    codes = {f["code"] for f in diagnose(lib, {"toy": dom}).to_dict()["findings"]}
    assert "NO_CHECKED" in codes and "NO_VERIFIED" not in codes


def test_survival_a_checked_entry_evaluated_this_session_may_prune() -> None:
    lib, dom = Library(k_permutations=4), _toy()
    entry = _best(lib, dom)
    advice = advise_pruning(lib, entry.entry_id, dom, [{"x": 0.95}])
    assert advice["may_prune"] is True and advice["entry_status"] == "checked"


def test_a_state_file_with_the_old_verified_status_loads_as_checked_and_does_not_prune(tmp_path: Path) -> None:
    lib, dom = Library(k_permutations=4), _toy()
    entry = _best(lib, dom)
    path = tmp_path / "state.json"
    lib.save(str(path), {dom.name: dom})
    state = json.loads(path.read_text())
    for e in state["library"]["entries"]:
        e["status"] = "verified"  # written before the rename
    path.write_text(json.dumps(state))
    lib2, doms = Library.load(str(path))
    assert lib2.get(entry.entry_id).status == "checked"
    advice = advise_pruning(lib2, entry.entry_id, doms[dom.name], [{"x": 0.95}])
    assert advice["may_prune"] is False


def test_the_adapter_writes_g6_states_and_a_g6_code() -> None:
    lib, dom = Library(k_permutations=4), _toy()
    kept = attach_abstraction_envelope("evaluate", _best(lib, dom), request_id="rq", run_id="rn")
    rejected = attach_abstraction_envelope("evaluate", lib.evaluate(Quotient(radius=0.5, lipschitz=0.0), dom,
                                                                    budget=20))
    assert kept["completion_state"] == "qualified-draft" and rejected["completion_state"] == "blocked-escalated"
    assert (kept["request_id"], kept["run_id"]) == ("rq", "rn")
    for envelope in (kept, rejected, attach_abstraction_envelope("evaluate", object())):
        assert check_envelope(envelope) == [], envelope
        assert envelope["warning_card"]["code"].startswith("G6_E_")
    assert surface_state("verified")[0] == "blocked-escalated"  # the old literal is no longer a status


def test_the_next_step_a_note_names_exists_in_the_python_api(tmp_path: Path) -> None:
    lib, dom = Library(k_permutations=4), _toy()
    entry = _best(lib, dom)
    path = tmp_path / "state.json"
    lib.save(str(path), {dom.name: dom})
    lib2, doms = Library.load(str(path))
    notes = advise_pruning(lib2, entry.entry_id, doms[dom.name], [{"x": 0.95}])["notes"]
    assert any("Library.evaluate(..., force=True)" in n for n in notes), notes
    assert hasattr(Library, "evaluate") and not hasattr(Library, "revalidate")
