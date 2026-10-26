# SPDX-License-Identifier: Apache-2.0
"""g6.agents.agent_claude — hand a coding task to Anthropic's Claude Code, which you installed: read tools only unless you grant edits, never its shell or the web.

An extension in the agents family, an adapter on its one runtime (``g6.agents._runtime``, the ``agent_runtime``
rewrite), built after ``agent_codex``'s pattern. The owner, 2026-10-20: the archive's ``agent_claude`` rewritten; what
it labelled ``verified`` is a claim here, and its hosted gates, its paid fallback and its unbounded transport are gone.

Capability label: ``implemented``.

    from g6.agents._runtime import run_agent
    from g6.agents.agent_claude import ClaudeCodeRuntime
    task = ClaudeCodeRuntime.task("Make the failing test pass", "/path/to/project", writes=True)
    outcome = run_agent(ClaudeCodeRuntime(), task)                 # a dry run: nothing starts
    outcome.code, outcome.detail["plan"]["argv"]                   # "G6_E_AGENT_DRY_RUN", the command line it would run
    outcome = run_agent(ClaudeCodeRuntime(), task, dry_run=False)  # starts Claude Code, signed in as you
    outcome.state, outcome.claimed_status                          # "qualified-draft", its own word on its work

Claude Code is a program you install and sign in to yourself (``npm install -g @anthropic-ai/claude-code``, then
``claude`` once to sign in), used under Anthropic's terms; G6 finds it through ``G6_CLAUDE`` or on PATH when it is
called, never bundles or installs it, and never passes it a key: it signs in on its own, and no key in G6's key file
or in the environment reaches it. A dry run needs none of it. A run
(:mod:`~g6.agents.agent_claude.adapter`) goes through the runtime's gate before anything starts: the workspace must be
an existing folder named by its full path (never G6's own state folder or installed code, a drive's root, or your home
folder or a folder that holds it), the task grants the network (Claude Code sends what it reads to its model), and
``g6.action_gating`` must allow reading the folder (or writing it, with ``writes``). A write run needs the folder under
version control. It is a dry run unless you say ``dry_run=False``. Then Claude Code is started as ``claude -p`` in that
folder, through ``g6.core.component_process``, with every one of its tools G6 knows named on the command line: its
read tools allowed, its edit tools allowed only with ``writes``, and its shell, web tools and sub-agents denied on
every run; in its default permission mode, with no MCP server and none of the repository's own Claude Code settings;
the prompt on stdin; the environment only what a program needs to start and find its sign-in; the deadline (600
seconds by default, at most the trusted ceiling of 900, or a tighter time limit you set) ends its whole process tree,
as G6's stop does. A finished run is ``qualified-draft``: its result is the output, and what it says of its own work is
``claimed_status``, a claim. Send its patch to ``g6.workforce.submit_patch``, or its output to ``g6.harness.run``.

What this does not narrow: Claude Code's read tools read every file you can read (``~/.g6/.env`` and ``~/.ssh`` among
them) and it sends what it reads to its model (``leaked_secret``); a file in the folder (a README, a ``CLAUDE.md``) can
carry instructions it follows (``prompt_injection``); your own Claude Code settings and hooks still apply; and its
tools are kept to the posture by Claude Code's own permission system, which G6 trusts.

Ops (:mod:`~g6.agents.agent_claude.agent_claude_block`): ``run`` and ``available``. Both start Claude Code, so both are
sensitive: callable from Python, and over MCP only when the person who runs the server lists them in
``G6_MCP_SENSITIVE``. ``ops`` and ``help`` answer over MCP.

Not ported from the archive: the gated tool loop over Anthropic's model service directly (it needs a tool-calling
caller in ``g6.core.llm``, which the library does not have yet), the MCP session, skill and agent registries, the
pipeline and meta-loop (``g6.harness`` and ``g6.operator`` do that), the deployment guard, the key firewall, the paid
entitlement, the spend ledger, the fallback to another provider on the platform's key, the prompt on the command line,
the folder that defaulted to the server's own, the step-count "safety check", the injection-pattern scan and the
scaffold.
"""
from g6.agents.agent_claude.adapter import CLAUDE, CLAUDE_ENV, ClaudeCodeRuntime
from g6.agents.agent_claude.agent_claude_block import SENSITIVE_OPS, AgentClaudeBlock, AgentClaudeInput

__g6_component__ = True
__g6_block__ = "AgentClaudeBlock"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # they start the claude program you installed
__g6_requires__: tuple[str, ...] = ("action_gating", "safety_engine")  # through the family's runtime (g6.agents._runtime)
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (Claude Code reads
# what your account can read and sends it to its model; it follows instructions it finds in the folder).
__g6_mitigates__: tuple[str, ...] = ("unguarded_tool_use", "overprivileged_agent")
__g6_introduces__: tuple[str, ...] = ("leaked_secret", "prompt_injection")
__g6_outside__ = (CLAUDE,)

__all__ = ["CLAUDE", "CLAUDE_ENV", "AgentClaudeBlock", "AgentClaudeInput", "ClaudeCodeRuntime"]