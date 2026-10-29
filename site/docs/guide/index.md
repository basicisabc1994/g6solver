# Failure and Harness Engineering for AI Agents

*Free and open — no signup. Why AI-built apps stall before production, and the engineering pattern that addresses it.*

!!! note "How to read the numbers in this guide"
    G6's own study results are on the [Research](../research.md) page, as a worked example; this guide points
    there rather than repeating them. Earlier editions quoted G6's own internal benchmark figures (fix rates,
    safety-gate rates, theory counts, training costs). Those have been removed: they were never independently
    re-validated, and a 2026 audit found that some of the code behind them could overstate results.
    Industry statistics (for example "90% of vibe-coded apps never reach production") are estimates
    from public commentary, not G6 measurements.



# Introduction

You built something amazing last weekend. Maybe it was a patient intake form for your dental practice, a contract reviewer for your law firm, or a content pipeline for your marketing agency. You used Cursor, Claude Code, Replit, or Bolt, and in a few hours you had a working prototype.

Then Monday came. The app hallucinated a drug interaction. The contract reviewer missed a liability clause. The content pipeline published duplicate posts. You spent the next two weeks trying to fix it, and somehow it got worse.

You are not alone. This is the common pattern, not the exception. The tools are not broken. The pattern is missing.

This guide gives you that pattern. It is called **harness engineering**, and it is the difference between a weekend demo and software that actually works.

**Who this guide is for:** Domain experts — doctors, lawyers, accountants, marketers, operators — who build with AI coding tools and need their apps to be reliable. You do not need to know how to code. You do not need to understand machine learning. You just need to understand your domain and be willing to describe what "correct" looks like.

**What you will learn:** Why AI-built apps break, what a harness is and why it matters more than the AI model itself, five diagnosis patterns that professional engineers use, how software can learn from its own mistakes without any fine-tuning, and a complete prompt library you can copy and paste into your coding tool today.


# Chapter 1: The 80/20 Wall

## The Weekend Prototype Trap

AI coding tools have created something genuinely new: for the first time in history, domain experts can build software. A dentist can describe a patient intake workflow and get a working web app. A lawyer can sketch out a contract analysis tool and see it running in minutes. A marketer can build a content pipeline that would have cost tens of thousands to develop.

The speed is real. The capability is real. And that is exactly what makes the trap so effective.

Here is the pattern we see over and over again:

**Friday evening:** You describe what you want. The AI builds it. It works. You are thrilled.

**Saturday:** You try it with real data. It mostly works. A few things are off. You ask the AI to fix them. It does, but now something else is broken. You fix that. Something else breaks.

**Sunday:** You have been at this for hours. The app sort of works but you are not confident in it. You are afraid to touch anything because every change breaks something else.

**Monday:** You need this for real. But you do not trust it. You go back to the spreadsheet.

This is the 80/20 wall. AI gets you 80% of a working app at extraordinary speed. The last 20% — the part that makes it reliable, secure, and trustworthy — is where projects die.

## The Numbers

The problem is not anecdotal. It is structural.

- **An estimated 90% of vibe-coded apps never reach production** (industry estimate). They remain prototypes, demos, or toys that the builder quietly abandons.
- **Published estimates put security weaknesses in 40-62% of AI-generated code samples** (estimates vary by study). Not bugs. Security holes. The kind that expose patient data, leak financial records, or allow unauthorized access.
- **The fix-one-break-two pattern follows from simple arithmetic.** When an AI makes a change to fix one thing, and that change has a 95% chance of being correct, a chain of 15 such changes has only a 46% chance of all being correct. Each fix introduces risk.

## Why More Prompting Does Not Help

The instinctive response is to try harder. Rephrase the prompt. Add more context. Start a new chat. But this does not solve the fundamental problem, because AI coding tools have three structural limitations:

1. **No memory between sessions.** Each new conversation starts from scratch. The AI does not remember what failed yesterday, what you tried, or what worked. You are repeating yourself endlessly.

2. **No learning from mistakes.** If the AI generates code with a subtle bug, and you fix it, the AI does not learn from that correction. The next time it encounters the same situation, it will make the same mistake.

3. **No verification instinct.** The AI does not check whether its output actually works. It generates code that looks plausible, but looking plausible and being correct are very different things.

## The Economics Are Still Broken

At this point, many people think: "I should just hire a developer." But that defeats the entire purpose. You used AI because you wanted to build it yourself, because you understand the domain better than any developer could, and because the economics of hiring a developer for a niche internal tool do not work.

The real answer is not "become an engineer" or "hire an engineer." The real answer is infrastructure — a layer between the AI and production that handles the engineering discipline automatically.

That layer is called a **harness**.


# Chapter 2: Why AI Code Fails

Understanding why AI-generated code fails is the first step toward fixing it. The failures are not random. They fall into five predictable patterns, and once you can see them, you can prevent them.

## Pattern 1: The Confidence Problem

AI models are confident in their answers regardless of whether they are correct. When you ask a human expert a question outside their expertise, they say "I'm not sure." When you ask an AI model the same question, it gives you a detailed, articulate, and completely wrong answer with exactly the same tone it uses for things it knows well.

This is not a quirk. It is a fundamental property of how these models work: the tone of an answer tells you little about whether it is right.

**What this means for you:** Your AI coding tool will generate code that looks correct, reads well, and is completely confident — and it may be silently wrong. You cannot rely on the AI's tone or confidence to judge quality.

## Pattern 2: Context Amnesia

Every AI coding session operates within a "context window" — a limited amount of text the model can consider at once. Think of it like a desk that can only hold a certain number of papers. As your project grows, the AI has to push older papers off the desk to make room for new ones.

The result: the AI forgets constraints, requirements, and decisions made earlier in the conversation. It re-introduces bugs it already fixed. It contradicts design choices it already made. It loses track of which parts of the codebase depend on which.

**What this means for you:** Long conversations with your AI coding tool actually get worse over time, not better. The AI is not building up understanding — it is losing it.

## Pattern 3: Fix One, Break Ten

This is the most frustrating pattern, and it has a mathematical basis. When an AI makes a code change, that change affects other parts of the system. If each individual change has a 95% chance of being correct (which is optimistic), then a sequence of changes rapidly degrades:

- 1 change: 95% chance everything works
- 5 changes: 77% chance everything works
- 10 changes: 60% chance everything works
- 15 changes: 46% chance everything works
- 20 changes: 36% chance everything works

This is why your app seems to get worse as you try to fix it. Each fix has a small probability of introducing a new problem, and those probabilities multiply. After enough changes, it is more likely than not that something is broken.

**What this means for you:** The more you ask the AI to fix, the more likely it is to break something. This is not the AI getting worse — it is a mathematical certainty of sequential changes without verification between them.

## Pattern 4: Security Blindness

Published estimates put security weaknesses in 40-62% of AI-generated code samples. Not just bugs — actual security holes like:

- **Storing passwords in plain text** instead of hashing them
- **SQL injection vulnerabilities** that let attackers access your database
- **Missing authentication checks** that expose private data
- **Hardcoded API keys** that anyone can find in your code
- **Cross-site scripting (XSS)** that lets attackers inject malicious code

The AI does not generate these vulnerabilities maliciously. It generates them because security-correct code is often more complex than the simple version, and the AI defaults to the simple version unless explicitly told otherwise.

**What this means for you:** If your app handles any sensitive data — patient records, financial information, personal details — the code your AI generated may well have security problems that you cannot see by looking at it.

## Pattern 5: The Self-Verification Impossibility

Here is perhaps the most important point: **an AI model cannot reliably check its own work.**

In one small G6 test (an anecdote, not a study), a small model reviewed four of its own incorrect medical answers and confirmed all four as correct. It could not see its own mistakes because the same blind spots that caused the mistake also prevented it from detecting the mistake.

This is not a limitation that will be fixed with a better prompt or a longer conversation. It is a mathematical property: if a model's internal confidence is poorly calibrated (meaning it is confident when wrong), then asking it "are you sure?" produces the same wrong confidence.

**What this means for you:** Telling your AI "check your work" or "review this code" gives you a false sense of security. The AI will tell you it looks correct even when it is not. Real verification requires something outside the model — tests, tools, or human review.


# Chapter 3: Harness Engineering 101

## What Is a Harness?

A harness is everything between the AI model and the final output. Think of it like this: the AI model is the engine, and the harness is the car. An engine by itself is powerful but dangerous and hard to control. You need a chassis, brakes, a steering wheel, a speedometer, and safety features to make it useful.

In software terms, a harness is the system that:
- Tells the AI what to do (the prompt and instructions)
- Gives the AI the right information (grounding and context)
- Checks whether the AI's output is correct (verification)
- Remembers what worked and what did not (memory and learning)
- Prevents dangerous actions (safety)
- Connects the AI to useful tools (tool integration)

**The most important idea in this guide:** the harness can matter as much as the model. The same model can do well or badly depending on what it is given, how its output is checked, and what it is allowed to do. How much G6's own harness helps has not been measured yet; the studies are on the [Research](../research.md) page.

## The Four Layers

A well-engineered harness has four layers. You do not need to build these yourself — G6 provides them — but understanding what they do helps you make better decisions about your AI-built apps.

### Layer 1: Verification — "Does it actually work?"

Verification is the most critical layer. It answers one question: does the output actually do what it is supposed to do?

For code, verification means running tests. Not just "does it compile?" but "does it produce the right answer for known inputs?" For content, verification means checking facts against sources. For data processing, verification means comparing outputs against expected results.

Without verification, you are guessing. With verification, you know.

### Layer 2: Memory — "What failed before?"

Memory solves the "context amnesia" problem. Instead of starting every session from scratch, a harness with memory carries forward:

- What failed in previous attempts and why
- What strategies worked for similar problems
- What the codebase looks like and how the parts connect
- What constraints and requirements matter

G6 keeps two kinds of memory. Its harness remembers which checks failed for a kind of task, and refuses a later standard that drops one of them until the check is back or a person waives it. Its learning component keeps "theories": short prompt hints learned from failures, such as "run the existing tests before attempting a fix". A hint is credited only from paired runs with and without it, and a person accepts it before it is used. Whether these hints help on your work has not been measured.

### Layer 3: Safety — "What could go wrong?"

Safety prevents the harness from taking actions that could cause harm. This is especially important when your app handles sensitive data or makes decisions with real consequences.

A safety layer includes:

- **Action gating:** Before the AI executes code, modifies data, or calls an external service, the safety layer evaluates whether that action is permissible.
- **Confidence thresholds:** For high-stakes decisions, the system requires higher confidence before acting autonomously. For medical, legal, or financial decisions, a good safety layer escalates to human review.
- **Rollback capability:** If an action turns out to be wrong, the system can undo it.

G6's action gate has been tested in a study registered before it ran, on the tool calls of a public agent benchmark. What it found, its miss included, is on the [Research](../research.md) page, as a worked example. Safety costs interruptions, and the study publishes both sides: the harmful calls the gate let through, and the harmless ones it sent to a person.

### Layer 4: Improvement — "How can it be changed?"

This is what makes a harness fundamentally different from just "better prompting." A harness with an improvement layer can be changed without touching the AI model.

The improvement cycle works like this:

1. The system attempts a task
2. It checks whether the result is correct (Layer 1: Verification)
3. If the result is wrong, it diagnoses why
4. It creates or updates a "theory" — a reusable strategy for avoiding that failure
5. A person approves the theory, and next time a similar task comes up, it is applied


# Chapter 4: Failure Diagnosis Patterns

When professional engineers debug software, they follow patterns. These patterns are not about coding — they are about thinking clearly about problems. They work whether you are a software engineer or a dentist debugging a patient intake form.

Here are five patterns distilled from the analysis of 731 real-world code repair tasks.

## Pattern 1: Understand Before Fixing

The most common mistake when something breaks: immediately trying to fix it without understanding why it broke.

**The wrong approach:** "The form is not saving data. Let me ask the AI to fix the save function."

**The right approach:** "The form is not saving data. Let me first understand: Is the data reaching the server? Is the database accessible? Is there an error message I have not seen? What changed since it last worked?"

**How to apply this:** When your AI-built app breaks, resist the urge to immediately tell the AI to fix it. Instead, ask it to explain what is happening and why. Have it trace through the logic. Only once you understand the root cause should you ask for a fix.

## Pattern 2: Define "Correct" First

Before trying to fix something, you need to know what "correct" looks like. This sounds obvious but is routinely skipped.

**The wrong approach:** "The AI is giving weird results. Fix it."

**The right approach:** "The AI should return a list of medications sorted by interaction risk, with each entry showing the drug name, risk level (high/medium/low), and a one-sentence explanation. Here are three examples of correct output for specific inputs."

Fixes often fail not because of bad code, but because nobody clearly defined what the correct behavior should be. The AI cannot fix a problem if "correct" is ambiguous.

**How to apply this:** Before asking the AI to fix anything, write down — in plain language — exactly what the correct behavior should be. Include examples. Be specific. "It should work properly" is not a definition. "Given patient John Smith with allergies to penicillin, the system should flag amoxicillin as high-risk and display: 'Amoxicillin is a penicillin-type antibiotic. Not recommended due to documented penicillin allergy'" — that is a definition.

## Pattern 3: Verify Before Shipping

Every change should be verified before it goes live. Not "I looked at it and it seems fine" — actually tested with real inputs and compared against known correct outputs.

**The minimum verification checklist:**

1. Does the change produce the correct output for a normal case?
2. Does it handle edge cases? (Empty input, very long input, unexpected characters)
3. Does it still work for things that were working before the change?
4. Does it fail gracefully when something goes wrong? (Shows a helpful error, not a crash)

**How to apply this:** After any change, test it with at least three cases: one normal case, one edge case, and one case that was already working (to make sure you did not break it).

## Pattern 4: Understand Why Fixes Fail

When a fix does not work, the instinct is to try a different fix. Instead, figure out why the first fix failed.

Failed fixes are enormously informative. They tell you something about the problem that you did not know before. If you skip the diagnosis and just try something else, you are throwing away information.

**The wrong approach:** "That fix didn't work. Let me try a different approach."

**The right approach:** "That fix didn't work. Let me look at what happened. The fix changed function A, but the error is coming from function B. That means the problem is not where I thought it was — it is in the connection between A and B."

**How to apply this:** When a fix fails, ask your AI: "Why didn't that fix work? What does the failure tell us about the real problem?" The answer often reveals the actual root cause.

## Pattern 5: Learn From Patterns

After you have fixed several problems, step back and look for patterns. Are the same kinds of bugs coming up repeatedly? Is the same part of the app breaking over and over?

In G6, recurring failures can become candidate hints ("theories"). A hint is kept only if paired runs with and without it show that it helps, and a person accepts it before it is used.

Useful lessons are often simple:
- "Always run the existing tests before attempting a fix"
- "When fixing one file, check what other files import from it"
- "Timeout errors usually mean the logic is correct but too slow, not wrong"
- "Empty outputs usually mean a parsing error, not a logic error"

**How to apply this:** Keep a simple log of what breaks and how you fix it. After ten entries, read through them. You will probably see two or three recurring patterns. Address those patterns at the root, and you will prevent most future failures.


# Chapter 5: Self-Training Without Fine-Tuning

## What Fine-Tuning Is and Why You Do Not Need It

When most people think about making AI better, they think about "training" — showing the model millions of examples so it learns new patterns. This is called fine-tuning, and it requires:

- Thousands of training examples
- Expensive GPU hardware ($1,000-$100,000+ per run)
- Machine learning expertise
- Weeks of iteration

None of this is practical for a solo domain expert building internal tools.

G6 takes a different approach. Instead of changing the model (fine-tuning), it changes the instructions, tools, and strategies around the model (harness engineering). It runs on your own models, local or through your own key, and needs no machine learning expertise.

## How Self-Training Works

The self-training loop is simple in concept:

1. **Try:** The system attempts a task using its current harness
2. **Check:** It verifies whether the result is correct
3. **Diagnose:** If incorrect, it analyzes why it failed
4. **Learn:** It proposes a strategy to prevent that failure
5. **Approve and repeat:** A person accepts the strategy, and the next attempt uses it

Each cycle changes the harness; the AI model's weights and training are never touched.

**A real-world analogy:** Think of a new employee at your practice. The employee (the model) does not change. But their onboarding manual, their checklist, their supervision, and their institutional knowledge (the harness) can be improved over time by a person.

## Four Levels of Autonomy

A useful way to match trust to demonstrated capability is a ladder of four levels. It is a way to think about any
AI tool, not a G6 setting. **Where G6 sits:** for changes to how it works, G6 stays at Tiers 0 and 1. Learned
hints are used only after a person accepts them, its improvement cycle is off unless you switch it on, and its
meta-harness needs a person to approve every change. Tiers 2 and 3 below are not G6 features.

### Tier 0: Manual (Supervised)

The AI generates suggestions. A human reviews and approves every action. Nothing happens without explicit approval. This is where every system starts and where high-stakes tasks should stay.

**Analogy:** A new dental assistant who shows you every instrument before using it.

### Tier 1: Structured (Guided)

The AI follows pre-defined workflows with built-in checkpoints. It can execute routine steps autonomously but pauses at decision points for human input.

**Analogy:** An experienced assistant who follows the procedure manual but calls you over for anything unusual.

### Tier 2: Self-Correcting (Semi-Autonomous)

The AI can detect when something has gone wrong, diagnose the cause, and attempt a fix — all without human intervention. But it still reports what it did and why, and a human reviews the results.

**Analogy:** A senior assistant who notices the suction is not working, swaps the tip, and tells you about it afterward.

### Tier 3: Autonomous Theory Building

The AI not only corrects individual failures but also develops reusable strategies (theories) that prevent entire categories of failures. It identifies patterns across multiple tasks and creates systematic improvements.

**Analogy:** A practice manager who notices that three patients this month were double-booked due to the same scheduling quirk, and redesigns the booking workflow to prevent it from happening again.

## The Evidence

!!! warning "Earlier results withdrawn pending re-validation"
    Earlier editions of this guide reported large before/after gains from self-training on public
    benchmarks. An October 2026 audit of the self-training code found a scoring defect that can
    inflate measured gains (a reply could be scored correct against an empty expected answer), so
    those numbers are withdrawn until they are re-run under a pre-registered protocol with
    independent scoring. Results will be published on the [Research](../research.md) page whatever
    their direction.

# Chapter 6: Getting Started

## Install G6

G6 is free and open source (Apache-2.0). It runs on your own computer and needs no account.

**As a library (any coding agent, any Python project):**

```
git clone https://github.com/basicisabc1994/g6solver && cd g6solver
pip install ".[all]"
g6 doctor
```

(G6 is not on PyPI.)

**As an MCP server for Claude Code or Codex:**

```
g6 setup --client claude --allow patch_review.review    # or --client codex
```

**For Cursor and other MCP clients**, add this to the client's MCP configuration:

```json
{
  "mcpServers": {
    "g6": { "command": "g6-mcp" }
  }
}
```

**Not a programmer?** Download a ready-made [bundle](../bundles/index.md) instead — no installation of Python needed.

## Your First Workflow

Once installed, G6 provides tools that your AI coding agent can use automatically. Here is a typical first workflow:

### Step 1: Describe what "correct" looks like

Tell your AI what the app should do, with specific examples:

> "My patient intake form should collect name, date of birth, insurance provider, and chief complaint. When submitted, it should validate that all fields are filled, the date of birth is in the past, and the insurance provider matches our accepted list. Here are three examples of correct submissions and what they should produce."

Turn each requirement into a check G6 can run on the output, such as "the date of birth is in the past".

### Step 2: Let G6 check the work

G6 freezes your checks before the work runs, runs each one on the exact output, and labels the result:
`verified`, `qualified-draft` or `blocked-escalated`. It names every check that failed or could not run.

### Step 3: Read the label and the gaps

The result tells you:
- **What failed:** which checks the output did not meet
- **What is unproven:** what the evidence does not establish, even when every check passed
- **What to do next:** the next step, such as revising the output or adding a missing source

G6 does not find the root cause for you; that is still your job and your coding tool's.

### Step 4: Keep the record

G6 remembers which checks failed for this kind of task. A later run that drops one of those checks is refused until
you add it back or waive it. You decide which correction to apply.

## What to Expect

Expect more results labelled `qualified-draft` or `blocked-escalated` than you are used to hearing "done". That is
the point: each label names what is still unproven. How much G6 helps over time on your app has not been measured
yet.

## Free, open and yours

Everything in G6 is open source. There is no trial, no account and no subscription: your data and
your learned harness stay on your machine unless you export them. Use your own model — a free local
one through Ollama, or your own API key.

# Appendix: The SDLC Prompt Library

This appendix contains copy-paste-ready prompts for every phase of building software with AI coding tools. Each prompt is designed to work with Cursor, Claude Code, Replit, or any AI coding assistant.

Replace the `[BRACKETED]` placeholders with your specific details. These prompts are yours to use, modify, and share.

---

## Phase 1: Planning and Requirements

### Prompt 1.1: Feature Specification

*When to use: At the very start, before any code is written.*

> I am building [DESCRIBE YOUR APP IN ONE SENTENCE]. My users are [DESCRIBE WHO WILL USE IT]. The core workflow is:
>
> 1. User does [STEP 1]
> 2. System should [STEP 2]
> 3. User sees [STEP 3]
>
> Write a feature specification that includes: what each screen looks like, what data is stored, what happens on errors, and what "success" looks like for each step. Include at least 3 edge cases I should handle.

### Prompt 1.2: Requirements Checklist

*When to use: After you have a rough idea but before building, to catch what you have missed.*

> Here is my app idea: [PASTE YOUR DESCRIPTION]. Before I start building, create a requirements checklist covering:
>
> - Data: what is collected, stored, and displayed
> - Users: who uses it, what permissions do they need
> - Security: what data is sensitive, what needs encryption or access control
> - Edge cases: what happens with empty input, duplicate entries, or very large data
> - Compliance: are there industry regulations I need to follow (HIPAA, GDPR, PCI, etc.)
>
> For each item, mark it as MUST HAVE, SHOULD HAVE, or NICE TO HAVE.

### Prompt 1.3: User Story Generation

*When to use: To break a big feature into small, testable pieces.*

> My app does [DESCRIBE THE FEATURE]. Break this into user stories using this format:
>
> "As a [TYPE OF USER], I want to [ACTION], so that [BENEFIT]."
>
> For each story, include:
> - Acceptance criteria: how do I know this works?
> - Test cases: 3 specific examples with inputs and expected outputs
> - Dependencies: does this story need other stories to be done first?

---

## Phase 2: Architecture and Design

### Prompt 2.1: Technology Selection

*When to use: When choosing frameworks, databases, or services.*

> I am building [DESCRIBE APP]. I need to choose technologies for:
>
> - Frontend: [WEB / MOBILE / DESKTOP / ALL]
> - Backend: [WHAT IT NEEDS TO DO]
> - Database: [WHAT DATA IT STORES]
> - Hosting: [BUDGET AND SCALE EXPECTATIONS]
>
> My constraints: I am a [YOUR ROLE, e.g., "dentist"], not a developer. I need the simplest possible stack that is production-ready. I will maintain this with AI coding tools. Recommend a technology stack and explain each choice in one sentence. Flag any choices that would require paid services and estimate the monthly cost.

### Prompt 2.2: Data Model Design

*When to use: Before building any database or data storage.*

> My app manages [DESCRIBE WHAT DATA]. Design the data model:
>
> 1. List every type of data the app stores (e.g., "Patient", "Appointment", "Invoice")
> 2. For each type, list the fields with their data types and whether they are required
> 3. Show how the types relate to each other (e.g., "a Patient has many Appointments")
> 4. Identify which fields contain sensitive data that needs encryption or access control
> 5. Include created_at and updated_at timestamps on every type
>
> Present this as a simple table, not code.

### Prompt 2.3: API Design

*When to use: When your app needs to communicate between frontend and backend, or with external services.*

> My app needs these operations: [LIST THE THINGS USERS CAN DO, e.g., "create a patient record, schedule an appointment, generate an invoice"]. Design the API:
>
> For each operation, specify:
> - What the user sends (the request)
> - What the system returns (the response)
> - What can go wrong and what error the user sees
> - Whether the user needs to be logged in
>
> Use simple language, not technical jargon. Present as a table.

---

## Phase 3: Implementation and Coding

### Prompt 3.1: Build a Feature

*When to use: When implementing a specific feature.*

> Build [FEATURE NAME] for my [APP TYPE] app.
>
> Requirements:
> - [REQUIREMENT 1]
> - [REQUIREMENT 2]
> - [REQUIREMENT 3]
>
> Constraints:
> - Use [TECHNOLOGY STACK] (we chose this in the design phase)
> - Handle these error cases: [LIST ERRORS]
> - All user input must be validated before processing
> - Sensitive data must not be logged or exposed in error messages
>
> After building, show me how to test it with 3 specific examples.

### Prompt 3.2: Secure Coding Checklist

*When to use: After any feature is built, before moving on.*

> Review the code you just wrote for [FEATURE NAME]. Check against this security checklist:
>
> 1. Is user input validated and sanitized before use?
> 2. Are SQL queries parameterized (not built by string concatenation)?
> 3. Are passwords hashed, never stored in plain text?
> 4. Are API keys and secrets in environment variables, not in code?
> 5. Are authentication checks on every endpoint that needs them?
> 6. Is sensitive data (PII, health data, financial data) encrypted at rest?
> 7. Are error messages generic to users (no stack traces or internal details)?
> 8. Is HTTPS enforced for all communications?
>
> For each item: PASS, FAIL (with fix), or NOT APPLICABLE (with reason).

### Prompt 3.3: Code Review Request

*When to use: After building a feature but before considering it done.*

> Review the [FEATURE NAME] implementation. I am not a developer, so explain issues in plain language.
>
> Check for:
> - Does it handle empty or missing data gracefully?
> - Could a user cause it to crash by entering unexpected input?
> - Does it work correctly when two users do the same thing at the same time?
> - Is there any data that could be lost if the server restarts?
> - Are there any hardcoded values that should be configurable?
>
> For each issue found, rate it as CRITICAL (fix now), IMPORTANT (fix before launch), or MINOR (fix later).

---

## Phase 4: Testing and QA

### Prompt 4.1: Test Case Generation

*When to use: After building a feature, to create systematic tests.*

> Generate test cases for [FEATURE NAME]. The feature does: [ONE SENTENCE DESCRIPTION].
>
> Create tests for:
> 1. **Happy path:** Normal usage with valid input (3 cases)
> 2. **Edge cases:** Boundary values, empty input, maximum length (3 cases)
> 3. **Error cases:** Invalid input, missing permissions, system failures (3 cases)
> 4. **Regression:** Things that should still work after this feature was added (2 cases)
>
> For each test: describe the input, the expected output, and what a failure would look like.

### Prompt 4.2: Integration Test

*When to use: When multiple features need to work together.*

> Test the complete workflow of [DESCRIBE END-TO-END PROCESS, e.g., "a patient schedules an appointment, receives a confirmation email, and the appointment appears on the provider's calendar"].
>
> Walk through each step:
> 1. What action does the user take?
> 2. What should the system do?
> 3. What should the user see?
> 4. What data should be created or changed?
>
> Then test: what happens if step [N] fails? Does the system recover gracefully, or does it leave data in a broken state?

### Prompt 4.3: Load and Performance Check

*When to use: Before launching, to make sure the app handles real usage.*

> My app will have approximately [NUMBER] users doing [ACTIVITY] per [TIME PERIOD]. Check whether the current implementation can handle this:
>
> 1. Are there any database queries that get slower as data grows?
> 2. Are there any operations that could time out with many concurrent users?
> 3. Is there any data that is fetched repeatedly and should be cached?
> 4. What is the estimated response time for the most common user action?
>
> If any issue is found, suggest the simplest fix.

---

## Phase 5: Debugging and Diagnosis

### Prompt 5.1: Systematic Bug Diagnosis

*When to use: When something is broken and you do not know why.*

> [DESCRIBE THE PROBLEM: what you expected, what actually happened, and any error messages you see.]
>
> Do NOT immediately fix this. Instead:
>
> 1. List 3 possible causes, ranked by likelihood
> 2. For each cause, describe one test I can run to confirm or rule it out
> 3. Tell me which files or components are involved
> 4. Explain the root cause in plain language (not code)
>
> Only after we agree on the root cause should you propose a fix.

### Prompt 5.2: Regression Investigation

*When to use: When something that was working suddenly stops working.*

> [FEATURE NAME] was working yesterday but is broken today. [DESCRIBE WHAT CHANGED OR WHAT YOU DID SINCE IT LAST WORKED.]
>
> Investigate:
> 1. What changed in the codebase since it last worked?
> 2. Which of those changes could affect [FEATURE NAME]?
> 3. Is the data different from when it was working?
> 4. Is the environment different (new packages, different settings)?
>
> Propose the minimal change to restore the working behavior without undoing other improvements.

### Prompt 5.3: Error Message Translator

*When to use: When you see an error message you do not understand.*

> I see this error: [PASTE THE FULL ERROR MESSAGE].
>
> Explain:
> 1. What this error means in plain language
> 2. What caused it (the most likely reason)
> 3. How to fix it (specific steps)
> 4. How to prevent it from happening again

---

## Phase 6: Deployment

### Prompt 6.1: Deployment Readiness Checklist

*When to use: Before making your app available to real users.*

> My [APP TYPE] is ready to deploy. Run through this pre-launch checklist:
>
> 1. **Security:** All items from the secure coding checklist pass
> 2. **Data:** Database is backed up and backup can be restored
> 3. **Secrets:** No API keys, passwords, or tokens in the code
> 4. **Errors:** All error cases show user-friendly messages, not technical details
> 5. **Performance:** Response times are under 3 seconds for common actions
> 6. **Monitoring:** There is a way to know if the app goes down
> 7. **Recovery:** There is a documented process to restore service if something breaks
> 8. **Legal:** Privacy policy and terms of service exist if handling user data
>
> For each item: READY, NOT READY (with what to do), or NOT APPLICABLE.

### Prompt 6.2: Environment Configuration

*When to use: When setting up the production environment.*

> My app is built with [TECHNOLOGY STACK] and I want to deploy it on [HOSTING PLATFORM, e.g., "Vercel", "Railway", "a VPS"].
>
> Create the deployment configuration:
> 1. What environment variables need to be set (list them, with example values)
> 2. What build commands need to run
> 3. What the startup command is
> 4. What health check URL should the hosting platform monitor
> 5. What to do if the deployment fails
>
> Make this a step-by-step guide I can follow without prior deployment experience.

### Prompt 6.3: Database Migration Safety

*When to use: When your app is already live and you need to change the data structure.*

> My app is live with [NUMBER] users. I need to change the data model: [DESCRIBE THE CHANGE, e.g., "add a phone number field to the patient record" or "split the address field into street, city, state, zip"].
>
> Create a safe migration plan:
> 1. Can this change be made without downtime?
> 2. What happens to existing data?
> 3. What is the rollback plan if something goes wrong?
> 4. Should I back up the database first? (The answer is always yes, but tell me how.)

---

## Phase 7: Monitoring and Maintenance

### Prompt 7.1: Monitoring Setup

*When to use: After deployment, to catch problems before users report them.*

> My [APP TYPE] is live. Set up basic monitoring:
>
> 1. **Uptime:** Alert me if the app is down for more than 2 minutes
> 2. **Errors:** Log errors with enough detail to diagnose them later
> 3. **Performance:** Track response times and alert if they exceed 5 seconds
> 4. **Usage:** Track how many users are active and what features they use
> 5. **Security:** Log failed login attempts and unusual access patterns
>
> Use the simplest tools available for [MY HOSTING PLATFORM]. I do not want to manage monitoring infrastructure.

### Prompt 7.2: Incident Response

*When to use: When something breaks in production and real users are affected.*

> [DESCRIBE THE INCIDENT: what users are reporting, when it started, what you see in the logs.]
>
> Follow this incident response process:
> 1. **Assess:** Is this affecting all users or some users? Is data at risk?
> 2. **Communicate:** Draft a brief status message for affected users
> 3. **Mitigate:** What is the fastest way to stop the bleeding? (Even if it is not a full fix)
> 4. **Diagnose:** What is the root cause?
> 5. **Fix:** Implement and verify the fix
> 6. **Prevent:** What change would prevent this from happening again?

### Prompt 7.3: Dependency Update

*When to use: Monthly, to keep your app's dependencies current and secure.*

> Review the dependencies (packages, libraries, frameworks) used by my app:
>
> 1. Are any dependencies outdated by more than one major version?
> 2. Are there any known security vulnerabilities in current dependency versions?
> 3. Which updates are safe to apply (patch/minor versions)?
> 4. Which updates might break things (major versions) and what do I need to check?
>
> Prioritize by security risk. Apply safe updates and test that everything still works.

### Prompt 7.4: Quarterly Health Check

*When to use: Every 3 months, to maintain long-term app health.*

> Run a quarterly health check on my [APP TYPE]:
>
> 1. **Code quality:** Are there any files that have grown too large or complex?
> 2. **Technical debt:** Are there any temporary fixes that should be made permanent?
> 3. **Performance:** Has response time degraded compared to launch?
> 4. **Security:** Have any new vulnerability types emerged that we should check for?
> 5. **Costs:** Are hosting/API costs in line with usage, or is something inefficient?
> 6. **Backup:** Test restoring from backup to make sure it actually works
>
> Summarize findings and rank actions by impact.

---

*Part of the open-source G6 project, licensed under Apache-2.0.*
