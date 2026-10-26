---
name: g6-adapt-synth
description: Use when you need a tone, a note, a chord, an arpeggio, a scale, a short melody, noise or a drum pattern as a WAV file, or want a few words read into musical parameters, and you want each sound measured against what you asked for before you use it.
---

# G6 adapt_synth

`g6.media.adapt_synth` makes sound with NumPy: band-limited oscillators (sine, square, saw, triangle), FM, coloured
noise, notes with ADSR envelopes, chords, arpeggios, scales, melodies and synthesised drums. Every sound is written as
a WAV into G6's media folder (`$G6_HOME/media`), inspected from disk, and then the written sound itself is measured
against the request: each note's fundamental, each chord tone, the FM sidebands, the noise's colour, each drum hit. A
sound that is off is refused and removed. Its best state is `qualified-draft`: nothing judges the music.

## When to use it

- You (a coding agent) need a test tone, a notification sound, a chord or a short melody for an app, a demo or a
  test fixture, at a known pitch and length, and you cannot listen to it.
- You need noise of a known colour, or a drum loop at a known tempo, to test audio code.
- You want a request such as "a sad slow piece in D minor" read into tempo, key, mode and sound, with where each value
  came from.

## When not to use it

- Editing, levelling or measuring an existing sound: use `adapt_audio`. Speech: `adapt_voice`.
- Composing music that should sound good: nothing here judges that; it makes what is asked and measures it.
- Sampled instruments, reverb or effects: not here (`adapt_audio` has the filters and dynamics).

## Ops

- `tone`: `waveform` (sine, square, saw, triangle) at `frequency` Hz for `duration` seconds at `volume` (peak, 0.5).
- `fm`: a carrier at `frequency` modulated at `ratio` times it with `index`.
- `noise`: `noise_type` white, pink or brown, from `seed` (a random one is reported when none is given).
- `note`: one `note` (A4, C#5, Eb3; A4 = 440 Hz) with `attack`, `decay`, `sustain`, `release`.
- `chord`: `root` and `chord_type` (major, minor, dim, aug, 7, maj7, min7, sus2, sus4, power).
- `arpeggio`: the chord's notes `up`, `down` or `updown` (`pattern`), `note_duration` each, `repeats` times.
- `scale`: `root`, `scale_type` (major, minor, pentatonic, minor_pentatonic, blues, dorian, mixolydian,
  harmonic_minor, chromatic), `octaves`, `note_duration`.
- `envelope`: a `note` shaped by `attack`, `decay`, `sustain` (a level, 0 to 1) and `release`, over `duration`.
- `sequence`: `notes`, a list of `{"note": "C4", "beats": 1}` or `{"rest": true, "beats": 0.5}`, at `bpm`.
- `drum_pattern`: `pattern`, one string per drum (`kick`, `snare`, `hat`, `clap`) of `x` (a hit) and `.` (a rest),
  covering a bar of four beats, at `bpm`, for `bars`.
- `prompt_to_params`: `prompt` read by fixed rules into `bpm`, `root`, `mode`, `waveform`, `duration` and `scale`, each
  with where it came from (`said`, a mood, or `default`) and the words not read. `use_model` also asks the model given
  to the block from Python, as a claim.

Every op that makes sound takes `name`, `overwrite`, `rate` (48000) and `bits` (16 or 24).

## Alone and composed

Alone over MCP: `g6_invoke("adapt_synth", "chord", '{"root": "C4", "chord_type": "maj7"}')`, then open
`result.artifact.path`. Nothing here reaches the network or runs a program, so every op runs over MCP as it is.

Composed: `prompt_to_params` gives the `bpm`, `root` and `scale` for `scale`, `sequence` or `drum_pattern`;
`adapt_audio` levels the result (`normalise_loudness`) or mixes a melody over a drum loop (`mix`); `adapt_ffmpeg`
turns it into MP3 or puts it under a video. From Python, `AdaptSynthBlock(llm=caller)` with any `g6.core.llm` caller
adds that model's reading of a prompt to the evidence when `use_model` is given.

## Failure modes it catches

- `audio_check_failed` (sound output that is silent, clipped or wrong, reported as done): the written sound is
  measured, so a render that is off is refused with `G6_E_MEDIA_PITCH_MISMATCH` and removed: a note whose
  fundamental (YIN) is more than 25 cents from the note asked for (one wrong note in a scale or a melody is named by
  its place), a chord tone with no spectral peak within 30 cents, FM sidebands that are not at the carrier plus or
  minus multiples of the modulator, a noise whose spectral slope is more than 1.5 dB per octave from its colour, a
  drum hit with no onset within 15 ms of its step. A silent result is refused with `G6_E_MEDIA_SILENT`.

## Reading the result

- A sound is `qualified-draft` with `G6_E_MEDIA_PROBED`: `artifact` (path, measurements, checks with what was
  measured, provenance with the seed) and `probe_after` (peak, true peak, loudness, length).
- `prompt_to_params` answers `G6_E_MEDIA_PARSED`: `params`, `sources`, `unread`, `suggested_op`, and any model's reading
  as a `claim` that changes none of them.
- A refusal is `blocked-escalated` with its code: those above, `G6_E_INPUT_VALIDATION` (a note that is not one, a drum
  pattern of other characters), `G6_E_MEDIA_INPUT_INVALID` (a sound of more than 30 million samples, about five
  minutes at 96 kHz or ten at 48 kHz, or a frequency at or above 0.45 of `rate`, which would alias: a tone, a note's
  fundamental, or an FM sound's sidebands by Carson's rule; each refused from the request before anything is made),
  `G6_E_MEDIA_EXTRA_MISSING`.

## Blind spots

- It measures; it does not listen. A melody that sounds wrong but is in tune, or a drum loop with no groove, passes.
- Pitch is measured between 30 Hz and 4 kHz; a note outside that is made and listed in `pitch_not_measured`, and the
  sound then carries no pitch check (a measured note that is off still refuses it). A chord's pitch is checked tone by
  tone in its spectrum, not by ear.
- `prompt_to_params` knows a few dozen words; a word it does not know is listed in `unread`, never guessed at. A model's
  reading is not compared with anything.

## What it needs

- `pip install "g6[audio] @ git+https://github.com/basicisabc1994/g6solver"` for NumPy (BSD-3-Clause). Without it every op that makes sound refuses with
  `G6_E_MEDIA_EXTRA_MISSING` and that line; `prompt_to_params`, `ops` and `help` still answer.

## Examples

```g6-example op=tone
{"waveform": "saw", "frequency": 220, "duration": 0.5, "name": "saw-a3", "overwrite": true}
```

```g6-example op=fm
{"frequency": 440, "ratio": 3.5, "index": 2, "duration": 0.5, "overwrite": true}
```

```g6-example op=noise
{"noise_type": "pink", "duration": 1, "seed": 7, "overwrite": true}
```

```g6-example op=note
{"note": "E4", "duration": 0.5, "waveform": "triangle", "overwrite": true}
```

```g6-example op=chord
{"root": "C4", "chord_type": "maj7", "duration": 1, "overwrite": true}
```

```g6-example op=arpeggio
{"root": "A3", "chord_type": "minor", "pattern": "updown", "note_duration": 0.2, "overwrite": true}
```

```g6-example op=scale
{"root": "D4", "scale_type": "dorian", "note_duration": 0.2, "overwrite": true}
```

```g6-example op=envelope
{"note": "G4", "duration": 1, "attack": 0.05, "decay": 0.1, "sustain": 0.5, "release": 0.2, "overwrite": true}
```

```g6-example op=sequence
{"notes": [{"note": "C4", "beats": 1}, {"rest": true, "beats": 0.5}, {"note": "G4", "beats": 1.5}], "bpm": 120, "overwrite": true}
```

```g6-example op=drum_pattern
{"pattern": {"kick": "x...x...x...x...", "snare": "....x.......x...", "hat": "x.x.x.x.x.x.x.x."}, "bpm": 100, "overwrite": true}
```

```g6-example op=prompt_to_params
{"prompt": "a calm pad in F major, 70 bpm, about 30 seconds"}
```

## The four open problems

- **Learning signals**: each refusal names the check and what was measured (the note, its frequency, how many cents
  off). Not yet: nothing counts them per op.
- **Evaluators the model cannot influence**: pitch, spectrum, slope and onsets are measured from the written file; what
  they must be comes from music theory and the request, and a model's reading of a prompt is only a claim.
- **Transfer by failure mode**: "wrong pitch", "silent" and "clipped" carry the same codes here as in `adapt_audio`
  and `adapt_voice`.
- **Enough held-out cases**: the tests hold known values (A4 is 440 Hz; pink noise falls 3 dB an octave). Not yet: no
  stored set of reference renders.
