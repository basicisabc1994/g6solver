# Development rounds

**Development, not a validation.** Each round develops the meta-harness on fresh cases from the meta-train workflows and
publishes its scoreboard, whatever it shows. A result is claimed only by a registered study.

Each `round-NN/` holds the round's splits (ids only), one row per arm and case (hashes, no model text), the tuning
records, the scoreboard and a README with the root causes of what it found.

## The programme

The experiments closed after round 3 (owner, 2026-10-20). The rounds are a worked example: they show what G6's
self-engineering can do on one local model and public task sets, and they are not a forecast for anyone's own work. A
registered study (V7) was designed but is not run.

| Round | What it asked | What it found |
|---|---|---|
| 1 | How far does G6 tune itself, with method v0? | Tuning and random edits through the same gate gained alike (99 cases each); every arm's measurement could move by up to 4 cases from the model server's prompt cache. |
| 2 | The same, with method v0.1 and every arm from a freshly loaded model | Self-tuning gained 100 cases over G6 as built (GSM8K 26 to 90, banking77 52 to 81, MASSIVE 67 to 74); the gate refused every change on ARC and MBPP; random edits gained 26; the repeat control matched 500 of 500 outputs. |
| 3 | Both modes together: every prebuilt component on (G), and self-tuning from there (DG), beside self-tuning from G6 as built (D) | They did not stack: DG had no change approved on any of the four datasets, so it equals G everywhere. D took GSM8K 21 to 81; G took MASSIVE 65 to 78 and did worse than the model alone on SciFact (57 against 64), where second looks at every label mask any instruction. MBPP's last 133 fresh cases left 40 to score, too few for the gate. Repeat control 340 of 340. Design: `frontier/round.py` `DESIGNS[3]` (V6b's call budget, so G is V6b's arm G). |

## Method versions

A hand edit to the method makes a new v0, recorded here; self-evolution then starts from it, and hand engineering is
never credited to it (frontier design §4.3). Each round's scoreboard records the `Method` digest and, from round 2, the
digest of the method's code (`method_code_digest`), since a code edit leaves the `Method` data unchanged.

| Version | First used | What changed | Why | By |
|---|---|---|---|---|
| v0 | round 1 | The method of the frontier design §4.1–4.2 (`Method` digest `c82a805a01e3`). | | hand |
| v0.1 | round 2 | A candidate left alone in a race keeps racing to the end of the selection split (`select.py`); a change that ties its parent on the dev split goes to racing (`reflect.py`). `Method` digest unchanged. | Round 1's root causes: each limit cost a real gain (MASSIVE +11, MBPP +6 on dev-eval). | hand |

Round 3 uses v0.1 with no hand edit to the method. DG starts from a workflow confirmed with every component on
(`confirm(initial=...)`, already in the library), so only the round's design changes. Its `Method` digest is round 2's
(`c82a805a01e3`); its `method_code_digest` (`c80f75490826`, the tree at `94dce32b42`) differs from round 2's only in
`g6/metaharness/__init__.py`'s docstring and maturity label, no behaviour.
