<!-- SPDX-License-Identifier: Apache-2.0 -->
# Worked examples: the selection record

PLAN.md's rule, applied on 2026-10-29 (read at 05:31 UTC), after the plan's commit and before any run. The reads were
read-only GitHub API calls (`selection.py`). The issue texts are kept in the local archive, not here.

## Projects

| Project | Licence (GitHub) | Stars | PyPI rank (30 days) | Kept | Why |
|---|---|---:|---:|---|---|
| [pydantic/pydantic](https://github.com/pydantic/pydantic) | MIT | 29,236 | 18 | yes | P1, P2, P4 |
| [psf/black](https://github.com/psf/black) | MIT | 42,083 | 248 | yes | P1, P2, P4 |
| [pallets/click](https://github.com/pallets/click) | BSD-3-Clause | 17,802 | 20 | yes | P1, P2, P4 |
| [pallets/jinja](https://github.com/pallets/jinja) | BSD-3-Clause | 11,809 | 41 | yes | P1, P2, P4; no pull request merged in the window, so no task |
| [encode/httpx](https://github.com/encode/httpx) | BSD-3-Clause | 15,712 | 33 | yes | P1, P2, P4; no pull request merged in the window, so no task |
| [encode/starlette](https://github.com/Kludex/starlette) (now Kludex/starlette) | BSD-3-Clause | 12,690 | 45 | yes | P1, P2, P4 |
| [fastapi/fastapi](https://github.com/fastapi/fastapi) | MIT | 104,268 | 74 | yes | P1, P2, P4 |
| [Textualize/rich](https://github.com/Textualize/rich) | MIT | 57,679 | 52 | yes | P1, P2, P4; its one pull request in the window closes no issue, so no task |
| [python-attrs/attrs](https://github.com/python-attrs/attrs) | MIT | 5,863 | 29 | yes | P1 by its PyPI rank (fewer than 10,000 stars), P2, P4 |
| [networkx/networkx](https://github.com/networkx/networkx) | NOASSERTION | 17,368 | 141 | no | P2: GitHub reports no licence it recognises, and P2 requires GitHub's licence to be one it lists |
| [pypa/packaging](https://github.com/pypa/packaging) | NOASSERTION | 754 | 2 | no | P2, as for networkx |
| [marshmallow-code/marshmallow](https://github.com/marshmallow-code/marshmallow) | MIT | 7,245 | 295 | no | P1: fewer than 10,000 stars and not in PyPI's top 100 |
| [pallets/flask](https://github.com/pallets/flask) | BSD-3-Clause | 74,936 | 215 | no | P4: a SWE-bench repository |
| [psf/requests](https://github.com/psf/requests) | Apache-2.0 | 54,597 | 7 | no | P4: a SWE-bench repository |
| [python-pillow/Pillow](https://github.com/python-pillow/Pillow) | NOASSERTION | 13,940 | 59 | no | P2, as for networkx (its licence is MIT-CMU) |

P3 (tests that run offline) is decided at each task's validation.

**A correction to the first read.** The first read found no pull requests for encode/starlette, because GitHub's
search finds nothing under the name a repository was moved from. The script now searches under the repository's
current name (Kludex/starlette), and a list as long as the search's limit (1,000) stops the script instead of
passing. The read above is the corrected one; the first read is kept in the archive.

## Pull requests read (newest first) and the tasks

The mechanical checks (T1, T2's one-issue part, T3, T4) ran on every merged pull request read; the T2 judgement (a
bug with a clear expected behaviour, not a security report) was made on the issue texts in order, until two passed.

| Project | Read | Passed over by the mechanical checks | Judged | Taken |
|---|---:|---|---|---|
| pydantic | 40 | 27 close no single issue; 3 change no Python source outside the tests; 3 change source in another language (pydantic-core's Rust); 1 merged into 1.10.X-fixes, not main | #14065, #14052 | #14065, #14052 |
| black | 39 | 32 close no single issue; 1 changes no Python source outside the tests | #5634, #5521 | #5634, #5521 |
| click | 54 | 26 close no single issue; 19 merged into stable, not main; 3 change no Python source outside the tests | #3781, #3769, #3637, #3678 | #3769, #3678 |
| starlette | 28 | 21 close no single issue; 1 adds or changes no test file | #3720, #3718 | #3720, #3718 |
| fastapi | 401 | 400 close no single issue | #15077 | #15077 |
| attrs | 26 | 24 close no single issue; 1 changes no Python source outside the tests | #1593 | #1593 |
| rich | 1 | 1 closes no issue | none | none |

Judgements (T2):

- pydantic #14065, issue #14064: kept: a regression with an example that worked in 2.13 and fails in 2.14.
- pydantic #14052, issue #14041: kept: a nested class's field docstring overwrites the outer field's description, with
  an example and the correct result at module level.
- black #5634, issue #496: kept: `# fmt: off` still trims a file's trailing empty lines; the expected behaviour (left
  unchanged) is clear.
- black #5521, issue #3424: kept: preview string processing at line length 8 gives "INTERNAL ERROR: Black produced
  different code on the second pass"; expected: stable formatting.
- click #3781, issue #2869: passed over: a request to accept `pathlib.Path` in `edit()`'s type hints, a feature, not
  a bug report.
- click #3769, issue #3571: kept: `progressbar` with `show_pos=True` and `update_min_steps` ends at 14/20 instead of
  20/20, with a reproducer.
- click #3637, issue #2672: passed over: a request for PowerShell completion, a feature.
- click #3678, issue #2819: kept: a parameter named `help` breaks the automatic help option, against the documented
  conflict resolution, with reproducers.
- starlette #3720, issue #3687: kept: `Config` strips an inner quote from a quoted value, with a reproducer and the
  expected value.
- starlette #3718, issue #3689: kept: `build_environ` strips `PATH_INFO` when `root_path` is a prefix but not a path
  boundary, with a reproducer.
- fastapi #15077, issue #15401: kept: an SSE route's `stream_item_type` is lost through `include_router`, so the
  OpenAPI schema omits `data.contentSchema`.
- attrs #1593, issue #1575: kept: a `ClassVar` imported only under `TYPE_CHECKING` is taken for a field on Python
  3.14. The containers run Python 3.12; the validation decides whether a maintainers' test shows the fix there.

## The run order

`tasks.json` lists the ten tasks in the run order, newest merge first, with each one's merge commit, parent commit,
the test modules the validation and the check run, and the install command:

1. black #5634 (issue [#496](https://github.com/psf/black/issues/496), fix [#5634](https://github.com/psf/black/pull/5634)), merged 2026-10-28
2. black #5521 ([#3424](https://github.com/psf/black/issues/3424), [#5521](https://github.com/psf/black/pull/5521)), 2026-10-24
3. pydantic #14065 ([#14064](https://github.com/pydantic/pydantic/issues/14064), [#14065](https://github.com/pydantic/pydantic/pull/14065)), 2026-10-23 13:16
4. starlette #3720 ([#3687](https://github.com/Kludex/starlette/issues/3687), [#3720](https://github.com/Kludex/starlette/pull/3720)), 2026-10-23 12:34
5. starlette #3718 ([#3689](https://github.com/Kludex/starlette/issues/3689), [#3718](https://github.com/Kludex/starlette/pull/3718)), 2026-10-23 12:25
6. pydantic #14052 ([#14041](https://github.com/pydantic/pydantic/issues/14041), [#14052](https://github.com/pydantic/pydantic/pull/14052)), 2026-10-22
7. click #3769 ([#3571](https://github.com/pallets/click/issues/3571), [#3769](https://github.com/pallets/click/pull/3769)), 2026-08-15
8. attrs #1593 ([#1575](https://github.com/python-attrs/attrs/issues/1575), [#1593](https://github.com/python-attrs/attrs/pull/1593)), 2026-07-31
9. fastapi #15077 ([#15401](https://github.com/fastapi/fastapi/issues/15401), [#15077](https://github.com/fastapi/fastapi/pull/15077)), 2026-07-28
10. click #3678 ([#2819](https://github.com/pallets/click/issues/2819), [#3678](https://github.com/pallets/click/pull/3678)), 2026-07-10

At up to $10.50 a run, the $100 stop may end the series before the tenth run; a task not reached is reported as not
run, with why.
