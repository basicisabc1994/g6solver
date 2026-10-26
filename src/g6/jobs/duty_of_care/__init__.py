# SPDX-License-Identifier: Apache-2.0
"""Matches a fixed list of English crisis phrases; a paraphrase or another language passes. A match goes to a person.

An extension in the jobs family: the door to the family's shared duty-of-care code (``g6.jobs._care``), which every
profession that works with people in distress uses. It guards against an AI missing signs that a vulnerable person is
at risk of suicide, self-harm, harming someone else, or a child being in danger.

    from g6.jobs._care import scan, score
    scan({"message": "...", "notes": "..."}).fired      # every field scanned, the model's own output too
    score("phq9", [0, 1, 0, 2, 1, 0, 0, 1, 0]).total     # 5: "score computed", nothing more

What it does:

* ``screen``: every key and value the caller gives, on every op and before the input is even validated, is scanned
  for listed crisis words (suicidal ideation, self-harm, harm to others, a child in immediate danger, someone being
  harmed) as whole words, after the text is normalised. A finding is ``blocked-escalated`` with
  ``G6_E_DUTY_OF_CARE_CRISIS``, a response written to safe-messaging practice and the dated directory, and nothing
  else. It also locks that subject for the run: every later op naming that run_id and subject returns the same result.
  Both are the caller's words, so a call naming another run or subject is not locked;
* ``score``: the PHQ-9 and the GAD-7, as their forms print them, added up as the forms add them ("score computed"; no
  band, risk level or diagnosis). Any PHQ-9 item 9 answer other than 0 goes to a person, whatever the total. A missing
  or out-of-range answer is refused, never defaulted;
* ``instruments``: each instrument with its licence state. PCL-5, AUDIT, DASS-21, K10, MUST and the Barthel Index are
  refused until their licence entries are settled; the BDI-II and the C-SSRS are not shipped;
* ``directory``: the dated crisis and helpline numbers, each with its source, retrieval date and age, emergency
  numbers first; past 180 days it says plainly that the numbers may have changed.

Every op tops out at ``qualified-draft``. The trigger catches stated words, not every person in crisis: a miss is
possible, so finding no listed words is never evidence that someone is all right. Listed as in the library once its
G6 certificate is current (DECISIONS #39); no qualified person has reviewed it.

Not ported from the archive's ``duty_of_care``, and why: the clinician id that lifted the crisis lock (DUTY_OF_CARE-01;
this lock has no release at all); the risk state machine that read flags the calling agent set instead of what the person said
(DUTY_OF_CARE-02); the undated crisis numbers labelled verified (DUTY_OF_CARE-03); the therapy exercise cards and the
keyword "distortion" detector (DUTY_OF_CARE-04, -05); scores labelled verified and severity bands (-06); the
SQLite store of sessions and crisis events under the home folder (-07: nothing is stored); the consent sessions, the
MCP server, the skills, agents, hooks and scaffold.
"""
from g6.jobs.duty_of_care.block import DutyOfCareBlock, DutyOfCareInput

__g6_block__ = "DutyOfCareBlock"
__g6_sensitive_ops__: tuple[str, ...] = ()  # nothing reaches the network, writes a file or runs a program
__g6_requires__: tuple[str, ...] = ("guards",)  # _care.crisis folds text with g6.guards' own folding
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("missed_risk_signs",)
__g6_introduces__: tuple[str, ...] = ("no_finding_read_as_safe",)
# Vetting (DECISIONS #39): listed once its G6 certificate is current; no qualified person has reviewed it.
__g6_vetting__ = {
    "kinds": ("clinical", "mental_health"),
    "register": "g6/jobs/_care/data/source_register.json",
    "independent_pass": "g6/jobs/_care/data/rule_review.json",  # the model reader's record, not a person's review
    "signed_review": None,
}

__all__ = ["DutyOfCareBlock", "DutyOfCareInput"]
