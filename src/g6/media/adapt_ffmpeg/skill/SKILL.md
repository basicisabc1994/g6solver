---
name: g6-adapt-ffmpeg
description: Use when you need to convert, cut, join, resize, retime, filter, subtitle or level a video or sound file with ffmpeg, or pull out its sound or a frame, and you want every file ffmpeg writes measured against what you asked for before you use it.
---

# G6 adapt_ffmpeg

`g6.media.adapt_ffmpeg` drives the ffmpeg and ffprobe you installed, over their command line. A source is a file name
in G6's media folder (`$G6_HOME/media`); ffmpeg writes a fresh file there, which is probed from disk against what the
request and the sources say it must be (its streams, a full decode, its length, frame size and rate, sample rate and
channels, not silent, not blank), and only then given the name you asked for. ffmpeg's exit code is not a check. Its
best state is `qualified-draft`.

## When to use it

- You (a coding agent) cut a clip, change its format, frame size or frame rate, join clips, speed one up, lay a logo
  on it, burn in subtitles, take a frame or a GIF from it, or bring its loudness to a target, and you cannot watch or
  hear the result yourself.
- You need ffprobe's account of a file, and whether ffmpeg can decode every frame of it.

## When not to use it

- Editing sound sample by sample (equalising, compressing, de-noising, measuring pitch or tempo): use `adapt_audio`.
- Making sound or speech: `adapt_synth`, `adapt_voice`.
- A file outside the media folder or at an address: there is no path or URL input, and a link in the folder to a file
  kept elsewhere is refused. Put the file itself in the media folder.
- Judging what a video shows or how it sounds: nothing here does.

## Ops

- `probe`: ffprobe's format and streams for `source`, and whether ffmpeg decodes every frame, its sound is silent and
  its pictures blank, each measured whatever the others found (`null` when there is no sound or picture to measure).
  Nothing is written.
- `convert`: `source` in another `format` (mp4, mov, mkv, webm, avi, mp3, wav, flac, ogg, opus, m4a); `video_codec`,
  `audio_codec` (plain names), `crf`, `audio_bitrate` (such as `128k`).
- `trim`: from `start` to `end` seconds. By default (`accurate: true`) it re-encodes for an exact cut; `accurate:
  false` is a fast stream copy, whose cut lands on key frames, so it often measures longer and is then refused.
- `concat`: `sources`, two to twenty files, one after another, at the first one's frame size and rate.
- `extract_audio`: the sound alone (`format` wav by default).
- `add_audio`: `source`'s video with the sound of `audio`, as long as the shorter of the two.
- `resize`: `width`, `height` or both; one side alone keeps the shape (an even size for a video format).
- `framerate`: `fps`.
- `filter`: `filter_expr`, a chain of allowed video filters (`eq`, `crop`, `scale`, `pad`, `hflip`, `vflip`,
  `transpose`, `rotate`, `hue`, `unsharp`, `gblur`, `fade` and a few more). Nothing that opens a file, a device or an
  address, runs a command or splits into several chains is accepted, so `curves` is not (it writes its plot to a file
  and reads a preset file).
- `thumbnail`: one frame at `time` (the middle by default) as `png` or `jpg`, at `width` or `height` if given.
- `gif`: an animated GIF with its own palette, at `fps` (10) and `width` (at most 480 by default), from `start` to
  `end`.
- `overlay`: the picture or video `overlay` laid on `source` at `x`, `y`.
- `speed`: `factor` from 0.25 to 4 (above 1 is faster); the sound keeps its pitch.
- `normalize_audio`: EBU R128 loudness in two passes to `target_lufs` (-16), `true_peak` (-1.5 dBTP) and `lra` (11),
  the sample rate kept; the result is measured again.
- `subtitles`: SRT, ASS or WebVTT `subtitles` (a file in the media folder) drawn onto the video.

Every op that writes takes `name` (G6 chooses the folder; a different file of that name is never replaced unless
`overwrite`), `format`, `tolerance` (seconds a length may differ by) and `timeout` (1 to 600 seconds).

## Alone and composed

Alone over MCP: `g6_invoke("adapt_ffmpeg", "trim", '{"source": "talk.mp4", "start": 5, "end": 15}')`, then read
`result.artifact.measured` and open `result.artifact.path`. Every op runs ffmpeg or ffprobe on the person's machine,
so every one is sensitive: over MCP it runs only when the person who runs the server lists it (`adapt_ffmpeg.trim`) or
the whole component (`adapt_ffmpeg`) in `G6_MCP_SENSITIVE`. Ask them; never set it yourself.

Composed:

- A picture from `adapt_image` or `adapt_generative_art` is an `overlay`; a sound from `adapt_synth` or `adapt_voice`
  is an `audio` for `add_audio`, by file name.
- A slideshow: make the pictures, `concat` short clips of them, `add_audio` a narration, `normalize_audio` the result,
  then `probe` it.
- `adapt_audio` edits a WAV this op extracted, and `convert` turns its WAV back into the format you need.

## Failure modes it catches

- `visual_check_failed` (an image, video or 3D output that is blank, corrupt or wrong, reported as done): every frame is
  decoded, its frame size and rate compared with the request, and twelve frames sampled across it; a result whose
  every sampled frame is nearly one grey (a filter that turned it black) is refused with `G6_E_MEDIA_BLANK`, one that
  does not decode with `G6_E_MEDIA_UNDECODABLE`, the wrong size with `G6_E_MEDIA_SIZE_MISMATCH`.
- `audio_check_failed` (sound output that is silent, clipped or wrong, reported as done): its sound is decoded and
  measured; a silent one is refused with `G6_E_MEDIA_SILENT`; a result shorter or longer than asked (a stream-copy cut
  that landed on key frames, a speed change gone wrong) with `G6_E_MEDIA_DURATION_MISMATCH`; the wrong sample rate,
  channels or streams with `G6_E_MEDIA_STREAM_MISMATCH`; a normalised file off its target with
  `G6_E_MEDIA_LOUDNESS_MISSED`.

ffmpeg exiting 0 with nothing written, an empty file or a broken one is refused (`G6_E_MEDIA_NO_OUTPUT`,
`_UNDECODABLE`), and the file is removed.

## Reading the result

- A result is `qualified-draft` with `G6_E_MEDIA_PROBED`: its `artifact` holds the path, the MIME type, what was asked
  for (`requested`), what was measured (`measured`: format, length, video and audio streams, levels), the checks, and
  the provenance (ffmpeg's version, the argument list, the sources' SHA-256). `probe` answers `G6_E_MEDIA_MEASURED`.
- A refusal is `blocked-escalated` with its code: the inspection codes above, `G6_E_OUTSIDE_PROGRAM_MISSING` (ffmpeg
  or ffprobe is not installed; the reason says how to install it), `G6_E_MEDIA_BACKEND_FAILED` (ffmpeg failed; its
  message is in the reason), `G6_E_MEDIA_TIME_LIMIT`, `G6_E_MEDIA_EXISTS` or `G6_E_MEDIA_INPUT_INVALID`.

## Blind spots

- The probe measures; it does not watch or listen. A cut at the wrong moment, a wrong subtitle or a muddy encode passes.
- Blank means every sampled frame (scaled to 160 x 90 in grey) is nearly one grey; a picture that is mostly black with
  a little in it passes, and so does a frozen picture.
- Silent means under 0.1 % of the samples are louder than -60 dBFS; a very quiet recording is called silent, and hum
  or noise is not.
- `subtitles` checks the video it writes, not that the words were drawn (the fonts are the system's).
- ffmpeg runs as you; this is a resource bound (a deadline, an output cap, a clean environment), not a sandbox. Its
  decoders read files a caller gave, which is why every op is sensitive.

## What it needs

- ffmpeg and ffprobe, installed by the person (ffmpeg.org; `winget install Gyan.FFmpeg`, `brew install ffmpeg` or
  `apt install ffmpeg`), on PATH or named by `G6_FFMPEG` and `G6_FFPROBE`. G6 never installs or bundles them; a
  missing one is refused with these steps. Their builds may be GPL, so G6 runs them over their command line only.
- No Python extra.

## Examples

A clip of the talk from 5 to 15 seconds, cut exactly (sensitive).

```g6-example op=trim
{"source": "talk.mp4", "start": 5, "end": 15, "name": "talk-cut"}
```

Its loudness brought to -16 LUFS and measured again (sensitive).

```g6-example op=normalize_audio
{"source": "talk-cut.mp4", "target_lufs": -16, "true_peak": -1.5, "name": "talk-level"}
```

From Python: a filter that would open a file is refused before ffmpeg is asked anything.

```python
from g6.media._types import MediaError
from g6.media.adapt_ffmpeg.commands import check_filter

assert check_filter("hflip,eq=contrast=1.2") == "hflip,eq=contrast=1.2"
try:
    check_filter("movie=../secret.mp4")
except MediaError as exc:
    assert exc.code == "G6_E_MEDIA_INPUT_INVALID"
else:
    raise AssertionError("an unsafe filter was accepted")
```

## The four open problems

- **Learning signals**: each refusal carries its code, the failed check and ffmpeg's argument list. Not yet: nothing
  counts them per op or per ffmpeg build.
- **Evaluators the model cannot influence**: ffprobe and ffmpeg's own decoder read the written file, and what it must
  measure is computed from the request and the sources' probes, never read from the result.
- **Transfer by failure mode**: "silent", "blank", "wrong length" and "does not decode" carry the same codes here as in
  `adapt_audio`, `adapt_synth` and `adapt_voice`.
- **Enough held-out cases**: Not yet: the tests make their media with ffmpeg's own test sources; there is no stored set
  of reference files with known lengths and loudness.
