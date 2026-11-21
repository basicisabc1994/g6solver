# SPDX-License-Identifier: Apache-2.0
"""Default-filtering adapters at the first unbound layer around bound builders.

These functions accept producer contracts, not arbitrary component values. They
never assign authorship by inspecting ok, a diagnostic code, or nested data.
The original builders remain untouched for the certificate issuance batch.
"""
from __future__ import annotations
from collections.abc import Mapping

from g6.core.agent_guidance import reply_for_agent, text_for_agent, _ISOLATION_ACTION
from g6.core.external_data import borrowed_envelope, plain_text

ISOLATION_ACTION = _ISOLATION_ACTION


def warning_card(*args, **kwargs):
    from g6.core.reliability_envelope import warning_card as build
    return reply_for_agent(build(*args, **kwargs))


def card(existing, *args, **kwargs):
    from g6.core.door import card as build
    return reply_for_agent(build(existing, *args, **kwargs))


def borrows_card(argument):
    """Record the bound card constructor's argument-selection branch.

    This is the exact guard of door.card, including non-dict mappings. It records
    which argument the constructor copies; no returned text or reply code is
    searched for authorship. Remove this adapter when the bound builder records
    its branch natively in the issuance batch.
    """
    from g6.invariants import ERROR_CODE_PATTERN
    return isinstance(argument, Mapping) and bool(ERROR_CODE_PATTERN.match(str(argument.get("code", ""))))


def envelope_fields(**kwargs):
    from g6.core.reliability_envelope import envelope_fields as build
    return reply_for_agent(borrowed_envelope(build(**kwargs), kwargs.get("warning_card")))


def door_envelope(claimed, **kwargs):
    from g6.core.door import door_envelope as build
    supplied = kwargs.get("warning_card") if claimed != "verified" else None
    return reply_for_agent(borrowed_envelope(build(claimed, **kwargs), supplied))


def door_reply_fields(value, *, card_owned=True):
    """Compatibility receiver; all unmarked prose is filtered by default."""
    return reply_for_agent(value)


def blocked_reply(*args, **kwargs):
    from g6.core.door import blocked_reply as build
    return reply_for_agent(build(*args, **kwargs))


def invoke_checked(component, op, params, *, registry=None, **kwargs):
    from g6.core.door import invoke_checked as door_invoke, said_state
    from g6.core.registry import get_registry
    from g6.core.consent_registry import GuardedRegistry
    from g6.core.external_data import ComponentRegistryIngress, ExternalDataLimit, component_failure_reply

    ingress = ComponentRegistryIngress(registry if registry is not None else get_registry())
    try:
        value = door_invoke(component, op, params, registry=GuardedRegistry(ingress), **kwargs)
    except ExternalDataLimit as exc:
        return blocked_reply(str(exc), request_id=kwargs.get("request_id", ""))
    if ingress.failure is not None:
        value = component_failure_reply(ingress.failure, request_id=value.get("request_id", ""))
    elif isinstance(ingress.value, Mapping) and said_state(ingress.value) != "verified":
        supplied = ingress.value.get("warning_card")
        if borrows_card(supplied):
            value = borrowed_envelope(value, supplied)
    return reply_for_agent(value)


def job_result(value):
    return reply_for_agent(value)


def script_run(*args, **kwargs):
    """First unbound receiver of sandbox's typed execution record.

    A no-returncode diagnostic or timeout is constructed by the host. An executed
    program's stdout/stderr are data. The bound sandbox prepends exactly one
    confine.import_failure sentence on the documented import-failure branch;
    filter that constructed prefix alone, preserving all following program bytes.
    """
    from dataclasses import replace
    from g6.core import sandbox, confine
    from g6.core.external_data import program_diagnostic, program_output

    run = sandbox.run_script(*args, **kwargs)
    isolation = reply_for_agent(run.isolation)
    stderr = run.stderr
    stdout = program_output(run.stdout) if run.returncode is not None else run.stdout
    if run.returncode is None or run.status == "timeout":
        stderr = text_for_agent(stderr)
    elif run.isolation.get("backend") is not None and run.returncode != 0:
        prefix = confine.import_failure(stderr)
        if prefix and stderr.startswith(prefix + "\n"):
            safe = text_for_agent(prefix)
            stderr = program_diagnostic(safe, stderr[len(prefix) + 1:])
        else:
            stderr = program_output(stderr)
    else:
        stderr = program_output(stderr)
    return replace(run, stdout=stdout, stderr=stderr, isolation=isolation)


def confine_record(plan):
    # A run's isolation record is data its report's exact-type schema reads: filtered, then without the reply role.
    return plain_text(reply_for_agent(plan.record()))


def confine_refused(plan):
    from g6.core import confine
    return text_for_agent(confine.refused(plan))


def confine_refusal(plan):
    from g6.core import confine
    return text_for_agent(confine.refusal(plan))


def confine_not_started(record, exc):
    from g6.core import confine
    return plain_text(reply_for_agent(confine.not_started(record, exc)))


def confine_summary(records):
    from g6.core import confine
    return plain_text(reply_for_agent(confine.summary(records)))
