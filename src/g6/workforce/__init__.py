# SPDX-License-Identifier: Apache-2.0
"""g6.workforce — a local coding mission under a frozen standard: G6 freezes what counts as done, a person confirms it, and G6 checks the agent's patch.

g6.workforce — a local coding mission under a frozen standard: G6 freezes what counts as done, a person confirms
it, the caller's agent submits a patch, G6 runs the checks and says what the evidence supports.

Capability label: ``implemented``.

Public API (``g6.workforce.api``, re-exported here)::

    wf = bind()                                    # local stores under $G6_HOME/workforce, no network
    allow_execution(wf, repo, approver)            # a person allows G6 to run the repository's code
    found = create_coding_mission(wf, objective, repo, module=..., function=...)
    confirm(wf, mission_id, approver)              # a person confirms the frozen standard
    submit_patch(wf, mission_id, {"path.py": "full new text"})
    status(wf, mission_id)                         # completion_state: verified / qualified-draft / blocked-escalated
    certificate(wf, mission_id)                    # B4 statement per frozen test, every gap named

Over MCP: the ``g6_mission_*`` tools (``g6.mcp.mission``), where a person's answer comes from the client's own dialog.

What it is: the archive's workforce runtime (missions, work graph, run ledger, rungs, the single call door, acceptance
contracts, the repair ladder, the scheduler's route choice) with the coding objective compiler, the test bench, the
differential regression probe, the premortem's replay-check construction and the certificate (``g6.evidence``).

Limits, stated plainly:

* the repository's tests run in a scratch copy in a trusted same-user child process: not an operating-system sandbox;
  the suite runs before the standard can be confirmed, so only after a person allowed G6 to run that revision's code
  (``allow_execution``, WF-007); a test's result is the runner's own record checked by a positive control, never its
  printed tally, but that record is still written inside the test process (WF-003);
* the repository's own tests and the premortem's constructed checks are the caller's and the task's, so by default
  (DECISIONS #22) they can refute a change and never lift it to ``verified``; a coding mission closes at most
  ``qualified-draft`` unless the owner sets ``G6_WORKFORCE_CERTIFICATE_POLICY=founder-d2`` (``coding_certificate``);
* an approver's (or the decide command's) answer is recorded under the identity it gives, with a principal that says
  it was not authenticated and is not independent of the caller; G6 cannot prove a person gave it (WF-013);
* the replay constructor is two fixed templates, each matched by one requirement sentence (or a confirmed relation),
  not harness intelligence; every standard says whether it applied (WF-015);
* the coding learner (``coding_learning``) and the §25 report (``activation``) are opt-in: nothing on
  a mission's path calls them, a learned probe can only lower a later result, its validation needs labels from outside
  the probe, and no thread is started (WF-008); selection precision needs a ground truth of the right route (WF-016);
* no formal analysis, outbox, mailbox, daemon or hosted mode (audit 010 §11: deferred or dropped).
"""
import sys

from g6.core.outside import OutsideProgram
from g6.workforce.api import (
    DEFAULT_VENTURE,
    Approver,
    NotConfirmed,
    allow_execution,
    answer_clarification,
    bind,
    certificate,
    confirm,
    create_coding_mission,
    repository_dir,
    status,
    submit_patch,
)

# ``api.decide`` (a person decides a gate) is not re-exported: it would shadow the ``g6.workforce.decide`` module.

__g6_component__ = True
__g6_maturity__ = "beta"  # no block, so no g6_invoke op: this API and ``__g6_mcp_tools__``
#: Its tools on the MCP server (``g6.mcp.mission``). Those that run a repository's code ask for ``workforce.mission``.
__g6_mcp_tools__: tuple[str, ...] = ("g6_mission_create", "g6_mission_clarify", "g6_mission_confirm",
                                     "g6_mission_submit", "g6_mission_status", "g6_mission_certificate")
__g6_sensitive_ops__: tuple[str, ...] = ("mission",)
__g6_requires__: tuple[str, ...] = ("evidence", "formal", "harness", "integrity", "intent_engine", "learning",
                                    "reliability_pipeline", "safety_engine")
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("moved_goalposts", "unreproduced_failure")
__g6_introduces__: tuple[str, ...] = ("runner_in_process", "live_working_tree")
__g6_outside__ = (OutsideProgram(
    name="git", purpose="the bench's in-place runs and the repository tests read and reset a working tree with git",
    install="Install git from git-scm.com and put it on PATH.", licence="GPL-2.0", command="git"), OutsideProgram(
    name="pytest", purpose="runs a repository's tests: the mission bench in the Python running G6 (found here), "
    "repo_tests.run and in-place runs in the repository's own .venv or venv first",
    install="pip install pytest, into the Python that runs G6 and into the repository's .venv or venv if it has one.",
    licence="MIT", command=sys.executable,  # -I: a pytest.py in the folder G6 runs from is never imported (B2-M2)
    version_args=("-I", "-c", "import pytest; print('pytest', pytest.__version__)")))

__all__ = ["DEFAULT_VENTURE", "Approver", "NotConfirmed", "allow_execution", "answer_clarification", "bind",
           "certificate", "confirm", "create_coding_mission", "repository_dir", "status", "submit_patch"]

#: How each refusal this component writes is repaired (``g6.core.refusals``): code -> who fixes it, and
#: G6's words where they are not the class's own. A census test holds every code it writes to one entry.
__g6_refusals__ = {
    "G6_E_CAPABILITY_UNAVAILABLE": ("tool:g6_select",
        "The step needs a capability this server does not offer: pick another call for the task."),
    "G6_E_CONFIG_INVALID": ("person",
        "G6's configuration or the plan is not valid: tell the person what the refusal says; g6 doctor checks the "
        "setup.",
        "g6 doctor"),
    "G6_E_COST_GATE_GATED": ("person",
        "The step is over its cost budget or has no price: only the person raises a budget. Tell them."),
    "G6_E_COST_GATE_OVER_BUDGET": ("person",
        "The step is over its cost budget or has no price: only the person raises a budget. Tell them."),
    "G6_E_DELIVERABLE_WITHHELD": ("person",
        "The result is withheld until the person resolves what it names: act on no part of it. Show the reply's report "
        "to the person as it is."),
    "G6_E_DEPENDENCY_UNAVAILABLE": ("tool:g6_mission_status",
        "A step this one depends on has no result yet: read the mission's state."),
    "G6_E_EXECUTION_ARTIFACT_UNAVAILABLE": ("none",
        "The copy G6 runs the tests in held something it cannot run with (a cache or an input it does not take). Show "
        "the reply's report to the person as it is."),
    "G6_E_GIT_COMMAND_FAILED": ("none", "git failed on the repository: tell the person what the refusal says."),
    "G6_E_GIT_MISSING_BINARY": ("person",
        "git is not installed where G6 runs: tell the person; g6 doctor says what this computer needs.",
        "g6 doctor"),
    "G6_E_GIT_REPO_INVALID": ("person",
        "repo is not the top folder of a git repository G6 can use: tell the person what the refusal says."),
    "G6_E_HUMAN_APPROVAL_REQUIRED": ("person",
        "Only the person can approve this. Ask the person; offer no other way to agree, and never answer for them."),
    "G6_E_INPUT_VALIDATION": ("input",
        "The input did not fit: the refusal names the field. For an op, read g6_describe; for a tool, its description. "
        "Fix the field and call again."),
    "G6_E_INTERNAL": ("none", "G6 failed inside: tell the person; it is G6's fault."),
    "G6_E_INVALID_PARAM": ("input",
        "An input has a value G6 does not take: the refusal names it. Fix it and call again; for an op, g6_describe "
        "lists its fields."),
    "G6_E_LEARNING_NOT_RECOMMENDED": "none",
    "G6_E_LEARNING_UNKNOWN_THEORY": "input",
    "G6_E_MISSING_PARAM": ("input",
        "An input is missing: the refusal names it. Fill it in and call again; for an op, g6_describe lists its "
        "fields."),
    "G6_E_PENDING_APPROVAL": ("person",
        "The step waits for the person's approval. Ask the person; offer no other way to agree, and never answer for "
        "them."),
    "G6_E_POLICY_BLOCKED": ("person",
        "A policy refused this step: only the person can change it. Tell them what the refusal says; never change the "
        "policy yourself."),
    "G6_E_REPO_ENV_INVALID": ("person",
        "The environment G6 is set to run the repository's tests with is not valid: only the person changes that "
        "setting; g6 doctor checks it.",
        "g6 doctor"),
    "G6_E_REPO_PYTHON_INVALID": ("person",
        "The Python G6 is set to run the repository's tests with is not usable: only the person changes that setting; "
        "g6 doctor checks it.",
        "g6 doctor"),
    "G6_E_RESOURCE_LIMIT": ("person", "The run was over a resource limit: tell the person; a narrower input may fit."),
    "G6_E_RESULT_NOT_VERIFIED": ("none",
        "The result did not pass its check: report it so, never as done. Show the reply's report to the person as it "
        "is."),
    "G6_E_RETRY": ("retry",
        "A tool failed for a passing reason: make the same call once more; if it fails again, tell the person."),
    "G6_E_SENSITIVE_COMPONENT_DENIED": ("person",
        "This op runs a program or changes the machine, and this server does not allow it. Only the person can, where "
        "the server is registered: tell them the step, and what code it lets run. Never change it yourself.",
        "g6 setup --allow <component.op>"),
    "G6_E_TASK_CONTRACT_UNVERIFIABLE": ("tool:g6_premortem",
        "The task's standard of done cannot be checked as it is stated: say what done means and how each part is "
        "checked."),
    "G6_E_TESTS_DID_NOT_RUN": ("none",
        "The repository's tests did not run, so nothing was checked. Show the reply's report to the person as it is."),
    "G6_E_TEST_REPORT_UNKNOWN": "none",
    "G6_E_TIMEOUT": ("person", "The step ran out of time: tell the person; a narrower step may fit."),
    "G6_E_WORKFORCE_LEARNED_PROBE_DISAGREES": "none",
    "G6_E_WORKFORCE_LEARNED_PROBE_UNMEASURED": "none",
    "G6_E_WORKFORCE_LEARNING_EXPERIMENTAL": "none",
    "G6_E_WORKFORCE_LEARNING_NOT_CLOSED": "none",
    "G6_E_WORKFORCE_PROBE_SELF_LABELLED": "none",
    "G6_E_WORKFORCE_REPOSITORY_LINKED": "none",
    "G6_E_WORK_LOOP_NO_PROGRESS": ("none",
        "The work loop made no progress. Show the reply's report to the person as it is."),
}
