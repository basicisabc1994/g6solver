---
name: g6-ctx-langextract
description: Use when an agent must pull named fields (a date, a reference, an amount, a name) out of free text and needs each value tied to where it sits in the text, so a value the model invented or reworded is refused or flagged rather than passed as found, and a long text is read whole, never cut off (a field the model leaves empty is reported not checked: a fact it passes over is not caught).
---

# G6 ctx_langextract

Pulls named fields out of a text and holds every value to the text it came from. A model (the caller you give the
block) reads the whole text window by window, or your own regular expressions run in bounded child processes; then
each value is looked up in the text itself.

## When to use it

- Pulling fields out of a contract, an invoice, an email or a report, where each value must be traceable to its
  place in the text.
- Long texts: the model reads every window, so nothing after the first few thousand characters goes unread. A fact
  the model reads and passes over is not caught: its field is `not_checked`.
- When a fixed pattern describes the field exactly (a reference format): use `backend` `regex`.

## When not to use it

- For summaries, or for facts the text does not state: a value is kept only where the text holds it.
- For values that must be computed or converted (a total, a date in another format): the model is told to copy,
  not to infer, and a reworded value is flagged.

## Ops

- `extract`: each field's value with its start and end in the text and whether the text there is exactly that value (grounded); a value nowhere in the text is refused, never passed as found; `backend` `model` (the caller given from Python) or `regex` (your own `patterns`, each in a bounded child process).

## Reading the result

`fields` holds one entry per field, with `status`:

- `found`: the text holds the value exactly, on word boundaries; `value` is the text's own words at `start`:`end`,
  `grounded` true, and `alternatives` lists other values found for the field.
- `flagged`: the text holds the value only inside a longer word ("50 mg" in "150 mg") or only once case and runs of
  white space are ignored (`why` says which); `value` is what the model said, `text_at_span` the text's own words
  there, and `grounded` is false.
- `refused`: the model gave values that are nowhere in the text (`refused_values`); `value` is null.
- `not_checked`: the model gave no value (it answered null), and `why` says so. Nothing looks the field up in the
  text, so the text may still hold it: a fact the model missed is not caught here.
- `not_found`: the regex backend's pattern matched nothing in the text.

A found or flagged value carries `occurrences`, how many times the text holds it exactly, and `on_word_boundaries`,
how many of those stand as words of their own: a value the text holds in several places is said to.

The reply is `G6_E_LANGEXTRACT_EXTRACTED`, `qualified-draft`: where each value sits is checked, whether it is the
right value for its field is not. Refusals are `blocked-escalated`: `G6_E_LANGEXTRACT_NO_MODEL` (no caller was given;
over MCP, none is configured), `G6_E_LANGEXTRACT_MODEL_FAILED` and `G6_E_LANGEXTRACT_PARSE_FAILED` (a window's call failed,
or its reply was not a JSON object holding every field as a string, a list of strings or null, so nothing is reported
as absent from text no model read),
`G6_E_LANGEXTRACT_TOO_LONG` (more than 60 windows), `G6_E_LANGEXTRACT_EXAMPLE_UNGROUNDED` (an example whose value is
not in its own text), `G6_E_LANGEXTRACT_PATTERN_TIMEOUT` (stopped after five seconds),
`G6_E_LANGEXTRACT_PATTERN_INVALID` and `G6_E_LANGEXTRACT_INPUT`.

## Alone and composed

Give `text` and `fields` (each name with what it is), and optionally up to five `examples`, each a text with the
values copied exactly from it. `window` (default 6000 characters, overlapping by 400) sets how much the model reads
per call. Over MCP the model backend uses the model the person set in `~/.g6/.env` (for example
`G6_LLM_PROVIDER=claude_code`); with none, use `backend` `regex`: `g6_invoke("ctx_langextract", "extract",
{"text": "...", "backend": "regex", "patterns": {"ref": "[A-Z]{2}-[0-9]+"}})`.

From Python: `CtxLangExtractBlock(caller=...)` takes any `g6.core.llm` caller, local first (an `OllamaCaller`).

Composed: `knowledge_converter` turns a PDF into text first; a found value's `start` and `end` make a
`g6.knowledge` passage (`Document.passage`) that `knowledge_manifest` can keep and `g6.grounding` can cite.

## Failure modes it catches

- `missed_key_fact` (key facts lost past a cut-off in long text, or given values the text does not hold), in two
  ways only. A fact past a cut-off: the model reads every window of the whole text, with an overlap so a value on a boundary is read whole, and a window that could not
  be read stops the extraction instead of being reported as holding nothing. An invented value: every value is
  checked against the text, so an invented one is refused. A fact the model reads and leaves null is not caught: the
  field is `not_checked`, never reported as absent from the text.

## Blind spots

- `prompt_injection`: the text can carry instructions; it is fenced as data in every prompt and instruction-like
  phrasing is named on the card, but a model can still be swayed by it.
- A value spread over two sentences, or paraphrased, is refused or flagged even when it is right.
- A value that appears in the text in the wrong place (a date the model took from the wrong clause) is found: the
  check is where the words are, not what they mean.
- Each regular expression has five seconds and finds at most 50 matches.

## What it needs

For the model backend, a `g6.core.llm` caller you give the block, or over MCP the model the person configured;
nothing else. The regex backend runs your patterns
in a child of G6's own Python. No key, no outside program, no extra, no network.

## Examples

```g6-example op=extract
{"text": "Order AB-12 ships on 3 May. Order CD-345 is held.", "backend": "regex", "patterns": {"order": "[A-Z]{2}-[0-9]+"}}
```

`order` is found as `AB-12` at 6 to 11, with `CD-345` among its alternatives.

```g6-example op=extract
{"text": "Payment is due on 1 March 2027.", "fields": {"due": "the due date"}}
```

With no model configured, this is refused with `G6_E_LANGEXTRACT_NO_MODEL`, which says how to configure one.

```python
import json

from g6.sources.ctx_langextract import CtxLangExtractBlock, CtxLangExtractInput

class Guesser:  # in this example, a model that answers a date the text does not hold
    def complete(self, system, user, *, max_tokens=1024):
        return json.dumps({"due": "2 March 2027"})

out = CtxLangExtractBlock(caller=Guesser()).infer(CtxLangExtractInput(
    op="extract", text="Payment is due on 1 March 2027.", fields={"due": "the due date"})).unwrap()
assert out["fields"][0]["status"] == "refused" and out["fields"][0]["value"] is None
```

## The four open problems

- **Learning signals**: each reply counts found, flagged, refused, not-found and not-checked fields, and which windows
  were read.
  Not yet: no gold set of the caller's labelled documents is scored against.
- **Evaluators the model cannot influence**: the source text itself; the span check is fixed code and the model's
  answer cannot change where the text holds a value.
- **Transfer by failure mode**: bound to `missed_key_fact` ("fact invented", "fact missed past the window"); the same
  check holds every backend, the regex one included.
- **Enough held-out cases**: Not yet: the tests use deterministic model doubles over synthetic texts.
