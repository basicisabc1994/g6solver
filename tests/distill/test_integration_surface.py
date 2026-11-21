"""Public integration boundaries, with no ambient host or provider capability."""
import asyncio
from uuid import uuid4
from pathlib import Path

import pytest


@pytest.fixture
def home(monkeypatch):
    # The brief supplies this ordinary in-tree home; do not change pytest temp permissions.
    path = Path('.build/home') / ('integration-' + uuid4().hex)
    path.mkdir(parents=True)
    monkeypatch.setenv('G6_HOME', str(path.absolute()))
    return path


def test_tools_use_backend_refusals_and_real_job_polling(home, monkeypatch):
    from g6.mcp import distill
    from g6.mcp.distill_backend import Service
    from g6.metaharness.jobs import Jobs
    jobs = Jobs()
    tools = {}
    class Server:
        def tool(self):
            return lambda fn: tools.setdefault(fn.__name__, fn)
    distill.register(Server(), object, jobs, backend=Service(jobs=jobs))
    monkeypatch.setattr('g6.core.door.allowed_sensitive', lambda: {'metaharness.distill_start'})
    try:
        assert tools['g6_distill_job']('missing')['warning_card']['code'] == 'G6_E_JOB_UNKNOWN'
        assert tools['g6_distill_start']('test_weakening')['warning_card']['code'] == 'G6_E_SYNTH_TRAINING_ONLY'
        assert tools['g6_simulate']('{}')['warning_card']['code'] == 'G6_E_DISTILL_INPUT'
        assert tools['g6_research_status']()['authority'] == 'off'
        result = asyncio.run(tools['g6_research_start'](object()))
        assert result['warning_card']['code'] == 'G6_E_RESEARCH_AUTHORITY'
        assert tools['g6_research_digest']()['ticks'] == 0
    finally:
        jobs.close()


def test_extension_reaches_rectifier_without_granting_evidence(home):
    from g6.mcp.distill_backend import Service
    from g6.distill.ledger import Stores
    from g6.distill.records import CapabilityExtension, FailureRecord
    result = Service().extend('Check Rust projects', 'user/repo')
    [extension] = Stores().for_model(CapabilityExtension).list()
    [failure] = Stores().for_model(FailureRecord).list()
    assert result['ext_id'] == extension.ext_id
    assert extension.gap_record == failure.record_id
    assert extension.status == 'requested' and extension.scope == 'local'
    assert failure.observed_by == 'self'


def test_gate_refusal_retains_stable_code(monkeypatch):
    from g6.mcp.distill import _call
    from g6.workforce.repo_tests import RepoRefused
    def refuse():
        raise RepoRefused('G6_E_GIT_COMMAND_FAILED', 'fixed safe refusal')
    assert _call(refuse)['warning_card']['code'] == 'G6_E_GIT_COMMAND_FAILED'


def test_restore_cancellation_never_reaccepts(home, monkeypatch):
    from g6.mcp import distill, consent
    from g6.mcp.distill_backend import Service
    tools = {}
    class Server:
        def tool(self):
            return lambda fn: tools.setdefault(fn.__name__, fn)
    backend = Service()
    distill.register(Server(), object, object(), backend=backend)
    monkeypatch.setattr(distill.surface, 'library', lambda *a: {'entry': {'entry_id': 'a' * 64, 'authority': 'A0'}})
    async def cancel(*a):
        return 'cancel', None, ''
    monkeypatch.setattr(consent, 'elicit', cancel)
    monkeypatch.setattr(backend, 'restore', lambda *a: pytest.fail('cancelled restoration reached admission'))
    assert asyncio.run(tools['g6_library_decide']('a' * 64, 'restore', 'retry', object()))[
        'warning_card']['code'] == 'G6_E_DISTILL_DIALOG'


def test_simulation_uses_trusted_context_and_records_no_gate_evidence(home):
    from g6.mcp.distill_backend import Service
    from g6.self_model.model import SelfModel
    from g6.self_model import simulate
    from tests.self_model.test_simulate import setup
    action, refs, manifest, graph = setup(simulate)
    backend = Service(snapshot=lambda now: SelfModel(), simulation_inputs=lambda now: {
        'reference_actions': refs, 'relationships': graph, 'protected_manifest': manifest}, now=lambda: 100.)
    result = backend.simulate(action.model_dump_json(exclude={'sha256'}))
    assert result['origin'] == 'self_prediction'
    assert result['affected'] == ['a', 'b', 'c', 'd']
    assert result['risk']['decision'] == 'proceed'


def test_research_dialog_start_tick_stop_and_status(home, monkeypatch):
    from g6.mcp import distill, consent
    from g6.mcp.distill_backend import Service
    from tests.distill.test_research_runner import fixture
    _, runner, _, _, _, repo = fixture()
    class Jobs:
        def start(self, operation):
            return {'result': operation()}
    backend = Service(jobs=Jobs(), runners={'fixture': runner}, now=lambda: 100.)
    tools = {}
    class Server:
        def tool(self):
            return lambda fn: tools.setdefault(fn.__name__, fn)
    distill.register(Server(), object, backend.jobs, backend=backend)
    async def accept(*a):
        return 'accept', {'confirm': 'change', 'name': 'Owner'}, ''
    monkeypatch.setattr(consent, 'elicit', accept)
    assert asyncio.run(tools['g6_research_start'](object(), 'fixture'))['result']['status'] == 'completed'
    assert tools['g6_research_stop']('fixture')['status'] == 'stopped'
    assert tools['g6_research_status']('fixture')['status'] == 'stopped'
    assert asyncio.run(tools['g6_research_start'](object(), 'fixture'))['warning_card']['code'] == 'G6_E_RESEARCH_STOPPED'
    assert repo.effects == []


def test_research_stop_reaches_an_injected_runner_before_first_tool_start(home):
    from g6.mcp.distill_backend import Service
    from tests.distill.test_research_runner import fixture
    _, runner, _, _, _, _ = fixture()
    backend = Service(runners={'fixture': runner})
    backend.research_stop('fixture')
    assert runner.stopped()
