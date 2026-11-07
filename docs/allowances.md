# Both agents know both allowances

`g6_allowances` returns Codex and Claude Code windows, their age and headroom, and advice before optional fan-out.
It calls no model. It blocks nothing, starts no subagents and switches no providers. Mandatory checks and the person's
task keep their existing policy. Concurrent sessions can consume the same displayed room, and work already running
is still in flight. Unused allowance alone says nothing about useful work or tomorrow's capacity.

Claude Code support is **not established** until a real sanitised 2.1.319 status-line capture is checked. The adapter
and observer are built against the documented shape using synthetic fixtures. The tool always reports this limitation;
synthetic tests do not establish provider support. A background-only session may supply no current observation.
Codex uses the existing `account/rateLimits/read` app-server reader, without a conversation or model request. That
protocol can involve provider I/O. G6 reads no credentials or sign-in files.

## Settings and binding

The owner can create `G6_HOME/allowances/settings.json`. No settings file is created or changed by installation preview.
All fields below are optional. With no profile token a provider is unknown and is not refreshed.

```json
{"profiles":{"codex":"opaque-local-profile-token","claude":"another-opaque-local-profile-token"},"policy":{"reserve_share":0.2,"reserve_release_fraction":0.8571428571428571,"max_age_seconds":120,"refresh_seconds":60,"near_full_share":0.95,"pace_tolerance":0.05,"parallelism_cap":2},"semantics":{}}
```

Profile tokens are local random labels, never account or session identifiers. Rotate the token on every sign-in or
profile change; a different token cannot read the old token's cache. The raw readers do not establish account continuity.
Therefore production advice never recommends increased optional spending from an unobserved sign-in binding. The pure
policy can evaluate an explicitly established binding; the shipped adapters do not claim to establish one.

Window semantics default to unknown. A percentage and reset do not establish fixed-window semantics. An owner who has
checked the provider's contract can declare `fixed`, `rolling` or `replenishing` by window identity under
`semantics.codex` or `semantics.claude`; only `fixed` weekly windows get a pace. Codex identities include
`default.primary` and `default.secondary`; extra limit sets get a digest rather than a raw label. Claude identities
are `five_hour` and `seven_day`. Every reported window stays separate, including malformed windows and extra limit sets.

For fixed weekly elapsed fraction f, target is f and ceiling is min(1, 7f/5), capped at 1 minus reserve until f reaches
6/7. Five-hour windows get no weekly pacing rule. Near-full is inclusive at 95%, tolerance is inclusive at five percentage
points, and readings are fresh through 120 seconds inclusive. Reset at or before now is expired; future observation times
are unusable. A fixed window whose implied start is still in the future supplies no current headroom or pace.
Unknown or failed fields never become zero. All-window capacity, provider blocks, caps and reserve dominate
weekly advice. Behind pace permits an increase only with useful independent work and fresh usable room in every window.
On pace keeps the usual workload; ahead of pace conserves optional fan-out. Unknown data never increases the no-reading
recommendation, whose additional optional-agent count is zero. These counts are advice, never launch admission.

Settings accept reserve in [0,1), reserve release and tolerance in [0,1], near-full in (0,1], and an integer parallelism
cap from 0 through 32. Age must be positive; refresh must be at least 35 seconds and no greater than maximum age.
Numbers must be finite and booleans do not count as numbers. Useful independent work is a caller assertion, not a
measurement made by this tool. Unknown semantics, support or continuity cannot be overridden by that assertion.

Codex refresh runs in a background worker from the MCP tool, outside renderer and hook paths. The raw reader's timeout
is bounded; a durable refresh lease prevents duplicate refreshes and expires after a crashed worker. A failed read retains
the old good observation time. The cache keeps last-attempt and last-good times separately, and merges by window identity
under a transaction. Late older writes cannot replace newer observations. Equal-time conflicts retain the stronger
constraint and become unknown; an equal-time capture never replaces the first capture. Profile changes also fence late
writers and invalidate old rows, including when switching back. A failed provider or missing window is visible.
The normalised observation commits before the optional capture is published. An interrupted publication can leave an
older or absent capture; it cannot roll back the committed observation or freshen the old capture's time.

## Status-line installation preview and rollback

Installation is the owner's choice. G6 never edits Claude Code settings. Preview supports an existing `statusLine`
object of type `command`, preserving its command and additional properties. Other forms, missing renderers and recursive
G6 wrappers are refused. Choose the same renderer shell the original command uses: `powershell`, `cmd` or `sh`.
The invocation shell used by Claude Code must also be explicitly matched. Windows `cmd` preview accepts executable paths
without shell expansion characters; paths with spaces are quoted. PowerShell and POSIX shell previews use their own
argument quoting. Native Windows CMD and PowerShell forwarding are checked with harmless renderers. POSIX preview quoting
has structural checks only; POSIX execution has not been established here. This is a bounded set of forms, not a promise
for every shell or client version.

In Python, with the existing configuration supplied by the owner:

```python
import json
import sys
from g6._allowance.statusline import preview, rollback

p = preview(original_config, python=sys.executable, profile="opaque-local-profile-token",
            shell="powershell", invocation_shell="cmd")
print(p["settings_line"])  # exact one-line JSON settings value for this preview
print(p["support"])
saved_original_config = p["original_config"]  # save separately before applying the preview
# Later: preserve other settings changed since installation.
print(json.dumps(rollback(current_config, saved_original_config), separators=(",", ":")))
```

Keep the original configuration separately. Apply only the previewed status-line property through the client's normal
settings mechanism. Rollback restores that property's saved value and preserves unrelated current settings. Preview
prints a complete JSON object so the exact property, padding and quoting can be inspected before applying it.
No new G6 CLI command is added.

The wrapper forwards the original stdin bytes exactly once to the original command, preserving its stdout, stderr and
exit code. Its observer is a separate process with a 0.75-second budget; parse/write failure or timeout cannot corrupt
the renderer. A renderer failure does not suppress a valid observation. A recursion marker prevents nested wrappers.
The observer has a 1 MiB parsing limit. It persists only allow-listed numbers and checked reset times from `rate_limits`,
plus the read time in its capture file. It never stores account or session identifiers, repository content or raw input.

The observer's saved capture is under `G6_HOME/allowances/captures/<profile-digest>/claude-2.1.319.json` and contains
only `rate_limits` and `read_at`. It is replaced atomically under the cache's writer transaction, ordered by observation
time. Missing/malformed fields record failure without renewing last-good time. To check real support later, the owner
can inspect and copy a real sanitised capture to `tests/fixtures/claude_statusline_2_1_319.capture.json`. The admission test
skips until that file exists. Adding a file alone does not turn on a support claim; its provenance and version must be
checked and the adapter's support declaration deliberately updated.

## Agent guidance and limits

Both agents read the same MCP tool before optional fan-out. Claude's plugin emits one line of context only when the
recommendation changes. Hooks read cached state only; they never refresh Codex or call a model. Failure emits no context.
There is no automatic cross-provider suggestion: a configured executor, comparable task/tool support and a source-specific
disclosure grant would all be needed. This capability infers none of them and implements no optional launch control.

The deterministic tests measure normalisation, policy and failure handling. They do not measure usage benefit. Weekly
exhaustion, unused allowance, refused work, useful completed tasks, interruptions, uptake and added cost need equal-workload
periods or paired decisions under a separately frozen evaluation protocol, reporting missingness, in-flight work and
confounding. No efficiency or exhaustion-prevention claim follows from these fixtures.
