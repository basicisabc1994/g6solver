<!-- SPDX-License-Identifier: Apache-2.0 -->
# Stage 4: the selection record

**Status: drawn on 2026-11-04, before any of its runs.** The rule is PLAN.md's of 2026-11-04 (late morning): stage 2's, applied to the repositories the 2026-10-31 screen kept at PyPI ranks 45 to 100, in rank order, each giving at most its two newest candidates judged to report a bug with a clear expected behaviour (T2), security reports left out. T2 was judged by a model reader on each issue's text alone, never a fix, a test or a run, and its reasons are its own words. Preparation (`images`, `validate`, `selfcheck`, `freecheck`) comes next; a bug that fails it gives way to the next candidate in order, and the result column is filled in then.

Looked at: 45. Kept: 31.

| Rank | Repository | Pull request | Issue | Judgement and reason | Result |
|---:|---|---:|---:|---|---|
| 46 | Kludex/uvicorn | 3236 | 3202 | Kept: Rejected websocket handshake gets no response and connection stays open; 400 expected like legacy implementation. | to prepare |
| 46 | Kludex/uvicorn | 3199 | 3149 | Kept: Pre-bound listening socket loses TCP_NODELAY; option should be enabled as on other paths. | to prepare |
| 48 | python-jsonschema/jsonschema | 1300 | 1257 | Dropped: best_match ordering complaint with alternative proposal; correct expected result is a design question, unclear. | Not a task |
| 50 | aio-libs/yarl | 1852 | 1829 | Kept: URL(str) accepts invalid hosts that URL.build rejects; consistent validation implied. | to prepare |
| 50 | aio-libs/yarl | 1919 | 1821 | Kept: IndexError raised for empty host in host_port_subcomponent; should not crash. | to prepare |
| 53 | python-poetry/tomlkit | 666 | 665 | Dropped: Performance improvement request for array appends; no wrong result. | Not a task |
| 53 | python-poetry/tomlkit | 552 | 546 | Kept: item() reorders dict-valued keys despite sort_keys=False; original order expected. | to prepare |
| 53 | python-poetry/tomlkit | 581 | 580 | Kept: Array.add_line with comment emits invalid TOML that fails to reload; valid output expected. | to prepare |
| 54 | executablebooks/markdown-it-py | 416 | 415 | Kept: IndexError on table inside blockquote at end of input; should parse without exception. | to prepare |
| 54 | executablebooks/markdown-it-py | 391 | 390 | Dropped: Feature request for plugin-registered inline terminator characters. | Not a task |
| 55 | aio-libs/propcache | 302 | 244 | Dropped: CI performance regression note; no concrete wrong behaviour or expected result. | Not a task |
| 60 | open-telemetry/opentelemetry-python | 5854 | 5853 | Kept: Schema-defined container resource detector cannot be loaded in declarative config; should load. | to prepare |
| 60 | open-telemetry/opentelemetry-python | 5823 | 5811 | Kept: W3C baggage extract keeps only first header; all baggage headers should be merged. | to prepare |
| 63 | pypa/pip | 14011 | 8671 | Kept: Invalid use-feature config value breaks every pip command; should be rejected or warned. | to prepare |
| 63 | pypa/pip | 14218 | 12248 | Kept: Build error shows raw traceback without pip's usual wrapped messages; wrapped error expected. | to prepare |
| 65 | pyasn1/pyasn1 | 108 | 107 | Kept: Huge long-form length raises bare OverflowError instead of PyAsn1Error. | to prepare |
| 65 | pyasn1/pyasn1 | 102 | 81 | Kept: asDateTime returns wrong fractional digits; correct value shown in report. | to prepare |
| 68 | python-websockets/websockets | 1752 | 1749 | Kept: Reference cycles keep closed connections alive, growing memory; freeing on close expected. | to prepare |
| 68 | python-websockets/websockets | 1745 | 1608 | Dropped: Upstream CPython serve_forever cancellation change; labelled documentation and blocked, no websockets defect. | Not a task |
| 68 | python-websockets/websockets | 1743 | 816 | Dropped: ValueError on HTTP/1.0 response; whether client should accept it is unclear. | Not a task |
| 68 | python-websockets/websockets | 1738 | 1596 | Kept: Race in sync implementation delays closing; closing should interrupt recv promptly. | to prepare |
| 71 | stub42/pytz | 142 | 108 | Kept: localize() raises OverflowError for 9999-12-31; should return localized datetime. | to prepare |
| 79 | pydantic/pydantic-settings | 1109 | 1107 | Kept: Nested env var not JSON-decoded for nested dict value types; decoded value expected. | to prepare |
| 79 | pydantic/pydantic-settings | 1089 | 1088 | Kept: Union dict value type not JSON-decoded, unlike union fields; same decoding expected. | to prepare |
| 80 | pypa/wheel | 695 | 692 | Kept: wheel tags writes invalid ZIP64 headers for large files; valid archive expected. | to prepare |
| 80 | pypa/wheel | 694 | 570 | Dropped: Enhancement request to add or change local version identifiers. | Not a task |
| 80 | pypa/wheel | 690 | 643 | Dropped: Metadata-Version bump breaks Artifactory; asks whether to change it and for workaround, unclear expectation. | Not a task |
| 80 | pypa/wheel | 686 | 411 | Kept: wheel unpack rejects archive with normalized dist-info name; spec says it should be accepted. | to prepare |
| 81 | jd/tenacity | 668 | 532 | Dropped: Question about pyright typing warnings; no clear expected behaviour. | Not a task |
| 81 | jd/tenacity | 660 | 658 | Kept: retry_if_exception_cause_type spins forever on cyclic cause chain; should return. | to prepare |
| 81 | jd/tenacity | 654 | 526 | Dropped: Optimization request to skip exponential computation once max reached; no wrong result. | Not a task |
| 81 | jd/tenacity | 653 | 519 | Dropped: Statistics on wrapped function; reads as design question or feature suggestion. | Not a task |
| 87 | sqlalchemy/sqlalchemy | 13342 | 12877 | Dropped: Quoting for postgresql_inherits resolved by documenting quoted_name; documentation-labelled, expected behaviour unclear. | Not a task |
| 88 | pypa/hatch | 2473 | 2393 | Kept: Build variant wrongly passed to uv --python-version, failing env lock. | to prepare |
| 88 | pypa/hatch | 2257 | 2110 | Kept: Regression: imports in build hook fail with require-runtime-dependencies; worked in 1.15.1. | to prepare |
| 90 | pyparsing/pyparsing | 653 | 560 | Dropped: match_previous_expr nested tags: vague, reads as capability request. | Not a task |
| 90 | pyparsing/pyparsing | 652 | 332 | Kept: expr[..., upper_bound] raises RecursionError for large bound; should work. | to prepare |
| 90 | pyparsing/pyparsing | 649 | 492 | Kept: QuotedString multiline drops newline in quote sequence; expected output given. | to prepare |
| 95 | fsspec/s3fs | 1065 | 1064 | Kept: cat_file returns whole object or errors for empty ranges; empty result expected. | to prepare |
| 95 | fsspec/s3fs | 1056 | 935 | Kept: glob omits matching path when folder lacks objects; expected paths stated. | to prepare |
| 98 | facelessuser/soupsieve | 316 | 314 | Kept: :has(~ b) matches element after b; expected only earlier siblings. | to prepare |
| 98 | facelessuser/soupsieve | 315 | 313 | Kept: '* > r' matches root element; expected empty result. | to prepare |
| 100 | openai/openai-python | 4166 | 4064 | Kept: responses.parse matches namespaced function call to top-level tool by name alone. | to prepare |
| 100 | openai/openai-python | 3993 | 3962 | Dropped: Typo in an error message; cosmetic wording fix. | Not a task |
| 100 | openai/openai-python | 3982 | 3969 | Kept: ssl.SSLError no longer retried or wrapped since 3.14.1; previous retry behaviour expected. | to prepare |
