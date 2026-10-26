# SPDX-License-Identifier: Apache-2.0
"""The declarative inputs of g6.optimisation and its one labelled result.

Every model is data: a linear expression is ``{variable: coefficient}``, a constraint is its terms plus bounds, a flow
network is a list of arcs. There is no formula string anywhere, so nothing a caller sends is ever evaluated. Unknown
fields are refused, numbers must be finite (``None`` is "no bound"), and the problem models check that the pieces fit
together (every name declared, every lower bound at or below its upper bound) before anything is solved.

A result's ``completion_state`` can only be ``qualified-draft`` or ``blocked-escalated``: ``verified`` is not a value
the model accepts, because a solver's answer on a caller's model is not a proof a person has inspected.
"""
from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

#: The most items any list or mapping may hold: a shape limit on what is parsed. Each op's solving limits are smaller
#: and are refused with their own code (``G6_E_OPTIM_INPUT_TOO_LARGE``).
STRUCTURAL_LIMIT = 100_000


def _as_text(value: Any) -> Any:
    """A node or machine named by an integer is read as its text, so ``0`` and ``"0"`` are the same node."""
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


Number = Annotated[float, Field(allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
NodeId = Annotated[str, BeforeValidator(_as_text), Field(min_length=1, max_length=200)]
Sense = Literal["minimise", "maximise", "minimize", "maximize"]
State = Literal["qualified-draft", "blocked-escalated"]


class _Data(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Variable(_Data):
    """A decision variable: bounds (``None`` for none; the lower bound is 0 unless given) and whether it is an
    integer."""

    lb: Number | None = 0.0
    ub: Number | None = None
    integer: bool = False


class LinearConstraint(_Data):
    """``lb <= sum(coefficient * variable) <= ub``; give either bound or both (equal bounds for an equality)."""

    name: str = Field("", max_length=200)
    terms: dict[Name, Number] = Field(default_factory=dict, max_length=STRUCTURAL_LIMIT)
    lb: Number | None = None
    ub: Number | None = None


class Arc(_Data):
    """A directed arc of a flow network, ``{"from": node, "to": node, "capacity": n, "cost": c}``."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    tail: NodeId = Field(alias="from")
    head: NodeId = Field(alias="to")
    capacity: Count
    cost: int = 0


class Task(_Data):
    """One task of a job: the machine it needs and for how long."""

    machine: NodeId
    duration: Count


def _row_label(index: int, row: LinearConstraint) -> str:
    return f"constraint {row.name!r}" if row.name else f"constraint {index}"


class LinearProblem(_Data):
    """A linear or mixed-integer programme: variables, linear constraints and a linear objective."""

    variables: dict[Name, Variable] = Field(min_length=1, max_length=STRUCTURAL_LIMIT)
    constraints: list[LinearConstraint] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)
    objective: dict[Name, Number] = Field(default_factory=dict, max_length=STRUCTURAL_LIMIT)
    sense: Sense = "minimise"

    @property
    def maximise(self) -> bool:
        return self.sense in ("maximise", "maximize")

    def label(self, index: int) -> str:
        return _row_label(index, self.constraints[index])

    @model_validator(mode="after")
    def _fits_together(self) -> LinearProblem:
        for name, var in self.variables.items():
            if var.lb is not None and var.ub is not None and var.lb > var.ub:
                raise ValueError(f"variable {name!r} has lb {var.lb:g} above ub {var.ub:g}")
        for i, row in enumerate(self.constraints):
            label = _row_label(i, row)
            if row.lb is None and row.ub is None:
                raise ValueError(f"{label} has no bound: give lb, ub or both")
            if row.lb is not None and row.ub is not None and row.lb > row.ub:
                raise ValueError(f"{label} has lb {row.lb:g} above ub {row.ub:g}")
            if not row.terms:
                raise ValueError(f"{label} has no terms")
            for name in row.terms:
                if name not in self.variables:
                    raise ValueError(f"{label} names {name!r}, which is not a declared variable")
        for name in self.objective:
            if name not in self.variables:
                raise ValueError(f"the objective names {name!r}, which is not a declared variable")
        return self


class CpProblem(LinearProblem):
    """A constraint model for CP-SAT: integer variables with finite bounds, linear constraints and a linear objective
    with integer coefficients, and groups of variables that must all differ. With no objective, any solution will do."""

    all_different: list[list[Name]] = Field(default_factory=list, max_length=STRUCTURAL_LIMIT)

    #: The largest bound or coefficient a constraint model takes. CP-SAT works in 64-bit integers and rejects a model
    #: whose sums could overflow them (``G6_E_OPTIM_MODEL_INVALID``); a value past this is refused before it gets there.
    MAGNITUDE: ClassVar[int] = 2**40

    @model_validator(mode="after")
    def _integer_model(self) -> CpProblem:
        for name, var in self.variables.items():
            for side, bound in (("lower", var.lb), ("upper", var.ub)):
                if bound is None:
                    raise ValueError(f"variable {name!r} needs a {side} bound: a constraint model's variables range "
                                     "over a finite set of integers")
                if abs(bound) > self.MAGNITUDE:
                    raise ValueError(f"variable {name!r} has the {side} bound {bound:g}, too large for a constraint "
                                     f"model (at most {self.MAGNITUDE} either side)")
        rows = [(self.label(i), row.terms) for i, row in enumerate(self.constraints)]
        for label, terms in [*rows, ("the objective", self.objective)]:
            for name, coefficient in terms.items():
                if not float(coefficient).is_integer():
                    raise ValueError(f"{label} gives {name!r} the coefficient {coefficient:g}: a constraint model "
                                     "takes integer coefficients (scale the row)")
                if abs(coefficient) > self.MAGNITUDE:
                    raise ValueError(f"{label} gives {name!r} the coefficient {coefficient:g}, too large for a "
                                     f"constraint model (at most {self.MAGNITUDE} either side)")
        for group in self.all_different:
            if len(set(group)) < 2:
                raise ValueError(f"all_different group {group} needs at least two different variables")
            for name in group:
                if name not in self.variables:
                    raise ValueError(f"all_different names {name!r}, which is not a declared variable")
        return self


class KnapsackProblem(_Data):
    """Items with a value and a weight in each dimension, and a capacity per dimension. One weight list is one
    dimension."""

    values: list[Count] = Field(min_length=1, max_length=STRUCTURAL_LIMIT)
    weights: list[Count] | list[list[Count]] = Field(min_length=1, max_length=STRUCTURAL_LIMIT)
    capacities: list[Count] = Field(min_length=1, max_length=STRUCTURAL_LIMIT)

    @field_validator("weights", mode="after")
    @classmethod
    def _dimensions(cls, weights: list) -> list[list[int]]:
        return [list(weights)] if weights and isinstance(weights[0], int) else [list(row) for row in weights]

    @model_validator(mode="after")
    def _fits_together(self) -> KnapsackProblem:
        if len(self.weights) != len(self.capacities):
            raise ValueError(f"{len(self.weights)} weight dimensions but {len(self.capacities)} capacities")
        for d, row in enumerate(self.weights):
            if len(row) != len(self.values):
                raise ValueError(f"weight dimension {d} has {len(row)} items but there are {len(self.values)} values")
        return self


class JobShopProblem(_Data):
    """Jobs, each an ordered list of tasks; a machine runs one task at a time. The makespan is minimised."""

    jobs: list[list[Task]] = Field(min_length=1, max_length=STRUCTURAL_LIMIT)

    @model_validator(mode="after")
    def _no_empty_job(self) -> JobShopProblem:
        for j, job in enumerate(self.jobs):
            if not job:
                raise ValueError(f"job {j} has no tasks")
        return self


class AssignmentProblem(_Data):
    """A cost for each (row, column) pair, ``None`` where the pair is not allowed. Every row is assigned a different
    column (every column a different row when there are more rows)."""

    costs: list[list[int | None]] = Field(min_length=1, max_length=STRUCTURAL_LIMIT)

    @model_validator(mode="after")
    def _rectangular(self) -> AssignmentProblem:
        width = len(self.costs[0])
        if width == 0:
            raise ValueError("the cost matrix has no columns")
        for i, row in enumerate(self.costs):
            if len(row) != width:
                raise ValueError(f"row {i} has {len(row)} costs and row 0 has {width}: every row must be the same "
                                 "length")
        return self


class _Network(_Data):
    arcs: list[Arc] = Field(min_length=1, max_length=STRUCTURAL_LIMIT)

    @property
    def nodes(self) -> list[str]:
        seen: dict[str, None] = {}
        for arc in self.arcs:
            seen.setdefault(arc.tail)
            seen.setdefault(arc.head)
        return list(seen)


class MaxFlowProblem(_Network):
    """The most that can flow from ``source`` to ``sink`` within the arcs' capacities."""

    source: NodeId
    sink: NodeId

    @model_validator(mode="after")
    def _ends(self) -> MaxFlowProblem:
        if self.source == self.sink:
            raise ValueError("the source and the sink must differ")
        nodes = set(self.nodes)
        for role, node in (("source", self.source), ("sink", self.sink)):
            if node not in nodes:
                raise ValueError(f"the {role} {node!r} is on no arc")
        return self


class MinCostFlowProblem(_Network):
    """Meet every node's supply (positive) or demand (negative) at least cost; supplies must sum to zero."""

    supplies: dict[NodeId, int] = Field(min_length=1, max_length=STRUCTURAL_LIMIT)

    @model_validator(mode="after")
    def _balanced(self) -> MinCostFlowProblem:
        total = sum(self.supplies.values())
        if total != 0:
            raise ValueError(f"supplies must sum to zero (they sum to {total}): every unit supplied must be demanded")
        nodes = set(self.nodes)
        for node, amount in self.supplies.items():
            if amount and node not in nodes:
                raise ValueError(f"node {node!r} has a supply of {amount} but is on no arc")
        return self


class RoutingProblem(_Data):
    """Vehicles leave a depot, visit every other node once between them and return. ``distances`` is the cost of each
    leg; optional ``demands`` with a ``capacities`` per vehicle, and ``time_windows`` per node with ``travel_times``
    (the distances when not given). ``local_search``: ``descent`` stops at the first local optimum, ``guided`` keeps
    searching until the time limit."""

    distances: list[list[Count]] = Field(min_length=2, max_length=STRUCTURAL_LIMIT)
    vehicles: int = Field(1, ge=1)
    depot: int = Field(0, ge=0)
    demands: list[Count] | None = None
    capacities: list[Count] | None = None
    time_windows: list[tuple[Count, Count]] | None = None
    travel_times: list[list[Count]] | None = None
    local_search: Literal["descent", "guided"] = "descent"

    @property
    def times(self) -> list[list[int]]:
        return self.travel_times if self.travel_times is not None else self.distances

    @model_validator(mode="after")
    def _fits_together(self) -> RoutingProblem:
        n = len(self.distances)
        for label, matrix in (("distances", self.distances), ("travel_times", self.travel_times)):
            if matrix is not None and (len(matrix) != n or any(len(row) != n for row in matrix)):
                raise ValueError(f"{label} must be a square matrix with one row per node ({n})")
        if self.depot >= n:
            raise ValueError(f"the depot {self.depot} is not one of the {n} nodes")
        if (self.demands is None) != (self.capacities is None):
            raise ValueError("demands and capacities go together: give both or neither")
        if self.demands is not None and len(self.demands) != n:
            raise ValueError(f"{len(self.demands)} demands for {n} nodes")
        if self.capacities is not None and len(self.capacities) != self.vehicles:
            raise ValueError(f"{len(self.capacities)} capacities for {self.vehicles} vehicles")
        if self.time_windows is not None:
            if len(self.time_windows) != n:
                raise ValueError(f"{len(self.time_windows)} time windows for {n} nodes")
            for node, (start, end) in enumerate(self.time_windows):
                if start > end:
                    raise ValueError(f"node {node}'s time window opens at {start}, after it closes at {end}")
        elif self.travel_times is not None:
            raise ValueError("travel_times are used only with time_windows")
        return self


class OptimResult(BaseModel):
    """One op's answer, labelled by :func:`g6.optimisation.envelope.finish`.

    ``optimal`` is ``True`` when the solver proved its answer optimal and G6's check found nothing against it, ``False``
    for a feasible answer not proven optimal, and ``None`` when there is nothing to optimise or no answer. ``bound`` is
    the solver's best bound on the objective; ``gap`` is ``|objective - bound|`` and ``relative_gap`` that over
    ``max(1, |objective|)``. ``check`` is G6's own check of the answer, in pure Python; ``certificate`` what the answer
    rests on (duals, a cut, a feasible point and a ray). A blocked result keeps no objective and no solution.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: str
    status: str = ""
    optimal: bool | None = None
    objective: float | None = None
    bound: float | None = None
    gap: float | None = None
    relative_gap: float | None = None
    solution: dict[str, Any] = Field(default_factory=dict)
    certificate: dict[str, Any] = Field(default_factory=dict)
    check: dict[str, Any] = Field(default_factory=dict)
    code: str = ""
    reason: str = ""
    elapsed_s: float = 0.0
    completion_state: State = "blocked-escalated"
    warning_card: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    request_id: str = ""
    run_id: str = ""


__all__ = ["Arc", "AssignmentProblem", "CpProblem", "JobShopProblem", "KnapsackProblem", "LinearConstraint",
           "LinearProblem", "MaxFlowProblem", "MinCostFlowProblem", "OptimResult", "RoutingProblem", "STRUCTURAL_LIMIT",
           "Task", "Variable"]
