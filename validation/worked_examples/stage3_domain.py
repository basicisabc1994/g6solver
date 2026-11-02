"""Stage 3's domain file, as ``g6.metaharness.runner`` names one (PLAN.md, 2026-11-02 evening): a Codex reviewer reads recorded patches of the bugs stage 1
scored, and the loop tries to make it read them better. A person enrols it. Nothing here is a change to the reviewer: the prompt is K1's own reading steps,
and the verdict line sits in each case, so what a proposer edits is how the reviewer reads and nothing about the form of its answer."""
import os
from pathlib import Path

from g6.core.host_models import CodexCaller
from g6.metaharness.coding_replay import CodingReplay
from g6.metaharness.method import Method

ARCHIVE = Path(os.environ.get("G6_WE_ARCHIVE", "").strip() or Path.home() / "Desktop" / "g6-continuation-artifacts" / "worked-examples")
CASES = ARCHIVE / "round3" / "reviewer_domain"
SEED = 20261106         # the first from 20261102 whose cut gives every side a spread of bugs and both labels; PLAN.md gives the rule
TIMEOUT_S = 600
K1_READING = """You review a candidate fix written by someone else. It may be complete or not. You are not told which. Judge it on its merits.

1. State the general rule the issue implies, in one or two sentences. Derive it from the issue and the repository's own code, not from the patch.
2. Read the patch against that rule. Look for where it handles the issue's example but not the rule: a hard-coded name or value that should be derived, a branch of the same code path left out, another scope or nesting level, a sibling construct, an input position, or behaviour that used to work and now does not."""
# One block of the whole selection split: cases come in bug order, so a shorter first block is one bug and one label, every candidate ties, and the race
# drops half of them on nothing (the rehearsal of 2026-11-02 lost a perfect candidate that way).
METHOD = Method(cycles=2, candidates_per_cycle=3, propose_budget=300, race_block=50, select_budget=400)


def domain(caller) -> CodingReplay:
    return CodingReplay(CASES / "cases.jsonl", CASES / "sealed" / "cases.jsonl", caller, prompt=K1_READING)


LOOP = {"domain": domain(CodexCaller(timeout=TIMEOUT_S)), "proposer": CodexCaller(timeout=TIMEOUT_S), "provider": "codex", "method": METHOD, "seed": SEED}
