---
hide: [navigation, toc]
title: G6 — build agents from parts you can check
---

<div class="hero" markdown>

# Build agents from parts you can check.

**G6 is free and fully open source** (Apache-2.0). It checks an agent's work, gates its risky actions, and labels
every result for exactly what was shown. It runs on your computer, with a local model or your own key, and needs no
account.

[Get started](install.md){ .md-button .md-button--primary } [See the bundles](bundles/index.md){ .md-button } [View the code](https://github.com/basicisabc1994/g6solver){ .md-button }

</div>

Our north star is AI for AI: agents construct applications, propose redesigns and test hypotheses. Application benefits remain to be demonstrated. An AI agent's "done" is a claim, not a fact. G6 turns it into a result you can check. You say what a correct result
looks like. G6 freezes those checks before the work starts, runs them on the exact output, and labels the result with
what the evidence supports, and nothing more. Every gap is named, with the next step. Before an agent acts, G6's
action gate answers allow, ask a person, or block. Each piece is a small component you can use on its own or
combine, and bundles package them for one problem each.

## Why you can trust the answer

<div class="grid cards" markdown>

-   **Honest labels**

    Every result is `verified`, `qualified-draft` or `blocked-escalated`. `verified` needs independent evidence, so
    a check you wrote yourself gives `qualified-draft` at best. A label never rises when results are combined.

-   **Evidence you can inspect**

    Results carry their evidence. A harness run records which checks ran, on which exact output, with what
    outcome. The research page publishes every study's verdict, misses included.

-   **Local-first**

    Use a free local model through Ollama, or your own Anthropic, OpenAI or OpenRouter key. No telemetry, no hosted
    service, no account.

-   **Open and composable**

    36 components in the core harness, each usable alone, with their dependencies declared, and extensions in
    families of their own. Over MCP, the CLI or Python. Read every line.

</div>

## What it does for you

<div class="grid cards" markdown>

-   **Checks before it claims**

    Claims are checked against your sources. It is a word-level check, so it misses many paraphrases, and says so.
    How it did on a public benchmark is on the [Research](research.md) page, as a worked example.

-   **Asks before it acts**

    Each action an agent proposes gets allow, ask a person, or block. Unknown facts never count as low risk. Its study
    on a public benchmark, its miss included, is on the [Research](research.md) page.

-   **Checks a coding agent's "fixed it"**

    G6 runs your tests itself, checks that none was weakened or skipped, and holds the agent's report to what it saw.

-   **Remembers what failed**

    A check that failed once is remembered for that kind of task. A later standard that drops it is refused until
    the check is back or a person waives it.

</div>

## Ready-made bundles

One real problem each. Use your own free local model or your own AI key; seven need no model at all.
Downloads come with the first release. Until then, developers run them with `g6 bundle <id>`.
[See all bundles →](bundles/index.md)

<div class="grid cards" markdown>

-   **Did my coding agent really fix it?** G6 runs your tests itself, checks none was weakened or skipped, and holds
    the agent's report to what it saw. Offline.
-   **Check claims against your sources**: which sentences of a draft your documents back. Offline.
-   **Safe agent actions**: allow, ask a person, or block, before an agent acts. Offline.
-   **Contract and document review**: usual clauses quoted, undefined terms, broken cross-references. Offline.
-   **Bookkeeping and GST check**: ledger against bank, each line's GST, BAS G1/1A/1B recomputed. Offline.
-   **Compliance gap check**: GDPR and HIPAA answers triaged, incident deadlines checked. Offline.
-   **ML result check**: train/test leakage, and the score recomputed from the labels. Offline.
-   **Grounded answer**: ask your documents a question; see which sentences of the answer they back.
-   **Code review with receipts**: every finding must point at a real line of your code.
-   **Plan pre-mortem**: why the plan could fail, the early warning signs, and a cheap test for each.
-   **Runbook safety check**: irreversible steps with no backup, missing rollback, missing final check.

</div>

## How it works

**Standard → intake → plan → execute → adjudicate → remember.**
The checks are frozen before the work begins, so success cannot be redefined afterwards.
[Read the developer guide →](developers.md)

## Tune it to your own work

**G6 is the open-source harness you can tune to your own work: it proposes changes to how it runs, tests them on
held-out tasks you trust, and changes nothing until you approve.** It cannot change the checks that judge it, and it
can always go back to a version you approved. When a change fails, it tells you why and what to try next. The
studies run with it are on the [Research](research.md#the-meta-harness) page, as a worked example.
[See how it works →](metaharness.md)

## Get started in three steps

```
git clone https://github.com/basicisabc1994/g6solver && cd g6solver
python -m venv --copies .venv                           # G6's own environment, with a copy of Python
source .venv/bin/activate                               # PowerShell: .venv\Scripts\Activate.ps1
pip install ".[all]" && g6 doctor
g6 setup --client claude --allow patch_review.review    # or --client codex
```

G6 gets an environment of its own, made with `--copies`: on Linux, G6 cannot yet tell its own Python from a project
environment linked to the same one, and refuses to review then.

Not a programmer? [Start here instead](install.md).
