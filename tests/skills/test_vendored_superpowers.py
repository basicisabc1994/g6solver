# SPDX-License-Identifier: Apache-2.0
"""The superpowers skills (MIT, (c) 2025 Jesse Vincent) ship unmodified with G6, for a coding agent's full lifecycle:
the 14 lifecycle skills, byte for byte as upstream released them, with their licence, provenance and credit."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from g6.core import skills

OSS = Path(__file__).resolve().parents[2]
VENDOR = skills.vendor_dir()
LIFECYCLE = {"using-superpowers", "brainstorming", "writing-plans", "executing-plans", "subagent-driven-development",
             "dispatching-parallel-agents", "using-git-worktrees", "test-driven-development", "systematic-debugging",
             "verification-before-completion", "requesting-code-review", "receiving-code-review",
             "finishing-a-development-branch", "writing-skills"}
RECORD = (VENDOR / "VENDORED.md").read_text(encoding="utf-8")


def test_the_lifecycle_skills_are_vendored() -> None:
    assert set(skills.vendored()) == LIFECYCLE


def test_each_vendored_skill_names_itself() -> None:
    for name, directory in skills.vendored().items():
        assert skills.front_matter((directory / "SKILL.md").read_text(encoding="utf-8"))["name"] == name


def test_their_licence_travels_with_them() -> None:
    licence = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    assert licence.startswith("MIT License") and "Copyright (c) 2025 Jesse Vincent" in licence
    assert "Permission is hereby granted, free of charge" in licence


def test_the_record_names_upstream_its_version_and_its_commit() -> None:
    assert "https://github.com/obra/superpowers" in RECORD and "7.0.0" in RECORD
    assert "bb92a77741419a4ab5f06e711a283343f1ada0c3" in RECORD
    assert not re.search(r"[A-Za-z]:[\\/]+Users|/home/|/Users/", RECORD)


def test_every_file_is_upstreams_byte_for_byte() -> None:
    listed = dict(re.findall(r"^\| `([^`]+)` \| `([0-9a-f]{64})` \|$", RECORD, re.MULTILINE))
    on_disk = {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*") if p.is_file() and p.name != "VENDORED.md"}
    assert set(listed) == on_disk
    for rel, digest in listed.items():
        assert hashlib.sha256((VENDOR / rel).read_bytes()).hexdigest() == digest, rel


def test_git_keeps_their_bytes() -> None:
    attributes = (OSS / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert "src/g6/skills/vendor/** -text" in attributes


def test_the_notice_credits_them() -> None:
    notice = (OSS / "NOTICE").read_text(encoding="utf-8")
    assert "superpowers" in notice and "Jesse Vincent" in notice and "MIT" in notice
