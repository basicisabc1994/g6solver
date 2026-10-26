# SPDX-License-Identifier: Apache-2.0
"""Content-addressed artifacts in the workspace: code files, admitted distillates and LoRA adapters (Phase 5).

A profile names artifacts only by digest, so a profile version always means the same bytes and a rollback restores
them. Nothing here is ever overwritten: an artifact is written once under its own digest, and a read checks the bytes
against it. A distillate is stored only with the admission report that admitted it; an adapter is a folder whose digest
is the digest of its files' manifest (``g6.integrity``).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from g6.integrity import build_manifest, manifest_sha256


class ArtifactError(ValueError):
    """An artifact that is missing, or whose bytes are not the ones its digest names."""


class ArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # code files --------------------------------------------------------------------------------------------------
    def put_blob(self, content: str) -> str:
        data = content.encode("utf-8")
        sha = hashlib.sha256(data).hexdigest()
        path = self.root / "blobs" / sha
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return sha

    def blob(self, sha: str) -> str:
        path = self.root / "blobs" / sha
        if not path.is_file():
            raise ArtifactError(f"no code blob {sha[:12]}")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ArtifactError(f"code blob {sha[:12]} does not match its digest")
        return data.decode("utf-8")

    # distillates --------------------------------------------------------------------------------------------------
    def put_distillate(self, source: str, report: dict[str, Any]) -> str:
        sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        folder = self.root / "distillates" / sha
        if not folder.exists():
            folder.mkdir(parents=True)
            (folder / "solve.py").write_text(source, encoding="utf-8")
            (folder / "admission.json").write_text(json.dumps(report, sort_keys=True, default=str), encoding="utf-8")
        return sha

    def distillate(self, sha: str) -> str:
        path = self.root / "distillates" / sha / "solve.py"
        if not path.is_file() or not (path.parent / "admission.json").is_file():
            raise ArtifactError(f"no admitted distillate {sha[:12]}")
        source = path.read_text(encoding="utf-8")
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != sha:
            raise ArtifactError(f"distillate {sha[:12]} does not match its digest")
        return source

    def admission(self, sha: str) -> dict[str, Any]:
        return json.loads((self.root / "distillates" / sha / "admission.json").read_text(encoding="utf-8"))

    # adapters -----------------------------------------------------------------------------------------------------
    def put_adapter(self, folder: str | Path) -> str:
        manifest = build_manifest(folder, patterns=("**/*",))
        sha = manifest_sha256(manifest)
        target = self.root / "adapters" / sha
        if not target.exists():
            shutil.copytree(folder, target)
        return sha

    def adapter_dir(self, sha: str) -> Path:
        folder = self.root / "adapters" / sha
        if not folder.is_dir() or manifest_sha256(build_manifest(folder, patterns=("**/*",))) != sha:
            raise ArtifactError(f"no adapter {sha[:12]}, or its files changed")
        return folder
