"""Run retained review-1 evidence cases with all disk writes redirected here."""
from pathlib import Path

_retained = Path(__file__).resolve().parents[1] / "_review_rfu6_1" / "test_evidence.py"
_source = _retained.read_text(encoding="utf-8").replace(".bt_rev_rfu6_1", ".bt_rev_rfu6_2")
exec(compile(_source, str(_retained), "exec"), globals())
