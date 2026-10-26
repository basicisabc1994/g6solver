# SPDX-License-Identifier: Apache-2.0
"""Inspecting the meshes Blender exports, from disk (standard library): OBJ through the family's mesh inspector, and
STL, GLB and FBX here; and, before Blender is given a mesh to import, refusing one that names another file.

* :func:`self_contained`: a glTF or GLB whose JSON holds a ``uri`` (a buffer's, an image's, an extension's) that is not
  a ``data:`` URI, or an OBJ with a ``mtllib``, ``maplib``, ``usemap``, ``call`` or ``csh`` statement, is refused:
  Blender's importers open what these name wherever it is, outside the media folder too, and a later export would
  write it into the media folder. An STL names nothing. FBX is never imported (its texture paths are absolute, and G6
  does not read FBX far enough to see them first).

* :func:`inspect_stl`: a binary STL whose size matches its triangle count, or an ASCII one; at least one triangle;
  finite coordinates; extent along at least two axes.
* :func:`inspect_glb`: the ``glTF`` magic, version 2, a length equal to the file's, a JSON chunk that parses, at least
  one mesh, and positions (counted from the accessors) whose bounds have extent along at least two axes. The bounds are
  the accessors' own ``min`` and ``max``: the binary chunk, which holds the vertices, is not read.
* :func:`inspect_fbx`: the binary or ASCII FBX header and its version only; FBX is not parsed further, so its vertex
  counts come from Blender's read-back, which is Blender's own account.
"""
from __future__ import annotations

import json
import math
import re
import struct
from pathlib import Path
from typing import Any

from g6.media._types import BLANK, FORMAT_MISMATCH, INPUT_INVALID, NO_OUTPUT, UNDECODABLE, Check, Inspection, MediaError
from g6.media._wav import exists_check

MIN_EXTENT = 1e-9
#: The most of a mesh file G6 reads, looking for the files it names, before Blender is given it (bytes).
MAX_READ = 256 * 1024 * 1024
#: OBJ statements that name another file: a material library (whose textures are more files), a texture map library
#: and a texture map, another OBJ to read, and a command to run.
OBJ_NAMING = ("mtllib", "maplib", "usemap", "call", "csh")
#: How far each export format's inspector reads the file: said on the export's card and in its evidence.
READ = {
    "obj": "every vertex and face parsed and counted, and its extent measured from the vertices",
    "stl": "every triangle parsed, its coordinates finite, and its extent measured from them",
    "glb": "its header and JSON chunk parsed and its positions counted from the accessors there; its extent is the "
           "accessors' own min and max, and its binary chunk was not read",
    "fbx": "its header and version read and nothing after them: its geometry was not parsed",
}


def _extent(lo: list[float], hi: list[float], axes: int = 2, said: str = "") -> Check:
    extent = [h - lowest for lowest, h in zip(lo, hi)]
    found = sum(1 for e in extent if e > MIN_EXTENT)
    ok = found >= axes
    return Check(name="not_degenerate", passed=ok, code="" if ok else BLANK,
                 detail=f"extent along {found} axes (needs {axes}): {[round(e, 6) for e in extent]}{said}")


def inspect_stl(path: Path | str) -> Inspection:
    path, name = Path(path), "g6.media STL inspector"
    checks = [exists_check(path)]
    if not checks[0].passed:
        return Inspection(inspector=name, kind="mesh", checks=tuple(checks))
    data = path.read_bytes()
    points: list[tuple[float, ...]] = []
    if len(data) >= 84 and len(data) == 84 + 50 * struct.unpack("<I", data[80:84])[0]:
        count = struct.unpack("<I", data[80:84])[0]
        for i in range(count):
            values = struct.unpack("<12f", data[84 + 50 * i:84 + 50 * i + 48])
            points += [values[3:6], values[6:9], values[9:12]]
    elif data[:5].lower() == b"solid":
        text = data.decode("ascii", "replace")
        points = [tuple(float(v) for v in m.groups()) for m in re.finditer(
            r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)]
        count = len(points) // 3
    else:
        checks.append(Check(name="parses", passed=False, code=UNDECODABLE,
                            detail="neither a binary STL whose size matches its triangle count nor an ASCII one"))
        return Inspection(inspector=name, kind="mesh", checks=tuple(checks))
    if not count:
        checks.append(Check(name="parses", passed=False, code=NO_OUTPUT, detail="no triangles"))
        return Inspection(inspector=name, kind="mesh", checks=tuple(checks), measured={"triangles": 0})
    if not all(math.isfinite(v) for p in points for v in p):
        checks.append(Check(name="parses", passed=False, code=UNDECODABLE, detail="a coordinate is not finite"))
        return Inspection(inspector=name, kind="mesh", checks=tuple(checks))
    checks.append(Check(name="parses", passed=True, detail=f"{count} triangles"))
    lo = [min(p[a] for p in points) for a in range(3)]
    hi = [max(p[a] for p in points) for a in range(3)]
    checks.append(_extent(lo, hi))
    return Inspection(inspector=name, kind="mesh", checks=tuple(checks), mime="model/stl",
                      measured={"triangles": count, "extent": [round(h - lowest, 6) for lowest, h in zip(lo, hi)]})


def inspect_glb(path: Path | str) -> Inspection:
    path, name = Path(path), "g6.media glTF inspector (GLB: header and JSON chunk, not the binary chunk)"
    checks = [exists_check(path)]
    if not checks[0].passed:
        return Inspection(inspector=name, kind="mesh", checks=tuple(checks))
    data = path.read_bytes()
    found: Any = None
    if len(data) >= 20 and data[:4] == b"glTF":
        version, length = struct.unpack("<II", data[4:12])
        size, kind = struct.unpack("<I4s", data[12:20])
        if version == 2 and length == len(data) and kind == b"JSON" and 20 + size <= len(data):
            try:
                found = json.loads(data[20:20 + size])
            except ValueError:
                found = None
    if not isinstance(found, dict):
        checks.append(Check(name="parses", passed=False, code=UNDECODABLE if data[:4] == b"glTF" else FORMAT_MISMATCH,
                            detail="not a glTF 2 binary whose length and JSON chunk hold together"))
        return Inspection(inspector=name, kind="mesh", checks=tuple(checks))
    accessors = found.get("accessors") or []
    positions = [accessors[p["attributes"]["POSITION"]] for m in found.get("meshes") or []
                 for p in m.get("primitives") or [] if isinstance(p.get("attributes", {}).get("POSITION"), int)
                 and p["attributes"]["POSITION"] < len(accessors)]
    vertices = sum(int(a.get("count") or 0) for a in positions)
    measured = {"meshes": len(found.get("meshes") or []), "vertices": vertices}
    if not vertices:
        checks.append(Check(name="parses", passed=False, code=NO_OUTPUT, detail="no mesh positions"))
        return Inspection(inspector=name, kind="mesh", checks=tuple(checks), measured=measured)
    checks.append(Check(name="parses", passed=True, detail=f"{measured['meshes']} meshes, {vertices} vertices, counted "
                                                           "from its JSON chunk (the binary chunk is not read)"))
    bounds = [a for a in positions if isinstance(a.get("min"), list) and isinstance(a.get("max"), list)]
    if bounds:
        lo = [min(a["min"][i] for a in bounds) for i in range(3)]
        hi = [max(a["max"][i] for a in bounds) for i in range(3)]
        checks.append(_extent(lo, hi, said=", from the accessors' own min and max"))
    return Inspection(inspector=name, kind="mesh", checks=tuple(checks), measured=measured, mime="model/gltf-binary")


def inspect_fbx(path: Path | str) -> Inspection:
    path, name = Path(path), "g6.media FBX inspector (header only)"
    checks = [exists_check(path)]
    if not checks[0].passed:
        return Inspection(inspector=name, kind="mesh", checks=tuple(checks))
    head = path.read_bytes()[:64]
    if head.startswith(b"Kaydara FBX Binary  \x00") and len(head) >= 27:
        found = f"binary FBX {struct.unpack('<I', head[23:27])[0]}"
    elif head.lstrip().startswith(b"; FBX"):
        found = "ASCII FBX"
    else:
        checks.append(Check(name="parses", passed=False, code=FORMAT_MISMATCH, detail="no FBX header"))
        return Inspection(inspector=name, kind="mesh", checks=tuple(checks))
    checks.append(Check(name="parses", passed=True, detail=f"a {found} header (the rest is not parsed)"))
    return Inspection(inspector=name, kind="mesh", checks=tuple(checks), measured={"fbx": found},
                      mime="application/octet-stream")


def _uris(tree: Any) -> list[str]:
    """Every string ``uri`` anywhere in a glTF's JSON: its buffers', its images' and any extension's."""
    found, stack = [], [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "uri" and isinstance(value, str):
                    found.append(value)
                else:
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node)
    return found


def _gltf(path: Path) -> Any:
    """The JSON of a glTF file, or of a GLB's first chunk."""
    data = path.read_bytes()
    if path.suffix.lower() == ".glb":
        if len(data) < 20 or data[:4] != b"glTF" or struct.unpack("<I", data[4:8])[0] != 2:
            raise ValueError("not a glTF 2 binary")
        size, kind = struct.unpack("<I4s", data[12:20])
        if kind != b"JSON" or 20 + size > len(data):
            raise ValueError("its first chunk is not its JSON")
        data = data[20:20 + size]
    return json.loads(data.decode("utf-8-sig"))


def self_contained(path: Path) -> None:
    """Refuse the mesh at ``path`` (:class:`MediaError`, ``G6_E_MEDIA_INPUT_INVALID``) when it names another file; see
    the module docstring. Called before Blender starts, on a file :func:`g6.media._types.media_source` found."""
    name, size = path.name, path.stat().st_size
    if size > MAX_READ:
        raise MediaError(INPUT_INVALID, f"{name} is {size} bytes, more than G6 reads to look for the files a mesh "
                                        f"names ({MAX_READ})")
    suffix = path.suffix.lower()
    if suffix in (".gltf", ".glb"):
        try:
            named = [uri for uri in _uris(_gltf(path)) if not uri.startswith("data:")]
        except (ValueError, UnicodeDecodeError, RecursionError, struct.error) as exc:
            raise MediaError(INPUT_INVALID, f"{name} could not be read as glTF to look for the files it names: "
                                            f"{exc}"[:500]) from None
        if named:
            listed = ", ".join(repr(uri[:80]) for uri in named[:3])
            raise MediaError(INPUT_INVALID, f"{name} names {len(named)} other files ({listed}), which Blender would "
                                            "open wherever they are: embed them as data: URIs, or import a GLB that "
                                            "holds them in its own binary chunk")
    elif suffix == ".obj":
        with open(path, encoding="utf-8", errors="replace") as f:
            for number, line in enumerate(f, 1):
                word = line.split(None, 1)[0].lower() if line.strip() else ""
                if word in OBJ_NAMING:
                    raise MediaError(INPUT_INVALID, f"{name} line {number}: {word} names another file, which "
                                                    "Blender would open wherever it is: remove the statement (G6 "
                                                    "imports an OBJ's geometry, not its materials)")


__all__ = ["MAX_READ", "OBJ_NAMING", "READ", "inspect_fbx", "inspect_glb", "inspect_stl", "self_contained"]
