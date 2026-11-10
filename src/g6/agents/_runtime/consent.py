# SPDX-License-Identifier: Apache-2.0
"""Consent boundary for cloud coding processes with their own file tools."""
from g6.agents._runtime.model import AgentRefusal
from g6.core.cloud_consent import (
    LLMError, Source, admission, boundary_sources, current_capture, current_sources, input_source, sha256,
)
from contextlib import contextmanager


_PROGRAMS = []


def register_program(program, provider):
    """A host adapter declares its program identity without a shared/member import."""
    if provider not in ('anthropic:claude_code', 'openai:codex'):
        raise ValueError('unknown coding-process provider')
    for known, recipient in _PROGRAMS:
        if program is known:
            if provider != recipient:
                raise ValueError('program already names another provider')
            return
    _PROGRAMS.append((program, provider))


def program_provider(program):
    from g6.core import host_models
    for known, recipient in ((host_models.CLAUDE, 'anthropic:claude_code'),
                             (host_models.CODEX, 'openai:codex'), *_PROGRAMS):
        if program is known:
            return recipient
    return 'cloud:unknown'


@contextmanager
def process_admission(provider, prompt, cwd, *, local=False, directories=()):
    # CLI permissions do not confine reads to cwd. User configuration, hooks,
    # configured MCP servers and any other outside reads are a separate source.
    refs = boundary_sources((Source.text('agent_environment', 'coding-agent-environment', ''),),
                            carried=current_sources() or (
                                (Source.text(input_source(prompt), 'coding-agent-input', prompt),) if prompt else ()),
                            directories=(cwd, *directories))
    capture = current_capture()
    supplied = capture.supplied if capture is not None and capture.client is not None \
        and capture.client[0] == provider else ()
    try:
        with admission(provider, refs, sha256(prompt), supplied=supplied, local=local):
            yield
    except LLMError as exc:
        code = str(exc).partition(':')[0]
        raise AgentRefusal(code, str(exc)) from None


def consent_run(runner, argv, *, provider, prompt, cwd, **kwargs):
    from g6.agents._runtime.cli import CliAgentRunner
    if isinstance(runner, CliAgentRunner):
        return runner.run(argv, prompt=prompt, cwd=cwd, **kwargs)
    with process_admission(provider, prompt, cwd):
        return runner.run(argv, prompt=prompt, cwd=cwd, **kwargs)
