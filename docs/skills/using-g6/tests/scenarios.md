# Test scenarios for the "using G6" skill

Each run uses a fresh agent with no tools. The prompt is `context.md`, then (for a run with the skill) the full text
of `../using-g6/SKILL.md` and `../using-g6/references/lifecycle.md` marked as a loaded skill, then one task below,
then the instruction:

> Write out, step by step, exactly how you would carry out this task from start to finish: each action you take,
> every G6 tool call or G6 Python call you would make (with the arguments you would pass, as precisely as you can),
> what you would do with each reply, and the final message you would send the user. Be concrete. Under 650 words.

**A, a coding task:** "The refund test in tests/test_refunds.py has been failing since yesterday's change to the
payments module. Fix it and get it ready to merge today."

**B, an AI system that keeps failing:** "Our support-ticket triage agent (it labels incoming tickets and drafts a first
reply) keeps getting the urgency wrong on billing tickets and sometimes promises refunds it can't give. I have about
300 past tickets with the correct labels. Make it reliable."

Score each run against the table in `../TESTING.md`. Run at least three of each, with and without the skill, whenever
the skill changes. Add a scenario whenever a new failure turns up in use.
