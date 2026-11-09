# SPDX-License-Identifier: Apache-2.0
"""Test isolation: all durable state goes to a throwaway G6_HOME, never the user's real one, no sensitive op is
allowed over MCP from the shell's own G6_MCP_SENSITIVE (a test that wants one passes ``allow_sensitive``), and no
model is chosen by the shell's own G6_LLM_PROVIDER (g6_invoke passes the configured model to the components that take
one)."""
from __future__ import annotations

import os
import sys
import tempfile
import asyncio
from contextlib import ExitStack
from types import SimpleNamespace

import pytest


def accepted_cloud_name(message):
    assert 'cannot be recalled' in message
    return 'Fixture person'


async def accepting_cloud_elicitation(message, response_type, params, context):
    return response_type(value=accepted_cloud_name(message))


@pytest.fixture
def cloud_smoke_dialog():
    from pathlib import Path
    return Path(__file__).parent / 'tools/_consented_smoke.py'

# Set before any g6 module is imported, so module-level defaults resolve inside it.
os.environ["G6_HOME"] = tempfile.mkdtemp(prefix="g6-test-home-")
os.environ.pop("G6_MCP_SENSITIVE", None)
for _setting in ("G6_LLM_PROVIDER", "G6_LLM_MODEL"):
    os.environ.pop(_setting, None)


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    """A G6_HOME of the test's own and a writer for the person's key file in it: G6 reads keys only from that file
    (owner ruling, 2026-10-21), and the session's shared G6_HOME would carry a key on into later tests."""
    home = tmp_path / "g6-home"
    home.mkdir()
    monkeypatch.setenv("G6_HOME", str(home))
    for name in ("G6_LLM_PROVIDER", "G6_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)

    def write(text: str) -> None:
        (home / ".env").write_text(text, encoding="utf-8")

    write.path = home / ".env"
    return write


@pytest.fixture
def registry_reset():
    """A fresh component registry, and fresh site catalogue caches, before and after the test."""
    import g6.core.registry as registry

    def clear() -> None:
        registry._REGISTRY = None
        catalogue = sys.modules.get("pages.catalogue")
        if catalogue is not None:
            catalogue._rows.cache_clear()
            catalogue._extension_rows.cache_clear()

    clear()
    yield
    clear()


@pytest.fixture
def accepted_cloud_dialog():
    """Opt-in grants through the same dialog acceptance path as the MCP client.

    Tests name each complete synthetic source and provider. This fixture changes
    no production policy, settings, environment flag, or admission function.
    """
    from g6.core.cloud_consent import Source, request_sources
    from g6.mcp.cloud_consent import request_grant

    class Session:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': accepted_cloud_name(kwargs['message'])})

    with ExitStack() as scopes:
        def grant(source, provider, *, text):
            ctx = SimpleNamespace(session=Session(), request_id='fixture-dialog')
            result = asyncio.run(request_grant(ctx, source, provider))
            assert result['status'] == 'granted'
            refs = (Source.text(source, 'fixture-inputs', text),)
            scopes.enter_context(request_sources(refs))
            return refs
        yield grant


@pytest.fixture
def consented_provider_fakes(tmp_path, monkeypatch, accepted_cloud_dialog, request):
    """Named, synthetic provider inputs for tests of metering and refusal flow.

    Every grant goes through request_grant and its accepting client dialog.
    Unknown recipients and unrecorded inputs remain ineligible.
    """
    from g6.core.cloud_consent import sha256

    monkeypatch.setenv('G6_HOME', str(tmp_path / 'consent-home'))
    source = request.path.read_text(encoding='utf-8')
    domain = request.path.parent / '_loop_domains.py'
    if domain.is_file():
        source += '\n' + domain.read_text(encoding='utf-8')
    for provider in ('anthropic:claude_code', 'openai:codex', 'openai:api', 'anthropic:api',
                     'endpoint:' + sha256('https://unused.invalid'),
                     'endpoint:' + sha256('https://unused.invalid/v1')):
        accepted_cloud_dialog('fixture:provider-inputs', provider, text=source)


@pytest.fixture
def declared_mcp_grants(monkeypatch, request):
    """Explicit opt-in for retained-source tests using an identifiable fake client.

    Grant at client startup, after a test has chosen its isolated G6_HOME. This
    uses the real accepting dialog path; it changes neither admission nor policy.
    The tool's own execution/approval dialog remains the test's original handler.
    """
    from fastmcp import Client
    from mcp.types import Implementation
    from g6.mcp.cloud_consent import request_grant

    class Accepting:
        async def elicit_form(self, **kwargs):
            return SimpleNamespace(action='accept', content={'value': accepted_cloud_name(kwargs['message'])})

    initialise, enter = Client.__init__, Client.__aenter__

    def identified(self, *args, **kwargs):
        kwargs.setdefault('client_info', Implementation(name='codex', version='test'))
        initialise(self, *args, **kwargs)

    async def granted(self):
        context = SimpleNamespace(session=Accepting(), request_id='fixture-dialog')
        parameters = getattr(getattr(request.node, 'callspec', None), 'params', {})
        scopes = ((parameters['sharing_scope'],) if 'sharing_scope' in parameters else
            ('jobs', 'missions', 'improvement', 'workflows', 'component_data', 'component_catalogue',
             'host_configuration', 'completion_history', 'model_inputs', 'consent_metadata'))
        for source in scopes if parameters.get('scope_granted', True) else ():
            result = await request_grant(context, source, 'openai:codex')
            assert result['status'] == 'granted'
        return await enter(self)

    monkeypatch.setattr(Client, '__init__', identified)
    monkeypatch.setattr(Client, '__aenter__', granted)


#: Set on CI's Linux jobs (tools/ci_bubblewrap.sh): the bwrap tests then fail, never skip, where it cannot confine.
EXPECT_BACKEND = "G6_EXPECT_BACKEND"


@pytest.fixture
def unconfined(monkeypatch):
    """For a test of what G6 does where no operating-system boundary confines a run (the git snapshot that names a file
    a test changed, the process tree's kill of what a test leaves running, a deadline or a stop reaching a test that
    signals it has started): the backend is switched off and the person's opt-in given, so the run goes ahead and the
    test's code can write outside its run folder, as it can on a machine with no boundary where the person allowed it.
    Elsewhere such a write is refused (``tests/core/test_confine_bwrap_linux.py``)."""
    from g6.core import confine

    monkeypatch.setattr(confine, "_select", lambda: (None, "unavailable: switched off for a test of the unconfined path"))
    monkeypatch.setenv(confine.ALLOW, "1")


@pytest.fixture
def bwrap_backend():
    """G6's Linux backend (``g6.core.confine._Bubblewrap``), chosen and ready to confine a child of this interpreter, or
    a skip that says why; a failure instead of the skip where ``G6_EXPECT_BACKEND=bubblewrap`` (CI's Linux jobs), so a
    missing boundary there is never a silent skip."""
    from g6.core import confine

    expected = os.environ.get(EXPECT_BACKEND, "").strip().lower() == confine.BUBBLEWRAP
    chosen, why = confine._select()
    if chosen is not None and chosen.name == confine.BUBBLEWRAP:
        why = chosen.ready(sys.executable)
        if not why:
            return chosen
    elif chosen is not None:
        why = f"this platform's backend is {chosen.name}, not bubblewrap (G6's backend for Linux)"
    (pytest.fail if expected else pytest.skip)(f"bubblewrap cannot confine a run here: {why}")


#: Top-level folders of an interpreter's standard library a confined child does not need, and the packages pytest needs.
_NOT_NEEDED = {"site-packages", "idlelib", "tkinter", "turtledemo", "ensurepip", "test", "lib2to3", "pydoc_data"}
_PYTEST = ("pytest", "_pytest", "pluggy", "iniconfig", "packaging", "pygments", "colorama", "py.py")


@pytest.fixture(scope="session")
def confined_python(tmp_path_factory):
    """A Python the Windows AppContainer can start (``g6.core.confine``), or a skip that says why: this interpreter when
    the owner has made the one-time grant, else a copy of its base installation, with pytest, in a folder this session
    made, to which the session gives ALL APPLICATION PACKAGES read and execute (the owner's one-time grant). Nothing
    here changes the access of a folder the tests did not make."""
    if sys.platform != "win32":
        pytest.skip("the AppContainer backend is Windows's")
    import shutil
    from pathlib import Path

    from g6.core import confine

    if confine.backend(sys.executable)[0] is not None:
        return sys.executable
    root, base = tmp_path_factory.mktemp("confined-python"), Path(sys.base_prefix)
    for item in base.iterdir():
        if item.is_file():
            shutil.copy2(item, root / item.name)
    shutil.copytree(base / "DLLs", root / "DLLs")
    shutil.copytree(base / "Lib", root / "Lib",
                    ignore=lambda folder, names: [n for n in names if Path(folder) == base / "Lib" and n in _NOT_NEEDED])
    site = root / "Lib" / "site-packages"
    site.mkdir()
    for entry in (Path(sys.prefix) / "Lib" / "site-packages").iterdir():
        if entry.name in _PYTEST or (entry.name.endswith(".dist-info") and entry.name.split("-")[0].lower() in _PYTEST):
            (shutil.copytree if entry.is_dir() else shutil.copy2)(entry, site / entry.name)
    chosen, _ = confine._windows()
    chosen.grant(str(root), chosen.all_packages, write=False)   # the owner's one-time grant, on the session's copy
    python = str(root / "python.exe")
    name, why = confine.backend(python)
    if name is None:
        pytest.skip(f"the Windows backend cannot run here, even on a copy of the interpreter: {why}")
    return python
