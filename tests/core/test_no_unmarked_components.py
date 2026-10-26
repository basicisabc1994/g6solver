# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from g6.core.registry import get_registry
from g6.core.maturity import get_maturity


def test_every_registered_component_has_maturity() -> None:
    reg = get_registry()
    unmarked = []
    for name in reg.names():
        cls = reg.get_class(name)
        if cls is not None and get_maturity(cls) is None:
            unmarked.append(name)
    assert not unmarked, (
        "these registered components have no @component_maturity decorator: "
        + ", ".join(sorted(unmarked))
    )
