---
name: g6-ctx-vision
description: Use when an agent must see what is in a picture (describe it, answer a question about it, read its text, list its objects) through an image-capable model it was given, and must keep the model's account as a claim, with the picture checked before it is sent and the text reading cross-checked by Tesseract when that matters.
---

# G6 ctx_vision

Shows one picture to the image-capable model you give the block and records what it says as a claim. The picture is
decoded and checked before it is sent. `extract_text` can add Tesseract's independent reading, compared in order,
word by word, with the model's, and `detect_objects` checks each box against the picture's measured size.

## When to use it

- An agent must know what a screenshot, chart, receipt, sign or photo shows.
- Reading the text in a picture where a misread number matters: ask with `ocr_check`.
- After `adapt_image` or another media op made a picture in G6's media folder: describe it by name.

## When not to use it

- To prove what a picture shows: every answer is a model's claim.
- For pictures on the web: give the bytes or put the file in the media folder; nothing is fetched.
- Without an image-capable model: over MCP the model is the one the person set in their key file, and Claude Code and
  Codex as G6's model take no images; without one, only `extract_text` with `ocr_check` (Tesseract alone) answers.

## Ops

- `describe`: the model's description of the picture (`image` or `image_base64`, optional `prompt`), recorded as its claim.
- `answer`: the model's answer to `question` about the picture, recorded as its claim.
- `extract_text`: the model's reading of the text in the picture, and with `ocr_check` Tesseract's reading compared in order, word by word. Sensitive: the check runs tesseract on your computer.
- `detect_objects`: the objects the model lists, each box checked against the picture's measured size.

## Reading the result

The picture comes as `image`, the plain name of a file in G6's media folder (`$G6_HOME/media`; no folders, no links),
or as `image_base64`, at most 20 MiB. Only PNG, JPEG, GIF and WebP are opened, so no other format's decoder (such as
the Ghostscript program Pillow uses for EPS) is ever started, and the picture is decoded in full first: one that is not
of those formats (`G6_E_VISION_FORMAT`), does not decode (`G6_E_VISION_UNDECODABLE`), or has more than 40 million
pixels (`G6_E_VISION_TOO_LARGE`) is never sent.

Answers are `qualified-draft` (`G6_E_VISION_DESCRIBED`), with the picture's size, format and hash and a `claim`
entry naming the caller in the evidence. `extract_text` gives `text` and a `document` (provenance
`model_transcribed`); with `ocr_check`, `ocr` holds Tesseract's text, `agree` (the two readings are the same words in
the same order), `differences` (each span where they differ, the model's words beside Tesseract's, named on the card
too), `matching_words`, and as diagnostics `only_in_model`, `only_in_ocr` (words counted, in no order) and
`character_similarity`. Without a model that takes images it gives Tesseract's reading alone
(`G6_E_VISION_OCR_ONLY`), labelled as Tesseract's, not a model's. `detect_objects` gives `objects`, each with `label`, `box` and `box_check`; there is no
confidence.

Refusals are `blocked-escalated`: `G6_E_VISION_NO_MODEL` (no model given; over MCP, none configured),
`G6_E_VISION_NO_IMAGES` (the model takes no images, as Claude Code and Codex as G6's model do not),
`G6_E_VISION_MODEL_FAILED`, `G6_E_VISION_PARSE_FAILED`
(objects not given as JSON), `G6_E_VISION_INPUT`, `G6_E_VISION_EXTRA_MISSING` (Pillow not installed),
`G6_E_VISION_OCR_FAILED` and `G6_E_OUTSIDE_PROGRAM_MISSING` (tesseract not on PATH, with the install steps).

## Alone and composed

From Python: `CtxVisionBlock(caller=OllamaCaller(model="llava"))`, or any `g6.core.llm` caller whose
`supports_images` is true; local models (LLaVA, Llama 3.2 Vision, Qwen2.5-VL on Ollama) cost nothing per call, hosted
ones (Anthropic, an OpenAI-compatible service) are paid on your own account. Over MCP:
`g6_invoke("ctx_vision", "extract_text", {"image": "receipt.png", "ocr_check": true})` runs only when the person who
runs the server lists it in `G6_MCP_SENSITIVE`.

Composed: `adapt_image` makes or converts the picture; `knowledge_chunker` cuts an `extract_text` document into
passages; `g6.grounding` checks a claim against the text read.

## Failure modes it catches

- `misread_image` (an image's content misread, or not read at all): the picture is decoded before it is sent, a
  caller that cannot see it is refused instead of answering blind, and with `ocr_check` an independent reader names
  every span where the two readings differ, so two amounts swapped or a dropped "not" are caught.

## Blind spots

- `prompt_injection`: text in a picture can carry instructions. The prompts tell the model it is content, the reply
  is untrusted data and instruction-like phrasing is named on the card, but a model can still be swayed.
- Descriptions, answers and object lists are not checked against the picture beyond the boxes' bounds.
- Tesseract misreads too: agreement is evidence, not proof, and it reads poorly on photos and handwriting.
- Sending an image is not proof the model looked at it.

## What it needs

An image-capable `g6.core.llm` caller you give the block, or over MCP the one the person configured; Pillow (`pip install 'g6[image]'`) for the decode check;
for `ocr_check`, Tesseract OCR (`tesseract` on PATH; an outside program, Apache-2.0, that you install; G6 never
installs it). No
key and no network of its own.

## Examples

```g6-example op=describe
{"image": "chart.png", "prompt": "What does the chart show?"}
```

With no model configured this is refused with `G6_E_VISION_NO_MODEL`, and with one that takes no images with
`G6_E_VISION_NO_IMAGES`: never answered blind.

```g6-example op=answer
{"image": "chart.png", "question": "Which bar is tallest?"}
```

Refused the same way without a model that takes images; with one it gives the model's answer as a claim.

```g6-example op=detect_objects
{"image": "street.png"}
```

Refused the same way without a model that takes images; with one, each box is checked against the picture.

```g6-example op=extract_text
{"image": "receipt.png", "ocr_check": true}
```

Sensitive: the cross-check runs tesseract on your computer.

```python
from g6.sources.ctx_vision import CtxVisionBlock, CtxVisionInput

class TextOnly:  # a caller with no image input: it is refused, never asked blind
    def complete(self, system, user, *, max_tokens=1024):
        return "a red square"

out = CtxVisionBlock(caller=TextOnly()).infer(CtxVisionInput(op="describe", image="chart.png")).unwrap()
assert out["code"] == "G6_E_VISION_NO_IMAGES" and out["completion_state"] == "blocked-escalated"
```

## The four open problems

- **Learning signals**: with `ocr_check` each reply records how far the two readings agree. Not yet: agreement rates
  are not collected across calls.
- **Evaluators the model cannot influence**: Tesseract's reading and the picture's measured size, both outside the
  model; the decode check runs before the model sees anything.
- **Transfer by failure mode**: bound to `misread_image` ("text misread", "object invented"); the cross-check works
  with any image-capable caller.
- **Enough held-out cases**: Not yet: the tests use generated pictures, a deterministic model double and a stand-in
  Tesseract; there is no frozen set of pictures with known text.
