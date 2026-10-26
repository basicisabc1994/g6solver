---
name: g6-align-prompt-library
description: Use when prompts, specifications or evaluation sets must be kept with their full history, each version addressed by the SHA-256 of its content so a result can cite exactly the version it used, with strict template rendering, tombstones instead of deletes and rollback as a new version.
---

# G6 prompt library

`g6.workspace.align_prompt_library` keeps prompts, specifications and evaluation sets with every version they ever had.
Each version is addressed by its digest, the SHA-256 of its content, so a run can cite exactly what it used and anyone
can read that version back later, whatever happened to the name since. It is an extension in the workspace family
(the archive's `align_artifacts` is merged into it); its best state is `qualified-draft`.

## When to use it

- A prompt, an acceptance specification or an evaluation set is shared between agents or runs, and changes over time.
- A result must say which prompt or specification produced it: cite the digest `put` or `render` gives.
- You need a template filled with every value it needs and no other, and the digest of the text you sent.
- You want to undo a change to a prompt without losing the change: roll back as a new version.

## When not to use it

- Judging whether a prompt is good: storing a version checks nothing about it. Test it with an evaluation set and a
  component that scores outputs.
- Working files of a task: use `workspace_manager`.
- Secrets or personal data: the library is plain text under the G6 home, kept for ever.

## Ops

- `put`: store a new version of a prompt, spec or eval_set (`kind`, `name`, `content`, `metadata`); returns its
  `version` and `digest`. The same content and metadata as the latest version add nothing (`unchanged`).
- `get`: one version (`name`, `version` or `digest`); the latest by name alone. A version or digest the library does
  not hold is refused, never answered with the latest; a `digest` alone finds every name holding that content.
- `history`: every version of a name (`name`), oldest first, tombstones included.
- `search`: the latest version of each name whose name, content or metadata contain the words (`query`, `kind`,
  `include_retired`, `limit`).
- `render`: a version's template with its `{{placeholders}}` filled from `values` in one pass (`name`, `version` or
  `digest`, `values`); a missing or an extra value is refused.
- `retire`: a tombstone (`name`); every version stays readable by its number or digest.
- `rollback`: a new version holding an earlier version's content (`name`, `to_version`); nothing is rewritten.

## Alone and composed

Alone over MCP: `g6_invoke("align_prompt_library", "render", '{"name": "summary", "values": {"document": "..."}}')`.
`put`, `retire` and `rollback` are sensitive: a stored prompt affects later tasks. They are refused with
`G6_E_SENSITIVE_COMPONENT_DENIED` on a surface using `block_sensitive=True`; over MCP the server must explicitly
allow each `align_prompt_library.<op>`. Reads remain non-sensitive. From Python:
`from g6.workspace.align_prompt_library import Library, ops`; `Library(path)` keeps a library in a file of your own.

How it is guarded:

- Every version is an entry in an append-only `g6.core.ledger` chain. On every open the chain's hashes and the
  library's rules are checked: versions run 1, 2, 3 with no gap, a name keeps its kind, each digest is the SHA-256 of
  its content, and a rollback holds the content of the version it names. A library that does not check out answers
  nothing (`G6_E_LIBRARY_CORRUPT`) and takes nothing more.
- Nothing is removed. `retire` adds a tombstone: a plain read of the name is refused (`G6_E_LIBRARY_RETIRED`, naming
  the versions still readable) until a new `put`.
- What you say about a record (an author, a reviewer, a test result, a safety tag) goes in `metadata` and comes back
  under `declared`, as your word. It opens nothing and raises no label; there is no field for a review status.
- Rendering replaces each `{{name}}` once, so a value that itself holds `{{...}}` is never expanded.
  Before substitution it computes the complete expanded length; more than 1,000,000 characters is refused with
  `G6_E_RESOURCE_LIMIT`, without constructing that output.
- Appends use the family's OS-backed mutex, released on process death without age-based takeover; limits are 200,000 characters a record and 50 MB a library.

Composed:

- `metaharness`: a profile or workflow can cite a library digest for the prompt it uses.
- `workspace_manager` and `shared_workspace`: keep a task's files beside the digest of the specification they meet.

## Failure modes it catches

- `untracked_prompt_version` (losing track of which prompt or spec version produced a result): every version is kept
  and addressed by the SHA-256 of its content, so a cited digest reads back exactly that content after any change,
  rollback or retirement; a version or digest the library does not hold is refused rather than answered with another;
  a record edited, removed or re-chained in the file is found and the library refuses to answer.

## Reading the result

- An answer is `qualified-draft`. Its `warning_card.code` says what it rests on: `G6_E_LIBRARY_STORED` (with
  `version`, `digest` and `unchanged`), `G6_E_LIBRARY_READ` (with `content`, `declared` and `also`),
  `G6_E_LIBRARY_LISTED` or `G6_E_LIBRARY_RENDERED` (with `text`, `template_digest` and `rendered_digest`).
- A refusal is `blocked-escalated` with a code such as `G6_E_LIBRARY_UNKNOWN_VERSION`, `G6_E_LIBRARY_RETIRED`,
  `G6_E_LIBRARY_NOT_FOUND`, `G6_E_LIBRARY_MISSING_VALUE`, `G6_E_LIBRARY_EXTRA_VALUE`, `G6_E_LIBRARY_KIND_MISMATCH`
  or `G6_E_LIBRARY_CORRUPT`. Over MCP `ok` can be true while `completion_state` is `blocked-escalated`: read the state.

## Blind spots

- Someone who can write the G6 home can rebuild the whole chain with matching digests; only a head kept elsewhere
  would show it, and the library does not keep one.
- It records which version a run says it used; it cannot see whether the run really sent that text.
- Search matches words, not meaning.
- Each op reads the whole library file; a very large library is slow.
- `prompt_injection`: a stored prompt is text and can carry instructions. G6 returns it as data; it does not scan it.

## What it needs

Nothing outside G6: the standard library and `g6.core.ledger`. No extra, no outside program, no model.

## Examples

Store a prompt (sensitive: requires server allowance), read it back, and see its history:

```g6-example op=put
{"kind": "prompt", "name": "example-summary", "content": "Summarise {{document}} for {{audience}}.", "metadata": {"author": "ana"}}
```

```g6-example op=get
{"name": "example-summary"}
```

```g6-example op=history
{"name": "example-summary"}
```

```g6-example op=search
{"query": "summarise"}
```

Fill the template (a missing or an extra value is refused):

```g6-example op=render
{"name": "example-summary", "values": {"document": "the quarterly report", "audience": "the board"}}
```

Roll back to the first version, then retire the name (both sensitive; every version stays readable):

```g6-example op=rollback
{"name": "example-summary", "to_version": 1}
```

```g6-example op=retire
{"name": "example-summary"}
```

From Python, a run that cites the version it used:

```python
from g6.workspace.align_prompt_library import Library, digest_of

library = Library("library.jsonl")
first = library.put("prompt", "triage", "Triage {{case}} by urgency.", {"reviewer": "dr-lee"})
used = library.render("triage", digest=first["digest"], values={"case": "a cough"})
assert used["template_digest"] == first["digest"] == digest_of("Triage {{case}} by urgency.")

library.put("prompt", "triage", "Triage {{case}} by urgency and age.")
library.retire("triage")
assert library.get(digest=used["template_digest"])["content"] == "Triage {{case}} by urgency."
assert library.get("triage", version=1)["declared"] == {"reviewer": "dr-lee"}   # their word, kept as such
assert [h["action"] for h in library.history("triage")] == ["put", "put", "retire"]
```

## The four open problems

- **Learning signals**: every version records when it was stored, what it replaced and what was declared about it, and
  every render the digests of its template and its text, so a result can be traced to its prompt. Not yet: nothing
  links a version to the scores its runs earned.
- **Evaluators the model cannot influence**: the library's rules are fixed code checked on every open, and nothing a
  caller declares reaches them. Not yet: the rules and the head of the chain are not pinned outside the library.
- **Transfer by failure mode**: the guard is bound to `untracked_prompt_version`, not to a kind of prompt: it
  addresses content by its SHA-256, which every record has. Not yet: how often runs cite a version is not measured.
- **Enough held-out cases**: Not yet: an evaluation set can be stored and versioned here, but its tests are fixed
  regressions, not a held-out set judged by G6's sequential test.
