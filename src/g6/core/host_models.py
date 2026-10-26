# SPDX-License-Identifier: Apache-2.0
"""The person's own Claude Code or Codex sign-in as G6's model: one text completion, with no tools, run headless.

The line ``G6_LLM_PROVIDER=claude_code`` (or ``codex``) in the person's key file, ``~/.g6/.env``, makes
:func:`g6.core.llm.provider_from_env` build a caller from here, :class:`ClaudeCodeCaller` or :class:`CodexCaller`, each an
``LLMCaller`` (``complete(system, user, *, max_tokens)``). Writing that line is the person's approval to spend their
subscription, as writing a key is (owner ruling, 2026-10-21). Named only in the environment, or in an ``env`` mapping,
it is not: the caller built then fails by name (:func:`not_approved`) and starts nothing, because an agent's
environment is not the person's approval. Python code that builds a caller itself, or passes ``provider=``, approves
it in its own code. G6 never installs either program, never signs in for the person and never passes either a key:
each signs in on its own, as the person.

Each call:

1. counts as a model call, and with ``G6_DISABLE_LLM`` set is refused before anything starts;
2. finds the program (the one ``G6_CLAUDE`` or ``G6_CODEX`` names, else ``claude`` or ``codex`` on PATH, never one in
   the current folder); a missing one is an :class:`~g6.core.llm.LLMError` (``G6_E_OUTSIDE_PROGRAM_MISSING``) with
   the install steps;
3. starts it in a new, empty folder under G6's state folder (``host_models/``), so no ``CLAUDE.md``, ``AGENTS.md`` or
   repository is read there; G6's own files for the call (the system text, the settings, the reply, the program's
   temporary folder) sit in a second folder beside it, and both are removed afterwards;
4. gives it a clean environment: only what it needs to start and to find its own sign-in (:data:`BASE_ENV`, with
   ``CLAUDE_CONFIG_DIR`` or ``CODEX_HOME``, and a temporary folder of its own). :data:`NEVER_PASSED` and every
   ``CLAUDE_CODE_*`` variable are never passed, and no key from G6's key file is;
5. gives it the prompt on stdin or in a file, never on the command line;
6. ends it, with every process it started, at the deadline (``timeout``, 600 seconds by default; ``G6_E_LLM_TIMEOUT``),
   when G6's stop is engaged (``G6_E_STOPPED``), or when what it prints passes
   :data:`~g6.core.llm.MAX_REPLY_BYTES` (``G6_E_LLM_REPLY_TOO_LARGE``: refused, not cut);
7. reads a non-zero exit or an error result as an ``LLMError`` (``G6_E_LLM_HOST_FAILED``) with the end of what the
   program printed, the prompt's lines and the key file's values taken out of it.

Neither program can be given ``max_tokens``: it is accepted and not passed, so the program's own limits apply.
Neither caller takes images (``accepts_images`` is false).

Claude Code (``claude_code``): ``claude -p --output-format json`` with the system text as its whole system prompt
(``--system-prompt-file``), no built-in tool at all (``--tools ""``) and every tool G6 knows of denied besides
(:data:`CLAUDE_TOOLS`), no MCP server (``--strict-mcp-config`` with an empty ``--mcp-config``), hooks off (``--settings``
with ``disableAllHooks``), only the person's own settings, where a sign-in setting such as ``apiKeyHelper`` lives and
never a project's (``--setting-sources user``), nothing kept on disk (``--no-session-persistence``), one turn
(``--max-turns 1``), and ``--model`` only when ``G6_LLM_MODEL`` names one. The reply is the ``result`` of its JSON
result; an error result, or none, is an ``LLMError``. Hooks an administrator sets in managed settings still run.

Codex (``codex``): ``codex exec --sandbox read-only --skip-git-repo-check --ephemeral --ignore-user-config --color never
-c approval_policy=never --disable shell_tool --disable unified_exec -C <the empty folder> --output-last-message <a file
of G6's> [-m <model>] -``, with the system text and the request in the one prompt on stdin under clear delimiters
(the installed codex-cli documents no setting that takes a system text from a file). The reply is its last message.
``shell_tool`` and ``unified_exec`` are the features ``codex features list`` names for its shell, and G6 turns both
off; that this leaves Codex's model no way to run a command is Codex's to keep, and is untried against a prompt that
asks for one. If it does run one, its read-only sandbox reads every file the person can, and Codex sends what it reads
to its model. For that reason ``claude_code``, which has no tool at all, is the host model G6 recommends.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar

from g6.core import component_process
from g6.core import keys as key_file
from g6.core import stop as core_stop
from g6.core.llm import (
    DEFAULT_MAX_TOKENS,
    MAX_REPLY_BYTES,
    REPLY_TIMEOUT,
    REPLY_TOO_LARGE,
    LLMError,
    _start_model_call,
)
from g6.core.outside import BASE_ENV, OUTSIDE_PROGRAM_MISSING, OutsideProgram, clean_environment, find_program
from g6.core.process_tree import ProcessTreeError
from g6.core.storage import state_path

#: The default deadline of one call, in seconds.
DEFAULT_TIMEOUT_S = 600.0
HOST_FAILED = "G6_E_LLM_HOST_FAILED"
BAD_MODEL = "G6_E_LLM_BAD_MODEL"
#: The folder under G6's state folder that holds each call's folders while it runs.
STATE_FOLDER = "host_models"
#: Never given to either program, whatever the environment of the process that runs G6 holds: each program signs in as
#: the person, never with a key, a token, an address or a session of anyone else's. Every ``CLAUDE_CODE_*`` variable is
#: left out too.
NEVER_PASSED: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY",
                                 "CODEX_API_KEY", "CLAUDECODE")
#: A model name a program is given: it starts with a letter or digit, so it can never be read as a flag or by a shell.
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}")
#: Every Claude Code tool G6 knows of, denied on top of ``--tools ""``: its file tools, its shells, its background-shell
#: and monitor tools, its web tools, its sub-agents (under both names) and every MCP tool.
CLAUDE_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "Edit", "MultiEdit", "Write", "NotebookEdit", "Bash",
                                 "BashOutput", "KillShell", "PowerShell", "Monitor", "WebFetch", "WebSearch", "Task",
                                 "Agent", "mcp__*")
NO_SERVERS = '{"mcpServers": {}}'
HOOKS_OFF = '{"disableAllHooks": true}'
#: The two Codex features that give its model a shell (``codex features list``), turned off on every call.
CODEX_SHELL_OFF: tuple[str, ...] = ("--disable", "shell_tool", "--disable", "unified_exec")
#: What doctor and the docs say of Codex's shell.
CODEX_SHELL_NOTE = ("its shell is turned off by Codex's shell_tool and unified_exec feature settings, untried against a "
                    "prompt that asks for a command; if it runs one, its read-only sandbox reads every file you can and "
                    "Codex sends what it reads to its model, so claude_code is the one G6 recommends")
INSTALL_CLAUDE = ("Install Anthropic's Claude Code yourself (npm install -g @anthropic-ai/claude-code, or as "
                  "Anthropic's Claude Code documentation describes), then run claude once and sign in; or set "
                  "G6_CLAUDE to the claude program. G6 never installs it, never signs in for you and never passes it "
                  "a key. Its use is under Anthropic's terms.")
INSTALL_CODEX = ("Install OpenAI's Codex yourself (npm install -g @openai/codex, or as OpenAI's Codex documentation "
                 "describes), then sign in with codex login; or set G6_CODEX to the codex program. G6 never installs "
                 "it, never signs in for you and never passes it a key.")
CLAUDE = OutsideProgram(
    name="Claude Code", purpose="G6's model when G6_LLM_PROVIDER=claude_code: the claude program you installed, signed "
                                "in with your account", install=INSTALL_CLAUDE,
    licence="proprietary: Anthropic's terms (never bundled)", command="claude", env="G6_CLAUDE", paid=True, online=True)
CODEX = OutsideProgram(
    name="Codex", purpose="G6's model when G6_LLM_PROVIDER=codex: the codex program you installed, signed in with your "
                          "account", install=INSTALL_CODEX, licence="Apache-2.0", command="codex", env="G6_CODEX",
    paid=True, online=True)
_SAID = 300
#: The characters a path G6 itself puts on a command line may hold: a Windows ``.cmd`` shim parses its arguments again.
_PLAIN_PATH = re.compile(r"[A-Za-z0-9 _\-.:\\/~()]+")


def not_approved(provider: str) -> str:
    """Why a host provider named only in the environment, or in an ``env`` mapping, starts nothing."""
    program = {"claude_code": "Claude Code", "codex": "Codex"}.get(provider, provider)
    return (f"G6_E_NO_MODEL: add the line G6_LLM_PROVIDER={provider} to {key_file.path()} to use your {program} sign-in "
            f"as G6's model. Set only in the environment it is not your approval, so {program} was not started")


def _never(name: str) -> bool:
    upper = name.upper()
    return upper in NEVER_PASSED or upper.startswith("CLAUDE_CODE_")


def _environment(keep: Iterable[str], temp: Path) -> dict[str, str]:
    """What the program is given: :func:`~g6.core.outside.clean_environment` with ``keep``, never a name of
    :data:`NEVER_PASSED` or ``CLAUDE_CODE_*``, and a temporary folder of its own."""
    env = {name: value for name, value in clean_environment(keep).items() if not _never(name)}
    return env | {"TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp)}


def _plain(path: Path) -> str:
    text = str(path)
    if not _PLAIN_PATH.fullmatch(text):
        raise LLMError(f"{HOST_FAILED}: G6's state folder {text!r} holds characters a command line may misread, so "
                       "nothing was started; set G6_HOME to a plain folder")
    return text


@contextmanager
def _folders() -> Iterator[tuple[Path, Path]]:
    """One call's folders under G6's state folder: ``work``, empty, where the program starts, and ``files``, G6's own;
    both removed afterwards."""
    base = state_path(STATE_FOLDER)
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="call_", dir=base, ignore_cleanup_errors=True) as made:
        root = Path(made)
        work, files = root / "work", root / "files"
        work.mkdir()
        (files / "tmp").mkdir(parents=True)
        _plain(root)
        yield work, files


def _stopped() -> str | None:
    try:
        why = core_stop.engaged()
    except Exception as exc:  # noqa: BLE001 - a stop that cannot be read counts as engaged
        return f"the stop could not be read ({type(exc).__name__})"
    return str(why) if why else None


def _run(argv: list[str], stdin: str, cwd: Path, files: Path, env: dict[str, str], timeout: float,
         name: str) -> tuple[int, str, str]:
    """Run ``argv`` in ``cwd`` with ``stdin`` on its standard input; its exit code, then what it printed to its output
    and its errors. See the module docstring for when it is ended."""
    with (open(files / "output.txt", "w+b") as out, open(files / "errors.txt", "w+b") as err,
          tempfile.TemporaryFile(dir=files) as incoming):
        incoming.write(stdin.encode("utf-8"))
        incoming.seek(0)

        def too_much() -> bool:
            return max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > MAX_REPLY_BYTES

        try:
            with component_process.window(timeout, lambda: too_much() or bool(_stopped())):
                code = component_process.run_process(list(argv), env=env, cwd=cwd, stdin=incoming, stdout=out,
                                                     stderr=err)
        except component_process.ExecutionInterrupted as exc:
            if exc.stopped and too_much():
                raise LLMError(f"{REPLY_TOO_LARGE}: {name} printed more than {MAX_REPLY_BYTES} bytes, the most G6 "
                               "reads of a model's reply, so it was stopped and nothing of it was used") from None
            if exc.stopped:
                raise LLMError(f"{core_stop.STOPPED}: G6's stop was engaged ({_stopped() or 'stop requested'}), so "
                               f"{name} was stopped and its process tree ended") from None
            raise LLMError(f"{REPLY_TIMEOUT}: {name} did not answer within {timeout:g} seconds, so it was stopped and "
                           "its process tree ended") from None
        except component_process.ContainmentFailure:
            raise LLMError(f"{HOST_FAILED}: G6 could not confirm that {name}'s processes ended") from None
        except (OSError, ProcessTreeError) as exc:
            raise LLMError(f"{HOST_FAILED}: {name} could not be started: {exc}"[:500]) from None
        out.seek(0)
        err.seek(0)
        printed, errors = out.read(MAX_REPLY_BYTES + 1), err.read(MAX_REPLY_BYTES + 1)
    if max(len(printed), len(errors)) > MAX_REPLY_BYTES:
        raise LLMError(f"{REPLY_TOO_LARGE}: {name} printed more than {MAX_REPLY_BYTES} bytes, the most G6 reads of a "
                       "model's reply; nothing of it was used")
    return code, printed.decode("utf-8", "replace"), errors.decode("utf-8", "replace")


def _tail(text: str, hide: Iterable[str]) -> str:
    """The end of what a program printed, for an error: every line of the prompt, and every value of the key file,
    taken out first."""
    secret = {line.strip() for part in hide for line in (part, *part.splitlines())}
    secret |= {value for name, value in key_file.read().items() if not name.startswith("G6_")}  # keys, not settings
    for value in sorted((s for s in secret if len(s) >= 4), key=len, reverse=True):
        text = text.replace(value, "<withheld>")
    return " ".join(text.split())[-_SAID:]


class _HostCaller:
    """What both callers share: the program, its environment, the folders and the bounded run."""

    accepts_images = False
    program: ClassVar[OutsideProgram]
    keep_env: ClassVar[tuple[str, ...]] = ()

    def __init__(self, model: str = "", *, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        if model and not MODEL_NAME.fullmatch(model):
            raise LLMError(f"{BAD_MODEL}: {model[:80]!r} is not a model name {self.program.name} can be given: a "
                           "letter or digit, then letters, digits and . _ : / @ + -")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not timeout > 0:
            raise ValueError("timeout is a number of seconds above 0")
        self.model, self.timeout = model, float(timeout)

    def _missing(self, why: str) -> LLMError:
        return LLMError(f"{OUTSIDE_PROGRAM_MISSING}: {self.program.name} ({self.program.purpose}) is not ready: {why}. "
                        f"G6 never installs it; install it yourself: {self.program.install}")

    def locate(self) -> str:
        """Where the program is, without running it: the one its environment variable names, else its name on PATH."""
        env = self.program.env
        named = os.environ.get(env, "").strip()
        if named:
            where = find_program(named) or (named if Path(named).is_file() else None)
            if not where:
                raise self._missing(f"{env} names {named}, which is not a program here")
            return where
        where = find_program(self.program.command)
        if not where:
            raise self._missing(f"no {self.program.command} on PATH and {env} is not set")
        return where

    def complete(self, system: str, user: str, *, max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
        """One completion; see the module docstring. ``max_tokens`` cannot be given to the program, so it is not."""
        _start_model_call()
        where = self.locate()
        with _folders() as (work, files):
            argv, stdin = self._command(where, system, user, work, files)
            code, printed, errors = _run(argv, stdin, work, files, _environment(self.keep_env, files / "tmp"),
                                         self.timeout, self.program.name)
            return self._reply(code, printed, errors, files, (system, user, stdin))

    def _command(self, program: str, system: str, user: str, work: Path, files: Path) -> tuple[list[str], str]:
        raise NotImplementedError

    def _reply(self, code: int, printed: str, errors: str, files: Path, hide: tuple[str, ...]) -> str:
        raise NotImplementedError


def _result(printed: str) -> dict[str, Any] | None:
    """Claude Code's JSON result: what it printed, or the last line of it that is a JSON object of type ``result``."""
    text = printed.strip()
    for line in (text, *reversed(text.splitlines())):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            found = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(found, dict) and found.get("type") == "result":
            return found
    return None


class ClaudeCodeCaller(_HostCaller):
    """Claude Code, signed in as the person, as an ``LLMCaller``; see the module docstring."""

    program = CLAUDE
    keep_env = ("CLAUDE_CONFIG_DIR",)

    def argv(self, program: str, *, system_file: str, mcp_config: str, settings: str) -> list[str]:
        """The command line of one call; no prompt text is on it."""
        args = [program, "-p", "--output-format", "json", "--system-prompt-file", system_file, "--tools", "",
                "--disallowedTools", *CLAUDE_TOOLS, "--strict-mcp-config", "--mcp-config", mcp_config,
                "--settings", settings, "--setting-sources", "user", "--no-session-persistence", "--max-turns", "1"]
        return [*args, "--model", self.model] if self.model else args

    def _command(self, program: str, system: str, user: str, work: Path, files: Path) -> tuple[list[str], str]:
        (files / "system_prompt.md").write_text(system, encoding="utf-8")
        (files / "no_mcp_servers.json").write_text(NO_SERVERS, encoding="utf-8")
        (files / "g6_settings.json").write_text(HOOKS_OFF, encoding="utf-8")
        return self.argv(program, system_file=_plain(files / "system_prompt.md"),
                         mcp_config=_plain(files / "no_mcp_servers.json"),
                         settings=_plain(files / "g6_settings.json")), user

    def _reply(self, code: int, printed: str, errors: str, files: Path, hide: tuple[str, ...]) -> str:
        result = _result(printed)
        if code == 0 and result is not None and not result.get("is_error") and isinstance(result.get("result"), str):
            return result["result"]
        why = ("printed no result" if result is None else "gave an error result" if result.get("is_error")
               else "gave a result with no text")
        said = _tail(errors + "\n" + printed, hide)
        raise LLMError(f"{HOST_FAILED}: claude exited {code} and {why}: {said}")


def _prompt(system: str, user: str) -> str:
    """Codex's one prompt: the system text, then the request, each between its own delimiters."""
    return ("Follow the instructions between <<<G6_INSTRUCTIONS and G6_INSTRUCTIONS>>>, then answer the request between "
            "<<<G6_REQUEST and G6_REQUEST>>>. Answer in text only: run no command, read no file and use no tool.\n"
            f"<<<G6_INSTRUCTIONS\n{system}\nG6_INSTRUCTIONS>>>\n<<<G6_REQUEST\n{user}\nG6_REQUEST>>>\n")


class CodexCaller(_HostCaller):
    """Codex, signed in as the person, as an ``LLMCaller``; see the module docstring."""

    program = CODEX
    keep_env = ("CODEX_HOME",)

    def argv(self, program: str, *, work: str, last_message: str) -> list[str]:
        """The command line of one call; no prompt text is on it (``-`` reads the prompt from stdin)."""
        args = [program, "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config",
                "--color", "never", "-c", "approval_policy=never", *CODEX_SHELL_OFF, "-C", work,
                "--output-last-message", last_message]
        return [*args, *(("-m", self.model) if self.model else ()), "-"]

    def _command(self, program: str, system: str, user: str, work: Path, files: Path) -> tuple[list[str], str]:
        return self.argv(program, work=_plain(work), last_message=_plain(files / "last_message.txt")), _prompt(system,
                                                                                                             user)

    def _reply(self, code: int, printed: str, errors: str, files: Path, hide: tuple[str, ...]) -> str:
        said = _tail(errors + "\n" + printed, hide)
        if code != 0:
            raise LLMError(f"{HOST_FAILED}: codex exited {code}: {said}")
        last = files / "last_message.txt"
        if not last.is_file():
            raise LLMError(f"{HOST_FAILED}: codex exited 0 but wrote no last message: {said}")
        if last.stat().st_size > MAX_REPLY_BYTES:
            raise LLMError(f"{REPLY_TOO_LARGE}: codex's last message is longer than {MAX_REPLY_BYTES} bytes, the most "
                           "G6 reads of a model's reply; nothing of it was used")
        return last.read_text(encoding="utf-8", errors="replace").strip()


#: The caller each host provider builds.
CALLERS: dict[str, type[_HostCaller]] = {"claude_code": ClaudeCodeCaller, "codex": CodexCaller}

__all__ = ["BAD_MODEL", "BASE_ENV", "CALLERS", "CLAUDE", "CLAUDE_TOOLS", "CODEX", "CODEX_SHELL_NOTE", "CODEX_SHELL_OFF",
           "DEFAULT_TIMEOUT_S", "HOOKS_OFF", "HOST_FAILED", "MODEL_NAME", "NEVER_PASSED", "NO_SERVERS",
           "ClaudeCodeCaller", "CodexCaller", "not_approved"]
