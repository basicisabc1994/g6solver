---
name: g6-adapt-blender
description: Use when you need a 3D scene built, lit, rendered or exported (OBJ, STL, GLB, FBX) with the Blender the person installed, and you want every change read back from Blender's scene and every render and export inspected before you use it.
---

# G6 adapt_blender

`g6.media.adapt_blender` builds 3D scenes in the Blender the person installed and renders and exports them. Blender
runs only G6's own script, which reads each op as JSON data, as a separate program with a deadline and a clean
environment. Work happens in a session (a scene kept in G6's state). Every change is read back from Blender's scene
and compared with what was asked; a render is a PNG in G6's media folder (`$G6_HOME/media`) inspected from disk (it
decodes, is the size asked for and is not blank), and an export is a mesh read from disk as far as its format allows:
all of an OBJ or STL, a GLB's JSON but not its binary chunk, an FBX's header alone. Its best state is
`qualified-draft`.

## When to use it

- You (a coding agent) need a product shot, an icon, a diagram of objects in space, or a model for a game or a 3D
  printer, and you cannot look at the render yourself.
- You have a scene as a `SceneSpec` (objects in a tree, camera, lights), or a mesh from `adapt_generative_art`, and need
  it rendered or exported.

## When not to use it

- Without Blender: every op but `session_new` and `session_delete` refuses with the install steps. For meshes alone,
  `adapt_generative_art` makes OBJ files with no outside program.
- Running your own Blender Python: there is no op for it, by design.
- Judging what a render shows: nothing here does.

## Ops

- `session_new`: an empty working scene; its `session` id goes to every other op.
- `session_delete`: the session's scene and folder removed.
- `create_primitive`: `object` (a name), `primitive` (cube, sphere, plane, cylinder, cone, torus), `location`,
  `rotation_euler` (radians), `scale`, `colour` (linear RGB, 0 to 1).
- `transform_object`: `object` with any of `location`, `rotation_euler`, `scale`.
- `delete_object`: `object`.
- `set_material`: `object`, `colour`.
- `set_camera`: `location`, `look_at`, `lens_mm`.
- `add_light`: `object` (its name), `kind` (point, sun, spot, area), `location`, `energy`, `colour`.
- `build_scene`: `scene`, a whole `SceneSpec` (objects, each a primitive or a `mesh` file in the media folder, with a
  transform, a colour and a parent; a camera; lights), built in a fresh scene.
- `render`: `width`, `height` (16 to 4096), `engine` (eevee, cycles, workbench), `samples`; a camera framing the
  scene, a sun and a dark backdrop are placed when the scene has none, and the reply says so.
- `viewport_preview`: a quick solid-shaded (workbench) render at `width`, `height`.
- `export`: `format` obj, stl, glb or fbx.
- `import_file`: `source`, an OBJ, STL or glTF (`.gltf` or `.glb`) file in the media folder, added to the scene. A file
  that names another file is refused before Blender starts, because Blender's importers open what it names wherever
  it is: every `uri` in a glTF must be a `data:` URI, and an OBJ may not have `mtllib`, `maplib`, `usemap`, `call` or
  `csh`. FBX is not imported (its texture paths are absolute); export it as GLB first. A `SceneSpec` mesh is checked
  the same way.
- `list_objects`: what Blender says the scene holds.
- `capabilities`: Blender's version, render engines and exporters.

Every op that writes a file takes `name` (G6 chooses the folder) and `overwrite`; every op takes `timeout` (at most
600 seconds).

## Alone and composed

Alone over MCP: `session_new`, then `g6_invoke("adapt_blender", "build_scene", '{"session": "s-...", "scene": {...}}')`,
then `render`, and open `result.artifact.path`. `session_new` and `session_delete` run over MCP as they are; every
other op runs Blender on the person's machine, so it is sensitive: over MCP it runs only when the person who runs the
server lists it (`adapt_blender.render`) or the component (`adapt_blender`) in `G6_MCP_SENSITIVE`.

Composed: a terrain mesh from `adapt_generative_art` is a `mesh` object in a `SceneSpec`; `adapt_image` resizes or
converts a render, but `adapt_image`'s `generate` never starts Blender (it refuses the `blender` backend, so its own
permission cannot run Blender); from Python the family's registry renders a `SceneSpec` with
`image_backends.generate("blender", ImageRequest(options={"scene": {...}}))`; `adapt_ffmpeg` turns a set of renders
into a video.

## Failure modes it catches

- `visual_check_failed` (an image, video or 3D output that is blank, corrupt or wrong, reported as done):
  - a render Blender finished but that is blank is refused with `G6_E_MEDIA_BLANK`, one of the wrong size with
    `G6_E_MEDIA_SIZE_MISMATCH`, a render or export that never appeared with `G6_E_MEDIA_NO_OUTPUT`; the file is
    removed;
  - an export whose mesh does not parse, has no faces or is flat along every axis but one is refused;
  - a change that does not read back (an object not where it was put, a colour not the one set, a light missing, a
    `SceneSpec` object off its transform or parent) is refused with `G6_E_MEDIA_READBACK_MISMATCH`, and the session
    keeps the scene it had before.

## Reading the result

- A scene change is `qualified-draft` with `G6_E_MEDIA_READ_BACK` and a `readback` evidence item listing each check;
  `scene` holds what Blender read back. A render is `qualified-draft` with `G6_E_MEDIA_INSPECTED` and its `artifact`;
  an export is `qualified-draft` with `G6_E_MEDIA_MESH_READ`, whose message and `mesh_read` evidence item say how far
  its format was read. `list_objects` and `capabilities` answer `G6_E_MEDIA_REPORTED`; sessions `G6_E_MEDIA_SESSION`.
- A refusal is `blocked-escalated` with its code: those above, `G6_E_OUTSIDE_PROGRAM_MISSING` (Blender is not
  installed; the reason says how), `G6_E_MEDIA_BACKEND_FAILED` (Blender's own error), `G6_E_MEDIA_TIME_LIMIT`,
  `G6_E_MEDIA_INPUT_INVALID` (an unknown session, a file not kept in the media folder or a link there to one kept
  elsewhere, a mesh that names another file), `G6_E_MEDIA_EXTRA_MISSING`.

## Blind spots

- The inspector measures; it does not look. A render of the wrong side of an object, or of an object hidden behind
  another, passes.
- The read-back is Blender's own account of its scene: it catches a value that did not land, not a scene that looks
  wrong.
- An FBX export is checked at its header only; its vertices are counted only in Blender's read-back. A GLB export's
  JSON is read but not its binary chunk, so its extent is the file's own account (its accessors' min and max).
- Blender runs as you: G6 gives it a deadline, a clean environment and its own script, not a sandbox.

## What it needs

- Blender, installed by the person (blender.org/download), on PATH or named by `G6_BLENDER`. G6 never installs or
  bundles it (GPL; run as a separate program).
- `pip install "g6[image] @ git+https://github.com/basicisabc1994/g6solver"` for Pillow, which inspects renders; without it `render` and `viewport_preview` refuse with
  `G6_E_MEDIA_EXTRA_MISSING` before Blender is started.

## Examples

A new session.

```g6-example op=session_new
{}
```

Removing a session that is not there says so.

```g6-example op=session_delete
{"session": "s-000000000000"}
```

A red cube on a plane, a camera and a sun, built in one go (sensitive: it runs Blender).

```g6-example op=build_scene
{"session": "s-000000000000", "scene": {"objects": [{"name": "Floor", "primitive": "plane", "transform": {"scale": [5, 5, 1]}}, {"name": "Box", "primitive": "cube", "colour": [0.8, 0.1, 0.1], "transform": {"location": [0, 0, 1]}}], "camera": {"location": [6, -6, 4], "look_at": [0, 0, 1], "lens_mm": 40}, "lights": [{"name": "Key", "kind": "sun", "location": [3, -3, 8], "energy": 3}]}}
```

Its render (sensitive).

```g6-example op=render
{"session": "s-000000000000", "width": 640, "height": 480, "engine": "eevee", "name": "red-box"}
```

## The four open problems

- **Learning signals**: each refusal names the check that failed (a read-back value, a blank render, a missing file).
  Not yet: nothing counts them per op.
- **Evaluators the model cannot influence**: the scene is read back from Blender and compared with the request; a
  render is decoded and measured from disk by Pillow; a caller's text never reaches Blender as code.
- **Transfer by failure mode**: "blank", "wrong size" and "no output" carry the same codes here as in `adapt_image`,
  `adapt_comfyui` and `adapt_generative_art`; "did not read back" has its own.
- **Enough held-out cases**: Not yet: the tests use a stand-in Blender; there is no stored set of scenes with known
  renders.
