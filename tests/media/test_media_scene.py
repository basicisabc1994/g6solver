# SPDX-License-Identifier: Apache-2.0
"""``SceneSpec``, the family's one scene type: a tree of uniquely named objects (a primitive or a mesh file in the media
folder), checked whenever it is built, so a parent loop, a missing parent, a path where a file name belongs or a number
out of range never makes a scene; and every change returns a new, checked spec."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from g6.media._scene import SceneCamera, SceneLight, SceneObject, SceneSpec, Transform

ROOM = SceneSpec(objects=(
    SceneObject(name="floor", primitive="plane", transform=Transform(scale=(10, 10, 1))),
    SceneObject(name="table", primitive="cube", parent="floor", colour=(0.6, 0.4, 0.2)),
    SceneObject(name="vase", primitive="cylinder", parent="table"),
    SceneObject(name="hill", mesh="terrain-1.obj"),
), camera=SceneCamera(), lights=(SceneLight(name="sun", kind="sun"),))


def test_a_scene_is_a_tree_of_named_objects() -> None:
    assert ROOM.children(None) == ("floor", "hill") and ROOM.children("floor") == ("table",)
    assert ROOM.descendants("floor") == ("table", "vase") and ROOM.object("vase").primitive == "cylinder"
    assert [o.name for o in ROOM.query("CUBE")] == ["table"] and [o.name for o in ROOM.query("terrain")] == ["hill"]


def test_every_change_returns_a_new_checked_scene() -> None:
    moved = ROOM.move("table", (1.0, 2.0, 0.5))
    assert moved.object("table").transform.location == (1.0, 2.0, 0.5)
    assert ROOM.object("table").transform.location == (0.0, 0.0, 0.0)                  # the original is unchanged
    assert [o.name for o in ROOM.remove("table").objects] == ["floor", "hill"]          # the vase went with it
    assert ROOM.reparent("vase", None).object("vase").parent is None
    assert ROOM.add(SceneObject(name="lamp", primitive="sphere", parent="table")).descendants("table") == ("vase",
                                                                                                          "lamp")
    with pytest.raises(ValueError):
        ROOM.move("table", (float("inf"), 0, 0))
    with pytest.raises(ValueError, match="no object named"):
        ROOM.remove("ghost")


def test_a_parent_loop_or_a_missing_parent_never_makes_a_scene() -> None:
    with pytest.raises(ValidationError, match="parent loop"):
        ROOM.reparent("floor", "vase")
    with pytest.raises(ValidationError, match="not in the scene"):
        ROOM.add(SceneObject(name="cup", primitive="cube", parent="cupboard"))
    with pytest.raises(ValidationError, match="its own parent"):
        SceneObject(name="cup", primitive="cube", parent="cup")
    with pytest.raises(ValidationError, match="unique"):
        ROOM.add(SceneObject(name="sun", primitive="sphere"))                            # a light's name is taken


@pytest.mark.parametrize("bad", [
    {"name": "a", "primitive": "cube", "mesh": "x.obj"},
    {"name": "a"},
    {"name": "a", "mesh": "../outside.obj"},
    {"name": "a", "mesh": "C:/models/x.obj"},
    {"name": "a", "mesh": "picture.png"},
    {"name": "a b", "primitive": "cube"},
    {"name": "a", "primitive": "teapot"},
    {"name": "a", "primitive": "cube", "colour": (1.2, 0, 0)},
    {"name": "a", "primitive": "cube", "transform": {"scale": (0, 1, 1)}},
    {"name": "a", "primitive": "cube", "transform": {"location": (1e9, 0, 0)}},
    {"name": "a", "primitive": "cube", "transform": {"location": (float("nan"), 0, 0)}},
    {"name": "a", "primitive": "cube", "material": "gold"},
])
def test_an_object_is_one_shape_with_bounded_numbers_and_a_media_file_name(bad: dict) -> None:
    with pytest.raises(ValidationError):
        SceneObject.model_validate(bad)


def test_merging_skips_a_taken_name_with_everything_under_it() -> None:
    other = SceneSpec(objects=(
        SceneObject(name="table", primitive="cube"),                   # taken: skipped, and its child with it
        SceneObject(name="cloth", primitive="plane", parent="table"),
        SceneObject(name="chair", primitive="cube"),
    ))
    merged, added, skipped = ROOM.merge(other)
    assert added == ("chair",) and skipped == ("table", "cloth")
    assert merged.object("cloth") is None and merged.object("chair") is not None and len(merged.objects) == 5


def test_a_camera_looks_somewhere_else() -> None:
    with pytest.raises(ValidationError):
        SceneCamera(location=(1, 1, 1), look_at=(1, 1, 1))
