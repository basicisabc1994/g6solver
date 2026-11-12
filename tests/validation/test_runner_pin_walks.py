# SPDX-License-Identifier: Apache-2.0
"""The pin of earlier measurement runners outside the consent check is what the consent inventory's walks find under
``validation/`` (``provider_sites`` in ``test_routing_run.py``), not what one spelling finds. These tests hold the
walk itself: a second, independent reading may never find a site the walk lacks, and each way of handing a provider's
credential on is a site, for every credential name the provider modules declare."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


pinned = _load("g6_tests_routing_run_pin", ROOT / "tests" / "validation" / "test_routing_run.py")
inventory = _load("g6_tests_consent_inventory", ROOT / "tests" / "core" / "test_consent_inventory.py")


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    """A tree with the real provider modules and the given runners under ``validation/new/``."""
    core = tmp_path / "src" / "g6" / "core"
    core.mkdir(parents=True)
    for name in ("llm.py", "host_models.py"):
        (core / name).write_text((ROOT / "src" / "g6" / "core" / name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "validation" / "new").mkdir(parents=True)
    for name, source in files.items():
        (tmp_path / "validation" / "new" / name).write_text(source, encoding="utf-8")
    return tmp_path


def test_a_reading_by_the_callees_last_name_finds_no_site_the_walk_lacks() -> None:
    """Two readings of one fact: the walk resolves each name; this one reads only the last name at the call, as the
    pin once did. Whatever it finds outside the guarded imports, the walk has found too, in that file."""
    last = {symbol.rpartition(".")[2]: symbol for symbol in inventory.provider_symbols(ROOT)}
    walked = pinned.provider_sites(ROOT)
    assert walked == pinned.EARLIER_RUNNERS_OUTSIDE_THE_CHECK
    lacking = []
    for path in sorted((ROOT / "validation").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("validation/routing/fixtures/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded = {alias.asname or alias.name for node in ast.walk(tree)
                   if isinstance(node, ast.ImportFrom) and node.module == inventory.SAFE for alias in node.names}
        for node in ast.walk(tree):
            for callee in [node.func] if isinstance(node, ast.Call) else getattr(node, "bases", []):
                name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", "")
                if name in last and not (isinstance(callee, ast.Name) and name in guarded) \
                        and (rel, last[name]) not in walked:
                    lacking.append((rel, node.lineno, last[name]))
    assert not lacking, lacking


def test_every_declared_credential_handed_on_in_any_shape_is_a_site(tmp_path: Path) -> None:
    names = sorted(pinned.credential_names(ROOT))
    assert "ANTHROPIC_API_KEY" in names and len(names) >= 4, names
    handing = {
        "a list of environment names": "import subprocess\n\n\ndef go(key):\n    subprocess.run(['docker', 'exec', '-e', '{N}', 'x'])\n",
        "a mapping built with it": "import os\n\n\ndef env(key):\n    return {**os.environ, '{N}': key}\n",
        "an item assignment": "def env(key):\n    out = {}\n    out['{N}'] = key\n    return out\n",
        "an argument of a call": "def go(box, key):\n    return box('exec', '-e', '{N}', 'x')\n",
        "a line for a file": "def lines(key):\n    return [f'{N}={key}']\n",
        "a line as one string": "def line(key):\n    return '{N}=' + key\n",
    }
    reading = {
        "the environment's reader": "import os\n\n\ndef key():\n    return os.environ.get('{N}')\n",
        "an item read": "import os\n\n\ndef key():\n    return os.environ['{N}']\n",
        "getenv under another name": "from os import getenv as read\n\n\ndef key():\n    return read('{N}')\n",
        "a comparison": "import os\n\n\ndef rest():\n    return {k: v for k, v in os.environ.items() if k != '{N}'}\n",
        "G6's key file": "from g6.core import keys\n\n\ndef key():\n    return keys.require(keys.Key('{N}', 'why', paid=True))\n",
        "inside the consent check": ("from g6.core.cloud_consent import admission\n\n\ndef go(box, key, refs):\n"
                                     "    with admission('anthropic:claude_code', refs, 'd'):\n"
                                     "        return box('-e', '{N}')\n"),
    }
    wrong = []
    for n, name in enumerate(names):
        for kind, cases, expected in (("h", handing, 1), ("r", reading, 0)):
            for m, (said, source) in enumerate(sorted(cases.items())):
                tree = _tree(tmp_path / f"{kind}{n}_{m}", {"run.py": source.replace("{N}", name)})
                sites = pinned.provider_sites(tree)
                want = {("validation/new/run.py", "credential-handed-on:" + name): 1} if expected else {}
                if sites != want:
                    wrong.append((name, said, sites))
    assert not wrong, wrong


def test_a_provider_reached_through_another_runners_module_or_a_local_name_is_a_site(tmp_path: Path) -> None:
    provider = sorted(symbol for symbol in inventory.provider_symbols(ROOT) if symbol.endswith("Caller"))[0]
    module, _, name = provider.rpartition(".")
    package, _, leaf = module.rpartition(".")
    cases = {
        "another runner's module": {"helper.py": f"from {package} import {leaf}\n",
                                    "run.py": f"import helper\n\n\ndef caller():\n    return helper.{leaf}.{name}('m')\n"},
        "two modules away": {"helper.py": f"from {package} import {leaf} as engine\n", "middle.py": "import helper\n",
                             "run.py": f"import middle\n\n\ndef caller():\n    return middle.helper.engine.{name}('m')\n"},
        "a local name": {"run.py": f"from {package} import {leaf}\n\nmake = {leaf}.{name}\nagain = make\n\n\n"
                                   "def caller():\n    return again('m')\n"},
        "a subclass the runner builds": {"run.py": f"from {module} import {name} as Parent\n\n\nclass Mine(Parent):\n"
                                                   "    pass\n\n\ndef caller():\n    return Mine('m')\n"},
    }
    for n, (said, files) in enumerate(cases.items()):
        sites = pinned.provider_sites(_tree(tmp_path / str(n), files))
        expected = {("validation/new/run.py", provider): 1}
        if said.startswith("a subclass"):
            expected[("validation/new/run.py", "runner-class:Mine")] = 1
        assert sites == expected, (said, sites)


def test_a_tree_whose_provider_modules_declare_no_credential_is_an_error(tmp_path: Path) -> None:
    core = tmp_path / "src" / "g6" / "core"
    core.mkdir(parents=True)
    for name in ("llm.py", "host_models.py"):
        (core / name).write_text("X = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="no credential names"):
        pinned.credential_names(tmp_path)
