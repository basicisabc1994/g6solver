<!-- SPDX-License-Identifier: Apache-2.0 -->
# Stage 2 and the development pool: the selection record

**Status: stage 2's selection is final, on 2026-11-02, before any of its runs.** All 20 bugs passed `images`, `validate`, `selfcheck` and `freecheck`, and they are in `tasks_unseen.json` in the frozen order. The development pool holds 2 of its 5 so far, in `tasks_dev.json`. The other three come from pathspec and filelock and have not been prepared. Preparation stopped once on 2026-11-01 when the machine ran short of memory, and it was restarted with memory freed.

## The rule

PLAN.md, 2026-10-31 (evening) and 2026-11-01. The kept repositories are taken in PyPI rank order, with the candidates that passed
the mechanical rules taken newest first. Each repository gives at most its two newest candidates judged to report a bug with a clear
expected behaviour (T2), leaving out security reports. A bug that fails `images`, `validate`, `selfcheck` or `freecheck` gives way to
the next candidate in order. Stage 2 takes 20. The development pool takes 5 from the repositories after stage 2's, at most two from
each, sharing no repository with stage 2. The candidates are the 2026-10-31 screen, which kept at most four passing candidates for
each repository. A repository whose four are used up gives way to the next repository.

Round 3, G6 c53d310. The task image runs Python 3.12.

## Repositories walked

Kept repositories up to rank 44 with no candidate that passed the mechanical rules, so no bug: boto3 (1), idna (4), six (15),
iniconfig (25), annotated-types (26), h11 (27), typing-inspection (28), httpx (33), markupsafe (34), httpcore (35), jinja (41),
jmespath.py (42).

## Candidates looked at, in order

"Kept" is the T2 judgement on the issue. "Result" is where the bug ended.

| Rank | Repository | Pull request | Issue | Judgement and reason | Result |
|---:|---|---:|---:|---|---|
| 5 | urllib3/urllib3 | 5347 | 5346 | Kept: a reproducer showing the cached key replaced on each request | **Stage 2** |
| 5 | urllib3/urllib3 | 5254 | 5206 | Kept: concurrent first connections leave threads blocked indefinitely | **Stage 2** |
| 8 | jawah/charset_normalizer | 825 | 824 | Kept: test failures on Python 3.16.0a1, expected: tests pass | Dropped at `validate`: no test fails on the parent and passes with the fix, in the image's Python 3.12 |
| 8 | jawah/charset_normalizer | 772 | 771 | Kept: 3.4.8 raises UnicodeDecodeError on valid UTF-8 that 3.4.7 handles | **Stage 2** |
| 8 | jawah/charset_normalizer | 717 | 716 | Dropped: a redundant list entry, a clean-up that names no wrong result | Not a task |
| 8 | jawah/charset_normalizer | 715 | 672 | Kept: a reproducing file, wrong encoding and language named, expected ones given | **Stage 2**, in 825's place |
| 10 | pypa/setuptools | 5362 | 5360 | Kept: wheel tags come from the build interpreter, not the target | **Stage 2**, after the install fix below |
| 10 | pypa/setuptools | 5193 | 5205 | Dropped: a feature request for abi3t support | Not a task |
| 10 | pypa/setuptools | 5352 | 5351 | Kept: cross compiling abi3 wheels for 3.15 fails | **Stage 2**, after the install fix below |
| 10 | pypa/setuptools | 5169 | 4810 | Kept in reserve: valid `define-macros` syntax is rejected | Not reached |
| 12 | pygments/pygments | 3204 | 3034 | Kept: math environments in LaTeX are not treated as math | **Stage 2**, after the install fix below |
| 12 | pygments/pygments | 3158 | 3133 | Kept: 2.20.0 renders a whole code block as one string | **Stage 2**, after the install fix below |
| 12 | pygments/pygments | 3171 | 2998 | Kept in reserve: a Ruby snippet gives duplicated output | Not reached |
| 12 | pygments/pygments | 3175 | 2975 | Kept in reserve: `escapeinside` strips newlines despite `stripnl=false` | Not reached |
| 13 | yaml/pyyaml | 937 | 897 | Dropped: a denial of service report, left out as a security issue | Not a task |
| 16 | pytest-dev/pluggy | 809 | 807 | Kept: `list_plugin_distributions()` raises KeyError for declined entry points | **Stage 2** |
| 16 | pytest-dev/pluggy | 799 | 792 | Kept: tracing turns a successful hook call into a TypeError | **Stage 2** |
| 17 | boto/botocore | 3852 | 3847 | Kept: `generate_presigned_post` changes the caller's list | **Stage 2** |
| 17 | boto/botocore | 3835 | 3827 | Kept: concurrent calls can return expired credentials during a refresh | **Stage 2** |
| 22 | agronholm/anyio | 1424 | 1423 | Kept: cancelling a receive leaves the transport reading | **Stage 2** |
| 22 | agronholm/anyio | 1354 | 1353 | Kept: async Hypothesis tests run on only one backend | **Stage 2** |
| 30 | boto/s3transfer | 423 | 422 | Kept: `read(-1)` reads past the chunk, expected and actual results given | **Stage 2** |
| 31 | aio-libs/aiobotocore | 1789 | 1797 | Dropped: a compromised certificate authority, left out as a security issue | Not a task |
| 31 | aio-libs/aiobotocore | 1744 | 1743 | Dropped: an automated sync report, not a bug report | Not a task |
| 31 | aio-libs/aiobotocore | 1709 | 1705 | Dropped: an automated sync report, not a bug report | Not a task |
| 31 | aio-libs/aiobotocore | 1704 | 1697 | Kept: `login_session` credential refresh always fails | Dropped at `validate`: no test fails on the parent and passes with the fix. The repository has no other candidate, so it gives way |
| 36 | theskumar/python-dotenv | 615 | 525 | Dropped: a feature request for `~` in `dotenv_path` | Not a task |
| 36 | theskumar/python-dotenv | 711 | 710 | Kept: the error names a temp file, expected: the `.env` path | **Stage 2** |
| 36 | theskumar/python-dotenv | 700 | 699 | Kept: `dotenv get` exits 1 for an empty value | **Stage 2** |
| 36 | theskumar/python-dotenv | 698 | 697 | Kept in reserve: `--no-override` expands with the wrong precedence | Not reached |
| 37 | jpadilla/pyjwt | 1216 | 1209 | Kept: 2.14.0 rejects tokens that 2.13.0 decodes | **Stage 2** |
| 37 | jpadilla/pyjwt | 1201 | 1013 | Kept: `get_jwk_set()` raises IncompleteRead, expected: the key set | **Stage 2** |
| 38 | tox-dev/platformdirs | 666 | 673 | Kept: `use_site_for_root` checks the real uid, not the effective one | **Stage 2**, the twentieth, in place of aiobotocore-1704 |
| 38 | tox-dev/platformdirs | 661 | 659 | Kept: media and font directories read inherited XDG settings | Left out: stage 2 was full, and the pool shares no repository with stage 2 |
| 39 | fsspec/filesystem_spec | 2304 | 2303 | Dropped: a request to speed up listing, not a bug report | Not a task |
| 39 | fsspec/filesystem_spec | 2346 | 2345 | Kept: HTTP `read(None)` does not read through to the end | **Development pool** |
| 39 | fsspec/filesystem_spec | 2287 | 1041 | Kept: `setitems` fails where item assignment succeeds | **Development pool** |
| 39 | fsspec/filesystem_spec | 2352 | 1586 | Kept in reserve: `fsspec.fuse -f` is inverted and hangs | Not reached |
| 43 | cpburnz/python-pathspec | 162 | 161 | Kept: a native backend rejects custom patterns | Next for the development pool; not yet prepared |
| 43 | cpburnz/python-pathspec | 132 | 129 | Kept: `GitIgnoreSpec` re-includes files under an excluded directory | Next for the development pool; not yet prepared |
| 44 | tox-dev/filelock | 880 | 879 | Kept: lock classes silently become None | Next for the development pool; not yet prepared |
| 44 | tox-dev/filelock | 861 | 860 | Not decided: "Add support for Python 3.15", with a traceback; judge on the whole text when reached | Not reached |

## Install commands

Each starts `uv pip install -q --python .venv/bin/python`.

| Repository | Arguments | `SETUPTOOLS_SCM_PRETEND_VERSION` |
|---|---|---|
| urllib3 | `-e ".[brotli,zstd,socks,h2]" --group dev` | 2.8.0 |
| charset_normalizer | `-e . --group dev` | |
| setuptools | `-e ".[test]" coverage` | |
| pygments | `-e . -r requirements.txt pytest wcag-contrast-ratio` | |
| pluggy | `-e . --group testing` | 1.6.0 |
| botocore | `-e . -r requirements-dev.txt` | |
| anyio | `-e ".[trio]" --group test` | 4.15.1 |
| s3transfer | `-e . -r requirements-dev.txt` | |
| aiobotocore | `--no-config -e ".[httpx]" --group test` | |
| python-dotenv | `-e ".[cli]" -r requirements.txt` | |
| pyjwt | `-e ".[crypto]" --group tests` | |
| platformdirs | `-e . --group test` | 4.9.0 |
| fsspec | `-e ".[test]"` | 2026.10.0 |

**Three install problems, fixed so the bug could be judged and not lost to the setup.** setuptools' pytest settings name a warning filter in `coverage`, which its test extra does not install, so pytest stopped before collecting. pygments' `requirements.txt` no longer brings pytest, so the two packages its `tox.ini` names were added. aiobotocore's `tool.uv.required-version` refuses the uv the image installs, so `--no-config` was added, and that bug then failed `validate` on its own account.

## Notes for the audit and the report

- **The audit is in `PLAN.md`, 2026-11-02 (morning).** A blind auditor read each issue in full, which is what this record asked before a task was final, and all kept bugs stand on the rule.
- **Issue 5346 (`urllib3-5347`)** gives its fix as a diff, so every arm may pass it, and it may add no pair that differs.
- **Issue 771 (`charset-normalizer-772`)** keeps its reproducing file at a URL the sandbox cannot reach, so an agent reads the report without the file.
- **Issue 672 (`charset-normalizer-715`)** has the same limit on its attachment.

## What was read

- **The judgements** used each issue's title, labels and text, and nothing else. Longer issues were read only in part: the first 450 to
  1,000 characters, except 5360, 716 and 672, which were read whole, as were the short issues that fit those limits. The blind auditor then read every kept issue whole.
- **Issue 824's text** quotes part of a test in its failure log. That was in the issue, not the fix, and the task was dropped anyway.
- **Packaging files** at parent commits were read to write install commands: dependency lists, extras, groups and pytest settings.
  No test, diff or pull request text was read for this.
- **The commands' results** were read as pass or fail, with counts of tests and no test names. Three install failures needed more: the
  last lines of two pytest consoles, with test names and test file names masked, and the error lines of one image build. These hold setup errors and no test.
- **`test_modules`** was filled in by script from the changed files' names, which were never printed.
