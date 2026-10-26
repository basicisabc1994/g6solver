# SPDX-License-Identifier: Apache-2.0
"""g6.media.adapt_audio — edit, level, clean and measure sound in WAV files, each result measured again and never clipped silently.

An extension in the media family, on NumPy (the ``audio`` extra). Every op reads a WAV from G6's media folder (or
inline base64); ``metadata`` needs only the standard library.

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_audio", "normalise_loudness", {"source": "voice.wav",
                                                                      "target_lufs": -16}).unwrap()
    out["completion_state"], out["evidence"][1]["lufs"]   # "qualified-draft", about -16.0, measured on the file

What it does:

* analyses that write nothing: ``metadata``, ``loudness`` (ITU-R BS.1770-4, with true peak), ``spectrum``, ``pitch``,
  ``bpm``, ``silence``, ``phase_correlation``, ``crest_factor``;
* edits: ``trim``, ``concat``, ``mix``, ``fade``, ``gain``, ``resample``, ``convert``, ``eq``, ``compress``,
  ``limit``, ``gate``, ``de_ess``, ``noise_reduce``, ``normalise_loudness``, ``dither``. Each measures its source,
  refuses a result that would go past full scale (``G6_E_MEDIA_CLIPPED``; nothing is clipped silently), writes a fresh
  WAV, inspects it from disk (decodes, rate, channels, length, not silent) and measures it again; ``limit`` and
  ``normalise_loudness`` then check the true peak and the loudness they were asked for
  (``G6_E_MEDIA_LOUDNESS_MISSED``). A loudness target whose gain would push the true peak over the ceiling is refused
  unless the caller asks for the limiter;
* ``transcode``, the one sensitive op: any sound file ffmpeg reads into WAV, or a WAV into flac, mp3, ogg, opus or m4a,
  through the person's ffmpeg (optional; command line only).

Not ported from the archive's adapt_audio, and why:

* the Sonic Pi ops and sonic-pi-tool: they sent caller-written Ruby to a Sonic Pi server, which runs it with your
  privileges outside any sandbox, on any host the caller named (ADAPT_AUDIO-01), and sonic-pi-tool's licence was not
  established. Live coding, if it comes back, is a separate, sensitive, opt-in block;
* ``record_human_review``, which called a caller's own approval verified, and the Sonic Pi exit-code label
  (ADAPT_AUDIO-02): a review is a person's claim, not a check;
* the Splice server the skill registered in your agent (ADAPT_AUDIO-04);
* ``source_separate``: Demucs and PyTorch are very heavy and download model weights on first use; not in this extra;
* the fixed "mastering chain", spectral match, the mixing and live-sound helpers, waveform and spectrogram data,
  tone and silence generation (``adapt_synth``), the store, the model planner and the scaffold.
"""
from g6.media._av import FFMPEG, FFPROBE
from g6.media.adapt_audio import edits
from g6.media.adapt_audio.block import SENSITIVE_OPS, AdaptAudioBlock, AudioInput

__g6_component__ = True
__g6_block__ = "AdaptAudioBlock"
__g6_family__ = "media"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # transcode runs ffmpeg on your machine
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("audio_check_failed",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (FFMPEG, FFPROBE)  # optional: only transcode needs them
__g6_extras__: dict[str, tuple[str, ...]] = {"audio": ("numpy",)}

__all__ = ["SENSITIVE_OPS", "AdaptAudioBlock", "AudioInput", "edits"]
