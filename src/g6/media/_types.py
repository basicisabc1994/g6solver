# SPDX-License-Identifier: Apache-2.0
"""What every media extension shares: the inspected file an op returns, the labelled result around it, where files go,
and the image backends registered by name.

``_``-prefixed, so it never registers as a component (``g6.core.registry``).

* :class:`MediaArtifact` is the one result type for a file a media op made: a file G6 wrote under
  ``state_path("media")``, its kind and MIME type as the inspector read them from the bytes, its size and SHA-256, what
  was asked for (``requested``) beside what was measured (``measured``), every check the inspector ran, and where it came
  from (:class:`Provenance`: backend, seed, the recipe and its hash). It can only be built from checks that all passed:
  a failed inspection is a refusal, never an artifact. Its state is ``qualified-draft`` and cannot be anything else; an
  inspector reads the file, it does not judge what the picture shows.
* :class:`MediaResult` is the envelope every op returns (``completion_state``, ``warning_card``, ``evidence``,
  ``request_id``, ``run_id``): :func:`answer` with an artifact, or :func:`refuse` with a stable ``G6_E_MEDIA_*`` code.
* :func:`media_file` and :func:`write_media` place a file the caller names (never the folder) in the media folder,
  atomically, and never replace a different file of that name unless asked. :func:`stage_media` and
  :func:`place_media` do the same in two steps, so a file is inspected before it replaces the one there;
  :func:`media_source` is the one way an op finds a file the caller names for it to read, and refuses a link, so a
  name never reads a file kept elsewhere.
* Sound and moving pictures carry their own codes (``G6_E_MEDIA_SILENT``, ``_DURATION_MISMATCH``,
  ``_STREAM_MISMATCH`` and the rest) and their own card, :data:`PROBED`: a probe measured the file, and nothing listened
  to it or watched it.
* :class:`ImageBackend` is what an image generator provides, and :class:`ImageBackends` is the registry backends join by
  name (:data:`image_backends`), so a router picks among them rather than hard-wiring one. A family member that
  registers a backend says so in its package (``__g6_image_backends__``, a literal tuple of names); the family's
  registry imports such members the first time it is asked, so no member imports another to find its backend.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from g6.core.reliability_envelope import WarningCard
from g6.core.storage import state_path

#: Stable codes. A refusal carries one of these; a qualified draft's warning card carries :data:`INSPECTED`.
NO_OUTPUT = "G6_E_MEDIA_NO_OUTPUT"
UNDECODABLE = "G6_E_MEDIA_UNDECODABLE"
FORMAT_MISMATCH = "G6_E_MEDIA_FORMAT_MISMATCH"
SIZE_MISMATCH = "G6_E_MEDIA_SIZE_MISMATCH"
BLANK = "G6_E_MEDIA_BLANK"
EXTRA_MISSING = "G6_E_MEDIA_EXTRA_MISSING"
BACKEND_UNAVAILABLE = "G6_E_MEDIA_BACKEND_UNAVAILABLE"
BACKEND_FAILED = "G6_E_MEDIA_BACKEND_FAILED"
BAD_NAME = "G6_E_MEDIA_BAD_NAME"
EXISTS = "G6_E_MEDIA_EXISTS"
INPUT_INVALID = "G6_E_MEDIA_INPUT_INVALID"
PENDING = "G6_E_MEDIA_PENDING"
PAID_NOT_ALLOWED = "G6_E_MEDIA_PAID_NOT_ALLOWED"
INSPECTED = "G6_E_MEDIA_INSPECTED"
DESCRIBED = "G6_E_MEDIA_DESCRIBED"
REPORTED = "G6_E_MEDIA_REPORTED"
MEASURED = "G6_E_MEDIA_MEASURED"
#: The card of text a media op wrote and checked itself (a diagram's source, an ASCII drawing) that no program parsed
#: or drew.
TEXT_ONLY = "G6_E_MEDIA_TEXT_ONLY"
#: Sound and moving pictures: what their inspectors and checks refuse with, and the card a probed file carries.
SILENT = "G6_E_MEDIA_SILENT"
DURATION_MISMATCH = "G6_E_MEDIA_DURATION_MISMATCH"
STREAM_MISMATCH = "G6_E_MEDIA_STREAM_MISMATCH"
CLIPPED = "G6_E_MEDIA_CLIPPED"
LOUDNESS_MISSED = "G6_E_MEDIA_LOUDNESS_MISSED"
PITCH_MISMATCH = "G6_E_MEDIA_PITCH_MISMATCH"
READBACK_MISMATCH = "G6_E_MEDIA_READBACK_MISMATCH"
TRANSCRIPT_DOUBTFUL = "G6_E_MEDIA_TRANSCRIPT_DOUBTFUL"
TIME_LIMIT = "G6_E_MEDIA_TIME_LIMIT"
PROBED = "G6_E_MEDIA_PROBED"
PARSED = "G6_E_MEDIA_PARSED"
TRANSCRIBED = "G6_E_MEDIA_TRANSCRIBED"
READ_BACK = "G6_E_MEDIA_READ_BACK"
SESSION = "G6_E_MEDIA_SESSION"
MESH_READ = "G6_E_MEDIA_MESH_READ"
#: The refusal of g6.core.outside when a program or service the person installs is not there or not answering.
OUTSIDE_MISSING = "G6_E_OUTSIDE_PROGRAM_MISSING"

_ACTIONS = {
    NO_OUTPUT: "Nothing usable was written; read the reason, change the request and try again.",
    UNDECODABLE: "The file does not decode as the format it claims; do not use it. Try again or change the request.",
    FORMAT_MISMATCH: "The file is not in the format that was asked for; ask for a format the backend writes.",
    SIZE_MISMATCH: "The file is not the size that was asked for; do not use it as if it were.",
    BLANK: "The output is blank, uniform or degenerate; change the parameters (or the seed) and try again.",
    EXTRA_MISSING: "Install the extra the reason names (pip install 'g6[...]') and try again.",
    BACKEND_UNAVAILABLE: "Use a backend that is registered and ready, or install what the reason names.",
    BACKEND_FAILED: "The backend failed; read the reason, correct the request and try again.",
    BAD_NAME: "Name the file with letters, digits, '.', '_' or '-' only: G6 chooses the folder, you choose the name.",
    EXISTS: "A different file already has that name; choose another name or pass overwrite=true.",
    INPUT_INVALID: "Correct the input as the reason says and ask again.",
    PENDING: "The work is not finished, so nothing has been inspected yet; ask for its result later with the id given.",
    PAID_NOT_ALLOWED: "Nothing was sent. A paid service spends the person's money, so G6's action gate decides each "
                      "request and sends it only on its allow; it holds a payment for a person. Show the person the "
                      "dry run and the gate's message: what happens next is theirs to decide.",
    OUTSIDE_MISSING: "Install and start the program the reason names (G6 never installs it), then ask again.",
    SILENT: "The sound is silent or nearly so; change the request (level, source, parameters) and try again.",
    DURATION_MISMATCH: "The file is not the length that was asked for; do not use it as if it were. Read the measured "
                       "length and change the request (an exact cut needs accurate=true).",
    STREAM_MISMATCH: "The file's streams (sample rate, channels, frame size or rate, audio or video present) are not "
                     "the ones asked for; do not use it as if they were.",
    CLIPPED: "The result would go past full scale and distort; lower the gain or the target, or ask for a limiter.",
    LOUDNESS_MISSED: "The measured loudness or peak is not the one asked for; read the measurements and change the "
                     "request.",
    PITCH_MISMATCH: "The sound's measured pitch is not the note asked for; do not use it as if it were.",
    READBACK_MISMATCH: "The program's own scene, read back, differs from what was asked; read the readback and try "
                       "again.",
    TRANSCRIPT_DOUBTFUL: "The transcript does not fit the sound (no speech measured, no words for measured speech, or "
                         "more words than the time allows); do not use it as what was said. Listen to the clip.",
    TIME_LIMIT: "The program did not finish within its time limit and was stopped; give it more time or less work.",
}

#: The kinds of file the family makes. Audio and video arrive with their own inspectors; a page is an HTML file.
MediaKind = Literal["image", "animation", "svg", "mesh", "scene", "audio", "video", "page"]
State = Literal["qualified-draft", "blocked-escalated"]

#: A file name the caller may choose: no folders, no drive, no leading dot, at most 100 characters.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))})


class MediaError(ValueError):
    """A refusal raised inside a media op, carrying its stable code."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code, self.reason = code, reason


class Check(BaseModel):
    """One measurement the inspector made: what it checked, whether it passed, what it found, and the code a failure
    refuses with."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    passed: bool
    detail: str = Field("", max_length=500)
    code: str = ""

    @model_validator(mode="after")
    def _a_failure_has_a_code(self) -> Check:
        if not self.passed and not self.code.startswith("G6_E_MEDIA_"):
            raise ValueError(f"the failed check {self.name!r} needs a G6_E_MEDIA_* code")
        return self


class Inspection(BaseModel):
    """What an inspector found in one file: each check it ran, what it measured, and the MIME type it read from the
    bytes. It passed only when at least one check ran and every check passed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    inspector: str = Field(min_length=1)
    kind: MediaKind
    checks: tuple[Check, ...]
    measured: dict[str, Any] = Field(default_factory=dict)
    mime: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    @property
    def failure(self) -> Check | None:
        """The first check that failed, or ``None``."""
        return next((c for c in self.checks if not c.passed), None)

    def evidence(self) -> dict[str, Any]:
        return {"kind": "media_inspection", "inspector": self.inspector, "passed": self.passed,
                "checks": [c.model_dump() for c in self.checks], "measured": dict(self.measured), "mime": self.mime}


class Provenance(BaseModel):
    """Where a file came from: the backend and its version, the seed, the technique, the full recipe (every parameter
    that shaped it) with its SHA-256, and the caller's prompt as given (a claim, never checked here)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str = Field(min_length=1, max_length=64)
    backend_version: str = ""
    technique: str = ""
    seed: int | None = None
    recipe: dict[str, Any] = Field(default_factory=dict)
    recipe_sha256: str = ""
    prompt: str = ""


def media_root() -> Path:
    """The folder every media file is written to: ``$G6_HOME/media``."""
    return state_path("media")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class MediaArtifact(BaseModel):
    """A file a media op made and the inspector passed. Build it with :func:`media_artifact`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: MediaKind
    name: str
    path: str
    mime: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested: dict[str, Any]
    measured: dict[str, Any]
    checks: tuple[Check, ...] = Field(min_length=1)
    inspector: str = Field(min_length=1)
    provenance: Provenance
    completion_state: Literal["qualified-draft"] = "qualified-draft"

    @model_validator(mode="after")
    def _only_what_passed_inspection_inside_the_media_folder(self) -> MediaArtifact:
        failed = [c.name for c in self.checks if not c.passed]
        if failed:
            raise ValueError(f"a failed inspection is a refusal, never an artifact (failed: {', '.join(failed)})")
        path = Path(self.path)
        root = media_root()
        if not path.is_absolute() or root not in path.parents or path.name != self.name:
            raise ValueError(f"an artifact is a file named {self.name!r} inside {root}")
        return self


def media_artifact(path: Path, inspection: Inspection, provenance: Provenance,
                   requested: dict[str, Any]) -> MediaArtifact:
    """The artifact for ``path``, which ``inspection`` passed; its size and hash are read from the file itself."""
    return MediaArtifact(kind=inspection.kind, name=path.name, path=str(path), mime=inspection.mime,
                         size_bytes=path.stat().st_size, sha256=sha256_file(path), requested=dict(requested),
                         measured=dict(inspection.measured), checks=inspection.checks, inspector=inspection.inspector,
                         provenance=provenance)


class MediaResult(BaseModel):
    """One op's labelled outcome. Op-specific fields sit beside the envelope's."""

    model_config = ConfigDict(extra="allow", frozen=True)

    operation: str
    code: str = ""
    reason: str = ""
    completion_state: State
    warning_card: dict[str, Any]
    evidence: list[dict[str, Any]]
    request_id: str = ""
    run_id: str = ""
    artifact: MediaArtifact | None = None

    @model_validator(mode="after")
    def _a_draft_carries_what_it_rests_on(self) -> MediaResult:
        if self.completion_state == "blocked-escalated" and not self.code:
            raise ValueError("a refusal needs its stable code")
        return self


_SAID = {
    INSPECTED: "Open the file and look at it yourself before relying on it: the inspector checked that it decodes, is "
               "the size asked for and is not blank, not that it shows what you wanted.",
    DESCRIBED: "This is what the package says about itself; nothing was made or measured.",
    REPORTED: "This is what the outside program said about itself; G6 passed it on and did not check it.",
    MEASURED: "These are measurements of the file's bytes; nothing judged what the picture shows.",
    TEXT_ONLY: "No program parsed or drew this text: G6 checked only what it wrote (escaping, names, that every edge "
               "joins declared nodes, line widths). Render it, or read it in a monospaced font, before relying on it.",
    PROBED: "Listen to it or watch it yourself before relying on it: a probe checked that it decodes, has the length, "
            "rate, channels or frame size asked for and is not silent or blank, not that it sounds or looks as you "
            "wanted.",
    PARSED: "These values were read from the words by fixed rules, each with where it came from; nothing was made. "
            "Words the rules do not know were left out, not guessed at.",
    TRANSCRIBED: "This is a speech service's transcript. G6 checked that the clip holds speech and that the words fit "
                 "its length, not that they are what was said: listen to the clip before relying on it.",
    READ_BACK: "This is the outside program's own account of its scene, read back and compared with what was asked; "
               "nothing was rendered, so look at a render before relying on how it looks.",
    SESSION: "A working session (a folder in G6's state) was opened or removed; nothing was made or measured.",
    MESH_READ: "Open it in a 3D program before relying on it: G6 read the file only as far as this card says for its "
               "format, and nothing checked that it holds the scene you asked for.",
}


def answer(operation: str, message: str, *, artifact: MediaArtifact | None = None,
           evidence: list[dict[str, Any]] | None = None, request_id: str = "", run_id: str = "",
           card: str = INSPECTED, **fields: Any) -> MediaResult:
    """A ``qualified-draft`` result. Its card says what it rests on: by default (:data:`INSPECTED`) the inspector
    measured the file and nothing judged what it shows; :data:`DESCRIBED` is a package describing itself;
    :data:`REPORTED` is an outside program's own account of itself; :data:`MEASURED` is a file measured without making
    one; :data:`TEXT_ONLY` is text G6 wrote and checked itself, which no program parsed or drew; :data:`PROBED` is a
    sound or a moving picture a probe measured; :data:`PARSED` is words read by fixed rules; :data:`TRANSCRIBED` is a
    speech service's transcript of a clip G6 measured; :data:`READ_BACK` is an outside program's scene read back and
    compared with the request; :data:`SESSION` is a working session opened or removed; :data:`MESH_READ` is a 3D file
    read as far as its format's inspector reads it, which the message says. With an artifact, the inspection is the
    first item of evidence."""
    if card not in _SAID:
        raise ValueError(f"{card!r} is not a card a qualified draft carries")
    card = WarningCard(code=card, message=message, severity="amber", suggested_action=_SAID[card]).model_dump()
    found = list(evidence or [])
    if artifact is not None:
        found.insert(0, {"kind": "media_inspection", "inspector": artifact.inspector, "passed": True,
                         "checks": [c.model_dump() for c in artifact.checks], "measured": dict(artifact.measured),
                         "mime": artifact.mime, "sha256": artifact.sha256})
    return MediaResult.model_validate({**fields, "operation": operation, "completion_state": "qualified-draft",
                                       "warning_card": card, "evidence": found, "request_id": request_id,
                                       "run_id": run_id, "artifact": artifact})


def refuse(operation: str, code: str, reason: str, *, evidence: list[dict[str, Any]] | None = None,
           request_id: str = "", run_id: str = "", **fields: Any) -> MediaResult:
    """A ``blocked-escalated`` refusal with its stable code and the reason in words."""
    card = WarningCard(code=code, message=reason, severity="blocked",
                       suggested_action=_ACTIONS.get(code, "Read the reason and correct the request.")).model_dump()
    found = [*(evidence or []), {"kind": "refusal", "code": code, "reason": reason}]
    return MediaResult.model_validate({**fields, "operation": operation, "code": code, "reason": reason,
                                       "completion_state": "blocked-escalated", "warning_card": card,
                                       "evidence": found, "request_id": request_id, "run_id": run_id})


def check_name(name: str) -> str:
    """``name`` if a caller may give a file that name, else :class:`MediaError` (``G6_E_MEDIA_BAD_NAME``)."""
    stem = name.split(".", 1)[0].lower()
    if not NAME.fullmatch(name) or ".." in name or name.endswith(".") or stem in _RESERVED:
        raise MediaError(BAD_NAME, f"{name!r} is not a plain file name (letters, digits, '.', '_' and '-'; no folder, "
                                   "no drive, no '..', at most 100 characters)")
    return name


def media_file(name: str, suffix: str) -> Path:
    """Where the file the caller named goes: ``name`` (with ``suffix`` added unless it already ends with it) inside
    :func:`media_root`. The caller chooses the name, never the folder."""
    file_name = name if name.lower().endswith(suffix.lower()) else f"{name}{suffix}"
    path = media_root() / check_name(file_name)
    if path.parent != media_root():
        raise MediaError(BAD_NAME, f"{name!r} does not stay inside the media folder")
    return path


def media_source(name: str, what: str = "source") -> Path:
    """The file ``name`` (a plain name, :func:`check_name`) in the media folder, for an op to read. Refused with
    :class:`MediaError` (``G6_E_MEDIA_INPUT_INVALID``) unless it is a regular file kept in the media folder itself: a
    symbolic link, a Windows reparse point (a junction, a mount point), a hard link (a second name for a file kept
    elsewhere) and anything whose real path is not in the folder are refused before a byte of it is read, so a name
    that looks like ``meeting.wav`` never reads, or sends, a file from somewhere else."""
    path = media_root() / check_name(name)
    try:
        found = os.lstat(path)
        real, root = path.resolve(strict=True), media_root().resolve(strict=True)
    except (OSError, RuntimeError):
        raise MediaError(INPUT_INVALID, f"{what} {name!r} is not a file in the media folder") from None
    linked = (stat.S_ISLNK(found.st_mode) or getattr(found, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
              or found.st_nlink > 1)
    if linked or not stat.S_ISREG(found.st_mode) or real.parent != root:
        raise MediaError(INPUT_INVALID, f"{what} {name!r} is a link or not a plain file kept in the media folder: G6 "
                                        "reads only files kept in the folder itself, so copy the file in")
    return path


def write_media(path: Path, data: bytes, *, overwrite: bool = False) -> str:
    """Write ``data`` to ``path`` (from :func:`media_file`) atomically, and say what happened: ``written``,
    ``unchanged`` (the file already held exactly these bytes) or ``replaced`` (only with ``overwrite``). A different
    file of that name is never replaced without ``overwrite``: :class:`MediaError` (``G6_E_MEDIA_EXISTS``)."""
    if path.parent != media_root():
        raise MediaError(BAD_NAME, f"{path} is not inside the media folder")
    existed = path.exists()
    if existed:
        if path.is_file() and sha256_file(path) == hashlib.sha256(data).hexdigest():
            return "unchanged"
        if not overwrite:
            raise MediaError(EXISTS, f"a different file is already named {path.name!r} in the media folder")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".partial-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(data)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return "replaced" if existed else "written"


def stage_media(path: Path, data: bytes, *, overwrite: bool = False) -> tuple[Path, str]:
    """Write ``data`` to a new temporary file beside ``path`` (from :func:`media_file`), to be inspected before
    :func:`place_media` puts it at ``path``; nothing at ``path`` changes here, so a file that fails inspection (remove
    it with :func:`discard`) never replaces the one there. Returns the staged file and what placing it will do, as
    :func:`write_media` says it: ``written``, ``unchanged`` or ``replaced``. A different file of that name is never
    replaced without ``overwrite``: :class:`MediaError` (``G6_E_MEDIA_EXISTS``)."""
    if path.parent != media_root():
        raise MediaError(BAD_NAME, f"{path} is not inside the media folder")
    said = "written"
    if path.exists():
        if path.is_file() and sha256_file(path) == hashlib.sha256(data).hexdigest():
            said = "unchanged"
        elif not overwrite:
            raise MediaError(EXISTS, f"a different file is already named {path.name!r} in the media folder")
        else:
            said = "replaced"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix=".staged-", suffix=path.suffix, dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(data)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise
    return Path(temp), said


def place_media(staged: Path, path: Path) -> None:
    """Put a file :func:`stage_media` wrote, once it passed inspection, at ``path`` in one step."""
    if staged.parent != media_root() or path.parent != media_root():
        raise MediaError(BAD_NAME, "a staged file is placed only inside the media folder")
    os.replace(staged, path)


def place(temp: Path, path: Path, *, overwrite: bool = False) -> str:
    """Move ``temp`` (a file an outside program wrote in the media folder) to ``path`` (from :func:`media_file`), with
    :func:`write_media`'s rule: ``written``, ``unchanged`` (``path`` already held exactly these bytes; ``temp`` is
    removed) or ``replaced`` (only with ``overwrite``); otherwise ``temp`` is removed and :class:`MediaError`
    (``G6_E_MEDIA_EXISTS``) raised."""
    if path.parent != media_root() or temp.parent != media_root():
        raise MediaError(BAD_NAME, f"{path} is not inside the media folder")
    existed = path.exists()
    if existed:
        if path.is_file() and sha256_file(path) == sha256_file(temp):
            temp.unlink(missing_ok=True)
            return "unchanged"
        if not overwrite:
            temp.unlink(missing_ok=True)
            raise MediaError(EXISTS, f"a different file is already named {path.name!r} in the media folder")
    os.replace(temp, path)
    return "replaced" if existed else "written"


def discard(path: Path) -> bool:
    """Remove a file G6 wrote that failed inspection, so it is never mistaken for a result; whether it was removed."""
    try:
        if path.parent == media_root() and path.is_file():
            path.unlink()
            return True
    except OSError:
        pass
    return False


class ImageRequest(BaseModel):
    """What a caller asks an image backend for. ``options`` are the backend's own parameters, which it validates; a
    backend that cannot honour ``prompt`` refuses it rather than ignoring it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    width: int = Field(512, ge=8, le=4096)
    height: int = Field(512, ge=8, le=4096)
    format: Literal["png"] = "png"
    seed: int | None = Field(None, ge=0, le=2**32 - 1)
    name: str = Field("", max_length=100)
    prompt: str = Field("", max_length=4000)
    options: dict[str, Any] = Field(default_factory=dict)
    overwrite: bool = False
    request_id: str = Field("", max_length=100)
    run_id: str = Field("", max_length=100)

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        return check_name(value) if value else value


@runtime_checkable
class ImageBackend(Protocol):
    """An image generator: what it is (``local``: it runs on this machine; ``network``: it reaches the network;
    ``paid``: it spends money), whether it is ready (:meth:`available` is ``""`` when it is, otherwise why not and how
    to make it ready), and :meth:`generate`, which returns an inspected artifact or a refusal."""

    name: str
    local: bool
    network: bool
    paid: bool

    def available(self) -> str: ...

    def generate(self, request: ImageRequest) -> MediaResult: ...


_BACKEND_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
#: What a family member assigns, as a literal tuple of names, in its ``__init__.py`` when it registers image backends.
BACKENDS_DECLARATION = "__g6_image_backends__"


def declares_backends(package: str) -> tuple[str, ...]:
    """The backend names ``package`` declares in ``__g6_image_backends__``, read from its source without importing
    it; ``()`` when it declares none or the source cannot be read."""
    try:
        spec = importlib.util.find_spec(package)
        if spec is None or not (spec.origin or "").endswith("__init__.py"):
            return ()
        with open(spec.origin, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (ImportError, OSError, SyntaxError, ValueError):
        return ()
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else getattr(
            node, "target", None)
        if isinstance(target, ast.Name) and target.id == BACKENDS_DECLARATION and node.value is not None:
            try:
                names = ast.literal_eval(node.value)
            except ValueError:
                return ()
            return tuple(n for n in names if isinstance(n, str)) if isinstance(names, tuple) else ()
    return ()


class ImageBackends:
    """The image backends registered by name. A name is taken once; :meth:`register` with ``replace`` swaps it.

    With ``discover``, the first lookup imports each media family member that declares backends
    (:data:`BACKENDS_DECLARATION`), and importing it registers them; a member that fails to import is skipped and its
    error kept in :attr:`discovery_errors`."""

    def __init__(self, *, discover: bool = False) -> None:
        self._backends: dict[str, ImageBackend] = {}
        self._discover = discover
        self.discovery_errors: dict[str, str] = {}

    def discover(self) -> None:
        """Import the media family members that declare image backends (once; later calls do nothing)."""
        if not self._discover:
            return
        self._discover = False
        from g6.core.registry import get_registry

        for ref in list(get_registry().refs.values()):
            if ref.family == "media" and declares_backends(ref.module):
                try:
                    importlib.import_module(ref.module)
                except Exception as exc:  # noqa: BLE001 - one member that fails to import never hides the others
                    self.discovery_errors[ref.name] = f"{type(exc).__name__}: {exc}"[:300]

    def register(self, backend: ImageBackend, *, replace: bool = False) -> None:
        if not isinstance(backend, ImageBackend):
            raise TypeError(f"{backend!r} is not an image backend (name, local, network, paid, available, generate)")
        if not _BACKEND_NAME.fullmatch(str(backend.name)):
            raise ValueError(f"{backend.name!r} is not a backend name (lower-case letters, digits and '_')")
        if backend.name in self._backends and not replace:
            raise ValueError(f"an image backend named {backend.name!r} is already registered")
        self._backends[backend.name] = backend

    def unregister(self, name: str) -> None:
        self._backends.pop(name, None)

    def get(self, name: str) -> ImageBackend | None:
        self.discover()
        return self._backends.get(name)

    def names(self) -> list[str]:
        self.discover()
        return sorted(self._backends)

    def describe(self, *, probe: bool = True) -> list[dict[str, Any]]:
        """Each backend: what it is and whether it is ready (``available`` is ``""`` when it is). Without ``probe``,
        ``available`` is ``None``: nothing was asked, so a backend that would reach an outside program is not."""
        return [{"name": b.name, "local": b.local, "network": b.network, "paid": b.paid,
                 "available": b.available() if probe else None}
                for b in (self._backends[n] for n in self.names())]

    def generate(self, name: str, request: ImageRequest) -> MediaResult:
        """``request`` on the backend ``name``: its own result when it returns a :class:`MediaResult`, otherwise a
        refusal. A backend that is not registered or not ready, or that raises, is refused, never worked round."""
        ids = {"request_id": request.request_id, "run_id": request.run_id}
        backend = self.get(name)
        if backend is None:
            listed = ", ".join(self.names()) or "none"
            return refuse("generate", BACKEND_UNAVAILABLE, f"no image backend named {name!r} (registered: {listed})",
                          **ids)
        why = backend.available()
        if why:
            return refuse("generate", BACKEND_UNAVAILABLE, f"the {name} backend is not ready: {why}", **ids)
        try:
            result = backend.generate(request)
        except Exception as exc:  # noqa: BLE001 - a backend's error is a refusal, never a crash or a result
            return refuse("generate", BACKEND_FAILED, f"the {name} backend failed: {type(exc).__name__}: {exc}"[:500],
                          **ids)
        if not isinstance(result, MediaResult):
            return refuse("generate", BACKEND_FAILED, f"the {name} backend returned {type(result).__name__}, not a "
                                                      "media result", **ids)
        if result.completion_state != "blocked-escalated" and result.artifact is None:
            return refuse("generate", NO_OUTPUT, f"the {name} backend answered without an inspected file", **ids)
        return result


#: The family's registry of image backends; it finds the members that declare backends the first time it is asked.
image_backends = ImageBackends(discover=True)

__all__ = ["BACKENDS_DECLARATION", "BACKEND_FAILED", "BACKEND_UNAVAILABLE", "BAD_NAME", "BLANK", "CLIPPED",
           "DESCRIBED", "DURATION_MISMATCH", "EXISTS", "EXTRA_MISSING", "FORMAT_MISMATCH", "INPUT_INVALID",
           "INSPECTED", "LOUDNESS_MISSED", "MEASURED", "MESH_READ", "NAME", "NO_OUTPUT", "OUTSIDE_MISSING",
           "PAID_NOT_ALLOWED", "PARSED", "PENDING", "PITCH_MISMATCH", "PROBED", "READBACK_MISMATCH", "READ_BACK",
           "REPORTED", "SESSION", "SILENT", "SIZE_MISMATCH", "STREAM_MISMATCH", "TEXT_ONLY", "TIME_LIMIT",
           "TRANSCRIBED", "TRANSCRIPT_DOUBTFUL", "UNDECODABLE", "Check", "ImageBackend", "ImageBackends",
           "ImageRequest", "Inspection", "MediaArtifact", "MediaError", "MediaKind", "MediaResult", "Provenance",
           "answer", "check_name", "declares_backends", "discard", "image_backends", "media_artifact", "media_file",
           "media_root", "media_source", "place", "place_media", "refuse", "sha256_file", "stage_media", "write_media"]
