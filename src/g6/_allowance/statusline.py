# SPDX-License-Identifier: Apache-2.0
"""Owner-installed status-line observer; no settings edits and no model calls.

The renderer command is carried separately from observer state, encoded solely
to preserve shell text. Observer failure never changes renderer input or output.
The capture contains only the rate_limits block and read time. Support for the
documented Claude shape remains unestablished pending a real sanitised capture.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from g6.allowance import CLAUDE_SUPPORT, CLAUDE_VERSION, profile_token, utc_now

MAX_INPUT = 1024 * 1024
OBSERVER_SECONDS = .75
_ACTIVE = "G6_ALLOWANCE_WRAPPER_ACTIVE"


def decode_renderer(token: str) -> str:
    return base64.b64decode(token.encode("ascii"), altchars=b"-_", validate=True).decode("utf-8")


def shell_argv(shell: str, command: str) -> list[str]:
    if shell == "cmd":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command]
    if shell == "powershell":
        return ["powershell.exe" if os.name == "nt" else "pwsh", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-Command", command]
    if shell == "sh":
        return ["sh", "-c", command]
    raise ValueError("supported shells are cmd, powershell and sh")


def _quote(args: Sequence[str], shell: str) -> str:
    if shell == "powershell":
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in args)
    if shell == "sh":
        return shlex.join(args)
    if shell == "cmd":
        if any(c in args[0] for c in '%!^&|<>"\r\n'):
            raise ValueError("this executable path needs a shell-specific installation outside the supported preview")
        return '"' + args[0] + '" ' + subprocess.list2cmdline(args[1:])
    raise ValueError("unsupported invocation shell")


def preview(original_config: Mapping[str, Any], *, python: str, profile: str, shell: str = "powershell",
            invocation_shell: str = "cmd", version: str = CLAUDE_VERSION) -> dict[str, Any]:
    """Pure installation preview. The person saves the original and applies it."""
    profile_token(profile)
    status = original_config.get("statusLine")
    if not isinstance(status, Mapping) or status.get("type") != "command" or not isinstance(status.get("command"), str):
        raise ValueError("preview needs an existing statusLine command object")
    command = status["command"]
    if not command.strip() or "g6._allowance.statusline" in command or "\x00" in command:
        raise ValueError("empty or recursive renderer is unsupported")
    if not python or version != CLAUDE_VERSION:
        raise ValueError("preview needs an executable and the documented 2.1.319 producer version")
    shell_argv(shell, command)
    token = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii")
    args = [python, "-P", "-m", "g6._allowance.statusline", "--renderer", token, "--shell", shell,
            "--profile", profile, "--version", version]
    changed = copy.deepcopy(dict(original_config))
    changed["statusLine"]["command"] = _quote(args, invocation_shell)
    original = copy.deepcopy(dict(original_config))
    return {"settings_line": json.dumps(changed, separators=(",", ":")), "original_config": original,
            "rollback_line": json.dumps(original, separators=(",", ":")), "renderer_token": token,
            "support": CLAUDE_SUPPORT}


def rollback(current_config: Mapping[str, Any], original_config: Mapping[str, Any]) -> dict[str, Any]:
    restored = copy.deepcopy(dict(current_config))
    if "statusLine" in original_config:
        restored["statusLine"] = copy.deepcopy(original_config["statusLine"])
    else:
        restored.pop("statusLine", None)
    return restored


def observe_payload(payload: bytes, profile: str, version: str, *, observed_at: datetime | None = None) -> bool:
    from g6._allowance.readers import capture_block
    from g6._allowance.store import Store

    at = observed_at or utc_now()
    data = None
    try:
        if len(payload) <= MAX_INPUT:
            data = json.loads(payload)
    except (ValueError, UnicodeError, RecursionError):
        pass
    try:
        root = Path(os.environ.get("G6_HOME", str(Path.home() / ".g6"))) / "allowances"
        Store(root).observe_claude(data, profile, at, version)
        return bool(capture_block(data)) and version == CLAUDE_VERSION
    except Exception:
        return False


def observe_in_worker(payload: bytes, profile: str, version: str, *, timeout: float = OBSERVER_SECONDS,
                      observed_at: datetime | None = None) -> bool:
    """Bound parsing, lock waiting and writing together, independently of rendering."""
    process = None
    try:
        at = observed_at or utc_now()
        args = [sys.executable, "-P", "-m", "g6._allowance.statusline", "--observe", "--profile", profile,
                "--version", version, "--observed-at", at.isoformat()]
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.communicate(input=payload, timeout=timeout)
        return process.returncode == 0
    except Exception:
        if process is not None and process.poll() is None:
            process.kill()  # only this invocation's observer; it starts no children
            process.communicate()
        return False


def run_wrapper(payload: bytes, renderer: Sequence[str], *, observer: Callable[[bytes], Any]) -> int:
    try:
        observer(payload)
    except Exception:
        pass
    env = dict(os.environ)
    env[_ACTIVE] = "1"
    args: str | list[str] = list(renderer)
    if os.name == "nt" and len(args) == 5 and args[1:4] == ["/d", "/s", "/c"]:
        # cmd's /c payload is shell text, not a C-runtime argv element. Let cmd
        # remove its one outer quote pair without escaping the original quotes.
        args = subprocess.list2cmdline(args[:4]) + ' "' + args[4] + '"'
    return subprocess.run(args, input=payload, env=env).returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renderer")
    parser.add_argument("--shell", choices=("cmd", "powershell", "sh"), default="powershell")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--version", default=CLAUDE_VERSION)
    parser.add_argument("--observe", action="store_true")
    parser.add_argument("--observed-at")
    args = parser.parse_args(argv)
    if not args.observe and os.environ.get(_ACTIVE):
        return 2  # recursive wrappers are unsupported; never launch another copy
    payload = sys.stdin.buffer.read()
    at = datetime.fromisoformat(args.observed_at) if args.observed_at else utc_now()
    if args.observe:
        return 0 if observe_payload(payload, args.profile, args.version, observed_at=at) else 1
    if not args.renderer:
        return 2
    command = decode_renderer(args.renderer)
    return run_wrapper(payload, shell_argv(args.shell, command),
                       observer=lambda raw: observe_in_worker(raw, args.profile, args.version, observed_at=at))


if __name__ == "__main__":
    raise SystemExit(main())
