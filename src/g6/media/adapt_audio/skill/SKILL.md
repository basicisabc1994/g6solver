---
name: g6-adapt-audio
description: Use when you need to cut, join, mix, fade, level, equalise, compress, limit, gate, de-ess, de-noise, resample or convert a WAV, or measure its loudness, true peak, pitch, tempo, spectrum or silences, and you want every result measured again before you use it, never clipped silently.
---

# G6 adapt_audio

`g6.media.adapt_audio` edits and measures sound in WAV files with NumPy. A sound is a file name in G6's media folder
(`$G6_HOME/media`; a link there to a file kept elsewhere is refused) or inline base64. Every edit measures its source,
refuses a result that would go past full scale
(nothing is clipped silently), writes a fresh WAV, inspects it from disk (it decodes, has the rate, channels and length
it must have, and is not silent) and measures it again: peak, true peak, integrated loudness (ITU-R BS.1770-4),
length, rate and channels, before and after. A level change is checked against its own target. Its best state is
`qualified-draft`.

## When to use it

- You (a coding agent) are preparing narration, a podcast, a sound effect or a music stem and need it at a loudness
  target, without clipping, cleaned of hiss or harsh "s" sounds, cut, joined or mixed, and you cannot listen to it.
- You need to know a sound's loudness, true peak, pitch, tempo, strongest frequencies or silent stretches.

## When not to use it

- Files that are not WAV: `transcode` turns them into WAV first (it needs ffmpeg). Video: `adapt_ffmpeg`.
- Making sound from nothing: `adapt_synth` (tones, notes, rhythms) or `adapt_voice` (speech).
- Judging how it sounds, or separating voices from music: nothing here does.

## Ops

- `metadata`: format, rate, channels, bits, frames, length, peak, RMS and the share of samples that are sound, read with
  the standard library alone.
- `loudness`: integrated loudness in LUFS (BS.1770-4: K-weighting, gated 400 ms blocks), true peak, peak and RMS.
- `spectrum`: the strongest frequencies, centroid, 85 % roll-off, and low, mid and high band energy.
- `pitch`: the fundamental of the loudest half second (YIN) and the nearest note, with its periodicity.
- `bpm`: the tempo, from how its onsets repeat.
- `silence`: the stretches quieter than `threshold_dbfs` (-50) for at least `min_silence` seconds.
- `phase_correlation`: for stereo, -1 (opposite) to +1 (the same).
- `crest_factor`: peak over RMS in dB, with the true peak.
- `trim`: `start` to `end` seconds, on exact samples.
- `concat`: `sources`, WAVs of one rate and channel count, one after another.
- `mix`: `sources` summed, each at its `gains_db`.
- `fade`: `fade_in`, `fade_out` seconds, `curve` `linear` or `equal_power`.
- `gain`: `gain_db`.
- `resample`: to `rate`, by band-limited windowed-sinc interpolation.
- `convert`: `channels` 1 or 2, `bits` 16, 24 or 32.
- `eq`: `bands`, each `{"type": "peak" | "low_shelf" | "high_shelf" | "low_pass" | "high_pass" | "band_pass",
  "frequency_hz", "gain_db", "q"}`.
- `compress`: `threshold_db` (-20), `ratio` (4), `attack_ms`, `release_ms`, `knee_db`, `makeup_db`.
- `limit`: a true-peak limiter to `ceiling_dbtp` (-1), with `release_ms`; the true peak is measured again.
- `gate`: closes to `floor_db` (-40) below `threshold_db` (-50), with `attack_ms`, `hold_ms`, `release_ms`.
- `de_ess`: the band above `frequency_hz` (6000) lowered by up to `reduction_db` while it is above `threshold_db`.
- `noise_reduce`: spectral gating by up to `reduction_db` (12) against the noise from `noise_start` to `noise_end`, or
  of the quietest tenth of the frames; it reports the noise floor before and after.
- `normalise_loudness`: to `target_lufs` (-16) under `ceiling_dbtp` (-1). When that gain would push the true peak over
  the ceiling it is refused, unless `limit: true`, which brings the peaks down and raises the gain again until the
  target is met; the result's loudness and true peak are measured again.
- `dither`: to `bits` 16 or 24 with TPDF dither (`seed` reported).
- `transcode`: a sound file in any format ffmpeg reads into WAV, or a WAV into `format` flac, mp3, ogg, opus or m4a.

Every edit takes `name` (G6 chooses the folder; a different file of that name is never replaced unless `overwrite`)
and `bits` (the source's by default).

## Alone and composed

Alone over MCP: `g6_invoke("adapt_audio", "normalise_loudness", '{"source": "voice.wav", "target_lufs": -16}')`,
then read the `probe_after` evidence and open `result.artifact.path`. Every op but `transcode` runs over MCP as it is;
`transcode` runs ffmpeg on the person's machine, so it is sensitive: over MCP it runs only when the person who runs the
server lists it (`adapt_audio.transcode`) in `G6_MCP_SENSITIVE`.

Composed: `adapt_voice` or `adapt_synth` makes a WAV, this levels and cleans it, and `adapt_ffmpeg` puts it under a
video (`add_audio`). `adapt_ffmpeg`'s `extract_audio` gives this a video's sound.

## Failure modes it catches

- `audio_check_failed` (sound output that is silent, clipped or wrong, reported as done):
  - a result that would go past full scale (a gain or a loudness target too high) is refused with
    `G6_E_MEDIA_CLIPPED`, before anything is written;
  - a loudness target whose gain would put the true peak over the ceiling is refused with `G6_E_MEDIA_CLIPPED`, unless
    you ask for the limiter; a result that misses its loudness (by more than 0.5 LU) or its true-peak ceiling (by more
    than 0.1 dB), measured again from the written file, with `G6_E_MEDIA_LOUDNESS_MISSED`;
  - an edit that leaves silence is refused with `G6_E_MEDIA_SILENT`; a file of the wrong length, rate or channels with
    `G6_E_MEDIA_DURATION_MISMATCH` or `_STREAM_MISMATCH`. The file is removed.

## Reading the result

- An edit is `qualified-draft` with `G6_E_MEDIA_PROBED`: `artifact` (path, measurements, checks, provenance) and the
  evidence `probe_before` and `probe_after`. An analysis answers `G6_E_MEDIA_MEASURED` with `measured`.
- A refusal is `blocked-escalated` with its code: those above, `G6_E_MEDIA_FORMAT_MISMATCH` (not a WAV),
  `G6_E_MEDIA_UNDECODABLE`, `G6_E_MEDIA_INPUT_INVALID` (sources that differ in rate or channels, a span outside the
  sound, a source or a result of more than 30 million samples, about five minutes of 48 kHz stereo: a `concat`,
  `resample` or conversion to stereo past it is refused before it is made), `G6_E_MEDIA_EXTRA_MISSING`, or for
  `transcode` `G6_E_OUTSIDE_PROGRAM_MISSING` and `G6_E_MEDIA_BACKEND_FAILED`.

## Blind spots

- It measures; it does not listen. An equaliser that makes a voice thin, a gate that cuts a soft word or a compressor
  that pumps passes every check.
- Silent means under 0.1 % of samples louder than -60 dBFS: a very quiet recording is refused as silent; hum and hiss
  are not silence.
- True peak is four times oversampled by G6's own windowed-sinc interpolator, not the standard's 48-tap filter; it may
  differ from a meter by a few hundredths of a dB. Loudness range (LRA) is not measured.
- `pitch` is one note (the loudest half second), `bpm` one tempo: a chord, a melody or a tempo change is not described.
- `noise_reduce` gates steady noise; it does not remove speech, music or clicks beneath other sound.

## What it needs

- `pip install "g6[audio]"` for NumPy (BSD-3-Clause). Without it every op but `metadata`, `ops` and `help` refuses with
  `G6_E_MEDIA_EXTRA_MISSING` and that line.
- For `transcode` only: ffmpeg and ffprobe, installed by the person (ffmpeg.org) and found on PATH or through
  `G6_FFMPEG` and `G6_FFPROBE`. G6 runs them over their command line and never bundles them.

## Examples

A half-second tone given inline (8-bit, 4 kHz), kept as a 16-bit WAV named `clip`; the examples after it use it.

```g6-example op=convert
{"audio_base64": "UklGRvQHAABXQVZFZm10IBAAAAABAAEAoA8AAKAPAAABAAgAZGF0YdAHAACAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNY4Cds7+8rZR2W0dAR1t2lK28v7OdgGNNQURTbIqlucC5pYpsU0RBTWOAnbO/vK2UdltHQEdbdpStvL+znYBjTUFEU2yKpbnAuaWKbFNEQU1jgJ2zv7ytlHZbR0BHW3aUrby/s52AY01BRFNsiqW5wLmlimxTREFNYw==", "channels": 1, "bits": 16, "name": "clip", "overwrite": true}
```

```g6-example op=metadata
{"source": "clip.wav"}
```

```g6-example op=loudness
{"source": "clip.wav"}
```

```g6-example op=spectrum
{"source": "clip.wav"}
```

```g6-example op=pitch
{"source": "clip.wav"}
```

```g6-example op=bpm
{"source": "clip.wav"}
```

```g6-example op=silence
{"source": "clip.wav", "threshold_dbfs": -50}
```

A stereo copy, and how alike its two channels are.

```g6-example op=convert
{"source": "clip.wav", "channels": 2, "name": "clip-stereo", "overwrite": true}
```

```g6-example op=phase_correlation
{"source": "clip-stereo.wav"}
```

```g6-example op=crest_factor
{"source": "clip.wav"}
```

```g6-example op=trim
{"source": "clip.wav", "start": 0.1, "end": 0.4, "name": "clip-cut", "overwrite": true}
```

```g6-example op=concat
{"sources": ["clip.wav", "clip-cut.wav"], "overwrite": true}
```

```g6-example op=mix
{"sources": ["clip.wav", "clip-cut.wav"], "gains_db": [-6, -6], "overwrite": true}
```

```g6-example op=fade
{"source": "clip.wav", "fade_in": 0.05, "fade_out": 0.1, "overwrite": true}
```

```g6-example op=gain
{"source": "clip.wav", "gain_db": -3, "overwrite": true}
```

```g6-example op=resample
{"source": "clip.wav", "rate": 8000, "overwrite": true}
```

```g6-example op=eq
{"source": "clip.wav", "bands": [{"type": "peak", "frequency_hz": 300, "gain_db": -4, "q": 1.0}], "overwrite": true}
```

```g6-example op=compress
{"source": "clip.wav", "threshold_db": -12, "ratio": 3, "overwrite": true}
```

```g6-example op=limit
{"source": "clip.wav", "ceiling_dbtp": -9, "overwrite": true}
```

```g6-example op=gate
{"source": "clip.wav", "threshold_db": -40, "overwrite": true}
```

```g6-example op=de_ess
{"source": "clip.wav", "frequency_hz": 1500, "threshold_db": -30, "reduction_db": 6, "overwrite": true}
```

```g6-example op=noise_reduce
{"source": "clip.wav", "reduction_db": 12, "overwrite": true}
```

```g6-example op=normalise_loudness
{"source": "clip.wav", "target_lufs": -20, "ceiling_dbtp": -1, "overwrite": true}
```

```g6-example op=dither
{"source": "clip.wav", "bits": 16, "seed": 7, "overwrite": true}
```

The tone as FLAC through the person's ffmpeg (sensitive).

```g6-example op=transcode
{"source": "clip.wav", "format": "flac"}
```

## The four open problems

- **Learning signals**: each refusal carries its code, the failed check and the measurements before and after. Not
  yet: nothing counts them per op.
- **Evaluators the model cannot influence**: the written file is read back and measured by BS.1770-4 loudness and an
  oversampled true peak; the targets come from the request, never from the result.
- **Transfer by failure mode**: "silent", "clipped", "wrong length" and "missed loudness" carry the same codes here as
  in `adapt_ffmpeg`, `adapt_synth` and `adapt_voice`.
- **Enough held-out cases**: the tests hold known values (a 997 Hz sine at -20 dBFS is -23.0 LUFS; a sine at a quarter
  of the rate peaks 3 dB between its samples). Not yet: no stored set of real recordings with meter readings.
