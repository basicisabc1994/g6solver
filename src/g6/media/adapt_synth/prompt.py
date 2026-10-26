# SPDX-License-Identifier: Apache-2.0
"""Reading musical parameters from a few words, by fixed rules (standard library only).

:func:`read` finds a tempo (a number with "bpm", or a tempo word), a key ("in D minor", "key of F#"), a mood (which
gives a key, a mode and a tempo when the words name none, from the table below), a sound ("bell", "pad", "bass"...), a
length ("20 seconds", "2 minutes") and a scale, and says where each value came from: ``said: <the words>``, ``from the
mood <word>`` or ``default``. Words it does not know are listed, never guessed at. It makes nothing.
"""
from __future__ import annotations

import re
from typing import Any

#: Each mood: the key, mode and tempo range it suggests when the words name none (first-party table carried from the
#: archive's music-composition reference).
MOODS: dict[str, dict[str, Any]] = {
    "happy": {"root": "C4", "mode": "major", "tempo": (110, 130)},
    "sad": {"root": "A3", "mode": "minor", "tempo": (60, 80)},
    "epic": {"root": "D3", "mode": "minor", "tempo": (80, 95)},
    "chill": {"root": "F4", "mode": "major", "tempo": (70, 85)},
    "dark": {"root": "E3", "mode": "harmonic_minor", "tempo": (60, 70)},
    "dreamy": {"root": "F4", "mode": "major", "tempo": (70, 90)},
    "aggressive": {"root": "E3", "mode": "minor", "tempo": (140, 160)},
    "romantic": {"root": "A#3", "mode": "major", "tempo": (70, 90)},
    "calm": {"root": "G4", "mode": "major", "tempo": (60, 75)},
    "tense": {"root": "B3", "mode": "minor", "tempo": (100, 120)},
}
_SYNONYMS = {"cheerful": "happy", "bright": "happy", "joyful": "happy", "uplifting": "happy", "melancholy": "sad",
             "gloomy": "dark", "ominous": "dark", "relaxed": "chill", "mellow": "chill", "peaceful": "calm",
             "angry": "aggressive", "heroic": "epic", "suspense": "tense", "suspenseful": "tense"}
TEMPI = {"largo": 50, "adagio": 70, "slow": 70, "andante": 90, "moderate": 110, "moderato": 110, "upbeat": 125,
         "allegro": 130, "fast": 140, "quick": 140, "presto": 170}
SOUNDS = {"bell": ("fm", {"ratio": 3.5, "index": 2.0}), "chime": ("fm", {"ratio": 2.0, "index": 1.5}),
          "pad": ("sine", {"attack": 0.4, "release": 0.6}), "soft": ("sine", {}), "warm": ("triangle", {}),
          "flute": ("triangle", {}), "bass": ("saw", {"octave": 2}), "lead": ("square", {}), "buzzy": ("saw", {}),
          "pluck": ("saw", {"attack": 0.002, "decay": 0.15, "sustain": 0.0}), "organ": ("square", {}),
          "piano": ("triangle", {"attack": 0.005, "decay": 0.3, "sustain": 0.3})}
SCALE_WORDS = {"pentatonic": "pentatonic", "blues": "blues", "dorian": "dorian", "mixolydian": "mixolydian",
               "chromatic": "chromatic"}
_FILLER = frozenset({"a", "an", "the", "in", "of", "and", "with", "for", "to", "at", "on", "some", "piece", "tune",
                     "song", "music", "track", "loop", "about", "around", "key", "bpm", "seconds", "second", "minutes",
                     "minute", "sec", "secs", "s", "major", "minor", "very", "quite", "little", "bit", "sound", "like"})
_BPM = re.compile(r"\b(\d{2,3})\s*(?:bpm|beats per minute)\b", re.IGNORECASE)
_KEY = re.compile(r"\b(?:in|key of)\s+([A-Ga-g])(#|b|\s+sharp|\s+flat)?(?:\s+(major|minor|maj|min))?\b")
_LENGTH = re.compile(r"\b(\d+(?:\.\d+)?)\s*(seconds?|secs?|s|minutes?|mins?)\b", re.IGNORECASE)
DEFAULTS = {"bpm": 120, "root": "C4", "mode": "major", "waveform": "sine", "duration": 8.0, "scale": None}


def read(text: str) -> dict[str, Any]:
    """The parameters the words give, where each came from, and the words the rules did not read."""
    params: dict[str, Any] = dict(DEFAULTS)
    sources = {k: "default" for k in params}
    used: set[str] = set()
    lowered = text.lower()

    def said(key: str, value: Any, words: str) -> None:
        params[key], sources[key] = value, f"said: {words.strip()!r}"
        used.update(re.findall(r"[a-z0-9#]+", words.lower()))

    if found := _BPM.search(text):
        said("bpm", max(20, min(300, int(found.group(1)))), found.group(0))
    if found := _KEY.search(text):
        letter, accidental, mode = found.group(1).upper(), (found.group(2) or "").strip(), found.group(3) or ""
        sharp = {"sharp": "#", "flat": "b"}.get(accidental, accidental)
        said("root", f"{letter}{sharp}4", found.group(0))
        if mode:
            said("mode", "minor" if mode.lower().startswith("min") else "major", found.group(0))
    if found := _LENGTH.search(text):
        number, unit = float(found.group(1)), found.group(2).lower()
        said("duration", min(600.0, number * (60 if unit.startswith("m") else 1)), found.group(0))
    words = re.findall(r"[a-z#]+", lowered)
    for word in words:
        mood = _SYNONYMS.get(word, word)
        if mood in MOODS and sources["mode"] == "default":
            table = MOODS[mood]
            params["mode"], sources["mode"] = table["mode"], f"from the mood {word!r}"
            if sources["root"] == "default":
                params["root"], sources["root"] = table["root"], f"from the mood {word!r}"
            if sources["bpm"] == "default":
                params["bpm"], sources["bpm"] = sum(table["tempo"]) // 2, f"from the mood {word!r}"
            used.add(word)
        if word in TEMPI and not sources["bpm"].startswith("said"):
            said("bpm", TEMPI[word], word)
        if word in SOUNDS and sources["waveform"] == "default":
            wave, extra = SOUNDS[word]
            said("waveform", wave, word)
            params.update({k: v for k, v in extra.items()})
            sources.update({k: f"from the sound {word!r}" for k in extra})
        if word in SCALE_WORDS and sources["scale"] == "default":
            said("scale", SCALE_WORDS[word], word)
    if params["scale"] is None:
        params["scale"] = "minor" if params["mode"] in ("minor", "harmonic_minor") else "major"
        if params["mode"] == "harmonic_minor":
            params["scale"] = "harmonic_minor"
        sources["scale"] = f"from the mode {params['mode']!r}"
    unread = [w for w in words if w not in used and w not in _FILLER and not w.isdigit()]
    suggested = "drum_pattern" if any(w in ("beat", "drums", "drum", "groove") for w in words) else "sequence"
    return {"params": params, "sources": sources, "unread": unread, "suggested_op": suggested}


__all__ = ["DEFAULTS", "MOODS", "SCALE_WORDS", "SOUNDS", "TEMPI", "read"]
