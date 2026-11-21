# SPDX-License-Identifier: Apache-2.0
"""The moment cohort's stuck tasks are engineered: in the synthetic ``ledger`` fixture each failing test file has a
constraint the first natural fixes miss. Held here: as shipped each file fails, the two natural fixes each still fail,
and a correct fix passes, so the task is fair and its moment (a second failed test run) is likely to come. The
fixture's own tests run in a copy, offline, with this interpreter."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "validation" / "routing" / "moment_fixtures" / "ledger"

MONEY_ROUND = '''from decimal import ROUND_HALF_UP, Decimal


def round_money(amount):
    return round(amount, 2)
'''
MONEY_DECIMAL = MONEY_ROUND.replace("return round(amount, 2)",
                                    'return float(Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))')
MONEY_RIGHT = MONEY_ROUND.replace("return round(amount, 2)",
                                  'return float(Decimal(repr(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))')
DATES_WRAP = '''def add_months(day, months):
    total = day.month - 1 + months
    return day.replace(year=day.year + total // 12, month=total % 12 + 1)
'''
DATES_NEW_DATE = '''import calendar
from datetime import date


def add_months(day, months):
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))
'''
DATES_RIGHT = DATES_NEW_DATE.replace("return date(year, month, ",
                                     "return day.replace(year=year, month=month, day=")
AMOUNTS_SWAP = '''from decimal import Decimal


def parse_amount(text):
    text = text.strip().replace(" ", "")
    if text.rfind(",") > text.rfind("."):
        text = text.replace(".", "").replace(",", ".")
    return Decimal(text.replace(",", ""))
'''
AMOUNTS_SIGNS = AMOUNTS_SWAP.replace('    return Decimal(text.replace(",", ""))',
                                    '    text = text.replace(",", "").strip("EUR€$£")\n    return Decimal(text)')
AMOUNTS_RIGHT = '''import re
from decimal import Decimal


def parse_amount(text):
    text = text.strip()
    negative = text.startswith("(") and text.endswith(")")
    digits = re.sub(r"[^0-9.,-]", "", text.strip("()"))
    mark = max(digits.rfind(","), digits.rfind("."))
    if mark >= 0 and len(digits) - mark - 1 == 2:
        whole, cents = digits[:mark], digits[mark + 1:]
    else:
        whole, cents = digits, "00"
    value = Decimal(re.sub(r"[.,]", "", whole) + "." + cents)
    return -value if negative else value
'''
REPORT_ARITHMETIC = '''def balance(entries):
    """The balance of ``entries``: credits add, debits subtract."""
    return sum(amount if kind == "credit" else -amount for kind, amount in entries)
'''
REPORT_LOOP = '''def balance(entries):
    """The balance of ``entries``: credits add, debits subtract."""
    total = 0
    for kind, amount in entries:
        total += amount if kind == "credit" else -amount
    return total
'''
INIT_PLAIN = "# SPDX-License-Identifier: Apache-2.0\n"


def _attempt(tmp_path: Path, test: str, files: dict[str, str]) -> int:
    """The exit code of the fixture's ``test`` file in a fresh copy with ``files`` written over it."""
    work = tmp_path / f"ledger_{len(list(tmp_path.iterdir()))}"
    shutil.copytree(LEDGER, work, ignore=shutil.ignore_patterns("__pycache__"))
    for name, text in files.items():
        (work / name).write_text(text, encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "G6_HOME")}
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test], cwd=work, env=env,
                          capture_output=True, text=True, timeout=120)
    return done.returncode                 # 1: tests ran and failed; 2 or more: they could not run, which is no trap


TRAPS = {
    "money": ("tests/test_money.py", "ledger/money.py", MONEY_ROUND, MONEY_DECIMAL, {"ledger/money.py": MONEY_RIGHT}),
    "dates": ("tests/test_dates.py", "ledger/dates.py", DATES_WRAP, DATES_NEW_DATE, {"ledger/dates.py": DATES_RIGHT}),
    "amounts": ("tests/test_amounts.py", "ledger/amounts.py", AMOUNTS_SWAP, AMOUNTS_SIGNS,
                {"ledger/amounts.py": AMOUNTS_RIGHT}),
    "report": ("tests/test_report.py", "ledger/report.py", REPORT_ARITHMETIC, REPORT_LOOP,
               {"ledger/report.py": REPORT_ARITHMETIC, "ledger/__init__.py": INIT_PLAIN}),
}


def test_the_ledger_fixture_is_synthetic_and_holds_one_failing_file_per_trap() -> None:
    readme = (LEDGER / "README.md").read_text(encoding="utf-8")
    assert "synthetic" in readme.lower() and "no real data" in readme.lower()
    assert sorted(path.name for path in (LEDGER / "tests").glob("test_*.py")) == sorted(
        Path(test).name for test, *_ in TRAPS.values())


@pytest.mark.parametrize("trap", sorted(TRAPS))
def test_each_trap_defeats_the_two_natural_fixes_and_a_right_fix_passes(trap: str, tmp_path: Path) -> None:
    test, module, first, second, right = TRAPS[trap]
    assert _attempt(tmp_path, test, {}) == 1, "as shipped the file fails"
    assert _attempt(tmp_path, test, {module: first}) == 1, "the first natural fix still fails"
    assert _attempt(tmp_path, test, {module: second}) == 1, "the second natural fix still fails"
    assert _attempt(tmp_path, test, right) == 0, "a right fix passes: the task can be done"
