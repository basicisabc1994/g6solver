# SPDX-License-Identifier: Apache-2.0
"""Metadata failures stay distinct from uncertain import-root identities."""
from functools import partial
from importlib import metadata
import sys
from types import ModuleType

import pytest


@pytest.fixture
def installation(monkeypatch):
    from g6.mcp import dependencies
    from packaging.requirements import Requirement

    Requirement("amend2-base")
    needs = {
        "g6": ["amend2-base", 'amend2-optional; extra == "abstraction"',
               'amend2-mcp; extra == "mcp"'],
        "amend2-base": ["amend2-leaf"], "amend2-leaf": [],
        "amend2-optional": [], "amend2-mcp": [],
    }
    declarations = {"amend2_required": ["amend2-base"], "amend2_leaf": ["amend2-leaf"],
                    "amend2_optional": ["amend2-optional"], "mcp": ["amend2-mcp"]}
    monkeypatch.setattr(metadata, "requires", lambda name: needs[name])
    monkeypatch.setattr(metadata, "packages_distributions", lambda: declarations)
    monkeypatch.setattr(metadata, "distributions", lambda: ())
    # Required metadata must surface even when the failed import root is installed.
    monkeypatch.setattr(dependencies.util, "find_spec", lambda name: object())
    dependencies.optional_import_roots.cache_clear()
    yield dependencies, needs
    dependencies.optional_import_roots.cache_clear()


def metadata_fault(monkeypatch, seam, failure):
    def fail(*args, **kwargs):
        raise failure

    if seam in {"g6", "amend2-base", "amend2-leaf", "extra-project"}:
        original = metadata.requires
        project_reads = 0

        def requires(name):
            nonlocal project_reads
            if name == "g6":
                project_reads += 1
            if name == seam or (seam == "extra-project" and name == "g6" and project_reads == 2):
                raise failure
            return original(name)

        monkeypatch.setattr(metadata, "requires", requires)
    elif seam in {"packages_distributions", "distributions"}:
        monkeypatch.setattr(metadata, seam, fail)
    else:
        class Distribution:
            @property
            def metadata(self):
                if seam == "distribution-name":
                    raise failure
                return {"Name": "amend2-base"}

            def read_text(self, name):
                if name == ("top_level.txt" if seam == "top-level" else "RECORD"):
                    raise failure
                return None

        monkeypatch.setattr(metadata, "distributions", lambda: (Distribution(),))


@pytest.mark.parametrize("seam", (
    "g6", "amend2-base", "amend2-leaf", "extra-project", "packages_distributions",
    "distributions", "distribution-name", "top-level", "record",
))
@pytest.mark.parametrize("error_type", (metadata.PackageNotFoundError, RuntimeError, ImportError))
@pytest.mark.parametrize("entry", ("classifier", "family", "startup"))
def test_metadata_failure_propagates_unchanged(monkeypatch, installation, seam, error_type, entry):
    from g6 import mcp
    from g6.mcp import families, registrations

    dependencies, _ = installation
    failure = error_type("unreadable distribution record")
    original = ModuleNotFoundError("original server failure", name="amend2_optional")
    metadata_fault(monkeypatch, seam, failure)

    def build():
        raise original

    if entry == "classifier":
        invoke = partial(dependencies.optional_dependency_missing, "abstraction", original)
    elif entry == "family":
        server = ModuleType("amend2_server")
        server.build = build
        monkeypatch.setitem(sys.modules, server.__name__, server)
        monkeypatch.setattr(registrations, "SERVERS", {"abstraction": server.__name__ + ":build"})
        monkeypatch.setattr(families, "_DETAILS", {})
        invoke = partial(families.describe, "load_state")
    else:
        original.name = "mcp.server"
        monkeypatch.setattr(mcp, "_quiet", build)
        invoke = mcp.build_server

    with pytest.raises(error_type) as stopped:
        invoke()
    assert stopped.value is failure


@pytest.mark.parametrize("error_type", (metadata.PackageNotFoundError, KeyError))
def test_absent_optional_metadata_still_allows_recovery(monkeypatch, installation, error_type):
    dependencies, _ = installation
    original = metadata.requires

    def requires(name):
        if name == "amend2-optional":
            raise error_type(name)
        return original(name)

    monkeypatch.setattr(metadata, "requires", requires)
    monkeypatch.setattr(dependencies.util, "find_spec", lambda name: None)
    failure = ModuleNotFoundError("optional is absent", name="amend2_optional")
    assert dependencies.optional_dependency_missing("abstraction", failure) is True


@pytest.mark.parametrize("error_type", (RuntimeError, ImportError))
def test_other_optional_metadata_errors_still_propagate(monkeypatch, installation, error_type):
    dependencies, _ = installation
    original = metadata.requires
    failure = error_type("optional distribution is unreadable")

    def requires(name):
        if name == "amend2-optional":
            raise failure
        return original(name)

    monkeypatch.setattr(metadata, "requires", requires)
    with pytest.raises(error_type) as stopped:
        dependencies.optional_dependency_missing(
            "abstraction", ModuleNotFoundError("optional is absent", name="amend2_optional"))
    assert stopped.value is failure
