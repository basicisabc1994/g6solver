---
name: g6-adapt-image
description: Use when you need to resize, crop, rotate, flip, recolour, combine or convert a picture, measure one strictly, or have one generated (locally through ComfyUI or a procedural technique, or by a paid image service only when G6's action gate allows the payment), and you want every resulting file checked before you use it.
---

# G6 adapt_image

`g6.media.adapt_image` edits and measures pictures with Pillow, and generates them through the media family's
registered image backends. Every picture it makes is written into G6's media folder (`$G6_HOME/media`) and inspected
from disk against the size it must have: it decodes, is the format and size asked for, and is not blank. A paid image
service is used only from `generate_paid`, for a backend the person registered, and only when G6's action gate lets
that payment through: the person's key file (`~/.g6/.env`) holding the backend's key is their approval to spend it.
Without it the reply is a dry run. Its best state is `qualified-draft`.

## When to use it

- You (a coding agent) need a thumbnail, a crop, a rotated or flipped copy, a lighter or more saturated version, a
  logo placed on a picture, or the same picture as JPEG or WebP, and you want to know the file is what you asked for.
- You need the true format, size, mode and frame count of a picture, read from its bytes, not from its name.
- You want a picture generated without choosing the backend yourself: a prompt goes to the person's ComfyUI, a named
  technique to the `procedural` backend, and nothing goes to a paid service unless the action gate lets it through.

## When not to use it

- Fetching a picture from the web: there is no URL input. Put the picture in the media folder, or send its bytes as
  base64 (at most 25 MiB).
- Judging what a picture shows: nothing here does. The optional vision check (from Python) records a model's
  description as a claim, never as a check.
- Upscaling with a model, image-to-image or inpainting: use `adapt_comfyui`. `resize` here is Lanczos resampling.
- Sound, video, diagrams or charts: other media extensions.

## Ops

- `resize`: `width`, `height` or both (one side alone keeps the aspect ratio), by Lanczos resampling.
- `crop`: `box` `[left, top, right, bottom]`, which must lie inside the picture (it is never padded).
- `rotate`: `angle` in degrees, counter-clockwise; a right angle turns exactly, any other grows the canvas to hold
  the picture (that size is not compared).
- `flip`: `direction` `horizontal` or `vertical`.
- `adjust_colour`: `brightness`, `contrast` and `saturation` as factors from 0 to 4, 1 for no change.
- `composite`: `overlay` (or `overlay_base64`) placed on the picture at `x`, `y`.
- `convert`: the same picture as `format` `png`, `jpeg` or `webp` (`quality` 1 to 100); a JPEG has no transparency,
  so a transparent picture is laid on white.
- `get_image_info`: the picture decoded in full and measured: format and MIME type from its bytes, width, height,
  mode, frames, bytes, SHA-256. Nothing is written.
- `generate`: a picture from a local backend: `backend` named, or `comfyui` with a `prompt` and `procedural`
  without (technique and parameters in `options`); `width`, `height`, `seed`. It never starts Blender: use
  `adapt_blender`.
- `generate_paid`: a picture from the paid `backend` the person registered, from `prompt`. Each request is a payment
  `g6.action_gating` decides first: it sends on `allow`, or on `allow_with_notice` with `G6_E_KEY_FILE_APPROVED` (the
  key file holds the backend's key); otherwise nothing is sent and the reply is the dry run with the gate's decision
  and message (such as `G6_E_KEY_MISSING` and the line to add). `allow_paid` is your word: without it nothing is sent;
  with it nothing more opens.
- `backends`: the image backends registered and what each is (local, network, paid), without asking any of them.

Every op that takes a picture takes `source` (a file name in the media folder) or `image_base64`, one of them. Every
op that makes one takes `name` (G6 chooses the folder) and `overwrite`; the edits also take `format` and `quality`.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_image", "resize", '{"source": "photo.png", "width": 320}')`, then open
`result.artifact.path`. The edits, `get_image_info` and `backends` run over MCP as they are. `generate` may run a
workflow on the person's ComfyUI and `generate_paid` spends money, so both are sensitive: over MCP each runs only when
the person who runs the server lists it (`adapt_image.generate`, `adapt_image.generate_paid`) in `G6_MCP_SENSITIVE`.
Ask them; never set it yourself, never write their key file, and never send `allow_paid` without their agreement.
Even then, `generate_paid` sends only when the action gate lets it through.

Composed:

- A picture from `adapt_comfyui` or `adapt_generative_art` is a `source` here by its file name.
- `action_gating` decides every `generate_paid` request as a `payment` before anything is sent, and keeps its audit
  record under `$G6_HOME`.
- A paid backend exists only when the person registers one before starting G6, its key in their key file:
  `image_backends.register(StabilityBackend.from_key_file(model=...))` (`STABILITY_API_KEY`; also `ReplicateBackend`,
  `REPLICATE_API_TOKEN`, and `ImagesEndpointBackend` for an OpenAI-compatible images endpoint, `G6_IMAGES_API_KEY`,
  from `g6.media.adapt_image.paid`). `g6_describe` lists these keys.
- From Python, `AdaptImageBlock(vision=caller)` with an image-capable `g6.core.llm` caller and `vision_check: true`
  adds the model's description of the result to the evidence as a claim.

## Failure modes it catches

- `visual_check_failed` (an image, video or 3D output that is blank, corrupt or wrong, reported as done): every file
  written is inspected against the size computed for it. A black result (brightness 0), a crop of an empty margin or a
  paid service's uniform picture is refused with `G6_E_MEDIA_BLANK`; a service that ignored the size asked for with
  `G6_E_MEDIA_SIZE_MISMATCH`; one that sent another format with `G6_E_MEDIA_FORMAT_MISMATCH`. The file is removed.
- `unchosen_paid_service` (a paid outside service used without the person choosing it): `generate` never uses a paid
  or online backend and never falls back to one; `generate_paid` needs a backend the person registered, over MCP the
  server's `G6_MCP_SENSITIVE`, and the action gate, which lets the payment through only when the person's key file
  holds the backend's key. `allow_paid` is recorded as a claim: a caller's word opens nothing.

## Reading the result

- A result is `qualified-draft`: `G6_E_MEDIA_INSPECTED` for a picture made (its `artifact` holds the path, the MIME
  type read from the bytes, the checks, measurements and provenance), `G6_E_MEDIA_MEASURED` for `get_image_info`,
  `G6_E_MEDIA_DESCRIBED` for `backends`.
- A refusal is `blocked-escalated` with its code: the inspection codes above, `G6_E_MEDIA_UNDECODABLE` (the source is
  not a whole picture in PNG, JPEG, WebP, GIF, BMP or TIFF; a format another program would draw, such as EPS or WMF,
  is never opened), `G6_E_MEDIA_INPUT_INVALID` (a box outside the picture, a picture given or a size asked for of more
  than 4096 x 4096 pixels, refused before anything is decoded, bad base64, a missing file),
  `G6_E_MEDIA_PAID_NOT_ALLOWED` (nothing was sent; the `action_gate` evidence says why and what the person must do),
  `G6_E_MEDIA_BACKEND_UNAVAILABLE`, `G6_E_MEDIA_BACKEND_FAILED` (a service's error, a
  content filter, a redirect to a private address) or `G6_E_MEDIA_EXTRA_MISSING`.

## Blind spots

- The inspector measures; it does not look. A crop of the wrong region or a paid picture of the wrong subject passes.
- Blank means nearly one colour; a washed-out or noisy result passes. The smallest detail it can see covers 0.1 % of
  the picture (about 130 x 130 pixels in a 4096 x 4096 one). A picture longer than 1024 pixels on a side is measured
  on a copy reduced by a whole factor (4 at 4096), each pixel the mean of a block, so lines thinner than that are
  averaged with what surrounds them and faint ones are not seen.
- An animation is read as its first frame. A JPEG or WebP written at low `quality` is lossy and still passes.
- A key in the key file approves every paid request to its service, with no spending cap: what a paid picture costs
  is not known to G6 (the service's own account says). Remove the key to stop it.
- A vision model's description is a claim: it is not compared with anything and never changes a label.

## What it needs

- `pip install "g6[image] @ git+https://github.com/basicisabc1994/g6solver"` for Pillow (MIT-CMU). Without it, every op that reads or makes a picture refuses with
  `G6_E_MEDIA_EXTRA_MISSING`; `backends`, `ops` and `help` still answer.
- For `generate` with a prompt: the person's ComfyUI, through `adapt_comfyui`'s `comfyui` backend.
- For `generate_paid`: an account with Stability AI, Replicate or an OpenAI-compatible images service, and its key in
  the person's key file, which they write themselves. Nothing here reads a key from anywhere else.

## Examples

A small picture measured from its bytes.

```g6-example op=get_image_info
{"image_base64": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAMCAIAAADkharWAAAAG0lEQVR42mNkYGgQYGAgHrEwiDCQBEY1jBQNAGacAnoKHKLTAAAAAElFTkSuQmCC"}
```

The same picture enlarged to 64 pixels wide (the height follows), as a WebP.

```g6-example op=resize
{"image_base64": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAMCAIAAADkharWAAAAG0lEQVR42mNkYGgQYGAgHrEwiDCQBEY1jBQNAGacAnoKHKLTAAAAAElFTkSuQmCC", "width": 64, "format": "webp", "name": "gradient-wide"}
```

Its left half.

```g6-example op=crop
{"image_base64": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAMCAIAAADkharWAAAAG0lEQVR42mNkYGgQYGAgHrEwiDCQBEY1jBQNAGacAnoKHKLTAAAAAElFTkSuQmCC", "box": [0, 0, 8, 12]}
```

The picture turned a quarter, counter-clockwise (16 by 12 becomes 12 by 16).

```g6-example op=rotate
{"image_base64": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAMCAIAAADkharWAAAAG0lEQVR42mNkYGgQYGAgHrEwiDCQBEY1jBQNAGacAnoKHKLTAAAAAElFTkSuQmCC", "angle": 90}
```

Mirrored left to right.

```g6-example op=flip
{"image_base64": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAMCAIAAADkharWAAAAG0lEQVR42mNkYGgQYGAgHrEwiDCQBEY1jBQNAGacAnoKHKLTAAAAAElFTkSuQmCC", "direction": "horizontal"}
```

Brighter and more saturated.

```g6-example op=adjust_colour
{"image_base64": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAMCAIAAADkharWAAAAG0lEQVR42mNkYGgQYGAgHrEwiDCQBEY1jBQNAGacAnoKHKLTAAAAAElFTkSuQmCC", "brightness": 1.2, "saturation": 1.5}
```

A small red square laid on it, 4 pixels in from the top left corner.

```g6-example op=composite
{"image_base64": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAMCAIAAADkharWAAAAG0lEQVR42mNkYGgQYGAgHrEwiDCQBEY1jBQNAGacAnoKHKLTAAAAAElFTkSuQmCC", "overlay_base64": "iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAIAAAAmkwkpAAAAFElEQVR42mO8IyfHAANMDEgANwcAOHIBID2Vy4IAAAAASUVORK5CYII=", "x": 4, "y": 4}
```

The same picture as a JPEG.

```g6-example op=convert
{"image_base64": "iVBORw0KGgoAAAANSUhEUgAAABAAAAAMCAIAAADkharWAAAAG0lEQVR42mNkYGgQYGAgHrEwiDCQBEY1jBQNAGacAnoKHKLTAAAAAElFTkSuQmCC", "format": "jpeg", "quality": 85}
```

What backends there are.

```g6-example op=backends
{}
```

A picture from a prompt on the person's ComfyUI (sensitive).

```g6-example op=generate
{"prompt": "a paper boat on a pond", "options": {"model": "your-checkpoint.safetensors"}, "width": 512, "height": 512}
```

From Python: a paid request sends nothing while the person's key file lacks the backend's key, even with a backend
registered and `allow_paid` (a key given in Python approves nothing).

```python
from g6.core.registry import get_registry
from g6.media._types import image_backends
from g6.media.adapt_image.paid import PaidService, StabilityBackend

image_backends.register(StabilityBackend(PaidService(key="the-persons-key", model="an-engine")), replace=True)
out = get_registry().invoke("adapt_image", "generate_paid", {"backend": "stability", "prompt": "a kite",
                                                              "allow_paid": True}).unwrap()
assert out["completion_state"] == "blocked-escalated" and out["code"] == "G6_E_MEDIA_PAID_NOT_ALLOWED"
gate = next(e for e in out["evidence"] if e["kind"] == "action_gate")
assert out["evidence"][0]["kind"] == "dry_run" and gate["decision"] != "allow"
image_backends.unregister("stability")
```

## The four open problems

- **Learning signals**: each refusal carries its code and the failed check; paid refusals carry the service's status.
  Not yet: nothing counts them across calls or per backend.
- **Evaluators the model cannot influence**: the inspector reads the written file with Pillow, and the expected size is
  computed from the request, not read from the result; a service's success and a vision model's words are recorded,
  never trusted.
- **Transfer by failure mode**: "blank", "wrong size" and "wrong format" carry the same codes from an edit, a local
  backend and a paid one.
- **Enough held-out cases**: Not yet: the tests use generated pictures and recorded service replies, not a stored set
  of reference pictures with hashes.
