# Why the reviews of G6's own code did not converge

2026-11-03. Counts only: the reports themselves stay private, since they can quote local paths.

## What was measured

From 1 to 3 November, 15 branches of G6's own code went through the same loop. A Codex review must write a failing
test for each finding. A repair follows, then a fresh review, up to four reviews. Of the 15 loops, one ended with a
clean review (`claude/loop-learn`, at its third). None of the other 14 did.

They found 222 defects, each reproduced by its test. `ledger.py` reads every report into one row a finding, by plain
code. A model then labelled each row's design error from a closed list (`CLASSES`), or named it as "other". It also
labelled the row's effect: changes an outcome without a word, fails with an error, or needs an unusual or adversarial
input. The model counted nothing. `git blame` places each cited line against the commit its review read.

| Class | Findings | Loops | Change an outcome silently | In a class the loop had met in an earlier round | In the last repair's lines |
|---|---|---|---|---|---|
| A guard covers less than it guards | 45 | 13 | 18 | 25 | 7 |
| One fact decided by two rules | 38 | 11 | 29 | 21 | 3 |
| Check, then act on a state that changed | 28 | 8 | 4 | 19 | 2 |
| A failure read as a success | 24 | 9 | 16 | 13 | 4 |
| State that must survive a restart held in memory | 22 | 8 | 10 | 13 | 7 |
| An identity taken from a rendering | 20 | 8 | 8 | 11 | 8 |
| A boundary case | 16 | 9 | 12 | 6 | 2 |
| A home-made parser for another tool's format | 6 | 4 | 1 | 1 | 2 |
| Untrusted input given authority | 4 | 2 | 0 | 2 | 0 |
| Other (13 kinds, each named) | 19 | | 14 | 5 | 5 |

**By round.** The four rounds found 66, 58, 53 and 44 defects, and one older loop's fifth review found 1. The share
that changes an outcome without a word was 55%, 48%, 51% and 48%. Late findings were not trivia.

**Where the later findings sit.** 156 findings came after a loop's first review, and the lines of 153 resolved. Of
those 153:
- 87 were in code the loop's first review had already read;
- 26 were in lines an earlier repair wrote;
- 40 were in lines the last repair wrote.

**Did a repaired class stop coming back?** 116 later findings were in a class the same loop had met in an earlier
round. To see what chance gives, each loop's labels were shuffled across its own findings, keeping the rounds. Over
5,000 shuffles the mean was 110, and 95% of them fell between 101 and 118 (`by_chance`). A repair that removed its
class would push the count below that band. The observed count sits inside it.

**The cap.** The brief asked each review for "a few strong findings, at most six". 19 of the 60 reviews with findings
reported five or six.

## The root causes

1. **A review is a sample, not a census.** More than half of the later findings were in code the first review had read
   and not reported. Four reviews of at most six report at most 24 findings, and none of those 14 branches was clean
   after the reviews it got.
2. **Repairs added defects.** 66 of the 153 later findings were in lines a repair wrote. One loop (`claude/k-followup`)
   is the extreme: 8 of its 9 later findings were in its last repair's lines, because each repair of its design exposed
   the next case.
3. **Repairs patched instances.** Classes came back at the rate chance gives.

## What changes, and what each change predicts

Each prediction is scored on the next loops, which none of these counts came from.

| Change | Prediction | Now |
|---|---|---|
| The first review walks every class in `CHECKS` and reports every instance, one test each, with no cap; later reviews keep the cap | Under 30% of later findings in code the first review read | 57% |
| A finding in a class the loop or this ledger met before is repaired by removing the class: one shared definition, the producer's own tool for its format, or the product's own function in place of a copy of its rule. The commit names the class it removes | The observed recurrence falls below the shuffle mean, with at most 20% of shuffles at or below it | 116 against 110 |
| Before the next review, each repair's own lines are checked against `CHECKS` | Under 25% of later findings in lines a repair wrote | 43% |
| The three together | At least half of the next loops end with a clean review within four | 1 of 15 |

**If they fail.** If two loops under these changes move none of the four measures, sampling and patching are wrong or
not enough. The next hypothesis is size: each change is split so that one review covers less code.

**Each class's check** is `CHECKS` in `ledger.py`, the one place it is written. It is a lesson for code never seen,
not for these findings, and it is kept as data. The loop can then add a check, measure it by whether its class stops
coming back, and drop it if not.

## What this does not show

- The labels are a model's reading. A second labeller, blind to the first, chose the same class for 75% of the findings
  (Cohen's kappa 0.71). The two agreed on whether a finding changes an outcome without a word for 90% (kappa 0.79).
  Under the second labels recurrence is still at chance: 114 against a shuffle mean of 111, with 95% of shuffles between
  102 and 119. The order of the two largest classes depends on the labeller. The second put "one fact decided by two
  rules" first, with 52 findings against the first labeller's 38, mostly from findings the first had called "other".
  The blame split does not use the labels at all.
- A finding about a missing check cites the code around the gap, so blame places the gap, not a line that is wrong.
- "Code the first review read" includes code older than the branch. That share is not split by who wrote it.
- The 15 loops are one reviewer (Codex) on one author's code, so the classes may be the author's habits as much as
  general ones.
