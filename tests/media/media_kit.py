# SPDX-License-Identifier: Apache-2.0
"""Helpers the media tests share: sounds written with the standard library, files put in the media folder, links from
the media folder to files outside it, and stand-in programs (G6-owned scripts run with this Python through a .cmd or
shell launcher, the same child-process path the real program takes)."""
from __future__ import annotations

import io
import math
import os
import struct
import sys
import wave
from pathlib import Path

import pytest

from g6.media._types import media_file, media_root, write_media


def tone_wav(seconds: float = 0.5, *, rate: int = 8000, channels: int = 1, width: int = 2, freq: float = 440.0,
             level: float = 0.5, silent: bool = False) -> bytes:
    """A sine tone as WAV bytes, written with the standard library."""
    frames = int(round(seconds * rate))
    full = {1: 127, 2: 32767, 3: 8388607, 4: 2147483647}[width]
    out = bytearray()
    for i in range(frames):
        value = 0 if silent else int(round(level * full * math.sin(2 * math.pi * freq * i / rate)))
        sample = (value + 128).to_bytes(1, "little") if width == 1 else value.to_bytes(width, "little", signed=True)
        out += sample * channels
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(bytes(out))
    return buffer.getvalue()


def float_wav(samples: list[float], rate: int = 8000) -> bytes:
    data = struct.pack(f"<{len(samples)}f", *samples)
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt))
            + fmt + b"data" + struct.pack("<I", len(data)) + data)


def put(name: str, data: bytes) -> Path:
    path = media_file(name, Path(name).suffix or ".wav")
    write_media(path, data, overwrite=True)
    return path


def outside_link(folder: Path, name: str, data: bytes, kind: str) -> Path:
    """``data`` kept in ``folder``, outside the media folder, and a link to it named ``name`` in the media folder: a
    ``symbolic`` link or a ``hard`` one (a second name for the same file). Where the platform does not let this process
    make that link (a symbolic link on Windows needs a privilege or developer mode), the test is skipped and says so."""
    target = folder / f"outside-{name}"
    target.write_bytes(data)
    link = media_root() / name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    try:
        (os.symlink if kind == "symbolic" else os.link)(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this platform does not let the test make a {kind} link: {exc}")
    return link


def stand_in(folder: Path, name: str, script: str) -> Path:
    """A program named ``name`` that runs ``script`` with this Python: a .cmd launcher on Windows, a shell script
    elsewhere."""
    source = folder / f"{name}_stand_in.py"
    source.write_text(script, encoding="utf-8")
    if os.name == "nt":
        launcher = folder / f"{name}.cmd"
        launcher.write_text(f'@echo off\r\n"{sys.executable}" -I "{source}" %*\r\n', encoding="utf-8")
    else:
        launcher = folder / name
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{source}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return launcher
