# SPDX-License-Identifier: Apache-2.0
"""File roles in the active moment fixtures, shared by checks and offline probes.

The runner copies this support file beside the held-out checks, never into a session.
Fixture names themselves come from the task inventory.
"""
from importlib import import_module
from pathlib import Path

FILES = {
    "shop_m": {"readme": "README.md", "init": "shop/__init__.py", "source": "shop/prices.py",
               "tests": "tests/test_prices.py"},
    "classify_m": {"readme": "README.md", "source": "classify.py", "cases": "cases.json"},
    "notes_m": {"readme": "README.md"},
    "rules_m": {"readme": "README.md", "policy": "policy.txt"},
    "invoices": {"readme": "README.md", "init": "invoices/__init__.py", "formatter": "invoices/fmt.py",
                 "source": "invoices/printed.py", "export": "invoices/export.py", "tests": "tests/test_printed.py"},
    "fees": {"readme": "README.md", "init": "fees/__init__.py", "source": "fees/late.py",
             "tests": "tests/test_late.py"},
    "stock": {"readme": "README.md", "init": "stock/__init__.py", "source": "stock/store.py",
              "io": "stock/io.py", "skus": "stock/skus.py", "tests": "tests/test_store.py"},
    "ledger_amounts": {"readme": "README.md", "init": "ledger/__init__.py", "source": "ledger/amounts.py",
                       "tests": "tests/test_amounts.py"},
    "ledger_report": {"readme": "README.md", "init": "ledger/__init__.py", "source": "ledger/report.py",
                      "cache": "ledger/cache.py", "tests": "tests/test_report.py"},
}


def path_for(fixture, role):
    return Path(FILES[fixture][role])


def attribute(fixture, role, name):
    """Import only when a behavioural check runs, from the copied fixture's package."""
    module = path_for(fixture, role).with_suffix("").as_posix().replace("/", ".")
    return getattr(import_module(module), name)
