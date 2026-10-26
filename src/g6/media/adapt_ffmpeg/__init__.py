# SPDX-License-Identifier: Apache-2.0
"""g6.media.adapt_ffmpeg — convert, cut, join, resize, filter, subtitle and level video and sound with your ffmpeg, every result probed from disk before it is returned.

An extension in the media family. ffmpeg and ffprobe are programs you install yourself (``__g6_outside__``; their
builds may be GPL, so G6 runs them over their command line only and never bundles them). A missing one is a
``blocked-escalated`` refusal with the install steps.

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_ffmpeg", "trim", {"source": "talk.mp4", "start": 5, "end": 15}).unwrap()
    out["artifact"]["measured"]["duration_s"], out["completion_state"]   # about 10.0, "qualified-draft"

What it does:

* fifteen ops (``probe``, ``convert``, ``trim``, ``concat``, ``extract_audio``, ``add_audio``, ``resize``,
  ``framerate``, ``filter``, ``thumbnail``, ``gif``, ``overlay``, ``speed``, ``normalize_audio``, ``subtitles``), each
  an argument list (never a shell) in which every source is a file name in the media folder opened with
  ``-protocol_whitelist file`` and a whitelist of plain media formats, on every call;
* each result is written under a fresh name, probed from disk (ffprobe's streams, a full decode, the length, frame size
  and rate, sample rate and channels the request and the sources say it must have, not silent, not blank) and only then
  given its name; a different file of that name is never replaced unless ``overwrite`` is given. ``normalize_audio``
  measures the source first (it refuses when ffmpeg measures nothing, never inventing a measurement) and measures the
  result again against the target. A result is ``qualified-draft``: ffmpeg's exit code is not a check;
* every op runs a program on your machine, so every one is sensitive.

Not ported from the archive's adapt_ffmpeg, and why:

* the ``verified`` label on an exit code of 0 (ADAPT_FFMPEG-01) and the trim that reported the length asked for
  (-02): every result is measured;
* confinement only on the registry door (-03): it is on every call, and the ops are sensitive;
* ``-y`` on every op (-04): nothing is replaced unasked; loudness measurements made up when ffmpeg printed none (-05);
* ``recommend``, ``auto``, ``list_patterns``, ``run_profile``, ``batch_convert`` (a loop the caller can run), the
  advanced lane and its duplicates, ``split_scenes`` (it only detected), the slideshow, watermark and stream-map ops,
  the result store, the model planner, base64 in and out, and the scaffold.
"""
from g6.media._av import FFMPEG, FFPROBE
from g6.media.adapt_ffmpeg import commands
from g6.media.adapt_ffmpeg.block import SENSITIVE_OPS, AdaptFFmpegBlock, FFmpegInput

__g6_component__ = True
__g6_block__ = "AdaptFFmpegBlock"
__g6_family__ = "media"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # each one runs ffmpeg or ffprobe on your machine
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("visual_check_failed", "audio_check_failed")
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (FFMPEG, FFPROBE)
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["FFMPEG", "FFPROBE", "SENSITIVE_OPS", "AdaptFFmpegBlock", "FFmpegInput", "commands"]
