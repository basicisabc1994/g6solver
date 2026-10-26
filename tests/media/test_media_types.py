# SPDX-License-Identifier: Apache-2.0
"""The media family's shared types: a file the caller names goes only into G6's media folder and never replaces a
different file unasked; a ``MediaArtifact`` exists only for a file every check passed and is never ``verified``; every
result is a labelled envelope; and image backends are registered by name, so one from outside the package joins the
family (D5) and a backend that is missing, not ready, raising or answering without a file is refused."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

import g6.media
from g6.core.registry import get_registry
from g6.invariants import check_envelope
from g6.media._types import (
    BACKEND_FAILED,
    BACKEND_UNAVAILABLE,
    BAD_NAME,
    EXISTS,
    INSPECTED,
    NO_OUTPUT,
    Check,
    ImageBackend,
    ImageBackends,
    ImageRequest,
    Inspection,
    MediaArtifact,
    MediaError,
    MediaResult,
    Provenance,
    answer,
    declares_backends,
    discard,
    media_artifact,
    media_file,
    media_root,
    refuse,
    write_media,
)

PASSED = Inspection(inspector="test inspector", kind="image", mime="image/png",
                    checks=(Check(name="exists", passed=True), Check(name="not_blank", passed=True)))


@pytest.mark.parametrize("name", ["art", "art-1.png", "Terrain_02.obj", "a.b.c"])
def test_a_plain_name_lands_in_the_media_folder(name: str) -> None:
    path = media_file(name, ".png" if not name.endswith(".obj") else ".obj")
    assert path.parent == media_root() and path.name.startswith(name.split(".")[0])


@pytest.mark.parametrize("kind", ["symbolic", "hard"])
def test_a_source_is_read_only_when_it_is_a_plain_file_kept_in_the_media_folder(tmp_path: Path, kind: str) -> None:
    from media_kit import outside_link

    from g6.media._types import INPUT_INVALID, media_source

    kept = media_file("kept", ".wav")
    write_media(kept, b"RIFF", overwrite=True)
    assert media_source("kept.wav") == kept
    outside_link(tmp_path, "elsewhere.wav", b"RIFF", kind)
    for name in ("elsewhere.wav", "missing.wav"):
        with pytest.raises(MediaError) as caught:
            media_source(name, "source")
        assert caught.value.code == INPUT_INVALID and name in caught.value.reason
    with pytest.raises(MediaError) as caught:
        media_source("../escape.wav")
    assert caught.value.code == BAD_NAME


@pytest.mark.parametrize("name", ["../escape", "a/b", "a\\b", "C:art", ".hidden", "con", "COM1.png", "a..b", "art.",
                                  "", "x" * 101, "space name", "émoji"])
def test_the_caller_names_the_file_never_the_folder(name: str) -> None:
    with pytest.raises(MediaError) as caught:
        media_file(name, ".png")
    assert caught.value.code == BAD_NAME


def test_a_write_is_atomic_and_never_replaces_a_different_file_unasked() -> None:
    path = media_file("write-test", ".bin")
    assert write_media(path, b"first") == "written" and path.read_bytes() == b"first"
    assert write_media(path, b"first") == "unchanged"
    with pytest.raises(MediaError) as caught:
        write_media(path, b"second")
    assert caught.value.code == EXISTS and path.read_bytes() == b"first"
    assert write_media(path, b"second", overwrite=True) == "replaced" and path.read_bytes() == b"second"
    assert not [p for p in media_root().iterdir() if p.name.startswith(".partial-")]
    assert discard(path) and not path.exists() and not discard(path)
    with pytest.raises(MediaError):
        write_media(media_root().parent / "outside.bin", b"x")


def _artifact(**changes) -> MediaArtifact:
    path = media_file("artifact-test", ".png")
    write_media(path, b"\x89PNG not really", overwrite=True)
    made = media_artifact(path, PASSED, Provenance(backend="test", seed=1), {"width": 8, "height": 8})
    return MediaArtifact.model_validate({**made.model_dump(), **changes}) if changes else made


def test_an_artifact_reads_its_size_and_hash_from_the_file() -> None:
    made = _artifact()
    data = Path(made.path).read_bytes()
    assert made.size_bytes == len(data) and made.sha256 == hashlib.sha256(data).hexdigest()
    assert made.completion_state == "qualified-draft" and made.provenance.seed == 1 and made.mime == "image/png"


def test_a_failed_inspection_can_never_be_an_artifact() -> None:
    failed = [{"name": "not_blank", "passed": False, "detail": "uniform", "code": "G6_E_MEDIA_BLANK"}]
    with pytest.raises(ValidationError, match="a failed inspection is a refusal"):
        _artifact(checks=failed)
    with pytest.raises(ValidationError):
        _artifact(checks=[])
    with pytest.raises(ValidationError, match="needs a G6_E_MEDIA_"):
        Check(name="not_blank", passed=False)


@pytest.mark.parametrize("change", [{"completion_state": "verified"}, {"path": str(Path.home() / "x.png")},
                                    {"name": "other.png"}, {"label": "verified"}])
def test_an_artifact_is_never_verified_and_never_outside_the_media_folder(change: dict) -> None:
    with pytest.raises(ValidationError):
        _artifact(**change)


def test_every_result_is_a_labelled_envelope_and_none_is_verified() -> None:
    made = _artifact()
    good = answer("generate", "an image", artifact=made, request_id="r1", run_id="u1").model_dump(mode="json")
    assert check_envelope(good) == [] and good["completion_state"] == "qualified-draft"
    assert good["warning_card"]["code"] == INSPECTED and good["evidence"][0]["kind"] == "media_inspection"
    assert (good["request_id"], good["run_id"]) == ("r1", "u1") and good["artifact"]["sha256"] == made.sha256
    bad = refuse("generate", NO_OUTPUT, "nothing written").model_dump(mode="json")
    assert check_envelope(bad) == [] and bad["completion_state"] == "blocked-escalated"
    assert bad["warning_card"]["code"] == bad["code"] == NO_OUTPUT and bad["artifact"] is None
    with pytest.raises(ValidationError):
        MediaResult(operation="x", completion_state="verified", warning_card={}, evidence=[])
    with pytest.raises(ValidationError, match="stable code"):
        MediaResult(operation="x", completion_state="blocked-escalated", warning_card={}, evidence=[])


class Stripes:
    """An image backend from outside the package: draws nothing itself, it answers what the test tells it to."""

    def __init__(self, name: str = "stripes", ready: str = "", outcome: object = None) -> None:
        self.name, self.local, self.network, self.paid = name, True, False, False
        self.ready, self.outcome, self.seen = ready, outcome, []

    def available(self) -> str:
        return self.ready

    def generate(self, request: ImageRequest) -> MediaResult:
        self.seen.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]


def test_d5_a_backend_from_outside_the_package_registers_by_name_and_answers() -> None:
    backends = ImageBackends()
    made = _artifact()
    stripes = Stripes(outcome=answer("generate", "stripes", artifact=made))
    assert isinstance(stripes, ImageBackend)
    backends.register(stripes)
    assert backends.names() == ["stripes"] and backends.get("stripes") is stripes
    assert backends.describe() == [{"name": "stripes", "local": True, "network": False, "paid": False,
                                    "available": ""}]
    result = backends.generate("stripes", ImageRequest(width=8, height=8, seed=3, request_id="r"))
    assert result.artifact == made and stripes.seen[0].seed == 3
    with pytest.raises(ValueError, match="already registered"):
        backends.register(Stripes())
    backends.register(Stripes(ready="not today"), replace=True)
    assert backends.get("stripes").ready == "not today"
    with pytest.raises(TypeError):
        backends.register(object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        backends.register(Stripes(name="Bad Name"))


@pytest.mark.parametrize(("backend", "code"), [
    (None, BACKEND_UNAVAILABLE),
    (Stripes(ready="the server is not running; start it"), BACKEND_UNAVAILABLE),
    (Stripes(outcome=RuntimeError("boom")), BACKEND_FAILED),
    (Stripes(outcome={"completion_state": "qualified-draft"}), BACKEND_FAILED),
])
def test_a_missing_unready_raising_or_shapeless_backend_is_refused(backend, code: str) -> None:
    backends = ImageBackends()
    if backend is not None:
        backends.register(backend)
    result = backends.generate("stripes", ImageRequest(request_id="r9"))
    assert result.completion_state == "blocked-escalated" and result.code == code and result.request_id == "r9"
    assert check_envelope(result.model_dump(mode="json")) == []


def test_a_backend_that_answers_without_an_inspected_file_is_refused() -> None:
    backends = ImageBackends()
    backends.register(Stripes(outcome=answer("generate", "trust me")))
    assert backends.generate("stripes", ImageRequest()).code == NO_OUTPUT


def test_the_family_registry_finds_the_members_that_declare_backends_and_describes_without_asking(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "member_with_backend").mkdir()
    (tmp_path / "member_with_backend" / "__init__.py").write_text(
        '__g6_image_backends__: tuple[str, ...] = ("stripes",)\n', encoding="utf-8")
    (tmp_path / "member_without").mkdir()
    (tmp_path / "member_without" / "__init__.py").write_text("__g6_image_backends__ = BACKENDS\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert declares_backends("member_with_backend") == ("stripes",)
    assert declares_backends("member_without") == () and declares_backends("no_such_package") == ()
    backends = ImageBackends()
    asked = Stripes(ready="")
    asked.available = lambda: (_ for _ in ()).throw(AssertionError("asked"))  # type: ignore[method-assign]
    backends.register(asked)
    assert backends.describe(probe=False)[0]["available"] is None


def test_a_request_takes_only_its_own_fields_and_a_plain_name() -> None:
    with pytest.raises(ValidationError):
        ImageRequest(approved=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ImageRequest(name="../x")
    assert ImageRequest(name="ok-1").name == "ok-1"


def test_the_shared_modules_never_register_and_carry_the_licence_line() -> None:
    names = set(get_registry().names())
    assert not {"_types", "_inspect", "_scene", "media"} & names
    for path in Path(g6.media.__file__).parent.glob("_*.py"):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# SPDX-License-Identifier: Apache-2.0\n"), path.name
        assert "experimental" not in text.lower() and "all rights reserved" not in text.lower(), path.name
        tree = ast.parse(text)
        called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert not {"eval", "exec", "compile", "__import__"} & called, path.name
