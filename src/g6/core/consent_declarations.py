# SPDX-License-Identifier: Apache-2.0
"""Host-code source contracts for producers that can send to a model.

Contracts describe possible inputs, including retained inputs. They never infer
permission from a returned value. Source scopes are deliberately conservative:
for example component_data includes indexes, corpora and arbitrary component IO.
The inventory challenges additions independently of runtime result rendering.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from g6.core import cloud_consent as cloud


@dataclass(frozen=True)
class Declaration:
    classes: tuple[str, ...]

    def __post_init__(self):
        if not self.classes or any(c not in ('client_request', 'client_repository', 'g6_operational')
                and not re.fullmatch(r'source:[A-Za-z0-9_.:-]+', c) for c in self.classes):
            raise ValueError('unknown producer source class')

    def sources(self, arguments=None, *, payload=''):
        arguments = arguments or {}
        capture = cloud.current_capture()
        refs = []
        for kind in self.classes:
            if kind == 'g6_operational':
                continue
            if kind == 'client_request':
                actual = capture.client_arguments if capture is not None else None
                text = json.dumps(actual if actual is not None else arguments, sort_keys=True, default=str)
                source = cloud.input_source(text)
                if actual is not None:
                    capture.supplied.add(source)
            elif kind == 'client_repository':
                repo = arguments.get('repo')
                try:
                    source = cloud.repository_source(repo) if repo else 'repositories'
                except (ValueError, OSError):
                    # Validation belongs to the tool. An invalid client path
                    # must not replace its earlier refusal with a consent error.
                    source = cloud.input_source(str(repo))
            else:
                source = kind.removeprefix('source:')
            refs.append(cloud.Source.text(source, 'producer-declaration', text if kind == 'client_request' else payload))
        return tuple(refs)


def declares(*classes):
    declaration = Declaration(tuple(classes))
    def decorate(fn):
        fn._g6_declaration = declaration
        return fn
    return decorate


def contract(*classes):
    return Declaration(tuple(classes))


TOOLS = {
    'g6_help': contract('g6_operational'),
    'g6_components': contract('source:component_catalogue'),
    **{name: contract('client_request', 'source:component_catalogue') for name in ('g6_find', 'g6_describe')},
    **{name: contract('client_request', 'source:component_data') for name in ('g6_invoke', 'g6_compose')},
    'g6_review_patch': contract('client_request', 'client_repository', 'source:host_configuration'),
    'g6_review_until_clean': contract('client_request', 'client_repository', 'source:host_configuration',
                                     'source:completion_history'),
    'g6_premortem': contract('client_request', 'client_repository'),
    'g6_mission_create': contract('client_request', 'client_repository', 'source:missions'),
    'g6_mission_submit': contract('client_request', 'client_repository', 'source:missions', 'source:host_configuration'),
    **{name: contract('client_request', 'source:missions') for name in (
        'g6_mission_clarify', 'g6_mission_confirm', 'g6_mission_status', 'g6_mission_certificate')},
    **{name: contract('client_request', 'source:improvement') for name in (
        'g6_improve_start', 'g6_improve_stop', 'g6_improve_status')},
    **{name: contract('client_request', 'source:workflows') for name in (
        'metaharness_start', 'metaharness_confirm', 'metaharness_propose_code', 'metaharness_evaluate_start',
        'metaharness_run', 'metaharness_inspect', 'metaharness_propose', 'metaharness_evaluate',
        'metaharness_decide', 'metaharness_diagnose', 'metaharness_approve', 'metaharness_rollback')},
    # A retained job can contain any of the asynchronous producers' output.
    'metaharness_job': contract('client_request', 'source:jobs'),
    **{name: contract('g6_operational') for name in (
        'g6_cloud_grant', 'g6_cloud_revoke')},
    **{name: contract('source:consent_metadata') for name in ('g6_cloud_grants', 'g6_disclosures')},
}

def _arguments(content='', pointer=''):
    return {**dict.fromkeys(content.split(), 'content'), **dict.fromkeys(pointer.split(), 'pointer')}


# Resolving a pointer reads a separate declared source. JSON envelopes can
# contain pointers too; their producers declare component/workflow sources.
# The inventory checks this closed table against the actual MCP schemas.
TOOL_ARGUMENTS = {
    'g6_help': {}, 'g6_components': {}, 'g6_cloud_grants': {}, 'g6_disclosures': {},
    'g6_cloud_grant': _arguments('provider', 'source'),
    'g6_cloud_revoke': _arguments('provider', 'source'),
    'g6_find': _arguments('query limit'),
    'g6_describe': _arguments(pointer='component'),
    'g6_invoke': _arguments('op', 'component params_json'),
    'g6_compose': _arguments(pointer='composition_json'),
    'g6_review_patch': _arguments('task requirements claim intent timeout_s expected_artifact_digest',
                                 'repo tests sources base search import_roots proposed_checks template_instances'),
    'g6_review_until_clean': _arguments('task reason', 'repo session base'),
    'g6_premortem': _arguments('task intent', 'repo'),
    'g6_mission_create': _arguments('objective function budget_cents risk', 'repo module helpers'),
    'g6_mission_clarify': _arguments('replay_semantics', 'mission_id'),
    'g6_mission_confirm': _arguments(pointer='mission_id'),
    'g6_mission_submit': _arguments('claim timeout_s', 'mission_id repo'),
    'g6_mission_status': _arguments('detail', 'mission_id'),
    'g6_mission_certificate': _arguments(pointer='mission_id'),
    'g6_improve_start': _arguments(pointer='folder'),
    'g6_improve_stop': _arguments('reason', 'folder'),
    'g6_improve_status': _arguments(pointer='folder'),
    'metaharness_start': _arguments(pointer='specification_json'),
    'metaharness_confirm': _arguments('workflow_digest', 'workflow'),
    'metaharness_propose_code': _arguments('hypothesis files_json', 'workflow'),
    'metaharness_evaluate_start': _arguments('proposal_digest', 'workflow'),
    'metaharness_job': _arguments('detail', 'job_id'),
    'metaharness_run': _arguments('task_input', 'workflow'),
    'metaharness_inspect': _arguments(pointer='workflow'),
    'metaharness_propose': _arguments('profile_json proposer', 'workflow'),
    'metaharness_evaluate': _arguments('proposal_digest', 'workflow'),
    'metaharness_decide': _arguments('report_digest proposal_digest', 'workflow'),
    'metaharness_diagnose': _arguments(pointer='workflow'),
    'metaharness_approve': _arguments('decision_digest', 'workflow'),
    'metaharness_rollback': _arguments('version', 'workflow'),
}


AGENTS = {
    'g6.agents.agent_claude.adapter.ClaudeCodeRuntime': contract('client_request', 'client_repository',
                                                             'source:agent_environment'),
    'g6.agents.agent_codex.adapter.CodexRuntime': contract('client_request', 'client_repository',
                                                       'source:agent_environment'),
    'g6.agents._runtime.cli.CliAgentRunner': contract('client_request', 'client_repository', 'source:agent_environment'),
    'g6.agents.agent_autogen.adapter.AutoGenRuntime': contract('client_request', 'source:model_inputs'),
    'g6.agents.agent_smolagents.adapter.SmolagentsRuntime': contract('client_request', 'source:model_inputs'),
    'g6.agents.agent_langgraph.adapter.LangGraphRuntime': contract('client_request', 'source:model_inputs'),
}

CLI_COMPONENTS = {'agent_claude', 'agent_codex'}

# Every generic model input may contain earlier model replies or retained data.
# Specialised callers add their carried origins through request construction.
MODEL_INPUTS = contract('source:model_inputs')

SDK_SENDS = {'g6.harness.verification_proposer._RecordedMessages.create': MODEL_INPUTS}

# Identity declarations are separate from recipient classification. Adding a
# caller to classification alone cannot silently add a source declaration.
MODELS = {name: MODEL_INPUTS for name in (
    'g6.core.llm.LLMCaller', 'g6.core.llm.EmptyRetry', 'g6.core.llm.TimingLLMCaller',
    'g6.core.llm.FallbackCaller', 'g6.core.llm.FakeLLM', 'g6.core.llm.NoModel',
    'g6.core.llm.UnusableModel', 'g6.core.llm._NoKey', 'g6.core.llm.OllamaCaller',
    'g6.core.llm.OpenAICompatibleCaller', 'g6.core.llm.AnthropicCaller',
    'g6.core.host_models._HostCaller', 'g6.core.host_models.ClaudeCodeCaller', 'g6.core.host_models.CodexCaller',
    'g6.core.consent_callers._Consent', 'g6.core.consent_callers._Unknown',
    'g6.core.consent_callers.CallerWrapper', 'g6.core.consent_callers.ConsentClaude',
    'g6.core.consent_callers.ConsentCodex', 'g6.reasoning.budget.MeteredCaller',
    'g6.metaharness.measurement.TraceCaller', 'g6.metaharness.loop._Counted',
    'g6.harness.verification_proposer._Limits', 'g6.harness.verification_proposer.StudyCaller',
    'g6.harness.completion_loop.ToolSession', 'g6.self_maintenance.usage.WatchedClaude',
    'g6.grounding.judges.nli.LocalNLICaller', 'g6.metaharness.lora.AdapterCaller',
)}


def model_sources():
    return cloud.boundary_sources(MODEL_INPUTS.sources(), carried=cloud.prompt_sources())


def tool_declaration(fn):
    explicit = getattr(fn, '_g6_declaration', None)
    if explicit is not None:
        return explicit
    if fn.__module__ in ('g6.mcp', 'g6.mcp.cloud_consent'):
        return TOOLS.get(fn.__name__)
    return None
