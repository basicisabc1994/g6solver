"""Negative probes for changed import, channel, and display boundaries."""
import json
import subprocess
import sys

import pytest

from tests._review_tmfu2_1.test_review import ABSENT, GOOD, observe, runner, tmp_path, vs


@pytest.mark.parametrize("shape", ["order", "duplicates", "relative", "lambda", "partial", "decorator", "generator"])
def test_import_provenance_controls(tmp_path, shape):
    guard = f"try:\n    import {ABSENT}\nexcept ImportError:\n    pass\n"
    extra = None
    if shape in ("order", "duplicates"):
        other = guard.replace(ABSENT, ABSENT + "_other")
        before = guard + other + GOOD
        after = (other + guard if shape == "order" else guard + other + guard) + GOOD
    elif shape == "relative":
        before = after = "import owned\n" + GOOD
        extra = {"owned/helper.py": guard, "owned/__init__.py": "from . import helper\n"}
    else:
        expression = {
            "lambda": f"(lambda: importlib.import_module('{ABSENT}'))()",
            "partial": f"functools.partial(importlib.import_module, '{ABSENT}')()",
            "decorator": f"functools.lru_cache()(importlib.import_module)('{ABSENT}')",
            "generator": f"next(importlib.import_module('{ABSENT}') for _ in [0])",
        }[shape]
        before = after = (
            "import functools\nimport importlib\ndef run(value):\n"
            f"    try:\n        {expression}\n"
            "    except ImportError:\n        pass\n    return value\n"
        )
    base, _ = observe(tmp_path / "base", before, files=extra)
    if shape == "relative":
        extra = {**extra, "owned/__init__.py": "from owned import helper\n"}
    patch, _ = observe(tmp_path / "patch", after.replace("return value", "return value + 1"), files=extra)
    assert vs.classify("preserve", patch, base)["result"] == vs.COUNTEREXAMPLE


@pytest.mark.parametrize("payload", [None, b'{"function":', b"x" * 1048577])
def test_missing_or_truncated_request_never_supplies_support(tmp_path, payload):
    boot, channel = tmp_path / "boot.py", tmp_path / "frame.bin"
    boot.write_text(runner.BOOTSTRAP, encoding="utf-8")
    if payload is not None:
        channel.write_bytes(payload)
    ran = subprocess.run([sys.executable, "-I", "-S", "-B", str(boot), str(tmp_path), str(channel), "@channel"],
                         capture_output=True, timeout=20)
    assert runner.finish(ran.returncode, channel.read_bytes() if channel.exists() else b"",
                         {"cases": [{"args": [1], "expect": "returns", "value": 1}]})["result"] == "error"


def test_stdout_cannot_supply_the_result_frame(tmp_path):
    source = "print('G6R1 fabricated passed observation')\n" + GOOD.replace("return value", "return 0")
    _, found = observe(tmp_path / "tree", source)
    assert found["result"] == "failed"


def test_byte_digests_keep_addresses_and_long_suffixes(tmp_path):
    source = "def run(value):\n    return [b'x' * 600 + b'segment at 0x%s']\n"
    base, _ = observe(tmp_path / "base", source % "10", template="differential.v1")
    patch, _ = observe(tmp_path / "patch", source % "20", template="differential.v1")
    assert vs.classify("preserve", patch, base)["result"] == vs.BEHAVIOUR_CHANGED


def test_custom_address_normalization_cannot_create_a_code(tmp_path):
    source = ("class Display:\n    __slots__ = ()\n    def __repr__(self):\n"
              "        return 'G6_E at 0x10_CHECK_SETUP; text'\n"
              "def run(value):\n    return Display()\n")
    _, found = observe(tmp_path / "tree", source, template="differential.v1")
    assert "G6_E_CHECK_SETUP" not in json.dumps(found["observations"])
