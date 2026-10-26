---
name: g6-adapt-comfyui
description: Use when you need a picture made or edited from a text prompt by a diffusion model on the ComfyUI server the person runs on this machine (text to image, image to image, inpainting, upscaling, or your own ComfyUI workflow), with every picture fetched into G6's media folder and checked before you use it.
---

# G6 adapt_comfyui

`g6.media.adapt_comfyui` runs picture workflows on ComfyUI, a program the person installs and starts themselves. G6
builds the graph (or takes yours), checks it against the server's own node list, sends it, waits for it, fetches each
picture into G6's media folder (`$G6_HOME/media`) and inspects it from disk: it decodes, is a PNG, is the size you
asked for, is not blank, and there are as many as you asked for. It is an extension in the media family and the
family's `comfyui` image backend. Its best state is `qualified-draft`.

## When to use it

- You (a coding agent) need a picture of something described in words, and the person has ComfyUI running with a
  checkpoint: a hero image, an illustration, a texture from a prompt.
- You need to repaint part of a picture (`inpaint`), restyle one (`img2img`) or enlarge one with an upscale model.
- You have a ComfyUI workflow of your own in API format and want its pictures checked, not just its run reported.
- You want to know, not guess, that the picture you got exists, decodes, is the right size and is not black.

## When not to use it

- No ComfyUI on this machine: every op refuses with `G6_E_OUTSIDE_PROGRAM_MISSING` and the install steps. For a
  picture that needs no model, use `adapt_generative_art`.
- Judging whether a picture shows what you asked for: nothing here looks at the content. Open the file yourself.
- A server on another machine: G6 talks only to 127.0.0.1, ::1 or localhost.
- Downloading models, managing custom nodes or running ComfyScript: not here; the person does those in ComfyUI.

## Ops

- `txt2img`: pictures from `prompt` (and `negative_prompt`) with `model`, a checkpoint the server lists, at `width`
  by `height` (multiples of 8, 512 by default), `steps`, `cfg`, `sampler`, `scheduler`, `seed` (random when not
  given, and reported) and `batch_size` (1 to 8).
- `img2img`: `source`, a picture in the media folder, sampled again from `prompt` at `denoise` (0.75 by default).
- `inpaint`: `source` repainted where `mask` (also a media file) is white in its red channel.
- `upscale`: `source` enlarged by `model`, an upscale model the server lists; it must come back larger by one whole
  factor in both directions.
- `submit_workflow`: your own API-format `workflow` (a JSON object of nodes) of ComfyUI's core node classes
  (`capabilities` lists the ones this server has), ending in `PreviewImage`; give `images`, `width` and `height` to
  have the count and size checked too.
- `get_image`: the pictures of `prompt_id`, a prompt already sent (say `wait: false` to send without waiting).
- `list_models`: the checkpoints and upscale models the server lists.
- `capabilities`: the server's version, devices and node count, and which of G6's workflows its nodes and models can
  run. Call it first.
- `get_status`: the server's version, devices and how many prompts are running and waiting.

Every picture op takes `name` (the file name; G6 chooses the folder, and a batch adds `-1`, `-2`), `overwrite`,
`wait` and `timeout` (seconds to wait, 300 by default). A `source` or `mask` of more than 4096 x 4096 pixels is
refused from its header, before it is decoded or sent.

## Alone and composed

Alone over MCP: `g6_invoke("adapt_comfyui", "capabilities", "{}")`, then `txt2img` with a checkpoint it lists, then
open `result.artifact.path` (and `result.artifacts` for a batch). Every op but `ops` and `help` talks to the person's
ComfyUI, so every one is sensitive: over MCP it runs only when the person who runs the server lists `adapt_comfyui` (or
one op, such as `adapt_comfyui.txt2img`) in `G6_MCP_SENSITIVE`. Ask them; never set it yourself. From Python:
`get_registry().invoke("adapt_comfyui", op, params)`.

Composed:

- Through the family's backend registry: `image_backends.generate("comfyui", ImageRequest(prompt=..., options={"model":
  ...}))`, so a router can pick it beside the `procedural` backend.
- A picture from `adapt_generative_art` or any media file can be the `source` of `img2img`, `inpaint` or `upscale`.
- The artifact's `provenance.recipe.graph` is the exact graph that ran; send it back with `submit_workflow` (with the
  same seed) to make the picture again.

## Failure modes it catches

- `visual_check_failed` (an image, video or 3D output that is blank, corrupt or wrong, reported as done): every picture
  the server lists is fetched and inspected. A black picture (what a failed decode leaves) is refused with
  `G6_E_MEDIA_BLANK`; a wrong size with `G6_E_MEDIA_SIZE_MISMATCH`; a picture that could not be fetched, fewer pictures
  than the batch asked for, or a finished prompt with none, with `G6_E_MEDIA_NO_OUTPUT`. One bad picture refuses the
  whole result and removes every file of it.
- Before anything is sent: a node class the server lacks, a node class that is not one of ComfyUI's core nodes and
  that the person who runs G6 has not named (a custom node, or an API node that spends money), a link to a missing
  node or output, a missing required input, an output node other than `PreviewImage`, a path that leaves its folder,
  or (in G6's own graphs) a checkpoint,
  upscale model, sampler or scheduler the server does not list is refused with `G6_E_MEDIA_INPUT_INVALID`, naming what
  the server has.

## Reading the result

- A result is `qualified-draft` with `warning_card.code` `G6_E_MEDIA_INSPECTED`; `artifact` is the first picture,
  `artifacts` all of them, each with its checks, measurements, SHA-256 and provenance (ComfyUI's version, the seed, the
  graph and its hash). `cached_from` names the earlier run a cached prompt was read from.
- `list_models`, `capabilities` and `get_status` are `qualified-draft` with `G6_E_MEDIA_REPORTED`: what the server
  said about itself.
- A refusal is `blocked-escalated` with its code: the ones above, `G6_E_MEDIA_PENDING` (still running, or sent with
  `wait: false`: ask `get_image` with the `prompt_id` given), `G6_E_MEDIA_BACKEND_FAILED` (ComfyUI reported an error,
  with its message, or refused the workflow), `G6_E_OUTSIDE_PROGRAM_MISSING` (nothing answering, with the install
  steps), `G6_E_OUTSIDE_SERVICE_NOT_LOCAL` (the address points off this machine) or `G6_E_MEDIA_EXTRA_MISSING`.

## Blind spots

- The inspector measures; it does not look. A picture can pass and still be the wrong subject, malformed hands or
  unreadable text: a result depends on the checkpoint the person installed and on the prompt.
- Blank means nearly one colour. A noisy or washed-out picture passes. The smallest detail it can see covers 0.1 % of
  the picture; a picture longer than 1024 pixels on a side is measured on a copy reduced by a whole factor, so lines
  thinner than that factor are averaged with what surrounds them and faint ones are not seen.
- A fully cached prompt (the same graph and seed again) is answered with the earlier run's pictures. When that run has
  left the server's history, the prompt is refused rather than re-run; change the seed.
- A custom node the person allows can do whatever its code does (read or write files, reach the network, spend
  money): G6 checks node class names and output nodes, not what a node does. Source pictures are uploaded into
  ComfyUI's temporary folder, which ComfyUI empties when it starts.
- When ComfyUI refuses a workflow (HTTP 400), its reason stays in ComfyUI's log; G6 says which request failed.
- Files are kept in the media folder until you delete them.

## What it needs

- ComfyUI (GPL-3.0), installed and started by the person: `python main.py` listens on 127.0.0.1:8188, or set
  `G6_COMFYUI_URL` where G6 runs to another address on this machine. G6 never installs, bundles or stands in for it,
  and never downloads models: the person puts checkpoints (and upscale models) in its models folders, under their own
  licences.
- Optionally, `G6_COMFYUI_NODES` where G6 runs: more node classes a workflow may use, by name and comma-separated.
  Only the person who runs G6 sets it; no call can.
- `pip install "g6[image]"` for Pillow, which inspects every picture. Without it, the picture ops refuse with
  `G6_E_MEDIA_EXTRA_MISSING` before anything is sent.

## Examples

What the server can run, first (sensitive: it asks the person's ComfyUI).

```g6-example op=capabilities
{}
```

A picture from a prompt, with a checkpoint `capabilities` listed.

```g6-example op=txt2img
{"prompt": "a lighthouse on a cliff at dusk, oil painting", "model": "your-checkpoint.safetensors", "width": 768, "height": 512, "steps": 25, "seed": 7, "name": "lighthouse"}
```

That picture enlarged by an upscale model the server has.

```g6-example op=upscale
{"source": "lighthouse.png", "model": "your-upscaler.pth"}
```

Your own workflow, with the count and size checked.

```g6-example op=submit_workflow
{"workflow": {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "your-checkpoint.safetensors"}}, "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "a red kite", "clip": ["1", 1]}}, "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}}, "4": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 512, "batch_size": 1}}, "5": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0, "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}}, "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}}, "7": {"class_type": "PreviewImage", "inputs": {"images": ["6", 0]}}}, "images": 1, "width": 512, "height": 512}
```

## The four open problems

- **Learning signals**: each refusal carries its code, the failed check and the prompt id, so failures can be counted
  per workflow and checkpoint. Not yet: nothing records them across calls.
- **Evaluators the model cannot influence**: the inspector reads each file from disk with Pillow; the server's own
  "success" and the caller's word count for nothing until it passes.
- **Transfer by failure mode**: "no picture", "blank", "wrong size" and "missing node" have the same codes here as in
  every media backend.
- **Enough held-out cases**: Not yet: there is no fixed set of seeds and prompts with stored hashes per checkpoint; the
  tests run against a stand-in server, not a real model.
