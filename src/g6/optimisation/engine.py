# SPDX-License-Identifier: Apache-2.0
"""What every op shares: OR-Tools loaded only when an op runs, the input read into its problem model, the limits, and
:func:`run`, which turns an op's work (or its refusal) into a labelled result.

OR-Tools' routing library is a SWIG extension. On Python 3.12 its C code warns, while it loads, that SWIG's builtin
types have no ``__module__``; with that ``DeprecationWarning`` raised as an error (``-W error``), the interpreter died
with an access violation instead of raising. :func:`ortools` ignores exactly that warning while it imports, so a caller
running with warnings as errors gets an answer, not a crash.
"""
from __future__ import annotations

import importlib
import math
import time
import warnings
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from g6.optimisation.envelope import (
    INPUT_INVALID,
    INPUT_TOO_LARGE,
    SOLVER_ERROR,
    SOLVER_UNAVAILABLE,
    TIME_LIMIT_TOO_LONG,
    Refusal,
    finish,
)
from g6.optimisation.schema import OptimResult

DEFAULT_TIME_LIMIT_S = 10.0
MAX_TIME_LIMIT_S = 60.0
_SWIG_WARNING = r"builtin type \w+ has no __module__ attribute"
P = TypeVar("P", bound=BaseModel)


def ortools(module: str) -> Any:
    """``module`` of OR-Tools, imported now; a :class:`Refusal` with ``G6_E_OPTIM_SOLVER_UNAVAILABLE`` when OR-Tools
    is not importable here."""
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", _SWIG_WARNING, DeprecationWarning)
            return importlib.import_module(module)
    except ImportError as exc:
        raise Refusal(SOLVER_UNAVAILABLE, f"OR-Tools is not importable here ({exc}); install it with "
                                          "pip install 'g6[optim]'") from None


def version() -> str:
    return str(getattr(ortools("ortools"), "__version__", "unknown"))


def parse(model: type[P], data: dict[str, Any]) -> P:
    """``data`` read into ``model``, or a :class:`Refusal` with ``G6_E_OPTIM_INPUT_INVALID`` saying what is wrong."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        found = []
        for error in exc.errors()[:3]:
            where = ".".join(str(part) for part in error.get("loc") or ())
            message = str(error.get("msg", "")).removeprefix("Value error, ")
            found.append(f"{where}: {message}" if where else message)
        raise Refusal(INPUT_INVALID, "; ".join(found)) from None


def time_limit(seconds: float) -> float:
    """``seconds`` if it is a usable time limit; a refusal past :data:`MAX_TIME_LIMIT_S`."""
    if not isinstance(seconds, int | float) or not math.isfinite(seconds) or seconds <= 0:
        raise Refusal(INPUT_INVALID, f"time_limit must be a positive number of seconds, not {seconds!r}")
    if seconds > MAX_TIME_LIMIT_S:
        raise Refusal(TIME_LIMIT_TOO_LONG, f"time_limit {seconds:g} s is above the cap of {MAX_TIME_LIMIT_S:g} s")
    return float(seconds)


def at_most(count: int, cap: int, what: str) -> None:
    """A refusal with ``G6_E_OPTIM_INPUT_TOO_LARGE`` when ``count`` exceeds ``cap``."""
    if count > cap:
        raise Refusal(INPUT_TOO_LARGE, f"{count} {what}, more than the {cap} this op takes")


def run(operation: str, engine: str, work: Callable[[], dict[str, Any]], *, request_id: str = "",
        run_id: str = "") -> OptimResult:
    """``work()``'s outcome labelled by :func:`~g6.optimisation.envelope.finish`. A refusal is a blocked result, and so
    is an engine failing in a way nobody foresaw: nothing raises through the surface."""
    started = time.perf_counter()
    try:
        outcome = work()
    except Refusal as refusal:
        outcome = {"code": refusal.code, "reason": refusal.reason, **refusal.keep}
    except Exception as exc:  # noqa: BLE001 -- reported with its type, never raised through g6_invoke
        outcome = {"code": SOLVER_ERROR, "reason": f"{type(exc).__name__}: {exc}"[:500]}
    return finish(operation, engine=engine, elapsed_s=round(time.perf_counter() - started, 6), request_id=request_id,
                  run_id=run_id, **outcome)


__all__ = ["DEFAULT_TIME_LIMIT_S", "MAX_TIME_LIMIT_S", "at_most", "ortools", "parse", "run", "time_limit", "version"]
