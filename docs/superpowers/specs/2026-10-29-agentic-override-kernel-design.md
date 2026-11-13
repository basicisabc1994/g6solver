# Agentic override: a small kernel that lets a model stand in for part of G6's code, at runtime

Status: design, approved in outline by the owner on 2026-10-29. To be built after the first launch.

**Labels:** "the owner" marks a choice the owner made. "Designer's call" marks a call the designer made, which the
owner can overturn.

## 1. Intent

G6's code is fixed, tested and, in places, certified. Some use cases need more than that code can do. In cegis, for
example, a hole can only be an integer in a range, a boolean or one of a set of strings. The owner wants one small
function that, at runtime:
- loads the original code;
- assesses whether this use case needs agentic extension;
- substitutes that part with a prompt or with model-written code;
- runs the result instead.

In the owner's words, "the logic stays the same but it becomes agentic code as needed".

G6's code becomes a template the model may adapt to the use case. The owner sees this as scaffolding the model's
cognition in a lower-agency way: the system's structure stays, and the model fills in or improves one part of it.
It also gives a path to fuller extensibility as models get better.

## 2. Goals, non-goals, anti-goals

**Goals:**
- One small, invokable kernel function, reachable from Python and over MCP.
- Any part of G6's code outside the protected set (section 5) can be substituted for one call.
- The original runs first. Where it is adequate, its answer is returned unchanged.
- Every substituted result says what was substituted, why, by whom, and who confirmed it.

**Non-goals:**
- A general code generator.
- Changing G6's installed code or patching modules for everyone.
- A way around certificates, isolation or approvals.

**Anti-goals (a failure even if it works):**
- A model silently overriding code that works.
- A model's opinion becoming a verdict.
- Model-written code running with G6's own privileges, or inside G6's process.
- A result that looks like G6's tested behaviour when it is not.

## 3. The owner's decisions

1. **The kernel** (the owner): as in section 1. It is small and invokable, and it works at the code level, at
   runtime, as needed.
2. **When the substitute is used** (the owner):
   - the original refuses or fails on this input; or
   - the caller asks for it; or
   - a model judges the original's answer inadequate. **Then the person is asked to confirm the agentic override**,
     shown the model's explanation of why it believes a better result is possible through the extension.
3. **Responsibility** (the owner): the person who confirms makes the final judgement call. G6 and the model only
   propose. The dialog therefore gives the person what an informed decision needs (section 4, step 3), and the record
   keeps it.
4. **Timing:** after the first launch (designer's call; the owner's rule is that nothing new delays launch).

## 4. How one call works

`g6.agentic.run(component, op, params, *, part=None, mode="prompt" | "code", judge=None, ...)`. The names are
provisional. The final ones come from the canonical vocabulary, and the MCP tool is provisionally `g6_agentic_run`.

1. **Run the original.** The component's operation runs as it does today. If it returns and nobody asks for a
   judgement, its answer is returned unchanged: no model call, no child process.
2. **Decide whether extension is needed.**
   - **Refused or failed:** the original raised, or refused with a G6 error code. The refusal is kept.
   - **Asked:** the caller names the part to substitute and the mode.
   - **Judged:** a judgement says the original's answer is inadequate. It says why a better result is possible,
     which part to substitute (`module:function`), and in which mode. The judgement may come from the calling agent
     (caller first, as in verification search) or from G6's own call to the person's configured model, when that is
     allowed.

   G6 checks the named part: it must be a function in G6, inside the operation's own code, and outside the
   protected set (section 5).
3. **Ask the person (only for a judgement).** The question goes through the client's own dialog
   (`g6.mcp.consent.request_consent`), with no terminal fallback and no answer from an agent. G6 writes the question;
   the model's words appear only as a quoted, length-limited explanation. The question holds:
   - what G6's code returned (a summary);
   - the model's explanation of why a better result is possible, marked as the model's claim;
   - what would be substituted, and how (a prompt, or the model's edit of that function);
   - the limits: at most N model calls, run isolated;
   - that the substitute is not covered by that function's tests or by any certificate;
   - that the person makes the final call.

   A decline, a cancel, a client without dialogs or an empty name leaves the original's answer standing. The answer
   applies only to this question's digest.
4. **Substitute and run, isolated.** The operation runs again in a separate, confined child process: no network, and
   only its own folder writable. G6's own process is never changed. In the child, the named function is replaced:
   - **Prompt mode:** each call to the function asks the parent for a value. The parent makes the metered model call
     and checks the value against the function's declared return type, plus any validator the code already has
     (`Hole.accepts`, for example). Only then does the value go back to the child. Keys and sign-ins stay in the
     parent.
   - **Code mode:** the model edits the function's source, using G6's code as the template, keeping the signature.
     The edit is compiled and runs only in the child. The parent never imports it. Its return values pass the same
     checks.
5. **Return both answers.** The reply holds:
   - the substitute's result;
   - the original's answer or refusal, kept beside it;
   - the trigger (refused, asked, or judged and confirmed);
   - the part, the mode, the model, and the digests of the original source, the prompt and the edit;
   - the person's answer and the name they gave (recorded, not authenticated);
   - the checks that ran, and the cost.

   The completion state is `qualified-draft` at best, and never `verified`. A warning card says the result came from
   a model's substitute for the named part.
6. **Record it.** Each override is appended to a local ledger under G6's state folder. The ledger is owned by the
   same user, and the documentation says so. A part that keeps needing overrides becomes evidence for the
   self-improvement loop's constraint log. The loop may then propose a real change to G6's code, through the existing
   gate and the owner's merge. Nothing becomes permanent through this kernel.

## 5. Binding rules

1. **Original first.** With no trigger, every field of every reply is unchanged, and a test checks it.
2. **A model's judgement is never authority.** It can only lead to a question. Only a confirmation leads to a judged
   override, and that confirmation comes from the person in the client's dialog.
3. **Model-written code never runs in G6's process.** It runs only confined. Without isolation, code mode is refused
   and the original's answer stands.
4. **G6's process is never modified.** There is no patching of loaded modules. A call running at the same time gets
   the original code.
5. **The protected set is refused.** That covers:
   - every certificate-bound file;
   - the trust doors: isolation (`confine`), keys, consent, receipts and ledgers, `action_gating`;
   - patch review's decided fields, integrity and authority, and verification search's authority rules;
   - the meta-harness gate, evaluator and promotion;
   - this kernel itself.

   An override can never change a review's outcome, an approval or a gate.
6. **Model output is untrusted data.** It is validated by type and by the code's own validators, or it runs confined.
   An instruction planted in G6's inputs can make a model ask for an override. It cannot grant one.
7. **Counted and bounded.**
   - Model calls, characters and seconds have limits per call (designer's call: a small default, such as 3 calls).
   - Calls are metered through `MeteredCaller`, using the person's configured model or sign-in.
   - The kernel is off unless allowed, as a sensitive operation.
   - Tests use `FakeLLM`.
8. **Labelled, never verified.** A substituted result is never presented as G6's tested behaviour. It never passes a
   requirement or becomes evidence in another G6 check.

## 6. What it reuses

- `solver/agentic_runtime.py`'s pattern: a model decision with a deterministic fallback, and who decided, recorded.
  That module stays as it is.
- `g6.mcp.consent.request_consent` and its refusals, for the dialog.
- `g6.core.confine` and the confined-run machinery (used unchanged; it is certificate-bound), for the child.
- `g6.core.llm` (`provider_from_env`, `complete_structured`, `FakeLLM`) and `MeteredCaller`, for model calls.
- The component registry and `door_reply`, so an override is invoked like any operation and gets the same envelope.
- The `ChainLedger` pattern of the receipt store, for the record.

## 7. The owner's example: cegis holes

A sketch whose solution needs a value outside a hole's range (say 100, with the default range of -32 to 32) makes
cegis refuse with no candidate.
- **Prompt mode:** the part is `g6.cegis.hole:Hole.all_values`, the hole's candidate values. The model proposes them.
  Each must have the hole's kind (an integer here); the range is what this call relaxes, inside the child only. cegis's
  own verifier still decides whether a candidate meets the specification.
- **Code mode:** the model edits `extract_holes` to add the hole kind the use case needs, inside the child.

Either way the counterexample-guided search and the verifier are unchanged. Only the space of candidates became
agentic. This is the first worked example, labelled a mechanism demonstration.

## 8. Tests that pin the rules (development fixtures only)

- **No trigger:** the original's reply is unchanged, with no model call and no child process.
- **Refusal, prompt mode:** the value is validated, and the result is labelled with the original's refusal beside
  it. An invalid value is refused, and the original's refusal stands, marked degraded.
- **Judgement:**
  - a decline, cancel, unavailable dialog or empty name gives no override;
  - an accept runs the override, and its record holds the question's digest and the name as given;
  - an answer for a different question's digest does not apply.
- **Code mode:**
  - a model edit that sets state or writes a file shows G6's process unchanged;
  - the child has no network, cannot write outside its folder and cannot read keys or sign-ins;
  - with no isolation, code mode is refused.
- **Protected set:** each kind is refused, including this kernel and patch review's decided fields.
- **Concurrency:** a normal call running at the same time returns the original's answer.
- **Limits:** budgets are enforced and counted.
- **cegis:** the section 7 example, end to end, with `FakeLLM`.

## 9. Claims

- No claim that overrides give better results until a registered study measures it. Example measure: on tasks where
  G6's code refuses or a model judges it inadequate, the accepted overrides' correctness by an independent check.
- Never "verified", and never "experimental". The public status line stays as it is.

## 10. Calls after the builder check (2026-10-29; these override sections 4 to 8 where they differ)

A fresh builder read this design and the code it reuses, and asked eight questions. Each answer below is the
designer's call, made under the owner's decisions in section 3, unless it is marked otherwise.

1. **Who confirms over MCP.** Over MCP the caller is itself a model, so an agent's "ask", or its choice of part after
   a refusal, is a model's judgement. **Every override started over MCP therefore asks the person** in the client's
   dialog, whatever its trigger. This is the designer's reading of the owner's decisions 2 and 3. A standing
   sensitive-operation allowance only lets the tool be called; it is never consent to an override. From Python, the
   person's own code may name the part and mode with an explicit `allow`, as `g6.invoke` does. The kernel's
   allowance and the operation's own sensitive allowance are both required.
2. **Isolation when the operation starts its own confined child** (cegis does).
   - The kernel's confined child is the boundary for everything the operation runs inside it.
   - An operation that starts its own confined children needs a declared seam that runs that work in-process when it
     is already inside the kernel's child. For cegis this is a runner choice in `cegis/runner.py`. `confine.py`
     (certificate-bound) is not changed.
   - Prompt mode runs in a confined child too.
3. **The final answer is re-checked.**
   - In code mode the model's code shares the child with the operation's own checks, so it could forge the child's
     reply. The parent therefore re-checks the final answer with unchanged code, in a fresh confined run, through a
     checker the component declares. For cegis that checker is its own verifier, run on the final program against the
     specification.
   - A component that declares no checker can still be overridden. Its result says "not re-checked", and it is still
     `qualified-draft` at best.
4. **The protected set** is section 5's list, plus `g6.core.adaptation_gate.PROTECTED_FRAGMENTS` (oracles, graders,
   judges, verifiers, gates, ledgers, tests), plus each component's own declared verifiers. For cegis those are
   `verify.py`, `z3_verifier.py` and `loop._verifier_says`.
5. **What counts as "refused or failed".**
   - An exception inside the component's own computation counts. So does a reply carrying one of the codes the
     component declares as a limit of what it can do, such as cegis finding no candidate in its space.
   - A code from a trust door never counts: isolation unavailable, sensitive operation denied, input validation,
     approval required, budget exhausted and the like. An override can never route around a door.
6. **First version's scope: pure computation.**
   - It covers operations that compute from their parameters alone, with no model calls, no network, no reads of the
     person's files and no writes to G6's state.
   - Every other operation is refused with a stable code. Passing such an operation's model calls and file reads
     through the parent comes in a later version.
   - Running a pure operation twice repeats no side effect.
7. **The unit of substitution** is a named set of functions inside one component, listed up front and shown in the
   dialog. A single function is the commonest case.
8. **The owner's example, re-targeted.**
   - cegis can already widen an integer hole's range with no override (`Sketch.from_src(src, lo, hi)`), so the
     demonstration is a hole kind cegis does not support. Example: a floating-point hole `??f` with a candidate grid.
   - Code mode edits the cooperating set: `HoleKind`, `extract_holes`, `Hole.accepts` and `_child._values`.
   - cegis gets a small block, so the example runs over MCP as well as from Python. The block's oracle is data, a table
     of inputs and outputs, because a callable cannot cross into a child or come over MCP. That is a new, additive
     operation.
   - The parent's re-check with cegis's unchanged verifier still decides whether the final program meets the
     specification.
9. **One MCP tool.**
   - `g6_agentic_run` (provisional) runs the original first. When there is a trigger, it opens the dialog inside the
     same call. The answer is bound to that question's digest, so it cannot be replayed.
   - The override then runs as a job, polled through `metaharness_job` like patch review.
   - No pending proposals are stored.
10. **The self-improvement loop's log** does not exist yet.
    - The first version stops at the ledger. The loop reads it once phase 2 builds the log.
    - Rule 8's meaning: an override's record may direct where the loop looks next, but it never counts as evidence that
      a change works, and it never passes a check.
11. **Small calls the builder proposed, accepted:**
    - prompt mode also runs behind the operating-system boundary;
    - an exhausted budget keeps the original's answer;
    - "the operation's own code" means the component's own package.

## 11. Left to the builder

- The final names, from the canonical vocabulary.
- How the child and parent exchange values, and the child's start-up.
- Building the question's summary of G6's answer.
- The exact default limits.
- The skill text: when an agent should ask for a judgement, and how to show both answers.
- A fresh security review before merge: this runs model-written code, so it is security-sensitive.
