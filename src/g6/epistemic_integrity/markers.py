# SPDX-License-Identifier: Apache-2.0
"""English lexical markers with exact spans and local negation; no alignment score."""
import re

_LEXICON = {
    "uncertainty": (r"i['’]?m not sure", r"i don['’]?t know", "might", "could be", "perhaps", "possibly",
                    "uncertain", "i believe", "i think"),
    "refusal": (r"i can['’]?t", r"i won['’]?t", r"i['’]?m unable", r"i['’]?m not able to", "i must decline",
                "i cannot", "i will not"),
    "agreement": (r"that['’]?s correct", r"you['’]?re right", "i agree", "exactly", "absolutely", "indeed",
                  r"that['’]?s true", "yes"),
    "disagreement": ("actually", "i disagree", r"that['’]?s not quite right", "on the contrary",
                     r"that['’]?s incorrect", r"that['’]?s wrong"),
    "hedging": ("however", "although", "on the other hand", "while", "nevertheless", "that said", "with that in mind"),
    "safety": ("harmful", "unsafe", "inappropriate", "offensive", "dangerous", "illegal", "unethical"),
    "confidence": ("certainly", "definitely", "clearly", "without doubt", r"i['’]?m confident", "obviously",
                   "without question"),
}
_PATTERNS = {category: re.compile(r"\b(?:" + "|".join(phrases) + r")\b", re.IGNORECASE)
             for category, phrases in _LEXICON.items()}
_NEGATORS = {"not", "never", "no", "isn't", "isn’t", "cannot", "can't", "can’t"}


def markers(text: str) -> dict[str, list[dict]]:
    """Return spans per category; negation examines at most three preceding words in a clause.

    This is a lexical hint: quotations, irony, distant negation and other languages
    can mislead it. No score, polarity reward, model or behavioural conclusion.
    """
    if not isinstance(text, str) or len(text) > 100_000:
        raise ValueError("[G6_E_MARKER_INPUT] text must be at most 100000 characters")
    result = {}
    for category, pattern in _PATTERNS.items():
        hits = []
        for match in pattern.finditer(text):
            clause = re.split(r"[.!?;,\n]", text[max(0, match.start() - 100):match.start()])[-1]
            preceding = re.findall(r"[\w'’]+", clause.casefold())[-3:]
            hits.append({"text": match[0], "start": match.start(), "end": match.end(),
                         "negated": bool(set(preceding) & _NEGATORS)})
        result[category] = hits
    return result
