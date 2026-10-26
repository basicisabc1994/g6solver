# Bundles

Most people who could use AI to check their work cannot build the software that does the checking. A bundle is that
software, ready-made for one real-world problem: G6's components packaged into an app.
{: .why }

Download a bundle, double-click it, and it opens a private page in your web browser. Nothing is sent anywhere except to the AI model you choose: a free model
that runs on your own computer, or your own Anthropic, OpenAI or OpenRouter key.

!!! info "Status"
    Ten bundles work today; six of them fully offline. The compliance gap check is built and awaits its
    certificate, because its legal rules were written without their sources registered. The downloads appear on the
    [releases page](https://github.com/basicisabc1994/g6solver/releases) with the first release, once the automatic
    build has passed its smoke test on Windows, macOS and Linux. Until then, developers can run them from a copy of the code
    (`pip install .`, then for example `g6 bundle check-claims`).
    A new bundle is listed only once it has an offline end-to-end test.

Every result carries a label: **qualified-draft** (usable, read the notes) or **blocked-escalated**
(not safe to rely on; the page says why). The bundles never label their own output **verified**.
How well each bundle does on real tasks has not been measured yet.

| Bundle | What it does for you | Status |
|---|---|---|
| **Did my coding agent really fix it?** | Name the git repository your coding agent (Claude Code, Codex, any other) changed, what you asked, and what it said it did. G6 runs the tests itself, reruns the original versions of any test the agent edited, checks no test was weakened or skipped and no shortcut is in the diff, makes small deliberate mistakes in the changed lines to see whether the tests notice, then holds the agent's report to what it saw. Works offline; runs your tests on your computer. | works — first release pending |
| **Code review with receipts** | An AI security and bug review where every finding must quote a real line of your code; G6 locates it, and findings it cannot find in your code are marked unconfirmed. Needs a model. | works — first release pending |
| **Check claims against your sources** | Paste a draft (an AI answer, a report) and your source documents; every sentence is marked as backed, contradicted (wrong figure, flipped "not") or not found. Works offline. | works — first release pending |
| **Grounded answer from your sources** | Ask a question about your documents; an AI model answers from them, and G6 marks which of its sentences the documents actually back. Needs a model: free local (Ollama) or your own key. | works — first release pending |
| **ML result check** | Paste training and test data and the model's predictions (or a script that makes them, run with the test labels held back); G6 looks for train/test leakage, recomputes the score itself with an interval and a simple baseline, and compares it with the score you were told. Works offline. | works — first release pending |
| **Contract and document review** | Paste a contract: G6 finds the usual clauses and quotes them, lists terms used but never defined, references to clauses that are not there, and words that disagree with their figures ("thirty (60) days"), and points out vague wording to read closely. An optional AI model's issues are kept only when their quote is really in your document. Works offline. Not legal advice. | works — first release pending |
| **Bookkeeping and GST check (Australia)** | Paste your bank statement and ledger: G6 matches them line by line, checks each line's GST against its code (1/11 of a GST-inclusive amount), and recomputes BAS G1, 1A and 1B against what you reported. Works offline. Not tax advice. | works — first release pending |
| **Compliance gap check** | Answer yes, no or unknown for GDPR and HIPAA requirements, with your evidence, and paste breach or access-request records: G6 lists the gaps and what was not assessed, and checks the deadlines (72 hours, one month, 60 days) from your dates. Works offline. Not legal advice and not an audit; SOC 2 is not covered yet. | built, awaiting its certificate |
| **Plan pre-mortem** | Imagine your plan failed: an AI model lists the likely reasons; G6 makes each one come with an early warning sign and a cheap test, and marks what it says about your plan as "from your plan" or "assumption". Needs a model. | works — first release pending |
| **Runbook safety check** | Paste an operations runbook; an AI model reads it into steps, then G6 gates every step, flags irreversible steps with no backup named for them, and missing rollback or final checks. Needs a model. | works — first release pending |
| **Safe agent actions** | List the actions an AI agent wants to take, with what you know about them; each gets allow, ask a person, or block, plus the open questions. Works offline. | works — first release pending |

Want to build your own bundle? See [Contribute](../contribute.md).
