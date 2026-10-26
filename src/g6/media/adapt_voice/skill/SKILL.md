---
name: g6-adapt-voice
description: Use when you need text spoken into a WAV file (a local voice first, a paid one only when the person's key file and G6's action gate allow it), or a recording transcribed by a paid service, and you want each voice inspected, and a transcript refused when it is empty or too fast for the speech in the clip (its words are not checked), before you use it.
---

# G6 adapt_voice

`g6.media.adapt_voice` speaks text and transcribes speech. Its local voice is eSpeak NG, a program the person installs;
the text reaches it on its input, never on its command line. Its paid voices are OpenAI's and ElevenLabs', and its
transcription is OpenAI's: every request is a payment G6's action gate decides before anything is sent, and the key
comes only from the person's key file (`~/.g6/.env`), which is their approval to spend it. Every voice is written into
G6's media folder (`$G6_HOME/media`) and inspected from disk: it decodes, is not silent, and lasts a plausible time
for its text. A clip is transcribed only when it holds speech G6 can measure. Its best state is `qualified-draft`.

## When to use it

- You (a coding agent) need a spoken prompt, an announcement or narration for an app, a video or a test, and you cannot
  listen to it.
- You need a recording transcribed, and you want silence never sent (or paid for) and a transcript that cannot fit its
  sound refused rather than handed on.

## When not to use it

- Editing or levelling the voice afterwards: `adapt_audio`. Putting it under a video: `adapt_ffmpeg`.
- Judging whether a transcript says what was said: nothing here does; it checks only that the words fit the speech.
- Local transcription: not here yet; transcription is OpenAI's, a paid service.

## Ops

- `speak`: `text` (at most 5000 characters) spoken by a local `engine` (`espeak`) in `voice` (an eSpeak NG code such
  as `en-gb`, `fr` or `de+f3`) at `wpm` words a minute.
- `speak_paid`: `text` spoken by `engine` `openai` (`voice` alloy, ash, ballad, coral, echo, fable, nova, onyx, sage,
  shimmer or verse; `model` gpt-4o-mini-tts by default) or `elevenlabs` (`voice`, a voice id from the person's
  account). The action gate decides the request first; `allow_paid` is your word: without it nothing is sent, and with
  it nothing more opens.
- `transcribe`: `source`, a WAV kept in the media folder (a link there to a file elsewhere is refused, so nothing from
  outside the folder is ever sent), sent to `engine` `openai` (`whisper-1`; `language` optional) on the gate's allow,
  after G6 has measured the speech in it.
- `voices`: the voices eSpeak NG lists.
- `engines`: each engine, whether it is local, online or paid, and whether its key is in the key file, without asking
  any of them.

`speak` and `speak_paid` take `name` (G6 chooses the folder) and `overwrite`; every op takes `timeout`.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_voice", "speak", '{"text": "The build passed.", "voice": "en-gb"}')`, then open
`result.artifact.path`. `engines` runs over MCP as it is. `speak` and `voices` run eSpeak NG on the person's machine,
and `speak_paid` and `transcribe` spend their money, so all four are sensitive: over MCP each runs only when the person
who runs the server lists it (`adapt_voice.speak`) in `G6_MCP_SENSITIVE`. Ask them; never set it yourself, never write
their key file, and never send `allow_paid` without their agreement.

Composed: `adapt_audio` levels the voice (`normalise_loudness`) or turns a recording into a WAV for `transcribe`
(`transcode`); `adapt_ffmpeg` adds it to a video (`add_audio`). `action_gating` decides every paid request as a
`payment` and keeps its audit record under `$G6_HOME`. Another voice joins from Python with
`g6.media.adapt_voice.engines.register(engine)`.

## Failure modes it catches

- `audio_check_failed` (sound output that is silent, clipped or wrong, reported as done): a voice that is silent is
  refused with `G6_E_MEDIA_SILENT`, one too short or too long for its text with `G6_E_MEDIA_DURATION_MISMATCH`, one
  that does not decode with `G6_E_MEDIA_UNDECODABLE`, whichever engine made it, a paid one included; the file is
  removed.
- `wrong_transcript` (a transcript too empty or too long for the speech it came from), in part: it catches only an
  empty transcript and an impossible speaking rate. A silent clip, or one with no speech G6 can measure, is refused with
  `G6_E_MEDIA_SILENT` before anything is sent; a transcript with more than six words a second of measured speech, or
  with no words for a second or more of it, is refused with `G6_E_MEDIA_TRANSCRIPT_DOUBTFUL`. A wrong transcript of a
  plausible length passes: nothing here compares its words with what was said. The service's own signals
  (`avg_logprob`, `no_speech_prob`) are passed on under those names, never as a confidence.
- `unchosen_paid_service` (a paid outside service used without the person choosing it): `speak` never uses a paid or
  online voice; `speak_paid` and `transcribe` send only on the action gate's allow, which comes when the person's key
  file holds the engine's key; `allow_paid` is recorded as a claim; a key elsewhere approves nothing.

## Reading the result

- A voice is `qualified-draft` with `G6_E_MEDIA_PROBED`: `artifact` (path, measurements, checks, provenance) and the
  `engine`. A transcript is `qualified-draft` with `G6_E_MEDIA_TRANSCRIBED`: `text`, `language`, `segments`,
  `duration_s`, `speech_s`, `words`. `engines` answers `G6_E_MEDIA_DESCRIBED`, `voices` `G6_E_MEDIA_REPORTED`.
- A refusal is `blocked-escalated` with its code: those above, `G6_E_MEDIA_PAID_NOT_ALLOWED` (nothing was sent; the
  `action_gate` evidence says why, such as the line to add to the key file), `G6_E_OUTSIDE_PROGRAM_MISSING` (eSpeak NG
  is not installed; the reason says how), `G6_E_MEDIA_BACKEND_FAILED` (a service's error or a redirect it was not
  allowed to follow), `G6_E_MEDIA_FORMAT_MISMATCH` (`transcribe` takes a WAV).

## Blind spots

- The inspector measures; it does not listen. A voice that mispronounces a word, or a transcript with the wrong words
  at a plausible rate, passes.
- "Plausible" is wide: from 0.025 to 0.5 seconds a character (plus three seconds). Speech is 20 ms frames above
  -45 dBFS, so loud music counts as speech.
- A key in the key file approves every request to its service, with no spending cap: what a request costs is not known
  to G6 (the service's account says). Remove the key to stop it.
- eSpeak NG runs as you; G6 gives it a deadline and a clean environment, not a sandbox.

## What it needs

- eSpeak NG for `speak` and `voices`, installed by the person (github.com/espeak-ng/espeak-ng; `brew install
  espeak-ng`, `apt install espeak-ng`, or the Windows installer), on PATH or named by `G6_ESPEAK`. G6 never installs
  or bundles it (GPL; command line only).
- For `speak_paid` and `transcribe`: an account with OpenAI (`OPENAI_API_KEY`) or ElevenLabs (`ELEVENLABS_API_KEY`), the
  key in the person's key file, which they write themselves. `g6_describe` lists both keys.
- No Python extra.

## Examples

The engines, and whether the key file holds each paid one's key.

```g6-example op=engines
{}
```

A sentence in a British English voice (sensitive: it runs eSpeak NG).

```g6-example op=speak
{"text": "The build passed.", "voice": "en-gb", "name": "build-passed"}
```

A recording transcribed by OpenAI (sensitive: it spends money, on the action gate's allow).

```g6-example op=transcribe
{"source": "meeting.wav", "allow_paid": true}
```

From Python: without the key in the person's key file, a paid voice sends nothing, even with `allow_paid`.

```python
from g6.core.registry import get_registry

out = get_registry().invoke("adapt_voice", "speak_paid", {"text": "Hello", "engine": "openai",
                                                          "allow_paid": True}).unwrap()
assert out["completion_state"] == "blocked-escalated"
assert out["evidence"][0]["kind"] == "dry_run"
```

## The four open problems

- **Learning signals**: each refusal carries its code and what was measured (the length against the text, the words
  against the speech). Not yet: nothing counts them per engine.
- **Evaluators the model cannot influence**: the WAV is read back from disk and the speech measured by G6 before
  anything is sent; a service's success and its own signals are passed on, never trusted.
- **Transfer by failure mode**: "silent" and "wrong length" carry the same codes here as in `adapt_audio`,
  `adapt_synth` and `adapt_ffmpeg`; a doubtful transcript has its own.
- **Enough held-out cases**: Not yet: the tests use stand-in voices and recorded service replies, not clips with known
  transcripts.
