# SPDX-License-Identifier: Apache-2.0
"""Four-valued (Belnap) logic: true, false, both and neither, its connectives, and modal frames, in pure Python.

The archive's ``qa_logic`` (owner, 2026-10-20: the logic connectors). A claim is not a boolean: its value is two bits
of evidence, ``(support_for, support_against)``: T supported, F refuted, B both (a conflict, to be traced to its
cause), N neither (no evidence yet). ``g6.evidence`` gives each predicate of a certificate the same two bits
(``support``, ``refute``) but no connectives; :meth:`TruthValue.of` reads one, so its statements combine here and
nothing is duplicated.

The connectives, on the bits: ``~p`` swaps them; ``p & q`` is supported when both are and refuted when either is;
``p | q`` is the dual; ``p.implies(q)`` is ``~p | q``; ``p.iff(q)`` is both implications. No formula is a tautology:
with no evidence every formula is N, so ``p | ~p`` is N, and B on conflicting evidence. Formulas use
:mod:`g6.formal.propositional`'s syntax, with ``TRUE`` read as T and ``FALSE`` as F.

Modal frames: the archive kept the frame a claim is read under as a label (actual, alethic, epistemic, deontic,
temporal). Here a frame also has worlds and an access relation: "necessarily p" at a world is the meet (``&``) of p
over the worlds it sees, "possibly p" the join (``|``). Each reading needs conditions of the access relation
(``FRAME_CONDITIONS``), and ``check_frame`` names each one that fails, with a witness. A necessity at a world that sees
no world holds vacuously and is flagged, as a vacuous validity is.

The claim state is qa_logic's ``completion_state_for`` without its path to ``verified``, which only the evidence issuer
gives: T is ``qualified-draft``; F (refuted), B (a conflict) and N (no evidence yet, a draft in qa_logic) are
``blocked-escalated``. Every result passes
through :func:`g6.formal.envelope.apply_envelope` as backend ``four_valued``. Left out of the port: qa_logic's evidence
levels, risk-tier bar and ``QAClaim``, whose purpose was that promotion to ``verified``, and its conflict classes.
"""
from __future__ import annotations

import itertools
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import reduce
from typing import Any

from g6.formal.envelope import FRAME_UNFIT, INPUT_INVALID, INPUT_TOO_LARGE, PARSE_ERROR, Refusal, apply_envelope
from g6.formal.propositional import FormulaTooLarge, _atoms, parse, to_text
from g6.formal.schema import FourValuedResult

MAX_TABLE_ATOMS = 6
MAX_WORLDS = 1000
MAX_ACCESS = 10_000


class TruthState(Enum):
    """Belnap's four values."""

    T = "T"  # supported true: evidence for, none against
    F = "F"  # supported false: evidence against, none for
    B = "B"  # both: evidence on both sides, an unresolved conflict
    N = "N"  # neither: no evidence either way


_WORDS = {"t": "T", "true": "T", "f": "F", "false": "F", "b": "B", "both": "B", "n": "N", "neither": "N"}


@dataclass(frozen=True)
class TruthValue:
    """Two bits of evidence for a claim: ``(support_for, support_against)``."""

    support_for: bool = False
    support_against: bool = False

    @property
    def state(self) -> TruthState:
        if self.support_for:
            return TruthState.B if self.support_against else TruthState.T
        return TruthState.F if self.support_against else TruthState.N

    @property
    def requires_rca(self) -> bool:
        """Both is an unresolved conflict: trace it to its cause (root-cause analysis) before using the claim."""
        return self.state is TruthState.B

    def __invert__(self) -> TruthValue:
        return TruthValue(self.support_against, self.support_for)

    def __and__(self, other: TruthValue) -> TruthValue:
        return TruthValue(self.support_for and other.support_for, self.support_against or other.support_against)

    def __or__(self, other: TruthValue) -> TruthValue:
        return TruthValue(self.support_for or other.support_for, self.support_against and other.support_against)

    def implies(self, other: TruthValue) -> TruthValue:
        return ~self | other

    def iff(self, other: TruthValue) -> TruthValue:
        return self.implies(other) & other.implies(self)

    @classmethod
    def of(cls, statement: Any) -> TruthValue:
        """The value of anything with ``support`` and ``refute`` flags, such as a g6.evidence ``PredicateStatement``."""
        return cls(bool(statement.support), bool(statement.refute))

    @classmethod
    def parse(cls, text: Any) -> TruthValue:
        """``T``, ``F``, ``B``, ``N`` or ``true``, ``false``, ``both``, ``neither``, in any case."""
        state = _WORDS.get(text.strip().lower()) if isinstance(text, str) else None
        if state is None:
            raise Refusal(INPUT_INVALID, f"{text!r} is not a four-valued truth value: T, F, B (both) or N (neither)")
        return _BY_STATE[state]

    def __repr__(self) -> str:
        return f"TruthValue<{self.state.value}>"


T = TruthValue(True, False)
F = TruthValue(False, True)
B = TruthValue(True, True)
N = TruthValue(False, False)
_BY_STATE = {"T": T, "F": F, "B": B, "N": N}


class ModalFrame(Enum):
    """The frame a claim is read under (qa_logic). Never blur these."""

    ACTUAL = "actual"
    ALETHIC = "alethic"      # necessary / possible / impossible / contingent
    EPISTEMIC = "epistemic"  # known / believed
    DEONTIC = "deontic"      # obligatory / permitted / forbidden
    TEMPORAL = "temporal"    # always / eventually


#: What each reading needs of the access relation: alethic S5 (an equivalence), epistemic S4 (what is known is true and
#: known to be known), deontic KD (every world sees an ideal one, so nothing is obligatory without being permitted),
#: temporal K4 (later than later is later). The actual frame has no modality.
FRAME_CONDITIONS: dict[str, tuple[str, ...]] = {
    "actual": (),
    "alethic": ("reflexive", "symmetric", "transitive"),
    "epistemic": ("reflexive", "transitive"),
    "deontic": ("serial",),
    "temporal": ("transitive",),
}


def claim_state(value: TruthValue) -> str:
    """A claim's state, held below ``verified``: only T is a draft; F, B and N (no evidence yet) block."""
    return "qualified-draft" if value.state is TruthState.T else "blocked-escalated"


def _read(formula: Any) -> tuple:
    try:
        return parse(formula)
    except FormulaTooLarge as exc:
        raise Refusal(INPUT_TOO_LARGE, f"the formula: {exc}") from None
    except ValueError as exc:
        raise Refusal(PARSE_ERROR, f"the formula: {exc}") from None


def _eval(node: tuple, env: Mapping[str, TruthValue]) -> TruthValue:
    kind = node[0]
    if kind == "const":
        return T if node[1] else F
    if kind == "atom":
        return env[node[1]]
    if kind == "not":
        return ~_eval(node[1], env)
    a, b = _eval(node[1], env), _eval(node[2], env)
    return a & b if kind == "and" else a | b if kind == "or" else a.implies(b) if kind == "implies" else a.iff(b)


def _value(tree: tuple, env: Mapping[str, TruthValue]) -> TruthValue:
    try:
        return _eval(tree, env)
    except RecursionError:
        raise Refusal(INPUT_TOO_LARGE, "the formula is nested too deeply") from None


def _env(values: Any, atoms: set[str], where: str = "") -> dict[str, TruthValue]:
    """Each atom's value; an atom with none is refused (give N for no evidence): nothing is assumed for you."""
    if not isinstance(values, Mapping):
        raise Refusal(INPUT_INVALID, f"the values{where} map each atom to T, F, B or N")
    missing = sorted(atoms - set(values))
    if missing:
        raise Refusal(INPUT_INVALID, f"no value{where} for {', '.join(missing)}: give N for no evidence, nothing is "
                                     "assumed for you")
    return {atom: TruthValue.parse(values[atom]) for atom in sorted(atoms)}


def _result(operation: str, started: float, fields: dict[str, Any], request_id: str, run_id: str) -> FourValuedResult:
    result = FourValuedResult(operation=operation, backend="four_valued",
                              elapsed_s=round(time.monotonic() - started, 4), **fields)
    return apply_envelope(result, backend="four_valued", operation=operation, request_id=request_id, run_id=run_id)


def evaluate(formula: str, values: Mapping[str, str], *, request_id: str = "", run_id: str = "") -> FourValuedResult:
    """The four-valued value of ``formula`` given each atom's value (``{atom: T|F|B|N}``)."""
    started = time.monotonic()
    try:
        tree = _read(formula)
        env = _env(values, _atoms(tree, set()))
        fields: dict[str, Any] = {"formula": to_text(tree), "inputs": {a: v.state.value for a, v in env.items()},
                                  "value": _value(tree, env).state.value, "computed": True}
    except Refusal as refusal:
        fields = {"code": refusal.code, "reason": refusal.reason}
    return _result("belnap_eval", started, fields, request_id, run_id)


def truth_table(formula: str, *, request_id: str = "", run_id: str = "") -> FourValuedResult:
    """The value of ``formula`` for every assignment of T, F, B, N to its atoms (at most ``MAX_TABLE_ATOMS``), in that
    order; each row maps the atoms and ``value``."""
    started = time.monotonic()
    try:
        tree = _read(formula)
        atoms = sorted(_atoms(tree, set()))
        if len(atoms) > MAX_TABLE_ATOMS:
            raise Refusal(INPUT_TOO_LARGE, f"a truth table over {len(atoms)} atoms has 4**{len(atoms)} rows; at most "
                                           f"{MAX_TABLE_ATOMS} atoms")
        if "value" in atoms:
            raise Refusal(INPUT_INVALID, "rename the atom 'value': each row of the table uses that key for the result")
        rows = []
        for states in itertools.product("TFBN", repeat=len(atoms)):
            row = dict(zip(atoms, states, strict=True))
            rows.append({**row, "value": _value(tree, {a: _BY_STATE[s] for a, s in row.items()}).state.value})
        fields: dict[str, Any] = {"formula": to_text(tree), "inputs": {"atoms": atoms}, "table": rows, "computed": True}
    except Refusal as refusal:
        fields = {"code": refusal.code, "reason": refusal.reason}
    return _result("belnap_table", started, fields, request_id, run_id)


def _reading(modal_frame: Any, *, optional: bool = False) -> str:
    if optional and modal_frame in ("", None):
        return ""
    reading = modal_frame.strip().lower() if isinstance(modal_frame, str) else None
    if reading not in FRAME_CONDITIONS:
        raise Refusal(INPUT_INVALID, f"modal_frame {modal_frame!r} is not one of {', '.join(FRAME_CONDITIONS)}")
    return reading


def _frame(worlds: Any, access: Any) -> tuple[list[str], dict[str, list[str]]]:
    """The worlds (a list, or the keys of a mapping) and each one's successors under ``access`` (``[[from, to]]``)."""
    names = list(worlds) if isinstance(worlds, Mapping | Sequence) and not isinstance(worlds, str) else []
    if (not names or len(names) > MAX_WORLDS or not all(isinstance(w, str) and w for w in names)
            or len(set(names)) != len(names)):
        raise Refusal(INPUT_INVALID, f"worlds name 1 to {MAX_WORLDS} distinct worlds")
    if isinstance(access, str) or not isinstance(access, Sequence) or len(access) > MAX_ACCESS:
        raise Refusal(INPUT_INVALID, f"access is a list of at most {MAX_ACCESS} [from, to] pairs of worlds")
    successors: dict[str, list[str]] = {w: [] for w in names}
    for pair in access:
        if (isinstance(pair, str) or not isinstance(pair, Sequence) or len(pair) != 2
                or not all(isinstance(w, str) and w in successors for w in pair)):
            raise Refusal(INPUT_INVALID, f"access pair {str(pair)[:60]!r} is not [from, to] of two of the worlds")
        if pair[1] not in successors[pair[0]]:
            successors[pair[0]].append(pair[1])
    return names, successors


def _properties(names: Sequence[str], successors: Mapping[str, Sequence[str]]) -> dict[str, dict[str, Any]]:
    """Each frame condition, whether it holds, and the first worlds where it fails."""
    edges = {(u, v) for u in names for v in successors[u]}

    def first(failures: Any) -> dict[str, Any]:
        found = next(failures, None)
        return {"holds": found is None, "witness": list(found) if found else []}

    return {
        "reflexive": first((w,) for w in names if (w, w) not in edges),
        "serial": first((w,) for w in names if not successors[w]),
        "symmetric": first((u, v) for u in names for v in successors[u] if (v, u) not in edges),
        "transitive": first((u, v, w) for u in names for v in successors[u] for w in successors[v]
                            if (u, w) not in edges),
        "euclidean": first((u, v, w) for u in names for v in successors[u] for w in successors[u]
                           if (v, w) not in edges),
    }


def _unfit(reading: str, properties: Mapping[str, Mapping[str, Any]]) -> tuple[list[str], dict[str, str]]:
    missing = [c for c in FRAME_CONDITIONS.get(reading, ()) if not properties[c]["holds"]]
    if not missing:
        return missing, {}
    why = "; ".join(f"{c} fails at {properties[c]['witness']}" for c in missing)
    return missing, {"code": FRAME_UNFIT, "reason": f"a {reading} reading needs a frame that is "
                                                    f"{' and '.join(missing)}, and this one is not: {why}"}


def check_frame(worlds: Sequence[str] | Mapping[str, Any], access: Sequence[Sequence[str]], modal_frame: str, *,
                request_id: str = "", run_id: str = "") -> FourValuedResult:
    """Which conditions of ``modal_frame`` the frame (``worlds``, ``access``) meets, and a witness for each it
    misses: ``G6_E_FORMAL_FRAME_UNFIT`` when one is missing."""
    started = time.monotonic()
    try:
        reading = _reading(modal_frame)
        names, successors = _frame(worlds, access)
        properties = _properties(names, successors)
        missing, unfit = _unfit(reading, properties)
        fields: dict[str, Any] = {"frame": {"modal_frame": reading, "worlds": names, "properties": properties,
                                            "missing": missing}, "computed": True, **unfit}
    except Refusal as refusal:
        fields = {"code": refusal.code, "reason": refusal.reason}
    return _result("frame_check", started, fields, request_id, run_id)


def modal_evaluate(formula: str, worlds: Mapping[str, Mapping[str, str]], access: Sequence[Sequence[str]], world: str,
                   *, modality: str = "necessarily", modal_frame: str = "", request_id: str = "",
                   run_id: str = "") -> FourValuedResult:
    """"Necessarily" ``formula`` at ``world`` (the meet of its value over the worlds ``world`` sees) or "possibly" it
    (the join), given each world's atom values (``{world: {atom: T|F|B|N}}``). With a ``modal_frame`` the frame must
    meet that reading's conditions; a necessity at a world that sees no world is vacuous. Either is
    ``G6_E_FORMAL_FRAME_UNFIT``, the value still reported."""
    started = time.monotonic()
    try:
        if modality not in ("necessarily", "possibly"):
            raise Refusal(INPUT_INVALID, f"modality {modality!r} is not necessarily or possibly")
        tree = _read(formula)
        reading = _reading(modal_frame, optional=True)
        if not isinstance(worlds, Mapping):
            raise Refusal(INPUT_INVALID, "worlds map each world to its atom values")
        names, successors = _frame(worlds, access)
        if not isinstance(world, str) or world not in successors:
            raise Refusal(INPUT_INVALID, f"world {world!r} is not one of the frame's worlds")
        atoms, seen = _atoms(tree, set()), successors[world]
        values = {w: _value(tree, _env(worlds[w], atoms, f" at world {w!r}")).state.value for w in seen}
        necessity = modality == "necessarily"
        combine = TruthValue.__and__ if necessity else TruthValue.__or__
        value = reduce(combine, (_BY_STATE[v] for v in values.values()), T if necessity else F)
        properties = _properties(names, successors)
        missing, unfit = _unfit(reading, properties)
        vacuous = necessity and not seen
        if vacuous and not unfit:
            unfit = {"code": FRAME_UNFIT, "reason": f"world {world!r} sees no world, so 'necessarily' holds vacuously; "
                                                    "a serial frame (every world sees one) does not allow this"}
        fields: dict[str, Any] = {
            "formula": to_text(tree), "value": value.state.value, "vacuous": vacuous, "computed": True,
            "inputs": {"world": world, "modality": modality},
            "frame": {"modal_frame": reading, "successors": list(seen), "values": values, "properties": properties,
                      "missing": missing},
            **unfit}
    except Refusal as refusal:
        fields = {"code": refusal.code, "reason": refusal.reason}
    return _result("modal_eval", started, fields, request_id, run_id)


__all__ = ["B", "F", "FRAME_CONDITIONS", "MAX_TABLE_ATOMS", "N", "T", "ModalFrame", "TruthState", "TruthValue",
           "check_frame", "claim_state", "evaluate", "modal_evaluate", "truth_table"]
