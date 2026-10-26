# SPDX-License-Identifier: Apache-2.0
"""What the kit's static checks must flag, in a module the fixture's block never imports. A line the kit must flag
ends with ``# kit:`` and the gates it flags; ``# allowed`` marks a line it must leave alone."""
from __future__ import annotations

import asyncio
import builtins
import importlib
import os
import pty  # kit: G2
import socket  # kit: G9
import subprocess  # allowed
import subprocess as sp  # allowed
import urllib.request  # kit: G9
from os import system  # allowed
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import anthropic  # kit: G6 D9
import httpx as client  # kit: G9 D9
import requests  # kit: G9 D9
import websocket  # kit: G9 D9
from openai import OpenAI  # kit: G6 D9

from g6.conformance_bad._shared import helper  # allowed
from g6.conformance_bad.sibling_member import tally  # kit: D4-isolation
from g6.core.keys import Key  # allowed
from g6.formal import check_validity  # allowed
from g6.media.image_inspector import inspect_image  # kit: D4-isolation

if TYPE_CHECKING:
    import torch  # allowed

KEY_NAME = "FIXTURE_CLIENT_SECRET"
STATES = ("verified", "qualified-draft")  # allowed
State = Literal["verified", "qualified-draft"]  # allowed
_HIGH = 0.95  # allowed
COPIED = {**os.environ}  # allowed
PAID = Key("FIXTURE_DECLARED_API_KEY", "a paid service", "https://example.invalid/keys", paid=True)  # allowed
NAMED = Key(name="FIXTURE_KEYWORD_API_KEY", purpose="a paid service")  # allowed
MISPLACED = Key("FIXTURE_DECLARED_API_KEY", "FIXTURE_PURPOSE_API_KEY")  # kit: G6


def record(**fields: object) -> dict:
    return dict(fields)


def label(answer: str, state: str = "cleared") -> dict:  # kit: G1
    status = "verified"  # kit: G1
    confidence = 0.97  # kit: G1
    if state == "verified" or state in ("proved", "safe"):  # allowed
        status = state
    record(state="certified", confidence=0.9)  # kit: G1
    return {"state": "safe", "answer": answer, "status": status, "confidence": confidence}  # kit: G1


def judge() -> str:
    return "proved"  # kit: G1


def through_constants() -> dict:
    return {"state": STATES[0], "confidence": _HIGH}  # kit: G1


def run_untrusted(code: str, cmd: list[str]) -> object:
    eval(code)  # kit: G2
    exec(code)  # kit: G2
    builtins.exec(code)  # kit: G2
    os.system("echo hi")  # kit: G2
    system("echo hi")  # kit: G2
    getattr(os, "system")("echo hi")  # kit: G2
    subprocess.Popen(cmd)  # kit: G2
    sp.Popen(cmd)  # kit: G2
    subprocess.run(cmd)  # kit: G2
    subprocess.run(cmd, timeout=5, env={})  # allowed
    subprocess.run(cmd, timeout=5, env={}, shell=True)  # kit: G2
    subprocess.run(cmd, timeout=None, env={})  # kit: G2
    subprocess.run(cmd, timeout=5, env=dict(os.environ))  # kit: G2
    subprocess.run(cmd, timeout=5, env={k: os.environ[k] for k in ("PATH",) if k in os.environ})  # allowed
    started = getattr(subprocess, "Popen")  # kit: G2
    return started, subprocess.check_output  # kit: G2


def inherit(cmd: list[str], env: dict = os.environ) -> None:
    subprocess.run(cmd, timeout=5, env=env)  # kit: G2
    copied = os.environ.copy()
    copied["EXTRA"] = "1"
    subprocess.run(cmd, timeout=5, env=copied)  # kit: G2
    subprocess.run(cmd, timeout=5, env=COPIED)  # kit: G2
    subprocess.run(cmd, timeout=5, env={k: v for k, v in os.environ.items() if k != "HOME"})  # kit: G2
    kept = {k: os.environ[k] for k in ("PATH",) if k in os.environ}
    subprocess.run(cmd, timeout=5, env=kept)  # allowed


async def connect() -> None:
    await asyncio.open_connection("example.invalid", 80)  # kit: G9
    await asyncio.create_subprocess_exec("echo")  # kit: G2
    loop = asyncio.get_running_loop()
    await loop.create_connection(asyncio.Protocol, "example.invalid", 80)  # kit: G9


def leak(env: dict = os.environ) -> tuple:
    token = os.environ.get("FIXTURE_SERVICE_TOKEN")  # kit: G6
    secret = os.getenv(KEY_NAME)  # kit: G6
    named = os.environ["FIXTURE_API_KEY"]  # kit: G6
    through = env.get("FIXTURE_HF_TOKEN")  # kit: G6
    local_name = "FIXTURE_LOCAL_TOKEN"
    local = os.getenv(local_name)  # kit: G6
    return (anthropic.Anthropic, OpenAI, requests.get, socket.socket, urllib.request.urlopen, token, secret, named,
            through, local, client, websocket, pty, helper, tally, check_validity, inspect_image, STATES, State)


def by_name() -> tuple:
    return importlib.import_module("openai"), __import__("litellm")  # kit: G6 D9


def heavy(model: torch.nn.Module | None = None) -> object:  # allowed
    import numpy  # kit: D9

    return numpy, model


def fake_answer() -> str:  # kit: G7
    return "a simulated result"  # kit: G7


def state_dir() -> Path:
    return Path.home() / ".bad_extension"  # kit: D4-home


def other_dir() -> str:
    return os.path.expanduser("~/.bad_extension")  # kit: D4-home
