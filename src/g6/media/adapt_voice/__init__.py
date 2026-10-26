# SPDX-License-Identifier: Apache-2.0
"""g6.media.adapt_voice — speak text and transcribe speech, refusing a transcript that is empty or at an impossible speaking rate; a wrong one of a plausible length passes.

An extension in the media family. Its local voice is eSpeak NG, a program you install (``__g6_outside__``; GPL, so G6
runs it over its command line only and never bundles it); the text reaches it on its input, never on its command line.
Its paid voices are OpenAI's and ElevenLabs', and its transcription is OpenAI's: each key is declared in
``__g6_keys__`` and read only from your key file, ``~/.g6/.env``, and every request is a payment ``g6.action_gating``
decides before anything is sent (a key in the file is your approval; the caller's ``allow_paid`` can only hold a
request back).

    from g6.core.registry import get_registry
    out = get_registry().invoke("adapt_voice", "speak", {"text": "The build passed.", "voice": "en-gb"}).unwrap()
    out["artifact"]["path"], out["artifact"]["measured"]["duration_s"]   # a WAV under $G6_HOME/media, about 1.3

What it checks: every voice is written into the media folder and inspected from disk: it decodes, is not silent
(``G6_E_MEDIA_SILENT``) and lasts a plausible time for its text (``G6_E_MEDIA_DURATION_MISMATCH``), whichever engine
made it. A clip is sent for transcription only when it holds speech G6 can measure, so silence is never sent or paid
for, and a transcript is refused (``G6_E_MEDIA_TRANSCRIPT_DOUBTFUL``) only when it is empty for a second or more of
that speech or has more than six words a second of it. A wrong transcript of a plausible length passes: nothing
compares its words with what was said. Results are ``qualified-draft``.

Not ported from the archive's adapt_voice, and why:

* Coqui TTS (ADAPT_VOICE-01): it was third in the default chain and could load model weights under a non-commercial
  licence; the owner's standing decision is never Coqui. Asking for it is refused by name;
* the ``verified`` default and the fixed confidence of 1.0 on a paid transcript (ADAPT_VOICE-02), and Whisper's
  language probability reported as the transcript's confidence (-04): the service's own signals are passed on under
  their own names;
* edge-tts, tried first and online without consent (ADAPT_VOICE-03), LGPL and a Microsoft service; pyttsx3, whose Linux
  driver loads the GPL eSpeak library into G6's own process (eSpeak NG is used over its command line instead);
  local transcription with faster-whisper (its dependencies bundle FFmpeg libraries and it downloads its model on
  first use; a later port can add it as an outside program or extra);
* the key as a tool parameter and the hosted entitlement (-05): keys come only from the person's key file;
* the audio-edit duplicates (``adapt_audio`` has them), the backend-native lane, the planner, the store and the
  scaffold.
"""
from g6.media.adapt_voice import engines, gate
from g6.media.adapt_voice.block import SENSITIVE_OPS, AdaptVoiceBlock, VoiceInput

__g6_component__ = True
__g6_block__ = "AdaptVoiceBlock"
__g6_family__ = "media"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # they run eSpeak NG or spend money with a speech service
__g6_requires__: tuple[str, ...] = ("action_gating",)  # it decides every paid request before anything is sent
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("audio_check_failed", "wrong_transcript", "unchosen_paid_service")
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (engines.ESPEAK,)
__g6_extras__: dict[str, tuple[str, ...]] = {}
__g6_keys__ = engines.KEYS  # from the person's key file; a key there approves spending it

__all__ = ["SENSITIVE_OPS", "AdaptVoiceBlock", "VoiceInput", "engines", "gate"]
