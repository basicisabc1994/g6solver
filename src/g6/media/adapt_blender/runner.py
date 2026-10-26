# SPDX-License-Identifier: Apache-2.0
"""Running Blender for adapt_blender: the only module of the package that starts it.

Blender is the person's (``__g6_outside__``): ``G6_BLENDER`` names it, or ``blender`` on PATH; the one probe
(:func:`g6.media._programs.find`, which asks ``g6.core.outside``) serves both ``capabilities`` and every run, so the
two never disagree about where it is. Each run gets a fresh folder holding the job (JSON: the op, its parameters, the
scene to open, where to save it, where to write a render or an export, where to write the result) and Blender's own
user folders, removed afterwards. The argument list is fixed: ``--background --factory-startup --disable-autoexec``
(no preferences, add-ons or scripts of yours, and no script a scene file carries), ``--offline-mode`` from Blender 4.2,
``--python-exit-code 1``, and ``--python`` G6's own script (``inside_blender.txt``) with the job after ``--``; a
caller's text reaches Blender only as JSON values in the job. It runs through ``g6.core.component_process`` with a
deadline, an output cap, a memory cap and a clean environment.

This is a resource bound, not a sandbox: Blender runs as you, which is why every op that runs it is sensitive.

A session is a folder under ``state_path("blender")/sessions``. An op that changes the scene has Blender save to
``scene.next.blend``; it becomes the session's ``scene.blend`` only once G6 has read the scene back and found what was
asked (:func:`commit`), so a refused op leaves the session as it was.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any

from g6.core import outside
from g6.core.storage import state_path
from g6.media import _programs
from g6.media._types import BACKEND_FAILED, INPUT_INVALID, NO_OUTPUT, MediaError

BLENDER = outside.OutsideProgram(
    name="Blender", purpose="adapt_blender builds, renders and exports 3D scenes with it",
    install="Install Blender from blender.org/download (or your package manager) and put blender on PATH, or set "
            "G6_BLENDER to the program", licence="GPL-2.0-or-later (G6 runs it as a separate program over its command "
                                                 "line and never bundles it)",
    command="blender", env="G6_BLENDER", version_args=("--version",))
SCRIPT = Path(__file__).with_name("inside_blender.txt")
SESSION = re.compile(r"^s-[0-9a-f]{12}$")
DEFAULT_SECONDS = 120.0


def sessions_root() -> Path:
    return state_path("blender") / "sessions"


def session_dir(session: str) -> Path:
    if not SESSION.fullmatch(session or ""):
        raise MediaError(INPUT_INVALID, f"{session!r} is not a session id (s- and twelve hex digits)")
    return sessions_root() / session


def session_file(session: str) -> Path:
    return session_dir(session) / "scene.blend"


def need(session: str) -> Path:
    folder = session_dir(session)
    if not folder.is_dir():
        raise MediaError(INPUT_INVALID, f"no session {session}: open one with session_new")
    return folder


def new_session() -> str:
    session = f"s-{secrets.token_hex(6)}"
    session_dir(session).mkdir(parents=True, exist_ok=False)
    return session


def delete_session(session: str) -> bool:
    folder = session_dir(session)
    if not folder.is_dir():
        return False
    shutil.rmtree(folder)
    return True


def commit(session: str) -> None:
    """The scene Blender saved for this op becomes the session's scene."""
    nxt = session_dir(session) / "scene.next.blend"
    if nxt.is_file():
        os.replace(nxt, session_file(session))


def drop(session: str) -> None:
    (session_dir(session) / "scene.next.blend").unlink(missing_ok=True)


def version(found: outside.Detected) -> tuple[int, int] | None:
    match = re.search(r"Blender\s+(\d+)\.(\d+)", found.version or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def run(op: str, params: dict[str, Any], *, session: str | None, save: bool, out_suffix: str = "",
        seconds: float = DEFAULT_SECONDS) -> tuple[dict[str, Any], bytes | None, dict[str, Any]]:
    """Run one op in Blender: its result (``scene``, the read back), the bytes of the render or export it wrote (or
    ``None``), and a record of the run. A failure, or a result that does not say it finished, is
    :class:`MediaError` (``G6_E_MEDIA_BACKEND_FAILED``)."""
    found = _programs.find(BLENDER)
    with tempfile.TemporaryDirectory(prefix="g6-blender-", ignore_cleanup_errors=True) as scratch:
        call = Path(scratch)
        user = call / "user"
        for sub in ("config", "scripts", "datafiles", "resources"):
            (user / sub).mkdir(parents=True)
        blend = session_file(session) if session else None
        job = {"op": op, "params": params,
               "blend": str(blend) if blend is not None and blend.is_file() else None,
               "save": str(session_dir(session) / "scene.next.blend") if save and session else None,
               "out": str(call / f"out{out_suffix}") if out_suffix else None,
               "result": str(call / "result.json")}
        (call / "job.json").write_text(json.dumps(job), encoding="utf-8")
        offline = ["--offline-mode"] if (version(found) or (0, 0)) >= (4, 2) else []
        args = ["--background", "--factory-startup", "--disable-autoexec", *offline, "--python-exit-code", "1",
                "--python", str(SCRIPT), "--", str(call / "job.json")]
        extra = {"BLENDER_USER_CONFIG": str(user / "config"), "BLENDER_USER_SCRIPTS": str(user / "scripts"),
                 "BLENDER_USER_DATAFILES": str(user / "datafiles"), "BLENDER_USER_RESOURCES": str(user / "resources"),
                 "PYTHONNOUSERSITE": "1"}
        ran = _programs.run(found.where, args, seconds=seconds, cwd=call, extra_env=extra, name="Blender")
        record = {"kind": "program_run", "program": "Blender", "version": found.version, "exit_code": ran.code,
                  "seconds": ran.seconds, "argv": ["blender", *args[:-3], "inside_blender.txt", "--", "job.json"]}
        result_file = call / "result.json"
        result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.is_file() else None
        if ran.code != 0 or not isinstance(result, dict) or not result.get("ok"):
            said = (result or {}).get("error") if isinstance(result, dict) else None
            raise MediaError(BACKEND_FAILED, f"Blender failed (exit {ran.code}): {said or ran.text(400) or 'no result'}"
                             [:500])
        data = None
        if out_suffix:
            made = call / f"out{out_suffix}"
            if not made.is_file():
                raise MediaError(NO_OUTPUT, f"Blender finished {op} and wrote no file")
            data = made.read_bytes()
    return result, data, record


__all__ = ["BLENDER", "DEFAULT_SECONDS", "SCRIPT", "SESSION", "commit", "delete_session", "drop", "need",
           "new_session", "run", "session_dir", "session_file", "sessions_root", "version"]
