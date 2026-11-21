# Agentic override kernel v1

Owner ruling, 2026-11-17: overrides are covered by consent. The person is not asked per override. The model call
uses the existing consent check for its provider in `core/cloud_consent.py` for hosts and endpoints. Process callers
run under the caller's Python allowance, recorded as `allowed in code`. A separate
change owns the same-provider exemption. This kernel changes no provider permission or bound file. This ruling
replaces the earlier question and typed-name design.

## Intent and scope

G6's code stays the structure of the computation. A model may propose values for one declared part for one call.
The original runs first through the guarded door. With no trigger its reply is unchanged, without a model or child.
Only opted-in pure computations qualify: no model calls, network, user-file reads or state writes in the operation.
A part names functions in the component's package. Prompt mode is available; code mode refuses with
`G6_E_AGENTIC_MODE_UNAVAILABLE`. No override changes installed code or another concurrent call.

Triggers are a failure originating in the component's own computation, a reply carrying its declared limit, or the
caller naming a part after a successful answer. Trust-door refusals never trigger: isolation, sensitive allowance,
input validation, approval, consent and budget rules keep their authority. A model's judgement only proposes an
override; it cannot admit a call, pass a requirement or become evidence.

The protected set covers certificate-bound files, all core trust doors, keys, consent, isolation, receipts, ledgers,
gates, promotion, patch-review authority, tests, graders, oracles, judges, verifiers, component declared verifiers
and this kernel. Aliases are checked by resolved function identity. A part cannot be its own validator, checker or
inside-child seam. Running an opted-in operation again repeats no side effect.

## Interfaces and consent

Python uses `from g6._agentic.kernel import run`, then
`run(component, op, params, part=None, mode="prompt", reason="", allow=(), llm=None, name="", budget=None)`.
It requires `agentic.run` and the operation's sensitive allowance. The legacy
`name` is ignored. Explicit Python FakeLLM responders select `own_function`; configuration and MCP cannot select it.

Lead ruling, 2026-11-17: **the trust boundary is the process.** A responder, validator or caller object supplied by
the library caller runs with that caller's own authority in that caller's process. The kernel's original-first
execution, seal and digests, single use, authority record, confined child and prohibition on using substitutes as
evidence hold against the MCP client, model replies and child. They do not defend against Python code the library
caller runs in the same process on its own objects. MCP arguments are data only: `g6_agentic_run` builds the caller
from G6's own provider callers, and a client cannot introduce a responder, validator or caller object.

`prepare` and `override` remain useful for inspecting and staging execution. `prepare` runs the original and either
returns its reply/refusal or issues a `Pending`. `override(pending)` needs no confirmation or name. Optional legacy
`Confirmation` checks only its exact-string digest; its name and answer are ignored. `Pending.question` is retained
as an informational execution summary and never sent as a consent dialog. A failed legacy digest check leaves the
preparation available for a corrected attempt.

MCP exposes `g6_agentic_run(component, op, params_json="{}", part="", mode="prompt", reason="")`. It checks
sensitive allowances, decodes bounded JSON and runs the original. With a trigger it starts a job, polled through
`metaharness_job`. It never calls `request_consent` for the override. Hosts and endpoints pass the existing provider
consent guard. Process callers use the code's allowance. There is no new grant, provider switch or terminal fallback.

## Prepared value boundary

All preparation reads and sealing share the existing Exception-to-input-validation envelope. G6 builds a plain
copy of everything execution uses:

- Budgets require exact int call/character limits and exact int or float seconds, finite and positive. Booleans,
  subclasses and custom numeric conversions refuse. G6 builds its own Budget with float seconds.
- Parameters accept exact dict/list/tuple/str/int/float/bool/None, exact string keys and finite numbers. Tuples,
  including nested tuples, become JSON lists in the canonical round trip.
  Cycles, excessive depth, item count, integer size and more than 1,000,000 serialized characters refuse before the
  original. A canonical JSON round trip removes all aliases to caller containers.
- The caller snapshot has an owned exact class, exact model/program/URL strings, plain parsed route and finite
  positive timeout. The host program comes from its class. The explicit responder is retained by reference exactly
  as supplied. It is never read again from the caller or Pending at override.
- Operation names, functions, validator, checker, child seam, reason, original answer, trigger, binding, source
  digest, request ID and record fields are retained in immutable canonical JSON text. G6-owned code objects are
  retained by identity, including the validator and checker. Rebinding them before execution refuses.

G6 keeps sealed inputs, pins and digest only in its private module table under the weak identity of the issued Pending,
without hashing its caller-reachable fields. The Pending never holds its seal.
A copied or fabricated Pending has no seal. Public metadata cannot select the operation, parameters, model,
limits, recipient, validator, checker or duration. A declared checker always remains declared. The frozen caller
snapshot has its own private copy, retaining strong references to the responder and every retained callable until
Pending collection or eviction, including after consumption. Mutating public constructor metadata refuses before
construction or sending; execution only uses the private copy. Class/kind/route admission repeats independently.

The preparation digest covers the whole plain copy and retained class, responder and code identities. Pins follow
callable code recursively through closure cells, `__wrapped__` (including cache/lru_cache wrappers), bound methods'
and staticmethod/classmethod objects' `__func__`, partials' `func`, and callable instances' type-level `__call__`.
Classes reached through closures include their raw type-level attributes and `__call__`. Type inspection walks
built-in MRO and class-dictionary descriptors with class identity comparisons; it invokes no caller hooks.
Each function pins its code and the identities of its defaults and keyword
defaults; each partial also pins its args and keywords containers by identity. Every identity comparison uses `is`
against the strongly held object, including followed functions, code, binding containers, snapshot class and responder.
Integer identity tokens enter the digest only; they never decide whether a current object is the held one.
Callable cycles are visited once;
graphs exceeding 4,096 callables refuse. These are checked before calls, sends and child launches; a changed code
object or callable binding refuses with the digest-mismatch code. Pins are best-effort checks against accidental
change, not an in-process security boundary. Plain data in closure cells is not pinned: a
nonlocal counter can change normally. Module globals read by a validator and its annotations cannot be pinned in
process. Bound-method `__self__` data, instance data attributes, and in-place changes of containers in closures,
defaults or partial arguments/keywords remain caller-state limits. A confined Linux child does not share the parent's
objects. Builtins, C functions, classes, method descriptors and opaque callable wrappers remain accepted by holding
the object itself and comparing by `is`; a partial over any of them remains accepted.
Specifically, functions held as default values, dictionary values or partial arguments are not followed. These
are review 3 F1's `[default_function]`, `[dict_value]` and `[partial_argument]` limits. Review 3 F4's discovery and
rewriting of a seal through `gc.get_referrers` is also outside the process boundary. Pinning arbitrary caller Python
from within its own process cannot be complete.
Override captures a private snapshot, validates exact field types, rechecks identities and recomputes the digest from
the sealed inputs. Both the recomputed digest and the Pending's public digest must equal the sealed digest. Digest
operands must be exact strings before equality. Unknown callers refuse by class identity before vars or attribute
reads. All class checks use identity, never equality, membership or class-keyed hash tables. Configured anonymous
guards are remembered only from G6's factory by weak identity, with collection cleanup; ancestry enrols nothing.
Public caller metadata is compared by exact type, then value, only for str/int/float/bool/None and tuples of those
checked recursively. G6's exact SplitResult route is normalized to a plain tuple first; any other object refuses
before equality. Each entry's authority list has `max_length` equal to that run's model-call budget cap, including
custom budgets; historical entries may omit the list.

The module table owns retained objects strongly and references the Pending weakly. A responder reaching its own
preparation can create a cycle through that table; this is acceptable only because the 256-entry bound caps it.
Ordinary abandoned preparations have weak-reference cleanup. Override and cancellation mark the table entry consumed
once, retaining its seal but refusing reuse (an invalid legacy Confirmation does not consume it).
Consumed entries count toward the same bound. The oldest entry is evicted at capacity and its Pending then
refuses with the digest-mismatch code. Eviction releases the seal's references, so abandoned cycles can collect.

Names, digests, parameters and the execution copy are prepared before the deadline clock starts. Afterward the
override thread uses only G6-owned plain values. Fresh construction, consent guarding, completion, reply conversion
and error formatting run in its deadline worker. Every reply becomes exact str there: str subclasses are copied
with str.__str__, other objects refuse without formatting. Only plain text and flags cross the join. Formatting
failure produces G6's fixed error words and a failed model-call count. Late replies are unused; abandoned workers
cannot admit later sends.

## Execution and reply

The operation runs in a separate confined child: no network, only its own folder writable. Keys and sign-ins stay
in the parent. An unanswered substituted function asks the parent for a value and its call digest. The parent
meters the model call, bounds and decodes JSON, checks the declared return type and applies the retained validator.
Only then does a value enter the next confined run. Replies replay by call digest. The final result passes the
component's declared checker, unchanged, in a fresh confined run. Without OS confinement nothing starts. Operations
that start children need a declared seam for work already inside the kernel child. Candidates share that boundary.

The result is qualified-draft at best, with warning card `G6_E_AGENTIC_SUBSTITUTE`, no evidence, the model used and
the original answer beside it in `original`. The warning says to check the proposal before relying on it.
`agentic` contains the trigger, part, functions, mode, model, digests, checks, cost and ledger entry. A substitute
never passes a requirement or supplies evidence to another check. Any failure leaves the original standing with
a degradation reason: exhausted budget, model/child failure, invalid value, failed checker or failed ledger.

Every executed override is appended to the local hash-chained ledger, owned by the same user; it does not protect
against that user's programs. Records keep names, codes, trigger, digests, counts and model, without parameter,
result or exception text. Historical `digests.question` now stores the full preparation digest. The compatibility
`person` field records `provider consent` with its provider only for an admitted remote send (grant or same-provider
exemption); `local model` with its provider for a send admitted with `local=True`, taking no consent decision;
`allowed in code` for process callers with no consent-checked send; or `refused by the consent check` when it refused.
Names stay empty and `authenticated=false`; no person approval is claimed. Refusal leaves the outcome's reason
`model_failed` with nothing substituted. The reply and ledger's `authority_sends` list each send or refused attempt and
`summary` gives the observed Authority line, also included in the warning card. For mixed runs, refusal dominates,
then unobserved/code allowance, remote consent, then local admission, so observed consent never covers unchecked work.
Multiple providers at the selected level leave the aggregate provider empty; per-send entries retain each provider.
Endpoint completion enters the existing admission check on its fresh backend and observes successful entry at the
counted send. Host sends observe their own live admission, excluding any enclosing request's admission. A caller
kind alone never supplies authority. Historical entries without a provider stay readable.
The log can direct the self-improvement loop's attention but
cannot count as evidence. Jobs share one worker.

## Caller admission and budgets

Defaults are three model calls, 20,000 output characters and 120 seconds for the whole override, child runs and
final checker included. Calls count before sends, including retries and empty-reply retries. None starts with less
than one second left, or one tenth of a smaller budget. Children get at most 24 seconds and never more than time
left. G6 stops waiting at the deadline and uses no late reply.

| Caller | Admission | Bound |
| --- | --- | --- |
| Exact owned Claude Code / Codex host | Allowed | One session per counted call; time left read at its launch after preparation and consent-lock wait; process tree ended at deadline. |
| Exact owned process / no-send local caller | Allowed, rebuilt | Call/character budget and total wait deadline; no network send. |
| Explicit Python own_function | Python only | Invocations and reply characters counted; G6 stops waiting but cannot stop the function or see where it sends. |
| Exact Ollama / OpenAI-compatible endpoint | Loopback IP literal and local first hop only | Each request counted; per-operation timeout set to time left; rate-limit resend only within one second of first send; waiting stops at deadline. |
| Cloud endpoint or Anthropic SDK | Refused | `G6_E_AGENTIC_CALLER_UNMETERED`: v1 needs a model it can end at the deadline. |
| Unknown caller, subclass, wrapper or multiple backends | Refused | Same code, exact identities only. |
| Missing / NoModel / UnusableModel / _NoKey | Refused | `G6_E_AGENTIC_NO_MODEL`. |

Endpoint hosts are IP literals: 127.0.0.0/8, ::1 and IPv4-mapped 127.x.x.x. Hostnames including localhost, scoped
addresses, userinfo and remote proxies refuse. The first hop is checked again at override. A legacy host instance
created before the owned launch path loaded refuses with instructions to construct a new caller. Known wrappers
only select a backend; hooks, clocks, usage observers, caches, decoders and endpoint keys are dropped.

## Acceptance and retained limits

Tests pin original-first/no-trigger behavior, protected refusals, sealed inputs and mutation resistance, exact
class admission, digest coverage, declared code identity, optional-name-free execution, MCP without an override
question, budgets, worker error formatting/deadlines, degraded fallback, confinement and ledger. The AST census
scans the whole kernel for caller-held comparisons/arithmetic, vars membership and unsafe class checks. The worked
example remains cegis's unsupported float hole: a proposed grid checked by its unchanged verifier.

Earlier limits remain:

- A proxy inserted between the final first-hop check and bound opener construction remains a same-process race.
  Closing it needs a bound transport change. A proxy already present at override refuses.
- G6 cannot see past a loopback relay. A dripping reply belongs to the person's process; endpoint timeouts are per
  operation, not a total network deadline.
- Host sessions may make multiple provider requests while counting as one session. In-process constructors and
  own_function code cannot be interrupted; late values are unused and later admissions refuse.
- Adapter hub loading remains an unreproduced suspicion without optional dependencies; no download is attempted.
- Preparation digests have no nonce; parameters are represented by a digest; candidates share the child; parameter
  text is JSON-encoded twice; float representation can appear in checker detail; jobs share one worker.
- The process boundary and the named review 3 limits above apply to all library caller code, including validators
  and caller objects. The seal and pins do not sandbox that Python. Improved results require independent checks.
