# Model on by default

Status: implemented for lead review, awaiting outside-sandbox acceptance. The lead
reported all six original tests failing on unchanged base `3d5ab4d` for the intended
reasons (missing auto selection and same-client admission). Expanded tests and
follow-on regression tests were attempted as recorded in the build report; their
base/changed-tree assertion results require the lead's outside-sandbox run.
Sandbox basetemp access remains limited; no permission or runner workaround is used.

## Rule

Review-one ruling: the built-in default covers tool-less completions only. Every
host session with tools, file access, edits or a session spending budget needs its
provider explicitly named in the key file. `host_models.session_approved` is the
shared predicate; scout construction, direct/watched session admission and agent
runtime boundaries enforce it. Source admission remains a separate requirement.
The tool-less critic keeps its existing explicit critic setting.

Key-file `none` wins over every environment provider setting. Python's explicit
provider argument retains its separate meaning. An environment-only host setting
produces an unusable caller: calls fail and no default is used while it remains.
One `llm.model_calls_disabled` reader handles resolution, doctor and send checks;
a nonempty switch in either the file or environment wins over an empty override.

With no provider named in settings or a Python argument, resolve the built-in `auto`
default for each call. Prefer the MCP calling client's installed Claude Code or
Codex host, using its existing sign-in. Test availability through `locate()`;
never start a program during selection. If the matching host is unavailable,
choose the other installed host; if neither is available, use the plain-code
path. Do not cache a choice between requests or sessions.

An explicit key-file `G6_LLM_PROVIDER=none` opts out. `G6_DISABLE_LLM` remains
the kill switch. Explicit provider configuration keeps its existing meaning.
Existing explicit provider settings retain their precedence and validation: unknown
providers refuse, paid providers need their named key, and an environment-only
host starts nothing. An environment value of `auto` cannot override a key-file choice.
Selection must preserve the existing credential, endpoint, process and provenance
checks. Do not add model calls to components that do not already make them.

Builder ruling: absent a recognised calling host, use a stable Claude Code then
Codex availability order. Unknown callers and Python API calls acquire no
same-provider exemption. If both hosts are installed, reply evidence should name
the selected host and give the reason: matching calling client, explicit setting,
or stable fallback order. Also report when the matching host was unavailable.
Installation alone does not establish that a sign-in works.

## One consent decision

At the existing send-time admission check, a recognised recipient equal to the
server-populated `capture.client[0]` can admit only current request bytes, a
declared repository inside protocol-declared client workspace roots, and G6's
operational data without a grant. Missing roots authorize no repository. The
server obtains roots through MCP, never a tool argument or its own working folder.
Agent environments, folders outside the roots, retained records, knowledge and
other source classes still need a grant. A source id alone carries no authority
between requests: current supplied-byte and repository sets belong to the request.
The preliminary `refuse_unshared` check must use the same rule. Keep the durable
disclosure before send, existing locks, source union and nested-send handling.
Record `calling_client` as the source's disclosure basis when this exemption
authorises it; update the existing strict basis validator for that one value.
An existing grant continues to have basis `grant`.

A recorded revoke or decline for the source and recipient takes precedence over
the exemption. Preserve decision revisions and stale-dialog protection. The MCP
adapter records an explicit decline as a revision-checked revoke. Cancellation,
unavailable dialogs and invalid answers do not record a person's negative decision.
Do not silently make an old decline a grant.

Keep unrecorded origins fail closed. `cloud:unknown`, absent captures, Python API
calls and different providers keep their existing consent path. No tool argument
may populate or replace the calling provider. A fallback to the other host still
needs a grant; choosing an installed program is not permission to disclose data.
Malformed or unreadable sharing state still refuses sends. MCP replies and transport
handoffs explicitly exclude the model-send exemption: retained literals still need
a grant when they were not supplied in the current request. Collectors carry source
observations and model evidence, never copied client authority; model resolution and
admission read the nearest capture owning the request's client through its parent chain.

## Limits

The MCP client's name is self-declared, not authenticated. State that limitation
in reply limits. This policy assumes trusted server code and same-user processes;
it does not defend against arbitrary code running as that operating-system user.
It grants no provider switch, unrelated source disclosure or ability to change
permissions. The kernel and per-override dialogs remain outside this slice.

## Bound-file change inventory

These bound-file changes are for the issuance window:

| File | Intended change |
| --- | --- |
| `src/g6/core/llm.py` | Per-call auto resolution, existing explicit provider authority, opt-out and kill switch; provider/reason/availability metadata and guidance. |
| `src/g6/core/host_models.py` | Describe default host sign-in use and opt-out; preserve launch, environment and process checks. |
| `src/g6/core/consent_callers.py` | Carry selection evidence through existing guarded callers and owned-host admission terms without bypassing classification or admission. |
| `src/g6/core/cloud_consent.py` | Shared model exemption basis, negative-decision precedence, revision-checked decline persistence, strict basis handling, request-owner lookup and nested model evidence; preserve send ordering and reply restrictions. |
| `src/g6/core/discovery.py` | Model-default help text. |

`door.py` needs no edit: the server-owned capture already supplies the calling provider.

Update discovery help, MCP instructions, the repository using-g6 skill, README,
setup and doctor wording together with the runtime behavior. Keep skill word caps.
Certificate, hash and manifest updates belong to a later owner issuance window.

## Acceptance and remaining work

Pending checks moved to `tests/core/test_model_default.py` cover calling-host
preference, unavailable-host fallback, and admission plus disclosure for both
recognised recipients. They exercise real resolution and admission with synthetic
inputs and fake availability, without a completion or process launch.
Their six parameterized cases failed on the base in the lead's run. Builder
attempts hit basetemp access denial; those are not claimed as assertion failures.

The expanded tests cover the client by availability by key-file by kill-switch
matrix, conflicting environment settings, unknown and absent captures, sequential
and nested sessions, grants, revokes, durable declines, stale dialogs, cross-provider
fallback, tool-argument rejection, reply evidence and preservation of source receipts.
Existing consent suites retain corrupted-state and transport checks. The lead runs
all sandbox-limited tests on the base and changed tree before committing. Inventory
pins, hashes and manifests stay untouched; conflicts are reported individually.

The follow-up build restores explicit-provider regression behavior, retains the
child collector authority boundary, and restricts the exemption to model sends.
Additional tests cover retained reply/transport refusal followed by an explicit
grant, a live request owner's changed identity, environment-auto opt-out protection,
and selection evidence from owned-host admission. Their execution remains pending
the lead's run: the builder stopped pytest after the temporary-directory denial.
