<!-- SPDX-License-Identifier: Apache-2.0 -->
# Stage 2 and the development pool: the selection record

**Status: incomplete. This is not the final selection.** 3 of stage 2's 20 bugs have passed every step, and 0 of the development
pool's 5. The work stopped on 2026-11-01 when the machine ran short of memory and the task pipeline was killed. Free memory then
stood at 3.5 GB, below this job's 4 GB limit. The job's rule is not to restart anything killed for low memory, so nothing further
was built or validated. `tasks_unseen.json` holds only the three tasks that passed. `tasks_dev.json` is still empty.

## The rule

PLAN.md, 2026-10-31 (evening) and 2026-11-01. The kept repositories are taken in PyPI rank order, with the candidates that passed
the mechanical rules taken newest first. Each repository gives at most its two newest candidates judged to report a bug with a clear
expected behaviour (T2), leaving out security reports. A bug that fails `images`, `validate`, `selfcheck` or `freecheck` gives way to
the next candidate in order. Stage 2 takes 20. The development pool takes 5 from the repositories after stage 2's, at most two from
each, sharing no repository with stage 2. The candidates are the 2026-10-31 screen, which kept at most four passing candidates for
each repository. A repository whose four are used up gives way to the next repository.

Round 3, G6 c53d310. The task image runs Python 3.12.

## Repositories walked so far

Kept repositories up to rank 44 with no candidate that passed the mechanical rules, so no bug: boto3 (1), idna (4), six (15),
iniconfig (25), annotated-types (26), h11 (27), typing-inspection (28), httpx (33), markupsafe (34), httpcore (35), jinja (41),
jmespath.py (42).

## Candidates looked at, in order

"Kept" is the T2 judgement on the issue. "Result" is how far the task has got.

| Rank | Repository | Pull request | Issue | Judgement and reason | Result |
|---:|---|---:|---:|---|---|
| 5 | urllib3/urllib3 | 5347 | 5346 | Kept: a reproducer showing the cached key replaced on each request | **Passed all four** |
| 5 | urllib3/urllib3 | 5254 | 5206 | Kept: concurrent first connections leave threads blocked indefinitely | **Passed all four** |
| 8 | jawah/charset_normalizer | 825 | 824 | Kept: test failures on Python 3.16.0a1, expected: tests pass | **Dropped at `validate`:** no test fails on the parent and passes with the fix, in the image's Python 3.12 |
| 8 | jawah/charset_normalizer | 772 | 771 | Kept: 3.4.8 raises UnicodeDecodeError on valid UTF-8 that 3.4.7 handles | **Passed all four** |
| 8 | jawah/charset_normalizer | 717 | 716 | Dropped: a redundant list entry, a clean-up that names no wrong result | Not a task |
| 8 | jawah/charset_normalizer | 715 | 672 | Kept: a reproducing file, wrong encoding and language named, expected ones given | Not yet run; takes 825's place |
| 10 | pypa/setuptools | 5362 | 5360 | Kept: wheel tags come from the build interpreter, not the target | **Open:** `validate` ran no tests on either side; cause not read, not yet a drop |
| 10 | pypa/setuptools | 5193 | 5205 | Dropped: a feature request for abi3t support | Not a task |
| 10 | pypa/setuptools | 5352 | 5351 | Kept: cross compiling abi3 wheels for 3.15 fails | Images built; `validate` not run |
| 10 | pypa/setuptools | 5169 | 4810 | Kept in reserve: valid `define-macros` syntax is rejected | Reached only if 5362 or 5352 fails |
| 12 | pygments/pygments | 3204 | 3034 | Kept: math environments in LaTeX are not treated as math | Not yet run |
| 12 | pygments/pygments | 3158 | 3133 | Kept: 2.20.0 renders a whole code block as one string | Not yet run |
| 12 | pygments/pygments | 3171 | 2998 | Kept in reserve: a Ruby snippet gives duplicated output | Reached only if one above fails |
| 12 | pygments/pygments | 3175 | 2975 | Kept in reserve: `escapeinside` strips newlines despite `stripnl=false` | Reached only if one above fails |
| 13 | yaml/pyyaml | 937 | 897 | Dropped: a denial of service report, left out as a security issue | Not a task |
| 16 | pytest-dev/pluggy | 809 | 807 | Kept: `list_plugin_distributions()` raises KeyError for declined entry points | Not yet run |
| 16 | pytest-dev/pluggy | 799 | 792 | Kept: tracing turns a successful hook call into a TypeError | Not yet run |
| 17 | boto/botocore | 3852 | 3847 | Kept: `generate_presigned_post` changes the caller's list | Not yet run |
| 17 | boto/botocore | 3835 | 3827 | Kept: concurrent calls can return expired credentials during a refresh | Not yet run |
| 22 | agronholm/anyio | 1424 | 1423 | Kept: cancelling a receive leaves the transport reading | Not yet run |
| 22 | agronholm/anyio | 1354 | 1353 | Kept: async Hypothesis tests run on only one backend | Not yet run |
| 30 | boto/s3transfer | 423 | 422 | Kept: `read(-1)` reads past the chunk, expected and actual results given | Not yet run |
| 31 | aio-libs/aiobotocore | 1789 | 1797 | Dropped: a compromised certificate authority, left out as a security issue | Not a task |
| 31 | aio-libs/aiobotocore | 1744 | 1743 | Dropped: an automated sync report, not a bug report | Not a task |
| 31 | aio-libs/aiobotocore | 1709 | 1705 | Dropped: an automated sync report, not a bug report | Not a task |
| 31 | aio-libs/aiobotocore | 1704 | 1697 | Kept: `login_session` credential refresh always fails | Not yet run |
| 36 | theskumar/python-dotenv | 615 | 525 | Dropped: a feature request for `~` in `dotenv_path` | Not a task |
| 36 | theskumar/python-dotenv | 711 | 710 | Kept: the error names a temp file, expected: the `.env` path | Not yet run |
| 36 | theskumar/python-dotenv | 700 | 699 | Kept: `dotenv get` exits 1 for an empty value | Not yet run |
| 36 | theskumar/python-dotenv | 698 | 697 | Kept in reserve: `--no-override` expands with the wrong precedence | Reached only if one above fails |
| 37 | jpadilla/pyjwt | 1216 | 1209 | Kept: 2.14.0 rejects tokens that 2.13.0 decodes | Not yet run |
| 37 | jpadilla/pyjwt | 1201 | 1013 | Kept: `get_jwk_set()` raises IncompleteRead, expected: the key set | Not yet run |
| 38 | tox-dev/platformdirs | 666 | 673 | Kept: `use_site_for_root` checks the real uid, not the effective one | Not yet run |
| 38 | tox-dev/platformdirs | 661 | 659 | Kept: media and font directories read inherited XDG settings | Not yet run |
| 39 | fsspec/filesystem_spec | 2304 | 2303 | Dropped: a request to speed up listing, not a bug report | Not a task |
| 39 | fsspec/filesystem_spec | 2346 | 2345 | Kept: HTTP `read(None)` does not read through to the end | Not yet run |
| 39 | fsspec/filesystem_spec | 2287 | 1041 | Kept: `setitems` fails where item assignment succeeds | Not yet run |
| 39 | fsspec/filesystem_spec | 2352 | 1586 | Kept in reserve: `fsspec.fuse -f` is inverted and hangs | Reached only if one above fails |
| 43 | cpburnz/python-pathspec | 162 | 161 | Kept: a native backend rejects custom patterns | Not yet run |
| 43 | cpburnz/python-pathspec | 132 | 129 | Kept: `GitIgnoreSpec` re-includes files under an excluded directory | Not yet run |
| 44 | tox-dev/filelock | 880 | 879 | Kept: lock classes silently become None | Not yet run |
| 44 | tox-dev/filelock | 861 | 860 | Not decided: "Add support for Python 3.15", with a traceback; judge on the whole text when reached | Not yet run |

Which repositories stage 2 and the development pool end on depends on the validations still to run, so the split between them is
not settled.

## Where to resume

Start with `charset-normalizer-715`, then `setuptools-5362`, whose empty `validate` needs its cause read as an install problem, then
`setuptools-5352`, and on in the table's order. `setuptools-5352`'s images exist and it needs `validate` onward.

Install commands written for the tasks not yet validated. Each starts `uv pip install -q --python .venv/bin/python`. Only the
setuptools images have been built with theirs, and none of the others has been tried.

| Repository | Arguments | `SETUPTOOLS_SCM_PRETEND_VERSION` |
|---|---|---|
| setuptools | `-e ".[test]"` | |
| pygments | `-e . -r requirements.txt` | |
| pluggy | `-e . --group testing` | 1.6.0 |
| botocore | `-e . -r requirements-dev.txt` | |
| anyio | `-e ".[trio]" --group test` | 4.15.1 |
| s3transfer | `-e . -r requirements-dev.txt` | |
| aiobotocore | `-e ".[httpx]" --group test` | |
| python-dotenv | `-e ".[cli]" -r requirements.txt` | |
| pyjwt | `-e ".[crypto]" --group tests` | |
| platformdirs | `-e . --group test` | 4.9.0 |
| fsspec | `-e ".[test]"` | 2026.10.0 |

## What was read

- **The judgements** used each issue's title, labels and text, and nothing else. Longer issues were read only in part: the first 450 to
  1,000 characters, except 5360, 716 and 672, which were read whole, as were the short issues that fit those limits. A kept bug's
  judgement could change on the full text, so each should be read in full before its task is final.
- **Issue 824's text** quotes part of a test in its failure log. That was in the issue, not the fix, and the task was dropped anyway.
- **Packaging files** at parent commits were read to write install commands: dependency lists, extras, groups and pytest settings.
  No test, diff or pull request text was read.
- **The commands' results** were read as pass or fail, with counts of tests and no test names. No install problem has yet needed a
  look at a build log.
- **`test_modules`** was filled in by script from the changed files' names, which were never printed.
