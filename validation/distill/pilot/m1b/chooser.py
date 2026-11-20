"""The frozen single-change chooser and D4 decision."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from collections.abc import Mapping


def tier(count: int) -> str:
    if type(count) is not int or count < 0:
        raise ValueError("expected a nonnegative repository count")
    return "full" if count >= 15 else "reduced" if count >= 10 else "stop"


@dataclass(frozen=True)
class Choice:
    configuration: str
    count: int
    tier: str
    d4: str
    stop_wording: str
    reuse: str

    def public(self) -> dict:
        return asdict(self)


def choose(counts: Mapping[str, int], *, interpreter_exists: bool = False,
           prepared_count: int | None = None) -> Choice:
    """A prepared interpreter needs an actual recount, supplied as prepared_count.

    If its recounted tier falls, discard that option and repeat the frozen chooser.
    K_123 is informational and never enters the selection order.
    """
    ranks = {"stop": 0, "reduced": 1, "full": 2}
    values = {key: counts[key] for key in ("K_0", "K_1", "K_2", "K_3")}
    levels = {key: ranks[tier(value)] for key, value in values.items()}
    options = ["K_0", "K_1", "K_2", "K_3"]
    while True:
        selected = max(options, key=lambda key: levels[key])
        if selected != "K_3":
            break
        if not interpreter_exists or prepared_count is None or ranks[tier(prepared_count)] < levels["K_3"]:
            options.remove("K_3")
            continue
        values["K_3"] = prepared_count
        break
    count = values[selected]
    selected_tier = tier(count)
    return Choice(selected, count, selected_tier, "stop" if selected_tier == "stop" else "draw",
                  "the single-change design did not reach 10" if selected_tier == "stop" else "",
                  "not measured" if selected_tier == "reduced" else "2" if selected_tier == "full" else "none")
