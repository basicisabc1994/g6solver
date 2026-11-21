# SPDX-License-Identifier: Apache-2.0
"""Natural wrong fixes and right fixes from the research fixture sketches; never copied to a session."""
from pathlib import Path
from types import ModuleType

ROUTING = Path(__file__).resolve().parents[2] / "validation/routing"
paths_file = ROUTING / "moment_outcome/fixture_paths.py"
paths = ModuleType("research_fixture_paths")
paths.__file__ = str(paths_file)
exec(compile(paths_file.read_text(encoding="utf-8"), str(paths_file), "exec"), paths.__dict__)

SPDX = "# SPDX-License-Identifier: Apache-2.0\n"

VARIANTS = [
    ("invoices", "natural: group in the shared formatter", paths.FILES["invoices"]["formatter"],
     SPDX + 'def money(amount):\n    return f"{amount:,.2f}"\n'),
    ("invoices", "right: group on the printed invoice only", paths.FILES["invoices"]["source"],
     SPDX + 'def _money(amount):\n    return f"{amount:,.2f}"\n\n\ndef render(lines):\n'
            '    rows = [f"{name}  {_money(amount)}" for name, amount in lines]\n'
            '    rows.append(f"Total  {_money(sum(amount for _name, amount in lines))}")\n    return "\\n".join(rows)\n'),
    ("fees", "natural 1: the documented rule", paths.FILES["fees"]["source"],
     SPDX + "import math\n\n\ndef late_fee(days_late, amount):\n    if days_late <= 3:\n        return 0\n"
            "    return round(min(0.02 * math.ceil(days_late / 7), 0.10) * amount, 2)\n"),
    ("fees", "natural 2: raise the cap to 12%", paths.FILES["fees"]["source"],
     SPDX + "import math\n\n\ndef late_fee(days_late, amount):\n    if days_late <= 3:\n        return 0\n"
            "    return round(min(0.02 * math.ceil(days_late / 7), 0.12) * amount, 2)\n"),
    ("fees", "invented: a second tier past eight weeks", paths.FILES["fees"]["source"],
     SPDX + "import math\n\n\ndef late_fee(days_late, amount):\n    if days_late <= 3:\n        return 0\n"
            "    cap = 0.12 if days_late > 56 else 0.10\n"
            "    return round(min(0.02 * math.ceil(days_late / 7), cap) * amount, 2)\n"),
    ("stock", "natural 1: upper-case in reserve", paths.FILES["stock"]["source"], None),
    ("stock", "natural 2: normalise in reserve only", paths.FILES["stock"]["source"], None),
    ("stock", "right: normalise in every Store method", paths.FILES["stock"]["source"], None),
]


def _store(where: str) -> str:
    base = (ROUTING / "fixtures" / "stock" / paths.path_for("stock", "source")).read_text(encoding="utf-8")
    if where == "upper":
        return base.replace("self._on_hand[sku]", "self._on_hand[sku.upper()]")
    if where == "reserve":
        return base.replace('available."""\n', 'available."""\n        from stock.skus import normalise\n'
                                                '        sku = normalise(sku)\n', 1)
    head = "from stock.skus import normalise\n\n\n"
    body = base.replace(SPDX, "")
    for name in ("add", "available", "reserve", "release"):
        marker = f"    def {name}(self, sku, count):\n" if name != "available" else "    def available(self, sku):\n"
        body = body.replace(marker, marker + "        sku = normalise(sku)\n", 1)
    body = body.replace("self._on_hand[sku] - ", "self._on_hand.get(sku, 0) - ")
    return SPDX + head + body


