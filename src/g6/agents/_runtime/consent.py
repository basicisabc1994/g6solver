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


def _takes_stop(run):
    import inspect
    try:
        parameters = inspect.signature(run).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == 'stop' or p.kind is p.VAR_KEYWORD for p in parameters)


def consent_run(runner, argv, *, provider, prompt, cwd, stop=None, **kwargs):
    """The one start of a cloud coding client's session, shared by the Claude Code and Codex adapters and by the
    routing trials' runner. Before anything is started it refuses while model calls are switched off
    (``G6_DISABLE_LLM``) and while G6's stop is engaged (``stop``, or ``g6.core.stop.engaged`` when none is given:
    in process, ``G6_STOP``, or the ``STOP`` file); each refusal carries ``detail["started"] = False``. The stop is
    then handed to the runner, so a session under way is ended when it engages. A stand-in whose ``run`` takes no
    ``stop`` is still held to the check before the start."""
    import os
    from g6.agents._runtime import codes
    from g6.agents._runtime.cli import CliAgentRunner, _stopped
    from g6.core import stop as core_stop
    given = stop is not None
    stop = stop or core_stop.engaged
    if os.environ.get('G6_DISABLE_LLM'):
        raise AgentRefusal('G6_E_LLM_DISABLED', 'model calls are disabled by G6_DISABLE_LLM, and a coding client runs '
                                                'a model, so nothing was started', detail={'started': False})
    why = _stopped(stop)
    if why:
        raise AgentRefusal(codes.STOPPED, f'G6 is stopped: {why}, so nothing was started', detail={'started': False})
    if given or _takes_stop(runner.run):
        kwargs['stop'] = stop
    if isinstance(runner, CliAgentRunner):
        return runner.run(argv, prompt=prompt, cwd=cwd, **kwargs)
    with process_admission(provider, prompt, cwd):
        return runner.run(argv, prompt=prompt, cwd=cwd, **kwargs)
