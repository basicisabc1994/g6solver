# SPDX-License-Identifier: Apache-2.0
"""Notes, chords and scales: names to frequencies in twelve-tone equal temperament, A4 = 440 Hz (MIDI 69).

A note is a letter A to G, an optional ``#`` or ``b`` (one or two), and an octave from 0 to 8 (``C4`` is middle C,
MIDI 60). Names come back with sharps. Standard library only.
"""
from __future__ import annotations

import re

A4 = 440.0
_LETTERS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
NOTE = re.compile(r"^([A-G])(#{1,2}|b{1,2})?([0-8])$")
#: Semitones above the root.
CHORDS: dict[str, tuple[int, ...]] = {
    "major": (0, 4, 7), "minor": (0, 3, 7), "dim": (0, 3, 6), "aug": (0, 4, 8), "7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11), "min7": (0, 3, 7, 10), "sus2": (0, 2, 7), "sus4": (0, 5, 7), "power": (0, 7),
}
SCALES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10), "pentatonic": (0, 2, 4, 7, 9),
    "minor_pentatonic": (0, 3, 5, 7, 10), "blues": (0, 3, 5, 6, 7, 10), "dorian": (0, 2, 3, 5, 7, 9, 10),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10), "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "chromatic": tuple(range(12)),
}


def midi(name: str) -> int:
    """The MIDI number of a note name; ``ValueError`` for one that is not."""
    found = NOTE.fullmatch(name)
    if not found:
        raise ValueError(f"{name!r} is not a note (a letter A to G, # or b, and an octave 0 to 8, such as A4 or Eb3)")
    letter, accidental, octave = found.groups()
    shift = (accidental or "").count("#") - (accidental or "").count("b")
    return 12 * (int(octave) + 1) + _LETTERS[letter] + shift


def name(number: int) -> str:
    return f"{_NAMES[number % 12]}{number // 12 - 1}"


def frequency(note: str | int) -> float:
    """A note's frequency in Hz (a name or a MIDI number)."""
    number = midi(note) if isinstance(note, str) else note
    return A4 * 2 ** ((number - 69) / 12)


def chord(root: str, kind: str) -> list[str]:
    if kind not in CHORDS:
        raise ValueError(f"no chord {kind!r} (known: {', '.join(CHORDS)})")
    base = midi(root)
    return [name(base + step) for step in CHORDS[kind]]


def scale(root: str, kind: str, octaves: int = 1) -> list[str]:
    """The scale from ``root`` up ``octaves`` octaves, its top note included."""
    if kind not in SCALES:
        raise ValueError(f"no scale {kind!r} (known: {', '.join(SCALES)})")
    base = midi(root)
    steps = [12 * o + s for o in range(octaves) for s in SCALES[kind]] + [12 * octaves]
    return [name(base + s) for s in steps]


__all__ = ["A4", "CHORDS", "NOTE", "SCALES", "chord", "frequency", "midi", "name", "scale"]
