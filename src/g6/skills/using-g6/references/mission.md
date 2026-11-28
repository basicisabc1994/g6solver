# A coding mission

A standard of done, fixed before the work and confirmed by the person: `g6_premortem`, then the
`g6_mission_*` tools.

## A coding mission, over MCP

Use one when what counts as done must be fixed before the work and a person must confirm it. `g6_premortem` proposes the
standard, the ways the task can fail and candidate questions, with no model call and nothing run; put one of them to the
person, through `g6_confirm_behaviour` (one question per task by default; the person may allow more). `g6_mission_create`
freezes the standard from the commit a clean repository stands at; an objective about retries or duplicates first
asks a question, which you answer with `g6_mission_clarify`. `g6_mission_confirm` asks the person in the client's own
dialog. Only the person can confirm: no argument does, you never answer for them, and there is no terminal fallback.
After a decline, a cancel or a client with no dialog, the mission stays unconfirmed and takes no patch: ask the person,
and offer no other way. The recorded name is not authenticated.

Then change only the permitted file and call `g6_mission_submit` with the folder. It reviews your working tree with the
frozen tests as its stated checks. A reproduced defect or a sign the tests cannot be believed holds the patch back: it
uses an attempt and the mission cannot close on it. Otherwise the mission judges the same text on its frozen copy.
Insufficient evidence blocks nothing, and the mission says so: tell the person. A change to a test is refused. Show
`assessment["report"]` as it is, and follow the reply's mission: three attempts, a repeated or non-improving patch
moves to a focused brief, then to the person. You repair; G6 never writes
code. Merging stays the person's separate decision. `g6_mission_status` and `g6_mission_certificate` read the mission;
the tools that change it return a job, so poll `metaharness_job`. An `R3` mission's patch approval is not over MCP yet:
tell the person and stop. The tools that run code need host permission workforce.mission,
which the person grants; never change permissions yourself.

For the surrounding coding stages, [read the full lifecycle](lifecycle.md).
