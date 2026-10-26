---
name: g6-adapt-django
description: Use when drafting consistent Django model, view, URL, admin, form, serializer and settings source, then checking a complete project in the sandbox.
---

# G6 adapt_django

Deterministic Django source generation; Django is needed only to check a complete project.

## When to use it

Generate repeated boilerplate from structured, validated names. Check complete file maps for Django system errors
and model/migration drift before integration. Preserve field defaults, choices, help text and relationships.

## When not to use it

Use ordinary editing for context-sensitive changes to existing apps. This is not a project scaffolder, migration
writer, deployment runner or database inspector. It does not apply migrations or import project settings in G6.

## Ops

- `build`: `kind=django` and `spec` produce an in-memory source file map.
- `run_checks`: sensitive; checks `files` using `settings_module` (default `project.settings`), dry-run by default.
- `write_files`: sensitive; writes `files` to `dest` under declared roots, dry-run by default.

## Alone and composed

Use `AdaptDjangoBlock` with `AdaptDjangoInput`, or the registry/MCP name `adapt_django`.
A spec has app_name, project_name, models, views and optional parts (models, views, urls, admin, forms,
serializers, settings). Model fields use an allowed field_type and validated names. Strings are quoted as Python
literals; no input supplies an expression, interpreter or working directory.

Build, review, combine the file map with the caller's project, then run_checks. Supply manage.py and settings in
that map. G6 invokes Django's management dispatcher directly with the command arguments `manage.py check` and
`manage.py makemigrations --check --dry-run`; it does not trust a custom manage.py's exit status as evidence.
Both run inside the sandbox against the requested settings module, never in G6's process.
Any premature exit, including SystemExit(0), fails; a report needs the parent's per-run token and the wrapper's
completion marker. The token-bearing wrapper source is removed before project imports.
Custom manage.py initialisation is not run; move required setup into settings or inspect it separately.

Generated settings deliberately refuse an unset SECRET_KEY. Configure them yourself before checking; no project
.env or process secret is read by G6. The minimal settings draft uses an in-memory database and is not deployment
configuration. Selected admin output includes admin, auth, contenttypes, sessions and messages, with their required middleware and template context processors. Without admin, contrib apps are included only for selected login-required views or model relations. Serializers are source strings only: Django REST Framework is not installed or imported by this
member, and using the generated serializers in your application requires that application's own dependency.

Only a migrations package marker is emitted; no handwritten migration is supplied. Generate/review migrations
using your project's workflow, then submit the resulting file map. Missing migrations can cause the drift check
to refuse. A successful system check does not substitute for a migration check.

Trusted Python integrations can register other builders/checkers on backends. Local checks and writes are sensitive and dry-run by default.
Writing needs a destination under the person's `G6_CODE_CONFIG` roots, and never overwrites existing files.
Files are limited to 200, 1 MB each and 8 MB total; only portable relative paths
are accepted. Names and counts are checked before encoding; each file is encoded and byte limits checked before
the next file, including dry runs. Checks transport one archive into a fresh child temporary directory.

Supply independent integration oracles: fixtures, expected query results, permission scenarios and migration state.
Tests drafted by the same model are only smoke tests unless anchored in caller-supplied examples.

## Failure modes it catches

- `handwritten_boilerplate`: fixed templates align generated modules; invalid names are refused. Django checks
  catch framework errors, and the dry migration check catches changes missing from supplied migrations.

## Blind spots

`broke_existing_functionality`: consistency does not establish application intent, correct permissions, migration
data preservation, database compatibility, rendered templates or serializer behaviour. System checks are not tests
of request/response behaviour. Project code runs inside the check's own process, so a project written to defeat
the check can still forge its result; the check catches honest mistakes, not hostile projects.
The sandbox bounds time, memory and output, uses a clean environment and a Python socket guard, and runs as the same
user; it is not OS isolation and can read the user's files. Do not treat it as containment for deliberately hostile
projects. Windows and POSIX resource bounds differ.

The first failed check yields `blocked-escalated` with a stable code. All checks that ran passing gives only
`qualified-draft`, explicitly listing run and skipped checks. Nothing here returns `verified`.

## What it needs

Sensitive operations are dry-run by default. Set dry_run=false explicitly to execute from Python.
Over MCP the server operator must also allow the component or component.op in G6_MCP_SENSITIVE;
request approval or authority fields cannot grant access. Local writes, sandbox checks and network reads
do not consult the action gate. Model calls use only the injected caller. The action gate is for outward
acts such as sending, publishing and deploying, which these members do not provide.
A paid injected caller uses the person's key in their own `~/.g6/.env`, read through `g6.core.keys`, as approval to spend.

Build requires no extra or outside program. `g6[django]` supplies Django (BSD-3-Clause) only for checks.
If Django is not installed in the sandbox interpreter, both required checks fail with
`G6_E_CODE_CHECKER_NOT_INSTALLED` and `pip install "g6[django]"` instructions. Configure
settings and provide a complete project as files. No database server or interpreter path is accepted.

## Examples

```g6-example op=build
{"spec":{"app_name":"catalogue","project_name":"project","models":[{"name":"Entry","fields":[{"name":"title","field_type":"CharField","max_length":120}],"str_field":"title"}]}}
```

```g6-example op=run_checks
{"files":{"manage.py":"# Caller supplies the complete project here"}}
```

The check example is a dry run and does not need Django.

## The four open problems

- **Learning signals**: replies name checks that failed and their evidence, so a person can group failures by generator
  and prompt. Nothing learns from these events or stores a score.
- **Evaluators the model cannot influence**: G6 selects the fixed checkers; model claims cannot raise a label.
  Parser and numeric checks are independent of prose claims. Executed code still shares the sandbox user's rights;
  this is not protection against deliberately hostile code.
- **Transfer by failure mode**: the shared Artefact and CheckResult protocol gives each backend the same bounds,
  check accounting and refusal rule; passing one backend says nothing about another.
- **Enough held-out cases**: no broad held-out benchmark is claimed. Supply separate examples and properties from
  your domain, including boundary and failure cases, before relying on a result.
