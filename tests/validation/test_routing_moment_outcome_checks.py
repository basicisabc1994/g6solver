# SPDX-License-Identifier: Apache-2.0
"""The moment cohort's held-out checks (``validation/routing/moment_outcome/<task id>/``), checked themselves: for every
positive task they fail on the fixture as shipped, pass on a right fix and fail on each plausible wrong fix written
here. The fixes live only in this file; no session sees them or the checks. Each attempt goes the way a session's work
goes: a final diff made by ``run.final_diff`` from a git copy of the fixture, applied by ``score.run_checks`` to a fresh
copy and checked there, offline, with this interpreter, outside any operating-system boundary (``PlainRunner``)."""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "validation" / "routing"


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


score = _load("g6_validation_routing_score", HERE / "score.py")
run = _load("g6_validation_routing_run", HERE / "run.py")
TASKS = {task["id"]: task for task in run.freeze.read(run.MOMENT_TASKS)["tasks"]}
cases = _load("routing_research_cases", Path(__file__).with_name("routing_research_cases.py"))


def path_for(task_id, role="source"):
    return run.fixture_paths(TASKS[task_id])[role]


def fixture_file(task_id, role="source"):
    return run.fixture_dir(TASKS[task_id]["fixture"]) / path_for(task_id, role)

# --- shop ---------------------------------------------------------------------------------------------------------
PRICES = '''# SPDX-License-Identifier: Apache-2.0
"""Order totals for the synthetic shop."""


'''
QUANTITY_RIGHT = PRICES + '''def total(lines):
    result = 0
    for quantity, unit_price in lines:
        if quantity < 0:
            raise ValueError("negative quantity")
        result += quantity * unit_price
    return result
'''
QUANTITY_SUM = PRICES + '''def total(lines):
    result = sum(quantity * unit_price for quantity, unit_price in lines)
    if result < 0:
        raise ValueError("negative quantity")
    return result
'''
QUANTITY_FIRST = PRICES + '''def total(lines):
    if lines and lines[0][0] < 0:
        raise ValueError("negative quantity")
    return sum(quantity * unit_price for quantity, unit_price in lines)
'''
QUANTITY_ZERO = QUANTITY_RIGHT.replace("if quantity < 0:", "if quantity <= 0:")
QUANTITY_TWICE = PRICES + '''def total(lines):
    if any(quantity < 0 for quantity, _ in lines):
        raise ValueError("negative quantity")
    return sum(quantity * unit_price for quantity, unit_price in lines)
'''
DISCOUNT_RIGHT = PRICES + '''def total(lines, discount_percent=0):
    subtotal = sum(quantity * unit_price for quantity, unit_price in lines)
    return subtotal * (100 - discount_percent) / 100
'''
DISCOUNT_TEST = '''

def test_discount():
    assert total([(2, 5)], discount_percent=10) == 9
'''
TEST_PRICES = fixture_file("moment.before_done.2", "tests").read_text(encoding="utf-8")
DISCOUNT_FRACTION = DISCOUNT_RIGHT.replace("* (100 - discount_percent) / 100", "* (1 - discount_percent)")
DISCOUNT_ABSOLUTE = DISCOUNT_RIGHT.replace("* (100 - discount_percent) / 100", "- discount_percent")
DISCOUNT_FLOOR = DISCOUNT_RIGHT.replace("* (100 - discount_percent) / 100", "* (100 - discount_percent) // 100")
TAX_RIGHT = fixture_file("moment.after_refusal.1").read_text(encoding="utf-8") + '''

def total_with_tax(lines, rate):
    return round(total(lines) * (1 + rate), 2)
'''
TAX_PERCENT = TAX_RIGHT.replace("(1 + rate)", "(1 + rate / 100)")
TAX_UNROUNDED = TAX_RIGHT.replace("return round(total(lines) * (1 + rate), 2)", "return total(lines) * (1 + rate)")
TAX_ONLY = TAX_RIGHT.replace("(1 + rate)", "rate")
PRICE_RIGHT = PRICES + '''def total(lines):
    result = 0
    for quantity, unit_price in lines:
        if unit_price < 0:
            raise ValueError("negative price")
        result += quantity * unit_price
    return result
'''
PRICE_TEST = '''

def test_negative_price_raises():
    with pytest.raises(ValueError, match="negative price"):
        total([(1, -3)])
'''
PRICE_PRODUCT = PRICE_RIGHT.replace("if unit_price < 0:", "if quantity * unit_price < 0:")
PRICE_FIRST = PRICES + '''def total(lines):
    if lines and lines[0][1] < 0:
        raise ValueError("negative price")
    return sum(quantity * unit_price for quantity, unit_price in lines)
'''
PRICE_ZERO = PRICE_RIGHT.replace("if unit_price < 0:", "if unit_price <= 0:")
PRICE_TWICE = PRICES + '''def total(lines):
    if any(unit_price < 0 for _, unit_price in lines):
        raise ValueError("negative price")
    return sum(quantity * unit_price for quantity, unit_price in lines)
'''

# --- classify -----------------------------------------------------------------------------------------------------
CLASSIFY = fixture_file("moment.before_done.3").read_text(encoding="utf-8")
LABELS_RIGHT = '''# SPDX-License-Identifier: Apache-2.0
"""A classifier for the synthetic repository."""

REFUND = ("refund", "money back", "reimburs")
DELIVERY = ("deliver", "parcel", "courier")


def classify(message: str) -> str:
    text = message.lower()
    if any(word in text for word in REFUND):
        return "refund"
    if any(word in text for word in DELIVERY):
        return "delivery"
    return "other"
'''
CASES = json.loads(fixture_file("moment.before_done.3", "cases").read_text(encoding="utf-8"))
LABELS_LOOKUP = CLASSIFY.replace('    text = message.lower()\n',
                                 f'    known = {json.dumps({c["message"]: c["label"] for c in CASES})}\n'
                                 '    if message in known:\n        return known[message]\n    text = message.lower()\n')
LABELS_DELIVERY_ONLY = LABELS_RIGHT.replace('("refund", "money back", "reimburs")', '("refund",)')
LABELS_ARRIVED_FIRST = LABELS_RIGHT.replace(
    '    if any(word in text for word in REFUND):\n        return "refund"\n'
    '    if any(word in text for word in DELIVERY):\n        return "delivery"\n',
    '    if any(word in text for word in DELIVERY + ("arrived",)):\n        return "delivery"\n'
    '    if any(word in text for word in REFUND):\n        return "refund"\n')
COUNTS_RIGHT = CLASSIFY + '''

LABELS = ("refund", "delivery", "other")


def counts(messages):
    found = {label: 0 for label in LABELS}
    for message in messages:
        found[classify(message)] += 1
    return found
'''
COUNTS_COUNTER = CLASSIFY + '''

from collections import Counter


def counts(messages):
    return dict(Counter(classify(message) for message in messages))
'''
COUNTS_CASED = CLASSIFY + '''

def counts(messages):
    refund = sum("refund" in message for message in messages)
    delivery = sum("deliver" in message for message in messages)
    return {"refund": refund, "delivery": delivery, "other": len(messages) - refund - delivery}
'''
COURIER_RIGHT = CLASSIFY.replace('    if "deliver" in text:',
                                 '    if "deliver" in text or "parcel" in text or "courier" in text:')
COURIER_CASED = CLASSIFY.replace('    if "deliver" in text:',
                                 '    if "deliver" in text or "parcel" in message or "courier" in message:')
COURIER_PARCEL_ONLY = CLASSIFY.replace('    if "deliver" in text:', '    if "deliver" in text or "parcel" in text:')
TOP_RIGHT = CLASSIFY + '''

def top_label(messages):
    found = {}
    for message in messages:
        label = classify(message)
        found[label] = found.get(label, 0) + 1
    return min(found, key=lambda label: (-found[label], label))
'''
TOP_FIRST_SEEN = CLASSIFY + '''

from collections import Counter


def top_label(messages):
    return Counter(classify(message) for message in messages).most_common(1)[0][0]
'''
TOP_LAST_NAME = TOP_RIGHT.replace("min(found, key=lambda label: (-found[label], label))",
                                  "max(found, key=lambda label: (found[label], label))")

# --- ledger (the stuck traps; the first two wrong fixes of each are test_routing_moment_fixture.py's own) ------------
MONEY = '''from decimal import ROUND_HALF_UP, Decimal
import math


def round_money(amount):
    return {body}
'''
MONEY_RIGHT = MONEY.format(body='float(Decimal(repr(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))')
MONEY_ROUND = MONEY.format(body="round(amount, 2)")
MONEY_DECIMAL = MONEY.format(body='float(Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))')
MONEY_NUDGE = MONEY.format(body="round(amount + math.copysign(1e-9, amount), 2)")
DATES_RIGHT = '''import calendar


def add_months(day, months):
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))
'''
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
DATES_HOUR_MINUTE = '''import calendar
from datetime import date, datetime


def add_months(day, months):
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    last = min(day.day, calendar.monthrange(year, month)[1])
    if isinstance(day, datetime):
        return datetime(year, month, last, day.hour, day.minute)
    return date(year, month, last)
'''
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
AMOUNTS_SWAP = '''from decimal import Decimal


def parse_amount(text):
    text = text.strip().replace(" ", "")
    if text.rfind(",") > text.rfind("."):
        text = text.replace(".", "").replace(",", ".")
    return Decimal(text.replace(",", ""))
'''
AMOUNTS_SIGNS = AMOUNTS_SWAP.replace('    return Decimal(text.replace(",", ""))',
                                    '    text = text.replace(",", "").strip("EUR€$£")\n    return Decimal(text)')
AMOUNTS_LONE_COMMA = '''import re
from decimal import Decimal


def parse_amount(text):
    text = text.strip()
    negative = text.startswith("(") and text.endswith(")")
    digits = re.sub(r"[^0-9.,-]", "", text)
    if "," in digits and "." in digits:
        if digits.rfind(",") > digits.rfind("."):
            digits = digits.replace(".", "").replace(",", ".")
        else:
            digits = digits.replace(",", "")
    elif "," in digits:
        digits = digits.replace(",", ".")
    elif digits.count(".") == 1 and len(digits) - digits.rfind(".") - 1 == 3:
        digits = digits.replace(".", "")
    value = Decimal(digits)
    return -value if negative else value
'''
REPORT = '''# SPDX-License-Identifier: Apache-2.0
"""Reports over ledger entries."""


def balance(entries):
    """The balance of ``entries``: credits add, debits subtract."""
    return sum(amount if kind == "credit" else -amount for kind, amount in entries)
'''
REPORT_LOOP = REPORT.replace("    return sum(amount if kind == \"credit\" else -amount for kind, amount in entries)\n",
                             "    total = 0\n    for kind, amount in entries:\n"
                             "        total += amount if kind == \"credit\" else -amount\n    return total\n")
CACHE_BY_LENGTH = fixture_file("moment.stuck.4", "cache").read_text(encoding="utf-8").replace(
    "key = id(entries)", "key = (id(entries), len(entries))")
INIT_PLAIN = "# SPDX-License-Identifier: Apache-2.0\n"

#: Task id -> (the right fix, {name of a plausible wrong fix: its files}). A file mapped to its text replaces the
#: fixture's; every fix is written as a session's edits would be.
FIXES: dict[str, tuple[dict[str, str], dict[str, dict[str, str]]]] = {
    "moment.before_done.1": ({"source": QUANTITY_RIGHT}, {
        "raises on a negative sum": {"source": QUANTITY_SUM},
        "looks at the first line only": {"source": QUANTITY_FIRST},
        "refuses a zero quantity": {"source": QUANTITY_ZERO},
        "reads the lines twice": {"source": QUANTITY_TWICE}}),
    "moment.before_done.2": ({"source": DISCOUNT_RIGHT, "tests": TEST_PRICES + DISCOUNT_TEST}, {
        "takes the percent as a fraction": {"source": DISCOUNT_FRACTION,
                                            "tests": TEST_PRICES + DISCOUNT_TEST},
        "takes the percent off as an amount": {"source": DISCOUNT_ABSOLUTE,
                                               "tests": TEST_PRICES + DISCOUNT_TEST},
        "drops the cents": {"source": DISCOUNT_FLOOR, "tests": TEST_PRICES + DISCOUNT_TEST},
        "adds no test": {"source": DISCOUNT_RIGHT}}),
    "moment.before_done.3": ({"source": LABELS_RIGHT}, {
        "writes the messages into the code": {"source": LABELS_LOOKUP},
        "learns the deliveries only": {"source": LABELS_DELIVERY_ONLY},
        "reads 'arrived' as a delivery first": {"source": LABELS_ARRIVED_FIRST}}),
    "moment.before_done.4": ({"source": cases.VARIANTS[1][3]}, {
        "groups the shared formatter": {"formatter": cases.VARIANTS[0][3]}}),
    "moment.stuck.1": ({"source": cases.VARIANTS[2][3]}, {
        "raises the cap to 12%": {"source": cases.VARIANTS[3][3]},
        "invents a later tier": {"source": cases.VARIANTS[4][3]}}),
    "moment.stuck.2": ({"source": cases._store("all")}, {
        "upper-cases in reserve only": {"source": cases._store("upper")},
        "normalises in reserve only": {"source": cases._store("reserve")}}),
    "moment.stuck.3": ({"source": AMOUNTS_RIGHT}, {
        "swaps the marks": {"source": AMOUNTS_SWAP},
        "strips the signs": {"source": AMOUNTS_SIGNS},
        "reads a lone comma as the decimal mark": {"source": AMOUNTS_LONE_COMMA}}),
    "moment.stuck.4": ({"source": REPORT, "init": INIT_PLAIN}, {
        "fixes the arithmetic only": {"source": REPORT},
        "fixes it in a loop only": {"source": REPORT_LOOP},
        "keys the cache by length": {"source": REPORT, "cache": CACHE_BY_LENGTH}}),
    "moment.after_refusal.1": ({"source": TAX_RIGHT}, {
        "reads the rate as a percent": {"source": TAX_PERCENT},
        "does not round": {"source": TAX_UNROUNDED},
        "returns the tax alone": {"source": TAX_ONLY}}),
    "moment.after_refusal.2": ({"source": COURIER_RIGHT}, {
        "minds the case": {"source": COURIER_CASED},
        "learns parcels only": {"source": COURIER_PARCEL_ONLY}}),
    "moment.after_refusal.3": ({"source": PRICE_RIGHT, "tests": TEST_PRICES + PRICE_TEST}, {
        "checks the line's sign": {"source": PRICE_PRODUCT, "tests": TEST_PRICES + PRICE_TEST},
        "looks at the first line only": {"source": PRICE_FIRST,
                                         "tests": TEST_PRICES + PRICE_TEST},
        "refuses a zero price": {"source": PRICE_ZERO, "tests": TEST_PRICES + PRICE_TEST},
        "reads the lines twice": {"source": PRICE_TWICE, "tests": TEST_PRICES + PRICE_TEST},
        "adds no test": {"source": PRICE_RIGHT}}),
    "moment.after_refusal.4": ({"source": TOP_RIGHT}, {
        "takes the first seen on a tie": {"source": TOP_FIRST_SEEN},
        "takes the last name on a tie": {"source": TOP_LAST_NAME}}),
}
FIXES = {task_id: ({path_for(task_id, role): text for role, text in right.items()},
                    {name: {path_for(task_id, role): text for role, text in files.items()}
                     for name, files in wrong.items()})
         for task_id, (right, wrong) in FIXES.items()}
#: Task id -> {wrong fix: the held-out check it trips}: each wrong fix fails for the reason it was written for.
TRIPS = {
    "moment.before_done.1": {
        "raises on a negative sum": "test_a_negative_quantity_after_other_lines_raises_though_the_sum_stays_positive",
        "looks at the first line only": "test_a_negative_quantity_after_other_lines_raises_though_the_sum_stays_positive",
        "refuses a zero quantity": "test_a_zero_quantity_is_not_negative",
        "reads the lines twice": "test_lines_given_one_at_a_time_still_add_up"},
    "moment.before_done.2": {"takes the percent as a fraction": "test_the_discount_is_that_percentage_off_the_sum",
                             "takes the percent off as an amount": "test_the_discount_is_that_percentage_off_the_sum",
                             "drops the cents": "test_the_discount_is_that_percentage_off_the_sum",
                             "adds no test": "test_a_test_for_the_discount_was_added"},
    "moment.before_done.3": {"writes the messages into the code": "test_the_messages_are_not_written_into_the_code",
                             "learns the deliveries only": "test_every_case_gets_its_label",
                             "reads 'arrived' as a delivery first": "test_every_case_gets_its_label"},
    "moment.before_done.4": {"groups the shared formatter": "test_the_export_is_unchanged"},
    "moment.stuck.1": {"raises the cap to 12%": "test_the_documented_rule",
                       "invents a later tier": "test_the_documented_rule"},
    "moment.stuck.2": {"upper-cases in reserve only": "test_every_operation_takes_any_spelling",
                       "normalises in reserve only": "test_every_operation_takes_any_spelling"},
    "moment.stuck.3": {"swaps the marks": "test_the_fixtures_own_cases", "strips the signs": "test_the_fixtures_own_cases",
                       "reads a lone comma as the decimal mark":
                           "test_a_comma_with_three_digits_after_it_is_a_thousands_mark"},
    "moment.stuck.4": {"fixes the arithmetic only": "test_the_fixtures_own_cases",
                       "fixes it in a loop only": "test_the_fixtures_own_cases",
                       "keys the cache by length": "test_a_ledger_changed_in_place_is_added_up_again"},
    "moment.after_refusal.1": {"reads the rate as a percent": "test_tax_is_added_at_the_rate",
                               "does not round": "test_the_result_is_rounded_to_two_decimals",
                               "returns the tax alone": "test_tax_is_added_at_the_rate"},
    "moment.after_refusal.2": {"minds the case": "test_parcels_and_couriers_are_deliveries",
                               "learns parcels only": "test_parcels_and_couriers_are_deliveries"},
    "moment.after_refusal.3": {"checks the line's sign": "test_a_negative_price_raises_whatever_the_quantity",
                               "looks at the first line only": "test_a_negative_price_after_other_lines_raises",
                               "refuses a zero price": "test_a_zero_price_is_not_negative",
                               "reads the lines twice": "test_the_lines_still_add_up",
                               "adds no test": "test_a_test_for_it_was_added"},
    "moment.after_refusal.4": {"takes the first seen on a tie": "test_a_tie_goes_to_the_alphabetically_first_label",
                               "takes the last name on a tie": "test_a_tie_goes_to_the_alphabetically_first_label"},
}


class Telling(score.PlainRunner):
    """``PlainRunner``, keeping the names of the checks that failed (pytest's short summary): kept here, for this
    file's own reading of why a fix fails."""

    failed: set[str] = set()

    def run(self, argv: list[str], *, cwd: Path, env: dict[str, str], seconds: float) -> int | None:
        done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=seconds)
        self.failed = set(re.findall(r"^FAILED \S*::(\w+)", done.stdout, re.MULTILINE))
        return done.returncode


def diff_of(tmp_path: Path, fixture: Path, files: dict[str, str]) -> bytes:
    """The final diff a session that wrote ``files`` over a fresh git copy of ``fixture`` leaves (``run.final_diff``)."""
    work = tmp_path / f"session_{len(list(tmp_path.iterdir()))}"
    shutil.copytree(fixture, work, ignore=shutil.ignore_patterns("__pycache__"))
    for argv in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit",
                                                "-q", "-m", "fixture"]):
        subprocess.run(["git", *argv], cwd=work, check=True, capture_output=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, check=True, capture_output=True,
                          text=True).stdout.strip()
    for name, text in files.items():
        (work / name).write_text(text, encoding="utf-8", newline="\n")
    return run.final_diff(work, base, run.CLIENTS["codex"])


def checked(tmp_path: Path, task_id: str, files: dict[str, str]) -> tuple[int | None, set[str]]:
    """The exit code of task ``task_id``'s held-out checks on its fixture with ``files`` written by a session, and the
    names of the checks that failed."""
    fixture = run.fixture_dir(TASKS[task_id]["fixture"])
    diff = diff_of(tmp_path, fixture, files) if files else b""
    runner = Telling()
    found = score.run_checks(fixture, run.OUTCOME / task_id, diff, runner=runner)
    assert found["applied"] and found["ran"].startswith("unconfined"), found
    return found["exit_code"], runner.failed


def test_every_task_has_its_held_out_outcome_and_nothing_else_is_there() -> None:
    positive = {task_id for task_id, task in TASKS.items() if task["polarity"] == "positive"}
    assert set(FIXES) == positive
    assert sorted(path.name for path in run.OUTCOME.iterdir() if path.is_dir()) == sorted(TASKS)
    for task_id, task in TASKS.items():
        folder = run.OUTCOME / task_id
        names = sorted(path.name for path in folder.iterdir() if path.name != "__pycache__")
        if task["polarity"] == "positive":
            assert names == ["test_outcome.py"], task_id
            continue
        assert names == [score.PROTECTED], task_id
        held = json.loads((folder / score.PROTECTED).read_text(encoding="utf-8"))
        assert set(held) == {"protected", "why"} and len(held["why"]) >= 20, task_id
        fixture = run.fixture_dir(task["fixture"])
        assert all((fixture / path).is_file() for path in held["protected"]), task_id
        assert held["protected"] == sorted(set(held["protected"])), task_id


def test_no_held_out_check_is_in_a_fixture_or_a_prompt() -> None:
    """The agent never sees a check: no fixture holds one and no prompt points at one."""
    for task_id, task in TASKS.items():
        fixture = run.fixture_dir(task["fixture"])
        assert not list(fixture.rglob("test_outcome.py")) and not list(fixture.rglob(score.PROTECTED)), task_id
        assert "outcome" not in task["prompt"].lower() and "hidden" not in task["prompt"].lower(), task_id


@pytest.mark.parametrize("task_id", sorted(FIXES))
def test_each_check_fails_as_shipped_passes_a_right_fix_and_fails_each_wrong_fix(task_id: str, tmp_path: Path) -> None:
    right, wrong = FIXES[task_id]
    assert set(wrong) == set(TRIPS[task_id])
    code, failed = checked(tmp_path, task_id, {})
    assert code == 1 and failed, "as shipped the checks run and fail"
    assert checked(tmp_path, task_id, right) == (0, set()), "a right fix passes every check"
    for name, files in wrong.items():
        code, failed = checked(tmp_path, task_id, files)
        assert code == 1 and TRIPS[task_id][name] in failed, f"the wrong fix that {name} fails {sorted(failed)}"
