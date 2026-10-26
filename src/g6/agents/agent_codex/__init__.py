# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_codex — hand a coding task to OpenAI's Codex, the coding agent you installed: read-only unless you grant writes, in the folder you name, under a deadline.

An extension in the agents family, and the first adapter on its one runtime (``g6.agents._runtime``, the
``agent_runtime`` rewrite). The owner, 2026-10-20: the archive's ``agent_codex`` ported, its honest core kept (read-only
by default, never full access, the prompt on stdin, never ``verified``) and its hanging transport replaced.

Capability label: ``implemented``.

    from g6.agents._runtime import run_agent
    from g6.agents.agent_codex import CodexRuntime
    task = CodexRuntime.task("Make the failing test pass", "/path/to/project", writes=True)
    outcome = run_agent(CodexRuntime(), task)                 # a dry run: nothing starts
    outcome.code, outcome.detail["plan"]["argv"]              # "G6_E_AGENT_DRY_RUN", the command line it would run
    outcome = run_agent(CodexRuntime(), task, dry_run=False)  # starts Codex, signed in as you
    outcome.state, outcome.claimed_status                     # "qualified-draft", Codex's own word on its work

Codex is a program you install and sign in to yourself (``npm install -g @openai/codex``, then ``codex login``); G6
finds it through ``G6_CODEX`` or on PATH when it is called, never bundles or installs it, and never passes it a key. A
dry run needs none of it. A run (:mod:`~g6.agents.agent_codex.adapter`) goes through the runtime's gate before anything
starts: the workspace must be an existing folder named by its full path (never G6's own state folder or installed code,
a drive's root, or your home folder or a folder that holds it), the task grants the network (Codex sends what it reads
to its model), and ``g6.action_gating`` must allow reading the folder (or writing it, with ``writes``), so a
secret-looking folder goes to a person. A write run needs the folder under version control, so you can see and undo
what changed. It is a dry run unless you say ``dry_run=False``. Then Codex is started as ``codex exec`` in that folder,
through ``g6.core.component_process``: ``--sandbox read-only``, or ``workspace-write`` when you grant writes, never
full access or a flag that skips its sandbox or its approvals, with four settings stated on every run (no approval
prompt, no network for the commands it runs, nothing writable beyond the workspace, not ``/tmp``); the prompt goes in
on stdin; the environment holds only what a program needs to start and find its sign-in; the deadline (600 seconds by
default, at most the trusted ceiling of 900, or a tighter time limit you set) ends its whole process tree, as G6's stop
does. An error exit, a deadline, a missing program and an engaged stop are each ``blocked-escalated`` with their own
code. A finished run is ``qualified-draft``: Codex's last message is the output, and what it says of its own work
("all tests pass") is ``claimed_status``, a claim. Send its patch to ``g6.workforce.submit_patch``, or its output to
``g6.harness.run``, to have it checked.

What this does not narrow: Codex's sandbox, read-only included, reads every file you can (``~/.g6/.env`` and
``~/.ssh`` among them) and Codex sends what it reads to its model (``leaked_secret``); a file in the folder can carry
instructions Codex follows (``prompt_injection``); and only those four settings are G6's, so the rest of your own Codex
configuration (its MCP servers, which run outside its sandbox, its profiles, web search) still applies.

Ops (:mod:`~g6.agents.agent_codex.agent_codex_block`): ``run`` and ``available``. Both start Codex, so both are
sensitive: callable from Python, and over MCP only when the person who runs the server lists them in
``G6_MCP_SENSITIVE``. ``ops`` and ``help`` answer over MCP.

Not ported from the archive: the model planner that chose the sandbox on every run (the deterministic floor is the
control), the hosted paid gate (your own Codex sign-in pays; the server's owner decides through the sensitive ops), the
path through Codex's software development kit, which was a stub, and the openai probe, the duplicate ops, the
injection-pattern scan, the transport that could wedge on Windows, the workspace that defaulted to the server's
folder, exit 0 reported as a pass, and the scaffold. The model caller over Codex (``CodexLLMCaller``) belongs in
``g6.core.llm`` and is not part of this port.
"""
from g6.agents.agent_codex.adapter import CODEX, CODEX_ENV, CodexRuntime
from g6.agents.agent_codex.agent_codex_block import SENSITIVE_OPS, AgentCodexBlock, AgentCodexInput

__g6_component__ = True
__g6_block__ = "AgentCodexBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # they start the codex program you installed
__g6_requires__: tuple[str, ...] = ("action_gating", "safety_engine")  # through the family's runtime (g6.agents._runtime)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (Codex reads what
# your account can read and sends it to its model; it follows instructions it finds in the folder).
__g6_mitigates__: tuple[str, ...] = ("overprivileged_agent",)
__g6_introduces__: tuple[str, ...] = ("leaked_secret", "prompt_injection")
__g6_outside__ = (CODEX,)

__all__ = ["CODEX", "CODEX_ENV", "AgentCodexBlock", "AgentCodexInput", "CodexRuntime"]