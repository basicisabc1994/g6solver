# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 The G6 authors

"""The approval store must be PROVISIONED, not merely provisionable.

WHY THIS FILE EXISTS. Regulated work — lawyer, medical_surgical, pharmaceutical,
psychologist, allied_health, finance, tax, cybersecurity — now requires a
server-issued approval for every operation. Those records live in the database
named by ``G6_APPROVAL_DB_PATH``.

Leaving it unset is NOT a security hole. The store falls back to in-memory,
reports itself not durable, and issuance refuses by design, so nothing forgeable
is ever minted. It is an AVAILABILITY hole, and a total one: every regulated
agent is blocked and no customer can obtain the approval the gate demands,
because the thing that would issue it refuses to.

That failure mode is invisible in exactly the way that matters — the containers
boot clean, every healthcheck passes, and the product simply cannot do regulated
work. So the shipped configuration is asserted here rather than left to an
operator reading a doc.
"""
from __future__ import annotations

from pathlib import Path

from g6.core.approval_store import (
    DB_PATH_ENV,
    InMemoryApprovalStore,
    SqliteApprovalStore,
    configured_store,
)


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "components" / "mvp").is_dir() and (parent / "bases").is_dir():
            return parent
    raise AssertionError("could not locate the repo root")


# ---------------------------------------------------------------------------
# (a) `~` must be expanded — the sibling convention in .env.example uses it
# ---------------------------------------------------------------------------


def test_a_tilde_path_is_expanded(monkeypatch, tmp_path):
    """``~/.g6/approvals.db`` must land in the HOME directory.

    sqlite3 does NOT expand ``~``, and the store only does
    ``Path(db_path).parent.mkdir(...)``. Before this was handled, an operator
    following the convention already in ``.env.example``
    (``IMMUNE_DB_PATH=~/.g6/immune.db``) got a directory literally named ``~``
    in the process working directory — a store that "worked", in a place nobody
    looks, that no other container shares, and that no error ever mentioned.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Windows
    monkeypatch.setenv(DB_PATH_ENV, "~/.g6/approvals.db")

    store = configured_store()
    assert isinstance(store, SqliteApprovalStore)
    assert "~" not in store.db_path, f"tilde survived into the path: {store.db_path!r}"
    assert Path(store.db_path).is_absolute()
    assert store.is_durable is True


def test_an_absolute_path_is_untouched(monkeypatch, tmp_path):
    """POSITIVE CONTROL. Expansion must be a no-op on the container paths.

    Without this, an "expansion" that mangled absolute paths would satisfy the
    test above and break every deployed environment.
    """
    target = tmp_path / "approvals.db"
    monkeypatch.setenv(DB_PATH_ENV, str(target))
    store = configured_store()
    assert isinstance(store, SqliteApprovalStore)
    assert store.db_path == str(target)


def test_unset_still_fails_closed(monkeypatch):
    """The fail-closed default is deliberate and must stay.

    Someone running the code with no environment at all gets a non-durable
    store, and issuance refuses. That is correct — it is better to refuse than
    to mint an approval that does not outlive the process that issued it.
    """
    monkeypatch.delenv(DB_PATH_ENV, raising=False)
    store = configured_store()
    assert isinstance(store, InMemoryApprovalStore)
    assert store.is_durable is False
