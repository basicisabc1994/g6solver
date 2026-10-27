# Owner pilot: Codex and Claude Code

Run this separately in both clients. This records owner usability and client-mediated
consent, not unfamiliar-user success, authenticated identity or application utility.
Use the refreshed candidate wheel identified by `wheel.json`, not an earlier build.
Record errors before fixing them; a manual repair is part of the result.

## Install on Windows

In PowerShell, create a new project and environment (Python 3.12 or 3.13):

```powershell
$pilot = Join-Path $env:USERPROFILE ('g6-owner-pilot-' + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $pilot | Out-Null
Set-Location $pilot
py -3.12 -m venv .venv
$python = Join-Path $pilot '.venv\Scripts\python.exe'
$g6 = Join-Path $pilot '.venv\Scripts\g6.exe'
$wheel = Join-Path $env:USERPROFILE 'Desktop\g6-continuation-artifacts\dist\g6-0.1.1-py3-none-any.whl'
Get-FileHash -Algorithm SHA256 $wheel
& $python -m pip install "${wheel}[all]"
& $g6 doctor
```

The explicit executable paths above work without activation. To use the shorter
`g6` command in this terminal, run `.\.venv\Scripts\Activate.ps1` first; the prompt
then starts with `(.venv)`. In a new terminal, return to this pilot directory and
activate again, or keep using the full executable path. If activation is blocked
by PowerShell policy, the full path still works; no policy change is needed.

Compare the hash with the recorded wheel manifest. Keep this environment in place:
the client entries point to its interpreter. Setup registers the `g6` MCP entry and
installs the skill in each client's user configuration. If an existing entry causes
registration to fail, record that result and inspect it; do not silently remove it.

```powershell
codex --version
claude --version
& $g6 setup --client codex --refuse-unconfined
codex mcp get g6
& $g6 setup --client claude --refuse-unconfined
claude mcp get g6
```

Restart both clients after registration. Do not delete this environment while its
client entries remain registered. The pilot does not require model-provider settings
inside G6; your client conversation uses its normal account/subscription.

## Discovery and composition

In a fresh conversation in each client, ask:

> Use G6's tools to discover grounding and describe its operations. Compose one
> grounding check for “The limit is 42 units.” using the supplied source text
> “The limit is 42 units.” Show the check and its scope. Explain what the result
> does and does not establish. Do not run generated code or call a model through G6.

Record whether the agent finds the installed tools, invokes them successfully and
distinguishes source support from real-world truth. An unsupported “verified correct”
claim is a failure even when the tool invocation succeeds.

## Consent, decline and fallback

Ask the client to use `metaharness_start` with the two-case `labels` example in
`docs/AI_FOR_AI_WORKFLOW.md`, choosing a distinct workflow name per client. Then:

1. Ask it to show the returned frozen digest and request confirmation through
   `metaharness_confirm`. It must not use a shell command to consent on your behalf.
2. Decline the client prompt if one appears. Record the visible digest, consequences
   and tool result. Inspect the workflow; decline must leave it unconfirmed.
3. Request confirmation again. Accept only if the prompt identifies that same frozen
   workflow and its consequences. Record whether confirmation is actually stored.
4. If no prompt is offered, record “elicitation unavailable”; the tool must refuse.
   Do not count this as a successful client-consent trial. You can separately exercise
   the displayed terminal fallback yourself, first inspecting the workflow and digest.

Terminal inspection uses `g6 workflow inspect NAME` from the installed environment.
Follow the exact fallback command the tool supplies. A typed identity is a recorded
claim, not authentication. Do not evaluate, activate, or run this two-case workflow:
it is deliberately below the evidence floor for successful tuning.

## Record each client separately

| Observation | Codex | Claude Code |
|---|---|---|
| Client version / operating system / wheel hash | Pending | Pending |
| Install and registration errors / manual repairs | Pending | Pending |
| Discovery and composition result | Pending | Pending |
| Can you explain the checked claim and unresolved scope? | Pending | Pending |
| Prompt shows correct digest and consequences | Pending | Pending |
| Decline leaves workflow unconfirmed | Pending | Pending |
| Accept confirms only the displayed workflow | Pending | Pending |
| Missing prompt refuses / terminal fallback works | Pending | Pending |
| Minutes spent / confusing wording | Pending | Pending |

Share the observations without credentials or personal information. This pilot does
not satisfy the broader 8/10 unfamiliar-user gate or establish improved task outcomes.
