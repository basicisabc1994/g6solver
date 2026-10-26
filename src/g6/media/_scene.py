# SPDX-License-Identifier: Apache-2.0
"""``SceneSpec``: the one scene type the media family shares, a tree of named objects with transforms, a camera and
lights. ``_``-prefixed, so it never registers as a component.

Each object is a primitive or a mesh file in the media folder (an inspected ``MediaArtifact``, such as a terrain from
``adapt_generative_art``), with a transform (location, rotation in radians as XYZ Euler angles, scale) and an optional
base colour (linear RGB, 0 to 1). An object may name a ``parent`` in the same scene, so objects form a tree.

A spec is a value: it is checked when it is built (unique names, every parent present, no parent loops, finite and
bounded numbers) and never changed in place. :meth:`SceneSpec.add`, :meth:`~SceneSpec.remove`,
:meth:`~SceneSpec.move`, :meth:`~SceneSpec.reparent` and :meth:`~SceneSpec.merge` return a new, checked spec, and
:meth:`~SceneSpec.query` and :meth:`~SceneSpec.descendants` read one. A renderer (Blender) builds the scene from a spec
and reads its transforms and colours back as evidence; nothing here renders.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.media._types import NAME

Coordinate = Annotated[float, Field(ge=-1e6, le=1e6, allow_inf_nan=False)]
Factor = Annotated[float, Field(gt=0, le=1e6, allow_inf_nan=False)]
Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Vec3 = tuple[Coordinate, Coordinate, Coordinate]
Colour = tuple[Unit, Unit, Unit]
ObjectName = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.-]{0,62}$")]
Primitive = Literal["cube", "sphere", "plane", "cylinder", "cone", "torus"]
MESH_SUFFIXES = (".obj", ".gltf", ".glb")


class Transform(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    location: Vec3 = (0.0, 0.0, 0.0)
    rotation_euler: Vec3 = (0.0, 0.0, 0.0)
    scale: tuple[Factor, Factor, Factor] = (1.0, 1.0, 1.0)


class SceneObject(BaseModel):
    """One object: a ``primitive`` or a ``mesh`` file (a file name in the media folder), never both."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ObjectName
    primitive: Primitive | None = None
    mesh: str | None = None
    parent: ObjectName | None = None
    transform: Transform = Transform()
    colour: Colour | None = None

    @field_validator("mesh")
    @classmethod
    def _mesh_is_a_media_file(cls, value: str | None) -> str | None:
        if value is not None and not (NAME.fullmatch(value) and ".." not in value
                                      and value.lower().endswith(MESH_SUFFIXES)):
            raise ValueError(f"mesh {value!r} must be a file name in the media folder ending {', '.join(MESH_SUFFIXES)}")
        return value

    @model_validator(mode="after")
    def _one_shape(self) -> SceneObject:
        if (self.primitive is None) == (self.mesh is None):
            raise ValueError(f"object {self.name!r} needs exactly one of primitive or mesh")
        if self.parent == self.name:
            raise ValueError(f"object {self.name!r} cannot be its own parent")
        return self


class SceneCamera(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    location: Vec3 = (7.0, -7.0, 5.0)
    look_at: Vec3 = (0.0, 0.0, 0.0)
    lens_mm: float = Field(50.0, ge=1, le=500, allow_inf_nan=False)

    @model_validator(mode="after")
    def _looks_somewhere(self) -> SceneCamera:
        if self.location == self.look_at:
            raise ValueError("the camera cannot look at the point it stands on")
        return self


class SceneLight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ObjectName
    kind: Literal["point", "sun", "spot", "area"] = "point"
    location: Vec3 = (4.0, -4.0, 6.0)
    energy: float = Field(1000.0, gt=0, le=1e7, allow_inf_nan=False)
    colour: Colour = (1.0, 1.0, 1.0)


class SceneSpec(BaseModel):
    """A scene: objects in a tree, an optional camera and lights."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objects: tuple[SceneObject, ...] = Field((), max_length=1000)
    camera: SceneCamera | None = None
    lights: tuple[SceneLight, ...] = Field((), max_length=64)

    @model_validator(mode="after")
    def _a_tree_of_unique_names(self) -> SceneSpec:
        names = [o.name for o in self.objects] + [light.name for light in self.lights]
        repeated = sorted({n for n in names if names.count(n) > 1})
        if repeated:
            raise ValueError(f"names must be unique in a scene: {', '.join(repeated)}")
        parents = {o.name: o.parent for o in self.objects}
        for obj in self.objects:
            if obj.parent is not None and obj.parent not in parents:
                raise ValueError(f"object {obj.name!r} names a parent {obj.parent!r} that is not in the scene")
        for name in parents:
            seen, at = {name}, parents[name]
            while at is not None:
                if at in seen:
                    raise ValueError(f"object {name!r} is in a parent loop")
                seen.add(at)
                at = parents[at]
        return self

    def object(self, name: str) -> SceneObject | None:
        return next((o for o in self.objects if o.name == name), None)

    def children(self, name: str | None) -> tuple[str, ...]:
        """The objects whose parent is ``name`` (``None``: the objects with no parent)."""
        return tuple(o.name for o in self.objects if o.parent == name)

    def descendants(self, name: str) -> tuple[str, ...]:
        found: list[str] = []
        stack = list(self.children(name))
        while stack:
            child = stack.pop(0)
            found.append(child)
            stack.extend(self.children(child))
        return tuple(found)

    def _with(self, objects: list[SceneObject]) -> SceneSpec:
        return SceneSpec(objects=tuple(objects), camera=self.camera, lights=self.lights)

    def _need(self, name: str) -> SceneObject:
        found = self.object(name)
        if found is None:
            raise ValueError(f"no object named {name!r} in the scene")
        return found

    def add(self, obj: SceneObject) -> SceneSpec:
        return self._with([*self.objects, obj])

    def remove(self, name: str) -> SceneSpec:
        """Without ``name`` and everything under it."""
        self._need(name)
        gone = {name, *self.descendants(name)}
        return self._with([o for o in self.objects if o.name not in gone])

    def move(self, name: str, location: Vec3) -> SceneSpec:
        obj = self._need(name)
        moved = SceneObject.model_validate({**obj.model_dump(), "transform": {**obj.transform.model_dump(),
                                                                              "location": location}})
        return self._with([moved if o.name == name else o for o in self.objects])

    def reparent(self, name: str, parent: str | None) -> SceneSpec:
        """``name`` moved under ``parent`` (``None``: to the top); a move that would make a loop is refused."""
        obj = self._need(name)
        changed = SceneObject.model_validate({**obj.model_dump(), "parent": parent})
        return self._with([changed if o.name == name else o for o in self.objects])

    def merge(self, other: SceneSpec) -> tuple[SceneSpec, tuple[str, ...], tuple[str, ...]]:
        """This scene with ``other``'s objects added: ``(merged, added, skipped)``. An object whose name this scene
        already has is skipped with everything under it, so nothing is silently re-parented onto a stranger."""
        taken = {o.name for o in self.objects} | {light.name for light in self.lights}
        skipped: set[str] = set()
        for obj in other.objects:
            if obj.name in taken:
                skipped |= {obj.name, *other.descendants(obj.name)}
        added = [o for o in other.objects if o.name not in skipped]
        merged = self._with([*self.objects, *added])
        return merged, tuple(o.name for o in added), tuple(o.name for o in other.objects if o.name in skipped)

    def query(self, text: str) -> tuple[SceneObject, ...]:
        """The objects whose name, primitive or mesh contains ``text`` (any case)."""
        q = text.lower()
        return tuple(o for o in self.objects if q in o.name.lower() or q in (o.primitive or o.mesh or "").lower())


__all__ = ["MESH_SUFFIXES", "Colour", "Primitive", "SceneCamera", "SceneLight", "SceneObject", "SceneSpec",
           "Transform", "Vec3"]
