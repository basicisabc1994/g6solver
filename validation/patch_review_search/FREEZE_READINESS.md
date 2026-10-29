# V9 and V10: what the freeze needs

What the feasibility freeze entry needs before V9's patches and V10's arms run, each item with its status and the
evidence for it. A status is one of: **ready**, **not ready**, **for the owner** (the owner decides), or **not built,
as ruled**. Written 2026-10-29, after the paid rehearsal on the development fixtures
(`rehearsal_record/rehearsal-record.json`) and the coordinator's rulings of that day.

| # | Item | Status |
|---|---|---|
| 1 | G6's commit: the launch fix pass's final tip | not ready |
| 2 | The model: `claude-haiku-4-5` at Claude Code's default effort | ready |
| 3 | Per-session time and token caps, from the rehearsal's totals by kind | for the owner |
| 4 | V9's spending cap: $60 for the patch sessions and arm 2 | ready |
| 5 | V10's spending cap and each arm's share | for the owner |
| 6 | Ruling 48: the history check on the copies arms C, T and E use | ready |
| 7 | Ruling 66: OpenAI's published list of broken tasks | not ready |
| 8 | The six secondary measures that were not built | ready |
| 9 | The label-only step inside the vault | not built, as ruled |

## 1. G6's commit

**Not ready.** The freeze names the launch fix pass's final tip on `claude/launch`, which has not been reached: the
fix pass is still landing commits, and the proposer's `state_invariant.v1` prompt change is not on the branch yet (no
commit to `src/g6/harness/verification_proposer.py` after a24b6f2 names it; the two since, 1b32138 and 0c2a4f9, are the
study caller's install command and reviewer B's B4, which meters and caps the provider rather than G6's retry around
it). Evidence so far: `freeze_check.py` passed against the exports of the launch tips the rehearsal used (f9d769a7ab
and f3411d10ce) and of the latest, 0c2a4f98e4: ready, every parity fixture passing.
At the freeze it is run again against the final tip, and arms C and T bind to verification search at that commit
(ruling 67).

## 2. The model

**Ready.** `claude-haiku-4-5` for the patch session, arm 2 (the same session, resumed) and arm R (a fresh session),
at Claude Code's default effort, with nothing set (ruling 73). Arms C, T and E call the same model through the hosted
interface (`verification_proposer.StudyCaller`). Evidence: `launcher.MODEL` and `launcher.EFFORT`; the rehearsal's runs
3 to 5 ran on it, each session record naming the model, the effort, Claude Code 2.1.309 and an empty configuration
folder. At the default effort the model thinks: in run 5's codec patch session, 3,440 of the 4,720 output tokens were
thinking.

## 3. Per-session time and token caps

**For the owner**, proposed here from the rehearsal's measured totals by kind (item 76; the study's model only, 17
sessions, priced from each session's result record):

| Session | Tokens by kind, largest seen (input / output / cache read / cache write) | All kinds, largest | Seconds, largest | Calls, largest |
|---|---|---|---|---|
| Patch session (6) | 98 / 4,720 / 260,511 / 26,746 | 289,124 | 45 | 12 |
| Arm 2 (6) | 41 / 2,438 / 135,929 / 3,444 | 141,852 | 26 | 5 |
| Arm R (5) | 81 / 8,703 / 229,977 / 32,108 | 266,727 | 85 | 10 |

Cache reads are 86% to 96% of each kind's tokens. Each turn reads the session's context again, so a session's
tokens grow with its turns and with the repository's size. The fixtures are two small modules, so a real task's
sessions will be several times larger. The proposal:

- **Patch session:** 1,200 seconds, 40 turns and $1.50 (the launcher's caps, `launcher.CAPS`).
- **Arm 2:** 1,800 seconds (V9's limit in the rehearsal's freeze file), and a token cap of **2,000,000 tokens of all
  kinds, cache reads included**. That is
  about 14 times the largest arm 2 session seen, which is room for 20 turns of a context of about 100,000 tokens. A
  session past it abstains (V9's arm 2 rule). Plus the launcher's 20 turns and $0.75.
- **Arm R:** V9's arm 2 limits, as the protocol says: the same 1,800 seconds and 2,000,000 tokens, and arm 2's 20
  turns (ruled 2026-10-29: version 2 registers R "with the same model and budget" as V9's arm 2). Plus the launcher's
  $1.00, against arm 2's $0.75.

## 4. V9's spending cap

**Ready.** $60 for V9's patch sessions and arm 2, the owner's choice of 2026-10-28 (ruling 73). The rehearsal
projects $2.00 to $4.58 for V9's counts at fixture size (30 to 45 patch sessions and arm 2s), against a bound of
$146.25 if every session reached its cap with its margin. So the $60 cap binds only if a patch session and its arm 2
average more than $1.33 together over 45 tasks.

## 5. V10's spending cap and its shares

**For the owner.** The protocol's default is a quarter of the cap each for arms C, T, E and R (ruling 53), and the
freeze entry may set other shares.

**The rehearsal's figure, a proposal and not a decision: a cap of about $80, with about 58% for R and 14% each for C,
T and E.**

- **Arms C, T and E:** a proposer review's realistic worst case is 10 calls of a full input (60,000 characters, about
  15,000 tokens) and 4,096 output tokens, about $0.36. That is about $10.65 for each arm over 30 patches.
- **Arm R:** a session's cap plus its margin, $1.50, is $45 over 30 patches.
- **Together:** these come to about $77.

At fixture size the rehearsal projects $3.98 to $6.41 for all four arms. Equal quarters of $80 would let R's quarter
run out if R averages more than $0.67 a patch, while at fixture size C would use under 1% of its quarter.

## 6. Ruling 48: the history check on C, T and E's copies

**Ready.** Ruling 48 says the history check that arm R's copy passes (`arm_r.history_problem`) must hold for the
copies arms C, T and E use before the study's first run, and that a copy that fails it is refused, never run.

Built, test first: C, T and E's base and patched trees are now made as R's copy is (`arm_r.base_only`: a new
repository, the base commit alone fetched with `--depth=1`, checked out detached, the patch applied and not
committed). `study.build_trees` checks every tree with `history_problem` before any arm runs; a tree that fails it is
refused and the study never runs. The test gives the source a later commit: a full clone fails the check, and the new
trees pass it with the base commit as their only commit object. V9's own trees, V10's calibration and the label
step's reference-fix tree keep `trees.checkout`.

## 7. Ruling 66: the published list of broken tasks

**Not ready: the source could not be read.** V9's protocol, version 2, cites OpenAI's audit of SWE-bench Pro's public
tasks: "Separating signal from noise in coding evaluations", 8 July 2026, at
`https://openai.com/index/separating-signal-from-noise-coding-evaluations`. It estimates that about 30% of the tasks
are broken.

On 2026-10-29 the page was fetched read-only, with and without its final slash, and both times it answered HTTP 403
Forbidden. So whether OpenAI publishes the broken tasks' IDs there is **not known**, and `found` is not recorded as
false, since it is never guessed. A person reads the page in a browser at the freeze and records `found` true (with
the list's source, file and SHA-256) or false, with the URL. The protocol also asks after the benchmark's maintainers'
pages, which were not looked at here.

## 8. The six secondary measures

**Ready.** Each is built, test first, on fakes (interpretations 80 and 81). They are
in `secondary.py`, `analysis.py`, `driver.py`, `study.py` and `metering.py`:

- **Template applicability (arm T):** recorded by the driver before the record's hash, so before any label is seen.
- **The formal path's counts (arm T):** recorded the same way.
- **Whether surviving mutants predict defects:** test adequacy against the label, with Newcombe's interval for two
  independent proportions, checked against his published example.
- **Similarity to the hidden tests:** measured in the label step, inside the vault, after the arms' record is hashed.
  The headline counts are also given without the findings above the threshold.
- **The per-reason-code shares:** each share has the bootstrap's interval over tasks with all their findings.
- **Input and output tokens:** each arm's meter counts them by kind.

The freeze file must name the similarity measure, its run length and its threshold (`v10.similarity`): ruled
2026-10-29, `token_containment.v1` with runs of 8 tokens and a threshold of 0.5. Similarity covers every proposed
check C and T ran, whose texts the search arms now keep, and "refused for exceeding the limits" is read from G6's
codes where its record has them and from its pinned words where it does not. `freeze_check` checks both at the
frozen commit, and both pass at 0c2a4f98e4.

## 9. The label-only step inside the vault

**Not built, as ruled: feasibility runs unaudited (owner 2026-10-28).** V9's ruling on item 61. The feasibility
results say that the labels were not checked for broken tasks, with OpenAI's estimate. The step is built before any
audited level, when the owner names an auditor.
