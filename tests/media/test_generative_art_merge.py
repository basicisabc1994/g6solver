# SPDX-License-Identifier: Apache-2.0
"""adapt_synthetic_world is merged into adapt_generative_art and the family's SceneSpec: its diamond-square and Perlin
heightmaps are the ``terrain`` technique, its heightmap-to-mesh is the ``mesh`` op returning an inspected OBJ
MediaArtifact, and its scene tree is SceneSpec, which the mesh op hands back with the terrain in it. What was cut stays
cut: no computer use, no direct model calls, and no reseeding of the interpreter's random generators
(ADAPT_SYNTHETIC_WORLD-01, -02 and -04)."""
from __future__ import annotations

import random
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from g6.core.registry import get_registry  # noqa: E402
from g6.media._inspect import inspect_mesh  # noqa: E402
from g6.media._scene import SceneObject, SceneSpec  # noqa: E402
from g6.media.adapt_generative_art import techniques  # noqa: E402

SRC = Path(techniques.__file__).parent


def _invoke(op: str, **params):
    result = get_registry().invoke("adapt_generative_art", op, params)
    assert result.__class__.__name__ == "Success", result
    return result.unwrap()


@pytest.mark.parametrize(("width", "height"), [(64, 48), (33, 17), (100, 100)])
def test_merge_diamond_square_fills_exactly_the_size_asked_for(width: int, height: int) -> None:
    grid = techniques.diamond_square(width, height, roughness=0.6, rng=np.random.default_rng(4))
    assert grid.shape == (height, width) and np.isfinite(grid).all() and grid.std() > 0
    again = techniques.diamond_square(width, height, roughness=0.6, rng=np.random.default_rng(4))
    assert (grid == again).all()
    smoother = techniques.diamond_square(width, height, roughness=0.2, rng=np.random.default_rng(4))
    rough = np.abs(np.diff(grid, axis=1)).mean() / np.ptp(grid)
    assert np.abs(np.diff(smoother, axis=1)).mean() / np.ptp(smoother) < rough     # roughness does what it says


@pytest.mark.parametrize("kind", ["diamond_square", "perlin"])
def test_merge_terrain_is_a_procedural_technique_with_an_inspected_picture(kind: str) -> None:
    out = _invoke("generate", technique="terrain", params={"kind": kind}, width=64, height=40, seed=12,
                  palette="earth")
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    assert out["artifact"]["provenance"]["recipe"]["params"]["kind"] == kind


def test_merge_terrain_never_reseeds_the_interpreters_random_generators() -> None:
    random.seed(99)
    np.random.seed(99)
    state, np_state = random.getstate(), np.random.get_state()[1].copy()
    _invoke("generate", technique="terrain", params={"kind": "perlin"}, width=32, height=32, seed=5)
    _invoke("mesh", technique="terrain", width=16, height=16, seed=5)
    assert random.getstate() == state and (np.random.get_state()[1] == np_state).all()


def test_merge_heightmap_to_mesh_is_an_inspected_obj_artifact_in_a_scene() -> None:
    out = _invoke("mesh", technique="terrain", params={"kind": "diamond_square", "roughness": 0.5}, width=20,
                  height=12, seed=3, height_scale=0.4, name="island")
    assert out["completion_state"] == "qualified-draft", out.get("reason")
    art = out["artifact"]
    assert (art["kind"], art["mime"], art["name"]) == ("mesh", "model/obj", "island.obj")
    assert (art["measured"]["vertices"], art["measured"]["faces"]) == (20 * 12, 2 * 19 * 11)
    assert art["requested"] == {"vertices": 240, "faces": 418, "width": 20, "height": 12, "format": "obj"}
    extent = art["measured"]["extent"]
    # square cells two units across the longer side, heights from 0 to height_scale
    assert extent[0] == pytest.approx(2.0) and extent[1] == pytest.approx(0.4) and extent[2] == pytest.approx(2 * 11 / 19)
    assert inspect_mesh(art["path"], vertices=240, faces=418, min_axes=3).passed
    scene = SceneSpec.model_validate(out["scene"])
    assert [(o.name, o.mesh) for o in scene.objects] == [("terrain", "island.obj")]
    # the scene tree the synthetic world kept now lives in SceneSpec: build on the terrain
    built = scene.add(SceneObject(name="tower", primitive="cylinder", parent="terrain")).add(
        SceneObject(name="flag", primitive="plane", parent="tower"))
    assert built.descendants("terrain") == ("tower", "flag") and built.remove("tower").descendants("terrain") == ()
    merged, added, skipped = built.merge(SceneSpec(objects=(SceneObject(name="tower", primitive="cube"),
                                                             SceneObject(name="hut", primitive="cube"))))
    assert added == ("hut",) and skipped == ("tower",) and len(merged.objects) == 4


def test_merge_a_mesh_needs_a_heightmap_technique_and_a_bounded_grid() -> None:
    for params in ({"technique": "lsystem", "width": 16, "height": 16}, {"technique": "terrain", "width": 600,
                                                                        "height": 16},
                   {"technique": "terrain", "width": 16, "height": 16, "format": "svg"}):
        result = get_registry().invoke("adapt_generative_art", "mesh", params)
        assert result.__class__.__name__ == "Failure" and "G6_E_INPUT_VALIDATION" in str(result.failure())


def test_merge_what_was_cut_stays_cut() -> None:
    ops = _invoke("ops")["ops"]
    assert not [op for op in ops if "computer" in op or "world" in op or "backend" in op]
    for op in ("computer_use_click", "world_save", "export_scene", "backend_native_call"):
        result = get_registry().invoke("adapt_generative_art", op, {})
        assert result.__class__.__name__ == "Failure" and "G6_E_INPUT_VALIDATION" in str(result.failure())
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "random.seed(" not in text and "np.random.seed(" not in text and "anthropic" not in text.lower()
