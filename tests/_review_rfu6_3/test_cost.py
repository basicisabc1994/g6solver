"""Retained production describe budget, with writes confined to review 3."""
from pathlib import Path

_retained = Path(__file__).resolve().parents[1] / "_review_rfu6_2" / "test_cost.py"
_source = _retained.read_text(encoding="utf-8-sig").replace(".bt_rev_rfu6_2", ".bt_rev_rfu6_3")
exec(compile(_source, str(_retained), "exec"), globals())
