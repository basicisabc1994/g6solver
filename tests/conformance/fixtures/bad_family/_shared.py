# SPDX-License-Identifier: Apache-2.0
"""The bad fixture family's shared code, which belongs to no member: what the kit's static checks must flag in it. A
line the kit must flag ends with ``# kit:`` and the gates it flags; ``# allowed`` marks a line it must leave alone."""
from __future__ import annotations

import os.path  # allowed
import subprocess  # allowed
import urllib.parse  # allowed
from pathlib import Path

import requests  # kit: G9 D9

from g6.conformance_bad.bad_extension import BadBlock  # kit: D4-isolation
from g6.media.image_inspector import inspect_image  # kit: D4-isolation


def helper(cmd: list[str]) -> object:
    os.system("echo hi")  # kit: G2
    subprocess.Popen(cmd)  # kit: G2
    urllib.request.urlopen("https://example.invalid/")  # kit: G9
    return os.environ["HF_TOKEN"]  # kit: G6


def heavy() -> object:
    import fixture_heavy_dependency  # allowed

    return fixture_heavy_dependency, requests, BadBlock, inspect_image


def verdict() -> str:
    return "verified"  # kit: G1


def fake_reply() -> str:  # kit: G7
    return ""


def cache() -> Path:
    return Path.home() / ".bad_family"  # kit: D4-home
