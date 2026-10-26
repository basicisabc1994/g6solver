---
name: g6-adapt-webpage
description: Use when building an offline web page or design-token set and checking markup, declared colour contrast and optional horizontal overflow.
---

# G6 adapt_webpage

Build an in-memory artefact, then check it. The merged UI capability provides shade scales, typography, spacing,
shadows, radii and breakpoints, exported as CSS, Tailwind configuration JSON or DTCG-shaped JSON.

## When to use it

Use for deterministic page/component drafts and reusable tokens. Check a supplied file map before writing it.
Declare each text role's actual foreground and background. The contrast threshold is 4.5:1 for normal text and
3:1 for large text; the caller declares whether the text is large.

## When not to use it

Edit by hand for small, context-sensitive changes to an existing site. Do not use this as a full accessibility,
HTML5, CSS semantic, visual regression or security validator. No cloning, deployment, template atlas or bundled fonts.

## Ops

- `build`: `kind=web_page` or `design_tokens`, with a `spec`; returns files and template provenance.
- `backend_capabilities`: reports supported deterministic modes without probing optional dependencies.
- `run_checks`: sensitive; checks supplied `files`, `pairs`, `render` and `breakpoints`. Dry-run by default.
- `write_files`: sensitive; writes supplied `files` to `dest` under the person's declared roots. Dry-run by default.

## Alone and composed

From Python, construct `AdaptWebpageBlock` and `AdaptWebpageInput`; the registry/MCP name is `adapt_webpage`.
Compose build, review files, run_checks, then write_files. Pass the built `files` into each following request.
Python integrations can register trusted builder/checker pairs on the block's `backends`; request JSON cannot
replace a checker. The retained `WebpageBackend` protocol supports external deterministic adapters.

Web specs accept title, description, layout, theme, sections, custom_css, mode and component_type.
Modes are page, component, sections and css. Token specs accept colour (six-digit hex), format (css, tailwind,
dtcg), base_font_size, scale_ratio and include_dark_mode. Theme CSS uses system font fallbacks; no font is downloaded.
Custom CSS can reference external resources: review it before opening a written page. The renderer blocks external
HTTP and WebSocket requests and serves only files in the artefact.

An artefact has at most 200 files, 1 MB each and 8 MB total, using portable relative paths. Bytes are carried as
UTF-8 strings at the block door. Names and counts are checked first; encoding stops at the first exceeded file or
total byte limit, including dry runs. Files are checked in fresh sandbox directories, transported as one archive.
Local checks and writes remain sensitive, dry-run by default; caller approval fields are not accepted.
The person configures destinations through the code family's `G6_CODE_CONFIG` roots; no existing file is overwritten.

Supply contrast oracles as `pairs` of foreground, background, large and optional role; missing colours are refused.
Supply explicit breakpoints for layout expectations. A generated design cannot certify its own usability.

## Failure modes it catches

- `broken_responsive_layout`: optional Playwright measurements detect document horizontal overflow above one pixel
  at each requested width.
- `design_quality_failed`: explicit HTML tag balance/common nesting, CSS syntax and declared-pair contrast failures
  refuse the artefact.

## Blind spots

`broke_existing_functionality`: generated snippets may change behaviour or meaning when integrated.
The standard-library HTML parser has an explicit stack and selected nesting rules; it is not a full HTML5 validator
and conservatively rejects omitted closing tags. tinycss2 checks syntax, not property validity, layout or all
error recovery cases. Token contrast does not see gradients, transparency, compositing, rendered fonts or all
undeclared pairs. The initial-layout renderer does not check interactions, clipped text or computed-style contrast.
Tests written by the generating model are smoke tests unless independently anchored in caller-supplied oracles.
The sandbox runs as the same user, can read that user's files, and uses an interpreter socket guard, not OS isolation.

The first failed check returns `blocked-escalated` with a stable `G6_E_*` code. Otherwise the ceiling is
`qualified-draft`, with checks run and not run listed in the warning card. Skips are never passes.

## What it needs

Sensitive operations are dry-run by default. Set dry_run=false explicitly to execute from Python.
Over MCP the server operator must also allow the component or component.op in G6_MCP_SENSITIVE;
request approval or authority fields cannot grant access. Local writes, sandbox checks and network reads
do not consult the action gate. Model calls use only the injected caller. The action gate is for outward
acts such as sending, publishing and deploying, which these members do not provide.
A paid injected caller uses the person's key in their own `~/.g6/.env`, read through `g6.core.keys`, as approval to spend.

No extra for build. `g6[web]` provides tinycss2 (BSD-3-Clause); if it is not installed, the requested CSS check
blocks with `G6_E_CODE_CHECKER_NOT_INSTALLED` and `pip install "g6[web]"` instructions.
`g6[render]` provides Playwright (Apache-2.0). Playwright Chromium is an outside program: install its browser
yourself with `python -m playwright install chromium`. Its licences include BSD-3-Clause and third-party notices.
Rendering is opt-in; an unrequested render is skipped. A requested render without Playwright or its browser
blocks through `g6.core.outside.require` with `G6_E_OUTSIDE_PROGRAM_MISSING` and installation instructions.
An installed renderer that errors causes refusal. No program is installed automatically.

## Examples

```g6-example op=build
{"spec":{"title":"A draft page","theme":"minimal","sections":["hero","features"]}}
```

```g6-example op=build
{"kind":"design_tokens","spec":{"colour":"#336699","format":"css"}}
```

```g6-example op=backend_capabilities
{}
```

```g6-example op=run_checks
{"files":{"index.html":"<p>Draft</p>"},"pairs":[{"foreground":"#ffffff","background":"#000000"}]}
```

The last example is a dry run: no execution or dependency is needed.

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
