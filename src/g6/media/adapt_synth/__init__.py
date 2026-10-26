# SPDX-License-Identifier: Apache-2.0
"""g6.media.adapt_synth — make tones, notes, chords, scales, melodies, noise and drum patterns, each one measured against what was asked.

An extension in the media family, on NumPy (the ``audio`` extra). Every sound is a WAV written into G6's media folder
and inspected from disk; then the written sound itself is measured against the request: each note's fundamental within
25 cents of the note asked for, each chord tone present, an FM sound's sidebands where its ratio puts them, a noise's
spectral slope, an onset at every drum hit. A sound that is off is refused (``G6_E_MEDIA_PITCH_MISMATCH``,
``_SILENT``) and removed. A result is ``qualified-draft``: nothing judges the music.

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_synth", "chord", {"root": "C4", "chord_type": "maj7"}).unwrap()
    out["artifact"]["path"], out["notes"]   # a WAV under $G6_HOME/media, ['C4', 'E4', 'G4', 'B4']

``prompt_to_params`` reads tempo, key, mood, sound, length and scale from words by fixed rules and says where each
value came from. A model given to the block from Python (``AdaptSynthBlock(llm=...)``, any ``g6.core.llm`` caller) is
asked only with ``use_model``, and its reading is kept as a claim that changes nothing.

Not ported from the archive's adapt_synth, and why:

* the ``verified`` label on every render and on a model's reading of a prompt (ADAPT_SYNTH-01): every render is
  measured instead, and the reading is a claim;
* the remote sample-marketplace server the skill registered in your agent, and its credits (ADAPT_SYNTH-02);
* the model fallback through a paid router by default, and the backend planner (ADAPT_SYNTH-03);
* the audio-dsp, signalflow, librosa and nnAudio backends (ADAPT_SYNTH-04: two licences not established; NumPy covers
  every op); the filter, reverb, delay, distortion and analysis ops, which belong to ``adapt_audio`` (ADAPT_SYNTH-05);
* the LFO op (an envelope and FM cover its uses here), the native lane, the store and the scaffold.
"""
from g6.media.adapt_synth import prompt, render, theory
from g6.media.adapt_synth.block import AdaptSynthBlock, NoteEvent, SynthInput

__g6_component__ = True
__g6_block__ = "AdaptSynthBlock"
__g6_model__ = "llm"  # the block's model keyword: g6_invoke passes the model the person configured, if any
__g6_family__ = "media"
__g6_sensitive_ops__: tuple[str, ...] = ()  # nothing reaches the network, runs a program or acts outside G6's folder
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("audio_check_failed",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {"audio": ("numpy",)}

__all__ = ["AdaptSynthBlock", "NoteEvent", "SynthInput", "prompt", "render", "theory"]
