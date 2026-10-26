# SPDX-License-Identifier: Apache-2.0
"""g6.media.adapt_blender — build, light, render and export 3D scenes with your Blender, each change read back and each render inspected.

An extension in the media family, and the family's ``blender`` image backend (a ``SceneSpec`` rendered). Blender is a
program you install (``__g6_outside__``; GPL, so G6 runs it as a separate program over its command line and never
bundles it), found through ``G6_BLENDER`` or PATH by the one probe that serves ``capabilities`` and every run. It runs
only G6's own script (``inside_blender.txt``), which reads its op as JSON data, through ``g6.core.component_process``
with a deadline, an output cap and a clean environment, started ``--background --factory-startup --disable-autoexec``
(and ``--offline-mode`` from Blender 4.2).

    from g6.core.registry import get_registry
    reg = get_registry()
    session = reg.invoke("adapt_blender", "session_new", {}).unwrap()["session"]
    reg.invoke("adapt_blender", "create_primitive", {"session": session, "object": "Box", "primitive": "cube"})
    out = reg.invoke("adapt_blender", "render", {"session": session, "width": 640, "height": 480}).unwrap()
    out["artifact"]["path"]   # a PNG under $G6_HOME/media, inspected: decodes, 640x480, not blank

What it checks: each change (a primitive, a transform, a colour, a camera, a light, a whole ``SceneSpec``) is read back
from Blender's scene and compared with what was asked, and a difference refuses the op and leaves the session's scene
as it was (``G6_E_MEDIA_READBACK_MISMATCH``); a render is a PNG inspected from disk and an export (OBJ, STL, GLB, FBX)
a mesh inspected from disk (``G6_E_MEDIA_BLANK``, ``_SIZE_MISMATCH``, ``_NO_OUTPUT``), whatever Blender's exit code
said. Results are ``qualified-draft``: nothing judges what a render shows.

Not ported from the archive's adapt_blender, and why:

* ``run_script`` and the unsafe driver mode (ADAPT_BLENDER-01): they handed caller Python to Blender; no caller text
  ever becomes code here, and the script Blender runs is G6's own file, not generated from caller values;
* the ``verified`` label on a readback (-03): the readback is a check in the evidence, and the label stays
  ``qualified-draft``;
* the render's echoed resolution (-02): the PNG is measured;
* rigging, shape keys, drivers, physics bake, the backend-native op, the Poly Haven downloads (they may come later as
  sensitive ops), the result store, the MCP servers, the advisory planner and its router, tenancy and the platform
  compute guard (-04 to -07); the OBJ fallback without Blender (``adapt_generative_art`` makes meshes without it).
"""
from g6.media._types import image_backends
from g6.media.adapt_blender import inspect3d, runner
from g6.media.adapt_blender.block import SENSITIVE_OPS, AdaptBlenderBlock, BlenderBackend, BlenderInput

__g6_component__ = True
__g6_block__ = "AdaptBlenderBlock"
__g6_family__ = "media"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # each one runs Blender on your machine
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("visual_check_failed",)
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (runner.BLENDER,)
__g6_extras__: dict[str, tuple[str, ...]] = {"image": ("PIL",)}
__g6_image_backends__: tuple[str, ...] = ("blender",)  # the family's registry imports this package to find it

image_backends.register(BlenderBackend(), replace=True)

__all__ = ["SENSITIVE_OPS", "AdaptBlenderBlock", "BlenderBackend", "BlenderInput", "inspect3d", "runner"]
