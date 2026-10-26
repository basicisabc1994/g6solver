# SPDX-License-Identifier: Apache-2.0
"""The evaluator-only read boundary (graduated from ``research.echa.access``; ECHA-10; audit 037).

A vault seals the directories or files that hold held-out answers, hidden tests, oracle code and evaluator
transcripts, and records a digest of exactly what was sealed. A read by the builder, the learner or a reviewer goes
through ``guarded_read``: a sealed file is the same file whatever its spelling, so membership is decided by file identity
(device and inode, which a ``\\\\?\\`` prefix, a UNC name, a short name or a hard link cannot change) as well as by the
resolved location under a sealed root (which covers files added later), and the bytes are read from the very handle that
was checked. A refusal writes an incident (the role, the vault's digest and a hash of the path, never the path) to the
ledger given, and every record taken against that fixture is void from then on. A fixture changed after sealing is the
same kind of incident.

The ledger is any object with ``add_vault(vault_id, digest, *, files, roots)`` and ``add_incident(kind, **fields)``.

This is a software check on the read path, in the same process. It is NOT an operating-system boundary: code that opens
a file directly is not stopped. A result that depends on it is therefore ``unblinded`` (``blinding_status``).

Fixed on graduation (audit 037, VLT-001): the manifest is a read-only mapping, so a caller cannot edit what was sealed
to hide a later change.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from g6.core.ledger import digest

ROLES = ("builder", "learner", "reviewer", "evaluator")
ENFORCEMENT = "software_check_in_process"


class AccessDenied(PermissionError):
    """A role that may not read what the vault sealed tried to."""


def _plain(path: str | os.PathLike | bytes) -> str:
    """The path as a plain string, without a Windows extended-length (``\\\\?\\``) or ``\\\\?\\UNC\\`` prefix."""
    text = os.fsdecode(path)
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def _real(path: str | os.PathLike | bytes) -> str:
    return os.path.normcase(os.path.realpath(_plain(path)))


def _identity(path: str | os.PathLike | bytes) -> tuple[int, int] | None:
    try:
        info = os.stat(_plain(path))
    except OSError:
        return None
    return (info.st_dev, info.st_ino) if info.st_ino else None


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _files(roots: Sequence[Path]) -> list[tuple[int, Path, str]]:
    """Every sealed file as (root position, file, name under the root)."""
    found = []
    for index, root in enumerate(roots):
        if root.is_file():
            found.append((index, root, ""))
        else:
            found += [(index, p, p.relative_to(root).as_posix()) for p in sorted(root.rglob("*")) if p.is_file()]
    return found


def _manifest(roots: Sequence[Path]) -> dict[str, str]:
    """Every sealed file's name (its root's position and its path under it) and sha256."""
    return {f"{index}/{name}": hashlib.sha256(path.read_bytes()).hexdigest() for index, path, name in _files(roots)}


@dataclass(frozen=True)
class Vault:
    vault_id: str
    roots: tuple[str, ...]
    digest: str
    manifest: Mapping[str, str]
    identities: frozenset = frozenset()

    @classmethod
    def seal(cls, roots: Sequence[str | Path], *, ledger: Any, vault_id: str = "vault") -> Vault:
        paths = [Path(r) for r in roots]
        for path in paths:
            if not path.exists():
                raise ValueError(f"cannot seal {path.name!r}: it does not exist")
        manifest = _manifest(paths)
        identities = {i for _index, p, _name in _files(paths) if (i := _identity(p)) is not None}
        identities |= {i for p in paths if (i := _identity(p)) is not None}
        sealed = cls(vault_id=vault_id, roots=tuple(_real(p) for p in paths), digest=digest(manifest),
                     manifest=MappingProxyType(dict(manifest)), identities=frozenset(identities))
        if ledger is not None:
            ledger.add_vault(vault_id, sealed.digest, files=len(manifest), roots=len(paths))
        return sealed

    def covers(self, path: str | os.PathLike | bytes) -> bool:
        """Whether ``path`` is a sealed file or folder by identity, or lies under a sealed root by its resolved location."""
        identity = _identity(path)
        if identity is not None and identity in self.identities:
            return True
        real = _real(path)
        return any(_inside(real, root) for root in self.roots)

    def verify_unchanged(self, *, ledger: Any = None) -> tuple[str, ...]:
        """What changed under the vault's roots since it was sealed; a change is recorded as an incident."""
        now = _manifest([Path(r) for r in self.roots])
        problems = [f"{name} changed since sealing" for name in sorted(self.manifest)
                    if name in now and now[name] != self.manifest[name]]
        problems += [f"{name} was removed since sealing" for name in sorted(self.manifest) if name not in now]
        problems += [f"{name} was added since sealing" for name in sorted(now) if name not in self.manifest]
        if problems and ledger is not None:
            ledger.add_incident("fixture_modified", fixture_digest=self.digest, vault_id=self.vault_id,
                                changed=len(problems))
        return tuple(problems)


def _refuse(vault: Vault, role: str, path: str | os.PathLike | bytes, ledger: Any) -> AccessDenied:
    if ledger is not None:
        ledger.add_incident("access_denied", role=role, fixture_digest=vault.digest, vault_id=vault.vault_id,
                            path_sha256=hashlib.sha256(_real(path).encode("utf-8")).hexdigest())
        return AccessDenied(f"the {role} may not read a sealed evaluator fixture (incident recorded)")
    return AccessDenied(f"the {role} may not read a sealed evaluator fixture (no ledger given: nothing was recorded)")


def guarded_read(vault: Vault, role: str, path: str | os.PathLike | bytes, *, ledger: Any = None) -> bytes:
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if role == "evaluator":
        with open(_plain(path), "rb") as handle:
            return handle.read()
    if vault.covers(path):
        raise _refuse(vault, role, path, ledger)
    with open(_plain(path), "rb") as handle:  # checked and read through one handle: no gap between the two
        info = os.fstat(handle.fileno())
        if info.st_ino and (info.st_dev, info.st_ino) in vault.identities:
            raise _refuse(vault, role, path, ledger)
        return handle.read()


def blinding_status(vault: Vault) -> dict:
    """How blind a result that rests on this vault can honestly be called: never better than ``unblinded`` here."""
    return {"blinding": "unblinded", "enforcement": ENFORCEMENT,
            "reason": "the boundary is an in-process software check on the read path, not an operating-system "
                      "separation, so code that opens a file directly is not stopped"}
