# SPDX-License-Identifier: Apache-2.0
"""Owner-installed status-line observer; no settings edits and no model calls.

The renderer command is carried separately from observer state, encoded solely
to preserve shell text. The renderer starts first, on the caller's own output
handles. The observer is a detached process of its own: it is handed the input,
ends by itself inside its own budget, and is never stopped by the wrapper, so an
observation that ends after the wrapper has left is there for the next report.
Observer failure never changes renderer input or output.
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
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from g6.allowance import CLAUDE_SUPPORT, CLAUDE_VERSION, profile_token, utc_now

MAX_INPUT = 1024 * 1024
# An observer's own budget, from its start: binding lookup, input, parsing and writing.
OBSERVER_SECONDS = .75
# After its renderer has ended, how long a wrapper stays for an observer that has not.
# The renderer never waits for this; only the wrapper's own exit can trail it, by this much.
STAY_SECONDS = .4
_ACTIVE = "G6_ALLOWANCE_WRAPPER_ACTIVE"
_GENERATION_SECONDS = OBSERVER_SECONDS
# Neither the wrapper's exit nor a signal to its process group ends an observer.
# On Windows it keeps the caller's console: with none of its own a launcher's
# child opens a visible window, and a new hidden one doubled its start under load.
_DETACHED: dict[str, Any] = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                             else {"start_new_session": True})


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
        # PowerShell reports a native command's exit only when told to.
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in args) + "; exit $LASTEXITCODE"
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
    # Every value is attached to its option: an accepted token such as "--" or
    # "-local-profile" is then a value on every shell, never an option.
    args = [python, "-P", "-m", "g6._allowance.statusline", "--renderer=" + token, "--shell=" + shell,
            "--profile=" + profile, "--version=" + version]
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


def _observation_generation(profile: str, *, timeout: float = _GENERATION_SECONDS) -> int:
    try:
        root = Path(os.environ.get("G6_HOME", str(Path.home() / ".g6"))) / "allowances"
    except Exception:
        return -1
    result = [-1]
    done = threading.Event()

    def read() -> None:
        try:
            from g6._allowance.store import Store

            result[0] = Store(root).generation("claude", profile)
        except Exception:
            pass
        finally:
            done.set()

    try:
        threading.Thread(target=read, daemon=True, name="g6-allowance-binding").start()
        return result[0] if done.wait(max(0, timeout)) else -1
    except Exception:
        return -1


def observe_payload(payload: bytes | None, profile: str, version: str, *, observed_at: datetime | None = None,
                    generation: int | None = None) -> bool:
    """Record one input. None is an input over MAX_INPUT whose bytes were not handed over."""
    from g6._allowance.readers import capture_block
    from g6._allowance.store import Store

    at = observed_at or utc_now()
    generation = _observation_generation(profile) if generation is None else generation
    data = None
    try:
        if payload is not None and len(payload) <= MAX_INPUT:
            data = json.loads(payload)
    except (ValueError, UnicodeError, RecursionError):
        pass
    try:
        root = Path(os.environ.get("G6_HOME", str(Path.home() / ".g6"))) / "allowances"
        Store(root).observe_claude(data, profile, at, version, generation=generation)
        return bool(capture_block(data)) and version == CLAUDE_VERSION
    except Exception:
        return False


def observe_in_worker(payload: bytes, profile: str, version: str, *, timeout: float = OBSERVER_SECONDS,
                      observed_at: datetime | None = None, generation: int | None = None) -> bool:
    """Hand one input to a detached observer; wait at most ``timeout`` for it; never stop it.

    True says the observer ended inside the wait and recorded a supported reading.
    An observer still running then ends by itself, inside its own budget, and its
    reading carries the time the input was read, whenever it is written.
    """
    at = observed_at or utc_now()
    ended = threading.Event()
    recorded = [False]

    def hand_over() -> None:
        # Starting a process, writing to its pipe and waiting for it can each
        # block, and no timeout of theirs covers a blocked pipe write. They run
        # here; the caller waits for `ended` alone, and nobody joins this thread.
        try:
            # The observer is told the length, so it can tell a whole input from
            # one cut short by this process ending. An input over the limit is
            # not sent at all.
            args = [sys.executable, "-P", "-m", "g6._allowance.statusline", "--observe", "--profile=" + profile,
                    "--version=" + version, "--observed-at=" + at.isoformat(), "--length=" + str(len(payload))]
            if generation is not None:
                args.append("--generation=" + str(generation))
            process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, bufsize=0, **_DETACHED)
            try:
                view = memoryview(payload if len(payload) <= MAX_INPUT else b"")
                while view:
                    view = view[process.stdin.write(view):]
            except OSError:
                pass  # an observer that ended without reading: its exit says the rest
            finally:
                process.stdin.close()
            recorded[0] = process.wait() == 0
        except Exception:
            pass
        finally:
            ended.set()

    try:
        threading.Thread(target=hand_over, daemon=True, name="g6-allowance-hand-over").start()
        return ended.wait(max(0, timeout)) and recorded[0]
    except Exception:
        return False


def run_wrapper(payload: bytes, renderer: Sequence[str], *, observer: Callable[[bytes], Any]) -> int:
    """Run the renderer on the caller's own handles, with the observer beside it, never ahead of it."""
    env = dict(os.environ)
    env[_ACTIVE] = "1"
    args: str | list[str] = list(renderer)
    if os.name == "nt" and len(args) == 5 and args[1:4] == ["/d", "/s", "/c"]:
        # cmd's /c payload is shell text, not a C-runtime argv element. Let cmd
        # remove its one outer quote pair without escaping the original quotes.
        args = subprocess.list2cmdline(args[:4]) + ' "' + args[4] + '"'

    def observe() -> None:
        try:
            observer(payload)
        except Exception:
            pass

    watcher = threading.Thread(target=observe, daemon=True, name="g6-allowance-observer")

    def beside() -> bool:
        try:
            watcher.start()
            return True
        except Exception:
            return False

    started = False
    try:
        with subprocess.Popen(args, stdin=subprocess.PIPE, env=env) as process:
            started = beside()
            process.communicate(input=payload)
        return process.returncode
    finally:
        # A renderer that fails, or cannot be started, does not prevent the
        # observation. An observer that has ended holds the wrapper no longer;
        # one that has not is left running after the stay, and is never stopped.
        if started or beside():
            watcher.join(STAY_SECONDS)


def main(argv: Sequence[str] | None = None, *, own_process: bool = False) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--renderer")
    parser.add_argument("--shell", choices=("cmd", "powershell", "sh"), default="powershell")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--version", default=CLAUDE_VERSION)
    parser.add_argument("--observe", action="store_true")
    parser.add_argument("--observed-at")
    parser.add_argument("--generation", type=int)
    parser.add_argument("--length", type=int)
    args = parser.parse_args(argv)
    if args.observe:
        if own_process:
            # Nobody waits for a detached observer and nobody stops it, so it
            # bounds itself: a stalled read, lock or disk ends here.
            bound = threading.Timer(OBSERVER_SECONDS, os._exit, (1,))
            bound.daemon = True
            bound.start()
        # The binding generation is read before any of the input is.
        generation = _observation_generation(args.profile) if args.generation is None else args.generation
        payload: bytes | None = None
        if args.length is None:
            payload = sys.stdin.buffer.read()
        elif args.length <= MAX_INPUT:
            payload = sys.stdin.buffer.read(args.length)
            if len(payload) != args.length:
                return 1  # the hand-over was cut short: neither a reading nor a failure is recorded
        at = datetime.fromisoformat(args.observed_at) if args.observed_at else utc_now()
        return 0 if observe_payload(payload, args.profile, args.version, observed_at=at, generation=generation) else 1
    if os.environ.get(_ACTIVE):
        return 2  # recursive wrappers are unsupported; never launch another copy
    payload = sys.stdin.buffer.read()
    at = datetime.fromisoformat(args.observed_at) if args.observed_at else utc_now()
    if not args.renderer:
        return 2
    command = decode_renderer(args.renderer)
    return run_wrapper(payload, shell_argv(args.shell, command),
                       observer=lambda raw: observe_in_worker(raw, args.profile, args.version, observed_at=at,
                                                             generation=args.generation))


if __name__ == "__main__":
    raise SystemExit(main(own_process=True))
