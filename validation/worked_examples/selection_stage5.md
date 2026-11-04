<!-- SPDX-License-Identifier: Apache-2.0 -->
# Stage 5: the selection record

**Status: the draw is final, on 2026-11-04, before any of its runs.** 130 bugs from
74 projects. Tasks are drafted (`tasks_stage5.json`, below); next come the blind determinacy audit, then preparation
(`images`, `validate`, `selfcheck`, `freecheck`). A bug that fails preparation gives way with no replacement, as stage 5's
registration says (PLAN.md, "After the draw, in order"); the candidates kept past the stop are not reserves. (Corrected
2026-11-04, night: this line first said a failed bug gives way to the next kept candidate, which is stage 2's and stage 4's
rule, not stage 5's.)

## Tasks drafted

`tasks_stage5.json` holds the 130 in the draw's order, each with the earlier sets' twelve fields. All fields but `install`
and `env` come from the screens. Test modules are the pull request's own test files, by path alone; no test's content, diff,
fix or issue discussion was read. A project an earlier set holds reuses that set's install and env (32 tasks, 18 projects),
except tomlkit and opentelemetry, whose packaging differs at these parents and which were drafted afresh. The rest were
drafted from each project's packaging files at the parent, in the order: a dependency group for tests (37), a test extra (15),
a test requirements file (17), else the named packages the maintainers' tests need (29). 16 tasks set an environment
variable, a pretend version for a build backend that reads git tags. Every group, extra, requirements file and folder an
install line names exists at its parent; no install has been run, so preparation is where a line is shown to work.

What preparation is likely to meet, from the packaging files alone: 80 tasks carry at least one concern, among them GPU or
CUDA tests (6), a Python other than the image's 3.12 (4: private-gpt-2394 needs below 3.12, python-15435 3.15 or later, the
two exo tasks 3.13 only), a non-Python build step (15), very heavy dependencies (22) and services such as Redis, Postgres or
the network (20). deepspeed-8882's pull request changes no test module, only CI files and `tests/conftest.py`, so it has no
maintainers' test to fail at the parent. These are not reasons to leave a bug out before preparation: each stays in the draw,
and one that fails preparation gives way with no replacement, as the registration says.

## The rule

PLAN.md (runtime-k d21f2e5, 3ee4662, 6d080f6, b1f72467): the kept projects of the deeper screen (PyPI ranks 1 to 100), then of
ranks 101 to 250, then of GitHub's Python repositories with 10,000 stars or more in star order. Each project's candidates that
passed the mechanical rules are taken newest first, leaving out any candidate a task set holds (by pull request or issue) or
stage 4's T2 judged. Each project gives at most its two newest candidates judged to report a bug with a clear expected
behaviour (T2), security reports left out. The draw stops at 130.

**Who judged T2.** Claude (Opus) readers, one per batch, on each issue's title and text alone (cut at 6,000 characters), never
on the pull request, its files, a fix, a test or a run. Each gave a one-line reason. 374 candidates were judged in two
rounds; the draw reached 130 without needing a third.

## Candidates judged, in order

"Screen" and "place" are the screen and the project's rank in it. "Result" is where the candidate ended.

| Screen | Place | Repository | Pull request | Issue | Judgement and reason | Result |
|---|---:|---|---:|---:|---|---|
| deeper, ranks 1-100 | 5 | urllib3/urllib3 | 5271 | 5268 | Kept: Gives a pytest command as reproducer; two response tests fail with zlib.error on newer Python 3.14; expects the tests to pass. | **Stage 5** |
| deeper, ranks 1-100 | 5 | urllib3/urllib3 | 5212 | 5211 | Kept: Reproducer shows assert_fingerprint raising binascii.Error for non-hex input; expects SSLError like other invalid fingerprints. States it is not a verification bypass. | **Stage 5** |
| deeper, ranks 1-100 | 5 | urllib3/urllib3 | 3780 | 3779 | Kept: Reproducer with a tell-only body raises a confusing ValueError on retry; expects UnrewindableBodyError. | Kept, after the project's two |
| deeper, ranks 1-100 | 5 | urllib3/urllib3 | 5057 | 3289 | Not kept: Enhancement for gevent compatibility with no reproducer and no concrete wrong result; the reporter leaves the implementation open. | Not a task |
| deeper, ranks 1-100 | 8 | jawah/charset_normalizer | 825 | 824 | Kept: Steps: add Python 3.16 to nox and run tests; tests fail with UnicodeError from utf_7_imap; expects tests to pass. | **Stage 5** |
| deeper, ranks 1-100 | 10 | pypa/setuptools | 5193 | 5205 | Not kept: Feature request to support building abi3t extensions and wheels; no wrong behaviour named. | Not a task |
| deeper, ranks 1-100 | 12 | pygments/pygments | 3171 | 2998 | Kept: Gives the Ruby input and the HTML output; names the wrong result: the SELECT statement appears twice. | **Stage 5** |
| deeper, ranks 1-100 | 12 | pygments/pygments | 3175 | 2975 | Kept: pygmentize commands show escapeinside dropping leading/trailing newlines despite stripnl=false; the wrong output is shown. | **Stage 5** |
| deeper, ranks 1-100 | 12 | pygments/pygments | 3180 | 3170 | Not kept: Asks for transparent/alpha colours to be accepted; reads as a missing-feature request with no reproducer or wrong result. | Not a task |
| deeper, ranks 1-100 | 13 | yaml/pyyaml | 937 | 897 | Not kept: Reports a denial-of-service via exponential merge-key expansion: a security vulnerability, so left out. | Not a task |
| deeper, ranks 1-100 | 16 | pytest-dev/pluggy | 767 | 763 | Kept: Says list_plugin_distinfo still lists plugins after unregister and duplicates on re-register; expects unregister to drop the entries. | **Stage 5** |
| deeper, ranks 1-100 | 16 | pytest-dev/pluggy | 752 | 749 | Not kept: Downstream tests break on a deliberate new None check; what should happen is left open ("a lighter approach"). | Not a task |
| deeper, ranks 1-100 | 16 | pytest-dev/pluggy | 738 | 442 | Not kept: Framed as a question: the reporter asks whether the default-argument behaviour is intended, so expected behaviour is left open. | Not a task |
| deeper, ranks 1-100 | 16 | pytest-dev/pluggy | 741 | 733 | Kept: Says a failed register() leaves the plugin and a valid hook installed; expects failure to leave no plugin mapping or hookimpls. | **Stage 5** |
| deeper, ranks 1-100 | 17 | boto/botocore | 3796 | 3255 | Kept: Reproducer and log show TimestampRange milliseconds dropped (1726138176 vs .5); expects millisecond precision preserved. | **Stage 5** |
| deeper, ranks 1-100 | 22 | agronholm/anyio | 1386 | 1385 | Kept: Reproducer: a cancelled TLSStream.send makes later sends fail with BAD_RECORD_MAC; expects no effect on the stream or the record delivered. | **Stage 5** |
| deeper, ranks 1-100 | 22 | agronholm/anyio | 1367 | 1364 | Kept: Reproducer shows connect_tcp raising TimeoutError while leaving a connected socket open; implies the stream should be closed. | **Stage 5** |
| deeper, ranks 1-100 | 22 | agronholm/anyio | 1363 | 1362 | Kept: Reproducer: Lock.locked() outside an event loop raises NoEventLoopError; expects False, matching statistics().locked. | Kept, after the project's two |
| deeper, ranks 1-100 | 22 | agronholm/anyio | 1360 | 1349 | Not kept: Feature request to add SOCK_SEQPACKET support to Unix listeners; no wrong behaviour named. | Not a task |
| deeper, ranks 1-100 | 31 | aio-libs/aiobotocore | 1789 | 1797 | Not kept: About a compromised CA in the trust store (TLS MitM exposure): a security vulnerability, so left out. | Not a task |
| deeper, ranks 1-100 | 31 | aio-libs/aiobotocore | 1744 | 1743 | Not kept: Bot-generated sync chore asking maintainers design questions; not a bug report with expected behaviour. | Not a task |
| deeper, ranks 1-100 | 31 | aio-libs/aiobotocore | 1709 | 1705 | Not kept: Bot-generated botocore sync chore with open port questions for maintainers; no user-facing bug report with a reproducer. | Not a task |
| deeper, ranks 1-100 | 31 | aio-libs/aiobotocore | 1704 | 1697 | Kept: Reproducer shows the alias create_o_auth2_token missing on async clients, so login_session refresh raises AttributeError; expects refresh to work. | **Stage 5** |
| deeper, ranks 1-100 | 31 | aio-libs/aiobotocore | 1686 | 1169 | Not kept: CI chore: have the tests depend on the built dists. No wrong behaviour named. | Not a task |
| deeper, ranks 1-100 | 31 | aio-libs/aiobotocore | 1667 | 749 | Not kept: Feature request for pluggable HTTP clients and anyio/trio support. No bug described. | Not a task |
| deeper, ranks 1-100 | 31 | aio-libs/aiobotocore | 1539 | 1365 | Not kept: Design discussion about defining the StreamingBody API across backends. No wrong result named. | Not a task |
| deeper, ranks 1-100 | 31 | aio-libs/aiobotocore | 1587 | 1469 | Kept: Code and traceback show load_verify_locations as a blocking call on the event loop during head_bucket. Expects it to run off the loop. | **Stage 5** |
| deeper, ranks 1-100 | 36 | theskumar/python-dotenv | 615 | 525 | Not kept: Feature request to support ~ expansion in dotenv_path ("It would be great"); no wrong behaviour named. | Not a task |
| deeper, ranks 1-100 | 36 | theskumar/python-dotenv | 698 | 697 | Kept: Reproducer shows --no-override yielding file/suffix; expects environment/suffix, matching load_dotenv(override=False). | **Stage 5** |
| deeper, ranks 1-100 | 36 | theskumar/python-dotenv | 680 | 661 | Kept: Reproducer: set_key with C:\Users reads back as C:Users; values containing backslashes should round-trip. | **Stage 5** |
| deeper, ranks 1-100 | 37 | jpadilla/pyjwt | 1208 | 914 | Kept: Says get_jwk_set fails its dict check when keys come from the cache; says it should return the cached object. | **Stage 5** |
| deeper, ranks 1-100 | 38 | tox-dev/platformdirs | 661 | 659 | Kept: Example shows user_documents_dir reading another user's XDG config as root and creating it; expects /root/Documents and nothing created. | **Stage 5** |
| deeper, ranks 1-100 | 38 | tox-dev/platformdirs | 663 | 658 | Kept: Reproducer shows media dir lookup taking quadratic time on long blank runs (stalls at 300k); a wrong behaviour, stated as not a security issue. | **Stage 5** |
| deeper, ranks 1-100 | 38 | tox-dev/platformdirs | 664 | 660 | Kept: Command shows a relative HOME creating dirs under the working directory; expects it ignored like relative XDG values or an empty HOME. | Kept, after the project's two |
| deeper, ranks 1-100 | 38 | tox-dev/platformdirs | 662 | 657 | Not kept: Describes bypassing the base-directory containment check ("/etc/evil") via os.pathsep; framed as a security issue, so left out. | Not a task |
| deeper, ranks 1-100 | 39 | fsspec/filesystem_spec | 2304 | 2303 | Not kept: Performance wish to speed up TAR find/glob; no wrong result named. | Not a task |
| deeper, ranks 1-100 | 39 | fsspec/filesystem_spec | 2352 | 1586 | Kept: Commands show the -f flag inverted relative to --help, and with -f the mount hangs; behaviour should match the help text. | **Stage 5** |
| deeper, ranks 1-100 | 39 | fsspec/filesystem_spec | 2344 | 933 | Not kept: Documentation-only change adding a link; not a bug report. | Not a task |
| deeper, ranks 1-100 | 39 | fsspec/filesystem_spec | 2312 | 2311 | Kept: Reproducer shows the archive missing after close() and present only after GC; expects it published on close(). | **Stage 5** |
| deeper, ranks 1-100 | 43 | cpburnz/python-pathspec | 162 | 161 | Kept: Reproducer: a custom Pattern raises TypeError under the default backend when RE2/Hyperscan is installed; expects the custom matching kept. | **Stage 5** |
| deeper, ranks 1-100 | 43 | cpburnz/python-pathspec | 132 | 129 | Kept: Reproducer: match_file("build/keep.log") returns False; says it should be ignored, as git confirms. | **Stage 5** |
| deeper, ranks 1-100 | 44 | tox-dev/filelock | 880 | 879 | Kept: Reproducer shows ReadWriteLock silently None then an unrelated TypeError; expects an import error naming the missing module. | **Stage 5** |
| deeper, ranks 1-100 | 44 | tox-dev/filelock | 861 | 860 | Not kept: Framed as a request to add Python 3.15 support; shows a CI log but gives no reproducer steps or stated expected behaviour. | Not a task |
| deeper, ranks 1-100 | 44 | tox-dev/filelock | 827 | 770 | Kept: Read locks raise "no such table: sqlite_schema" on SQLite < 3.33, shown with the sqlite CLI; they should work there. | **Stage 5** |
| deeper, ranks 1-100 | 44 | tox-dev/filelock | 820 | 818 | Kept: Actual: opening via /dev/fd panicked the macOS kernel; expected: acquire avoids the /dev/fd lookup or docs say it uses it. No reproducer, but both are stated. | Kept, after the project's two |
| deeper, ranks 1-100 | 46 | Kludex/uvicorn | 3103 | 3074 | Kept: Reproducer and table show Connection: Close not closing or echoed; expects close and echo for any close token regardless of case. | **Stage 5** |
| deeper, ranks 1-100 | 46 | Kludex/uvicorn | 3041 | 3040 | Kept: Reproducer shows websockets-sansio emitting duplicate Content-Type/Content-Length; expects only the application's headers. | **Stage 5** |
| deeper, ranks 1-100 | 46 | Kludex/uvicorn | 3036 | 3035 | Not kept: Upstream compatibility notice listing several fix options; no reproducer, and the expected uvicorn behaviour is left open. | Not a task |
| deeper, ranks 1-100 | 53 | python-poetry/tomlkit | 610 | 336 | Not kept: Feature request to emit underscore-formatted integers; no wrong behaviour named. | Not a task |
| deeper, ranks 1-100 | 53 | python-poetry/tomlkit | 620 | 619 | Kept: Test run reproduces TypeError on replace() for the missing fold argument; expects tomlkit date/time types to accept fold like the stdlib types do. | **Stage 5** |
| deeper, ranks 1-100 | 53 | python-poetry/tomlkit | 563 | 562 | Kept: Snippet gives TypeError 'Float' has no len() on 0.15.0, not on 0.14.0; expects an array of floats to act like a Python list. | **Stage 5** |
| deeper, ranks 1-100 | 53 | python-poetry/tomlkit | 550 | 543 | Kept: Snippet shows dumps puts z under [a.c], so it is no longer a top-level key; names the wrong output and the meaning it should keep. | Kept, after the project's two |
| deeper, ranks 1-100 | 60 | open-telemetry/opentelemetry-python | 5656 | 5655 | Kept: Repro: export_timeout_millis=-1 accepted; expects ValueError, as export_interval_millis already does. | **Stage 5** |
| deeper, ranks 1-100 | 60 | open-telemetry/opentelemetry-python | 5650 | 5648 | Kept: Repro: BatchSpanProcessor accepts export_timeout_millis=-500; expects ValueError like the other validated arguments. | **Stage 5** |
| deeper, ranks 1-100 | 60 | open-telemetry/opentelemetry-python | 5286 | 5268 | Not kept: Typing refactor for strict pyright mode; no wrong runtime behaviour reported. | Not a task |
| deeper, ranks 1-100 | 60 | open-telemetry/opentelemetry-python | 5776 | 5775 | Kept: Repro shows the oldest attributes dropped when the limit is hit; expected output keeps them and drops the new one, per the spec. | Kept, after the project's two |
| deeper, ranks 1-100 | 63 | pypa/pip | 14231 | 9456 | Kept: Says 'pip wheel . --version' starts building a wheel; expects it to show the version or fail on an unrecognised option. | **Stage 5** |
| deeper, ranks 1-100 | 63 | pypa/pip | 14228 | 14227 | Kept: Reproducer script: pip wheel with an extra index URL fails on 26.2 but works on 26.1.2. A named regression, though the expected field is blank. | **Stage 5** |
| deeper, ranks 1-100 | 63 | pypa/pip | 14220 | 8573 | Kept: Steps install a wheel with a bad entrypoint and show a raw ValueError traceback; expects a cleaner error message. | Kept, after the project's two |
| deeper, ranks 1-100 | 63 | pypa/pip | 14217 | 13084 | Kept: Steps show the zipapp's outdated check comparing the venv's pip; expects the notice to reflect the zipapp's own version. | Kept, after the project's two |
| deeper, ranks 1-100 | 68 | python-websockets/websockets | 1736 | 1488 | Not kept: Near-empty text reading as an enhancement ('should behave like the asyncio server'); current behaviour not described. | Not a task |
| deeper, ranks 1-100 | 68 | python-websockets/websockets | 1732 | 1489 | Not kept: Only two status-code links; no behaviour described and no wrong result named. | Not a task |
| deeper, ranks 1-100 | 68 | python-websockets/websockets | 1730 | 1466 | Not kept: Feature request for a sync broadcast utility; no wrong behaviour named. | Not a task |
| deeper, ranks 1-100 | 68 | python-websockets/websockets | 1719 | 1694 | Not kept: Feature request for a text option on broadcast(); no wrong behaviour named. | Not a task |
| deeper, ranks 1-100 | 79 | pydantic/pydantic-settings | 1077 | 1066 | Kept: Example gives NameError for a discriminated-union dataclass default; expects the same value the plain field gives, as on 2.15.0. | **Stage 5** |
| deeper, ranks 1-100 | 79 | pydantic/pydantic-settings | 1076 | 1065 | Kept: Example shows the Field(discriminator=...) form keeping the default member's fields; expects WhiteCat, as the Discriminator form gives. | **Stage 5** |
| deeper, ranks 1-100 | 79 | pydantic/pydantic-settings | 1069 | 1062 | Kept: Script prints observed and expected values: fixed tuple positionals are treated as variadic, giving errors or wrong values. Worked on 2.15.0. | Kept, after the project's two |
| deeper, ranks 1-100 | 79 | pydantic/pydantic-settings | 1049 | 1046 | Kept: Script shows a JSON list of dicts merged into one dict and rejected; expects the list, as the env source gives. | Kept, after the project's two |
| deeper, ranks 1-100 | 88 | pypa/hatch | 2390 | 2389 | Kept: Names the error '--python option must be placed before' on env creation after the 1.18 upgrade; expects it to work as it did before. | **Stage 5** |
| deeper, ranks 1-100 | 88 | pypa/hatch | 2398 | 2384 | Kept: Example: version 2026.08.10 is written to METADATA as 2026.8.10; expects the version kept as written, like the name. Called an oversight. | **Stage 5** |
| deeper, ranks 1-100 | 88 | pypa/hatch | 2412 | 2413 | Not kept: Feature request ('It would be useful') for context formatting in lock-filename. | Not a task |
| deeper, ranks 1-100 | 88 | pypa/hatch | 2397 | 2402 | Kept: Example input gives the env var HATCH_ENV_TYPE_PIP-COMPILE_UV_PATH with a dash; names the incorrect formatting. | Kept, after the project's two |
| deeper, ranks 1-100 | 90 | pyparsing/pyparsing | 644 | 496 | Kept: Reproducer shows a \r overwriting the debug text; parsing is right but the debug output is wrong. | **Stage 5** |
| deeper, ranks 1-100 | 95 | fsspec/s3fs | 1047 | 838 | Kept: rm returns [] and deletes nothing, while rm_file shows PermissionError; expects rm to delete everything or raise. | **Stage 5** |
| deeper, ranks 1-100 | 95 | fsspec/s3fs | 1042 | 982 | Kept: Says concurrent find/glob sometimes returns incomplete listings (89k of 90k); expects retries or a raised error instead. | **Stage 5** |
| deeper, ranks 1-100 | 95 | fsspec/s3fs | 1041 | 1011 | Kept: Minimal moto reproducer: a KeyError 'response' in _error_wrapper hides the real error; expects a guard so the original error surfaces. | Kept, after the project's two |
| deeper, ranks 1-100 | 98 | facelessuser/soupsieve | 310 | 309 | Kept: Repro: [class!=...] returns 2 on 3.0, expected 3 as on 2.10; elements without the attribute are dropped. | **Stage 5** |
| deeper, ranks 1-100 | 98 | facelessuser/soupsieve | 308 | 307 | Kept: css_unescape('\\d800') returns U+D800 and \\110000 raises ValueError; both should give U+FFFD. | **Stage 5** |
| deeper, ranks 1-100 | 100 | openai/openai-python | 3875 | 3874 | Kept: MockTransport repro: a single-file zip is sent as empty JSON; expects multipart with the zip, as the list form produces. | **Stage 5** |
| deeper, ranks 1-100 | 100 | openai/openai-python | 3851 | 3840 | Kept: Repro: content null raises TypeError 'NoneType' not iterable in parse_response; expects null treated as empty. | **Stage 5** |
| deeper, ranks 1-100 | 100 | openai/openai-python | 3088 | 3084 | Kept: Says repeated responses.parse() calls leak memory without bound, growing until OOM; expects the memory freed. Wrong behaviour named, not just speed. | Kept, after the project's two |
| deeper, ranks 1-100 | 100 | openai/openai-python | 3861 | 3859 | Kept: Offline repro: plain-text commentary raises ValidationError json_invalid; expects the final_answer message used as the structured result. | Kept, after the project's two |
| ranks 101-250 | 101 | giampaolo/psutil | 2969 | 2512 | Kept: cpu_freq(True) returns one entry while cpu_count() returns 4 on this arm64 platform; a regression of a fixed issue, expects one entry per CPU. | **Stage 5** |
| ranks 101-250 | 101 | giampaolo/psutil | 2965 | 2964 | Kept: net_if_addrs() gives the host IP as the broadcast for a /32 address, with a failing test; expects None. | **Stage 5** |
| ranks 101-250 | 101 | giampaolo/psutil | 2890 | 2888 | Kept: A clock update causes a false NoSuchProcess 'PID has been reused' on FreeBSD/OpenBSD, with CI traces; expects no false reuse error. | Kept, after the project's two |
| ranks 101-250 | 101 | giampaolo/psutil | 2810 | 2809 | Kept: virtual_memory() fails when the meminfo line has no space after ShadowCallStack:; expects the line parsed correctly. | Kept, after the project's two |
| ranks 101-250 | 116 | modelcontextprotocol/python-sdk | 3776 | 3773 | Kept: Example shows extensions dropped from legacy initialize serialization; expects capabilities.extensions kept, as for 2026-07-28. | **Stage 5** |
| ranks 101-250 | 116 | modelcontextprotocol/python-sdk | 3758 | 3725 | Kept: Repro: '-> dict' gives structured_content None; expects it to behave like dict[str, Any]. | **Stage 5** |
| ranks 101-250 | 116 | modelcontextprotocol/python-sdk | 3756 | 3745 | Kept: Repro output: typing.Dict is wrapped in {"result": ...} but dict[str, Any] is not; expects both spellings to behave the same. | Kept, after the project's two |
| ranks 101-250 | 116 | modelcontextprotocol/python-sdk | 3726 | 3701 | Kept: Repro shows appended headers piling up across SSE responses (1,2,3,4,5); expects each response to get its own header list. | Kept, after the project's two |
| ranks 101-250 | 140 | redis/redis-py | 4513 | 4512 | Kept: Repro: a transaction on a lazy cluster client leads to 'Event loop is closed' errors on the next ping; names the wrong result clearly. | **Stage 5** |
| ranks 101-250 | 140 | redis/redis-py | 4528 | 4268 | Not kept: Asks whether to send ZRANGE REV instead of the deprecated ZREVRANGE; a design question with options listed, no wrong result. | Not a task |
| ranks 101-250 | 140 | redis/redis-py | 4425 | 4423 | Not kept: Performance report on decorator overhead per command; no functional wrong result named. | Not a task |
| ranks 101-250 | 140 | redis/redis-py | 4486 | 4478 | Kept: Repro: a MOVED retry drops the WATCH and EXEC commits; expects WatchError instead of the concurrent write being lost. | **Stage 5** |
| ranks 101-250 | 211 | pre-commit/pre-commit | 3774 | 3773 | Kept: Docker reproducer shows the submodule index replaced by the hook repo's files and the commit failing; expects the index to keep its own staged files. | **Stage 5** |
| ranks 101-250 | 211 | pre-commit/pre-commit | 3743 | 3737 | Kept: Config plus `pre-commit run --all-files` makes the node hook install fail with CalledProcessError, and later runs say the executable is not found; names the wrong result. | **Stage 5** |
| ranks 101-250 | 211 | pre-commit/pre-commit | 3727 | 3706 | Kept: Step-by-step reproducer shows a conflicted file passed three times to the hook; expects the file list to have no duplicates. | Kept, after the project's two |
| ranks 101-250 | 211 | pre-commit/pre-commit | 3719 | 3517 | Not kept: Chore: asks to remove a deprecated npm option; only an npm deprecation warning is named, with no reproducer or wrong result in this text. | Not a task |
| 10,000 stars | 7 | TheAlgorithms/Python | 15435 | 15434 | Kept: Reproducer with expected and actual output: the heap-sort fallback sorts the whole list instead of only the start/end range. | **Stage 5** |
| 10,000 stars | 7 | TheAlgorithms/Python | 12056 | 12043 | Not kept: Feature request to add a new algorithm; no wrong behaviour named. | Not a task |
| 10,000 stars | 7 | TheAlgorithms/Python | 15240 | 15234 | Not kept: Umbrella tracking issue for typing and test refactors across the sorts; not a bug report with a wrong result. | Not a task |
| 10,000 stars | 7 | TheAlgorithms/Python | 15238 | 15234 | Not kept: Duplicate umbrella tracking issue for typing and test refactors; not a bug report with a wrong result. | Not a task |
| 10,000 stars | 9 | microsoft/markitdown | 2450 | 2449 | Not kept: Performance report: the issue says the output is correct and only the time grows quadratically; no wrong result named. | Not a task |
| 10,000 stars | 9 | microsoft/markitdown | 2412 | 2411 | Kept: Reproducer shows CR-only CSV comes back as plain text (and the converter raises csv.Error); expects the same Markdown table as LF/CRLF. | **Stage 5** |
| 10,000 stars | 9 | microsoft/markitdown | 2430 | 2390 | Kept: Repro steps: header line is a one-field preamble, so data columns are silently dropped; expects no silent truncation (warning or wider header). | **Stage 5** |
| 10,000 stars | 9 | microsoft/markitdown | 2268 | 1982 | Kept: Says conversion crashes with NotImplementedError on functions missing from the FUNC dict; expects a fallback that renders the name as plain text. | Kept, after the project's two |
| 10,000 stars | 20 | nextlevelbuilder/ui-ux-pro-max-skill | 548 | 547 | Kept: Commands given; '3d'/'2xl' read as old versions return only deprecated rows or none; expects the same active guidance as without the number. | **Stage 5** |
| 10,000 stars | 20 | nextlevelbuilder/ui-ux-pro-max-skill | 532 | 529 | Kept: Commands plus expected vs actual for each item: JPEG saved under .png, exit 0 on failure, retired model id. | **Stage 5** |
| 10,000 stars | 20 | nextlevelbuilder/ui-ux-pro-max-skill | 526 | 525 | Kept: Exact command; expects an SVG written, but gets 'No valid SVG generated' and no file, because thinking tokens use up the output limit. | Kept, after the project's two |
| 10,000 stars | 20 | nextlevelbuilder/ui-ux-pro-max-skill | 501 | 496 | Kept: Shell repros: an existing Tailwind config is silently overwritten, and the sync script crashes with ENOENT; expects a refusal without --force, and mkdir. | Kept, after the project's two |
| 10,000 stars | 21 | Shubhamsaboo/awesome-llm-apps | 1204 | 1200 | Not kept: Request to change the default model or make it configurable; no wrong behaviour named. | Not a task |
| 10,000 stars | 22 | Graphify-Labs/graphify | 1341 | 1331 | Not kept: Feature follow-up to add .psd1 manifest parsing and new import-edge kinds; acceptance criteria for new support, not a reported wrong result. | Not a task |
| 10,000 stars | 22 | Graphify-Labs/graphify | 1323 | 1322 | Kept: Minimal JS probe plus extract_js output shows the listed function forms missing from the nodes; expects them extracted as symbols. | **Stage 5** |
| 10,000 stars | 22 | Graphify-Labs/graphify | 1176 | 1175 | Not kept: Offer to contribute a new database backend; feature proposal with no wrong behaviour. | Not a task |
| 10,000 stars | 22 | Graphify-Labs/graphify | 1282 | 1277 | Kept: Steps given: cluster-only ignores the model used by extract, auto-picks another model and hits 429s; expects a --model flag or reuse of the extract model. | **Stage 5** |
| 10,000 stars | 24 | harry0703/MoneyPrinterTurbo | 1645 | 1642 | Not kept: Framed as a question about why the model connection test fails; provider setup and errors are only in screenshots, too vague to say what is wrong. | Not a task |
| 10,000 stars | 24 | harry0703/MoneyPrinterTurbo | 1585 | 1584 | Kept: Says the helper with --video-source local fails with a cli.py error because --video-materials is never passed; expects it detected, validated early or handled. | **Stage 5** |
| 10,000 stars | 24 | harry0703/MoneyPrinterTurbo | 1390 | 1382 | Not kept: Feature request for reference-audio voice cloning; no wrong behaviour named. | Not a task |
| 10,000 stars | 24 | harry0703/MoneyPrinterTurbo | 1330 | 1320 | Not kept: Feature request for pause tags in scripts; no wrong behaviour named. | Not a task |
| 10,000 stars | 24 | harry0703/MoneyPrinterTurbo | 1296 | 1295 | Kept: A 200 response with a non-image body raises UnidentifiedImageError and fails the whole task. Expects that keyword to be skipped and the task to go on. | **Stage 5** |
| 10,000 stars | 24 | harry0703/MoneyPrinterTurbo | 1243 | 1155 | Not kept: Feature request for batch video creation from a file. No wrong behaviour named. | Not a task |
| 10,000 stars | 24 | harry0703/MoneyPrinterTurbo | 1259 | 1258 | Kept: Steps show that opening Settings rewrites both upload_post keys to false. Expects config.toml to stay unchanged. | Kept, after the project's two |
| 10,000 stars | 24 | harry0703/MoneyPrinterTurbo | 1209 | 1204 | Not kept: Feature request for Upload-Post controls in the WebUI settings. No bug named. | Not a task |
| 10,000 stars | 26 | browser-use/browser-use | 5552 | 5539 | Kept: Repro steps; the CLI launcher is blocked with os error 4551 under Smart App Control; expects it to work, or to fail early with a clear diagnostic. | **Stage 5** |
| 10,000 stars | 26 | browser-use/browser-use | 5640 | 5637 | Kept: Failing test and steps: mixed-case 'Display: None' code text appears in the extracted Markdown; expects it filtered like the lowercase form. | **Stage 5** |
| 10,000 stars | 26 | browser-use/browser-use | 5676 | 5658 | Kept: Failing code and output: image URLs with a query, fragment or uppercase scheme are rejected as unsupported; expects them accepted. | Kept, after the project's two |
| 10,000 stars | 26 | browser-use/browser-use | 5636 | 5632 | Kept: Failing test: uppercase media type is mislabeled image/jpeg and uppercase scheme becomes a URL source; expects a png base64 source. | Kept, after the project's two |
| 10,000 stars | 28 | Panniantong/Agent-Reach | 326 | 294 | Kept: Doctor's suggested install command fails with 'No matching distribution'; expects a hint that actually installs. | **Stage 5** |
| 10,000 stars | 37 | 3b1b/manim | 2501 | 2336 | Kept: Code reproducer raises ValueError on a shape mismatch in rotate_vector inside VectorField; says it worked in past versions. | **Stage 5** |
| 10,000 stars | 39 | D4Vinci/Scrapling | 406 | 394 | Kept: Expected: MCP server starts and lists its tools. Actual: a browserforge ValueError at import on macOS; steps given. | **Stage 5** |
| 10,000 stars | 39 | D4Vinci/Scrapling | 374 | 366 | Kept: Reproduction command and error: the get tool fails on pages containing control characters; expects the content handled (sanitized). | **Stage 5** |
| 10,000 stars | 44 | unclecode/crawl4ai | 2077 | 2071 | Kept: Reproducer shows blocked tasks still alive after aclose(); expected: all dispatcher tasks cancelled and awaited when the stream closes. | **Stage 5** |
| 10,000 stars | 55 | shareAI-lab/learn-claude-code | 603 | 577 | Kept: Prompt sequence given; after snip_compact the agent answers the old task; expects the active request to stay visible to the model. | **Stage 5** |
| 10,000 stars | 55 | shareAI-lab/learn-claude-code | 582 | 581 | Not kept: Design proposal to split trigger and target thresholds to cut repeated archive writes; an efficiency concern with defaults left open. | Not a task |
| 10,000 stars | 55 | shareAI-lab/learn-claude-code | 571 | 570 | Kept: Repro steps and output: a sibling branch's success hits 'I/O operation on closed file' and is not saved; expects results saved before the journal closes. | **Stage 5** |
| 10,000 stars | 55 | shareAI-lab/learn-claude-code | 569 | 567 | Kept: Repro command: an empty line exits silently even though the banner documents only q; expects a quit message or no exit on an empty line. | Kept, after the project's two |
| 10,000 stars | 62 | rohitg00/ai-engineering-from-scratch | 690 | 508 | Kept: Says the generator writes cp1252 bytes (em dash as 0x97) on Windows; expects UTF-8 output whatever the locale. | **Stage 5** |
| 10,000 stars | 62 | rohitg00/ai-engineering-from-scratch | 503 | 502 | Kept: Reproducer with expected vs actual: a URL with balanced parentheses is truncated at '(85', causing a false 404. | **Stage 5** |
| 10,000 stars | 62 | rohitg00/ai-engineering-from-scratch | 511 | 510 | Kept: Calculator prints wrong totals (e.g. Llama 3 8B as 7.5B against a published 8,030,261,248); names each miscount and the correct value. | Kept, after the project's two |
| 10,000 stars | 64 | hiyouga/LlamaFactory | 10632 | 10610 | Kept: Config reproducer raises AttributeError in flash_attention_forward with transformers 5.6.0. | **Stage 5** |
| 10,000 stars | 64 | hiyouga/LlamaFactory | 10518 | 9704 | Not kept: Mainly a question about whether multi-turn video is supported; no reproducer config, and the error is only in a screenshot. | Not a task |
| 10,000 stars | 69 | usestrix/strix | 1495 | 1494 | Not kept: Feature request for an LLM_EXTRA_BODY setting; no wrong behaviour named. | Not a task |
| 10,000 stars | 69 | usestrix/strix | 1324 | 1312 | Not kept: Feature request for a flag to force the responses or chat completions API; the tool-call failure is vague and possibly a config issue. | Not a task |
| 10,000 stars | 69 | usestrix/strix | 1192 | 1171 | Kept: Gives steps where mismatched tags in a finding make PDF generation raise a ValueError; expects the text to render literally without failing. | **Stage 5** |
| 10,000 stars | 69 | usestrix/strix | 1116 | 1109 | Kept: Says a non-object run.json makes --resume raise an uncaught TypeError; expects the same clean error already shown for unreadable JSON. | **Stage 5** |
| 10,000 stars | 70 | hugohe3/ppt-master | 324 | 323 | Kept: Reproducer shows /api/slide returns a prefixed ns0:svg and the preview is blank; expects the default SVG namespace and a rendered slide. | **Stage 5** |
| 10,000 stars | 76 | docling-project/docling | 4933 | 4931 | Not kept: Feature request to add double and triple pass modes to a VLM preset; names no wrong behaviour. | Not a task |
| 10,000 stars | 76 | docling-project/docling | 4596 | 4595 | Kept: Reproducer shows YAML frontmatter output as a spurious section header; expects metadata not to appear as a heading. | **Stage 5** |
| 10,000 stars | 76 | docling-project/docling | 4709 | 4708 | Not kept: Framed as a requested feature: a new opt-in orientation option for RapidOCR, not a report of broken existing behaviour. | Not a task |
| 10,000 stars | 76 | docling-project/docling | 4813 | 4793 | Kept: Reproducer with input and code; names the wrong result: the ext-link inside a citation loses its hyperlink target and survives only as text. | **Stage 5** |
| 10,000 stars | 77 | ai-job-search-dev/ai-job-search | 686 | 685 | Kept: Reproducer shows fetch_page.py crashing with UnicodeEncodeError under cp1252; expects streams forced to UTF-8 so pages print cleanly. | **Stage 5** |
| 10,000 stars | 77 | ai-job-search-dev/ai-job-search | 668 | 655 | Kept: Measured reproduction: the layout check exits 2 'skipped' on Git's xpdf even though Poppler is installed; names the wrong binary choice. | **Stage 5** |
| 10,000 stars | 77 | ai-job-search-dev/ai-job-search | 602 | 594 | Kept: Reproducer shows a BOM-prefixed salary_data.json makes every command fail with exit 1; expects it to load like plain UTF-8. | Kept, after the project's two |
| 10,000 stars | 77 | ai-job-search-dev/ai-job-search | 564 | 534 | Kept: Says failed fetches are marked 'expired', which is terminal and never retried; expects a distinct status that stays eligible for retry. | Kept, after the project's two |
| 10,000 stars | 78 | mem0ai/mem0 | 7506 | 7503 | Not kept: Security vulnerability: a service account private key is written to DEBUG logs. Excluded by the rule. | Not a task |
| 10,000 stars | 78 | mem0ai/mem0 | 6559 | 6557 | Kept: Says with euclidean_squared a $dist of 4.0 gives score -3.0; expects a metric-aware, bounded, higher-is-better score. | **Stage 5** |
| 10,000 stars | 78 | mem0ai/mem0 | 6547 | 6546 | Kept: Says euclidean distances above 1 all clamp to score 0.0, so ranking is lost; expects a metric-dependent bounded mapping. | **Stage 5** |
| 10,000 stars | 78 | mem0ai/mem0 | 6369 | 6368 | Kept: Repro steps: a reasoningContent block placed first makes parsing raise KeyError, then a generic RuntimeError; expects the content blocks to be iterated to find the text. | Kept, after the project's two |
| 10,000 stars | 80 | labmlai/annotated_deep_learning_paper_implementations | 326 | 245 | Kept: Thin, but gives a reproducer (run ppo experiment.py) and names the wrong result: a cv2.error in cvtColor at a cited line. | **Stage 5** |
| 10,000 stars | 81 | ZhuLinsen/daily_stock_analysis | 2416 | 2415 | Not kept: Feature request for an optional security middleware; names no wrong behaviour. | Not a task |
| 10,000 stars | 81 | ZhuLinsen/daily_stock_analysis | 2379 | 2349 | Kept: Says each watched stock's dashboard is now pushed twice; steps name two scheduler processes running without a per-stock lock. | **Stage 5** |
| 10,000 stars | 81 | ZhuLinsen/daily_stock_analysis | 2368 | 2352 | Kept: Says that with REPORT_LANGUAGE=en the stock dashboard report still comes back in Chinese on a local model; expects English output. | **Stage 5** |
| 10,000 stars | 81 | ZhuLinsen/daily_stock_analysis | 2361 | 2354 | Kept: Steps and log show chip distribution always fails because the packaged mini_racer.dll is missing; expects the feature to work, as shown by a manual fix. | Kept, after the project's two |
| 10,000 stars | 83 | mvanhorn/last30days-skill | 1336 | 1335 | Kept: Says the YouTube lane times out for every entity in comparison mode; expects some YouTube results per entity within the existing limits. | **Stage 5** |
| 10,000 stars | 83 | mvanhorn/last30days-skill | 1325 | 1290 | Kept: Says person mode kept a cluster about a different person and an unrelated X account; expects disambiguation to exclude both. | **Stage 5** |
| 10,000 stars | 83 | mvanhorn/last30days-skill | 1253 | 1020 | Kept: Says the reels search 404s for every query and user/reels items stay wrapped, so all are dropped; expects them unwrapped plus a working fallback. | Kept, after the project's two |
| 10,000 stars | 83 | mvanhorn/last30days-skill | 1291 | 1289 | Kept: Says an Instagram HTTP 404 is reported as 'silent' in quality guidance; expects it described consistently as failed or unavailable. | Kept, after the project's two |
| 10,000 stars | 85 | scrapy/scrapy | 8386 | 8297 | Kept: Reproducer: a path disallowed by robots.txt is fetched with 200 on the second concurrent request; expects every waiter to get the parser, or the request blocked. | **Stage 5** |
| 10,000 stars | 85 | scrapy/scrapy | 7776 | 5828 | Kept: Steps: setting an HTTPS proxy in meta makes every request time out; expects a successful connection and response, as with curl. | **Stage 5** |
| 10,000 stars | 85 | scrapy/scrapy | 7801 | 1615 | Not kept: Asks 'Do you think that it is a bug?' about 201 responses not being saved; expected behaviour is left as a question. | Not a task |
| 10,000 stars | 85 | scrapy/scrapy | 7915 | 2744 | Not kept: An enhancement idea to deduplicate pending cache requests; names no wrong result. | Not a task |
| 10,000 stars | 87 | jingyaogong/minimind | 883 | 878 | Kept: Minimal repro: in streaming mode a tool call is emitted in both delta.content and delta.tool_calls; expects it only in delta.tool_calls. | **Stage 5** |
| 10,000 stars | 90 | virattt/ai-hedge-fund | 719 | 718 | Kept: Reproducer: placing an order with a NaN price succeeds and sets cash to NaN; expects a non-finite price to be rejected before state changes. | **Stage 5** |
| 10,000 stars | 90 | virattt/ai-hedge-fund | 721 | 720 | Not kept: Discussion and proposal about model recall biasing backtests; names no defect where the code gives a wrong result. | Not a task |
| 10,000 stars | 92 | keras-team/keras | 24055 | 23251 | Kept: Reproducer shows weights restored from OrbaxCheckpoint differ from the saved weights; expects an exact round trip. | **Stage 5** |
| 10,000 stars | 92 | keras-team/keras | 23963 | 23239 | Not kept: Near-empty feature request to add Mosaic augmentation; no wrong behaviour named. | Not a task |
| 10,000 stars | 92 | keras-team/keras | 23782 | 23780 | Kept: Reproducer: LayerNormalization on large constant rows returns 0; expects beta (7.0). | **Stage 5** |
| 10,000 stars | 92 | keras-team/keras | 23938 | 23937 | Kept: Reproducer: on TensorFlow, float32 4.2 // 1.4 gives 3.0; expects 2.0, as on JAX, PyTorch and NumPy. | Kept, after the project's two |
| 10,000 stars | 99 | crewAIInc/crewAI | 8678 | 8679 | Kept: Steps show tool-call turns emit no completion event and structured output reports usage=None; expects usage on every call. | **Stage 5** |
| 10,000 stars | 99 | crewAIInc/crewAI | 8642 | 8641 | Kept: Steps show the async cleanup's synchronous list_files freezes a ticking coroutine; expects the listing to run off the event loop without blocking. | **Stage 5** |
| 10,000 stars | 99 | crewAIInc/crewAI | 8648 | 8647 | Kept: Reproducer: a guardrail returning (False, 42) leaves a started event with no completed event; expects a completed event with success=False. | Kept, after the project's two |
| 10,000 stars | 99 | crewAIInc/crewAI | 8215 | 8214 | Kept: Reproducer: suppress_logging leaves the level at 40 after an exception and breaks NOTSET; expects the original level restored. | Kept, after the project's two |
| 10,000 stars | 100 | MemPalace/mempalace | 2744 | 2727 | Not kept: Performance proposal: a delete scans the FTS table and is slow; results are correct, so no wrong result is named. | Not a task |
| 10,000 stars | 100 | MemPalace/mempalace | 2717 | 2709 | Kept: Reproducer: after rebuild-index, mempalace_embedder.json is gone and every open warns; expects the sidecar kept or rewritten. | **Stage 5** |
| 10,000 stars | 100 | MemPalace/mempalace | 2632 | 2618 | Kept: Steps: after kg_add the mine lock handle is never released and mining fails; expects the lock released once the write finishes. | **Stage 5** |
| 10,000 stars | 100 | MemPalace/mempalace | 2699 | 2676 | Not kept: Feature request (problem, proposal, alternatives) to accept a mempalace.yml filename, not a report of broken behaviour. | Not a task |
| 10,000 stars | 103 | ayghri/i-have-adhd | 57 | 53 | Kept: Says the unmetered-runner error is raised only after a paid call, wasting it; expects a pre-flight failure before any call. | **Stage 5** |
| 10,000 stars | 103 | ayghri/i-have-adhd | 48 | 44 | Kept: Reproducer: the release gate reports passed true when baseline and candidate were scored on different cases; expects it to raise. | **Stage 5** |
| 10,000 stars | 105 | jamiepine/voicebox | 1130 | 434 | Kept: Says offline startup retries every config file 5 times and Ready is delayed or never reached; expects offline mode (HF_HUB_OFFLINE / local files) to be respected. | **Stage 5** |
| 10,000 stars | 105 | jamiepine/voicebox | 895 | 884 | Kept: Repro steps show an MCP speak call unloading 0.6B and loading 1.7B; expects the tool to take a model_size and not force 1.7B. | **Stage 5** |
| 10,000 stars | 105 | jamiepine/voicebox | 955 | 890 | Kept: Steps given; expects TADA 1B to generate speech, actual is CUDA OOM during voice-prompt encoding. | Kept, after the project's two |
| 10,000 stars | 105 | jamiepine/voicebox | 853 | 852 | Kept: Steps to add a voice sample; the result is the error "No module named 'audioop'" and the profile is rolled back. | Kept, after the project's two |
| 10,000 stars | 110 | HKUDS/CLI-Anything | 419 | 418 | Kept: Commands and a minimal one-liner reproduce a UnicodeEncodeError when printing a check mark on a GBK console; the install should not crash. | **Stage 5** |
| 10,000 stars | 110 | HKUDS/CLI-Anything | 421 | 407 | Kept: Commands reproduce it: tone stored with duration=6 renders a 1.00s WAV instead of 6s. | **Stage 5** |
| 10,000 stars | 110 | HKUDS/CLI-Anything | 442 | 405 | Kept: Commands reproduce it: add-star emits sodipodi:type without its parameters and Inkscape renders no star. | Kept, after the project's two |
| 10,000 stars | 110 | HKUDS/CLI-Anything | 430 | 404 | Kept: Repro: document new with -s requests 720x960, the file is never written, and later commands use 1920x1080. | Kept, after the project's two |
| 10,000 stars | 112 | zylon-ai/private-gpt | 2394 | 2392 | Kept: Upload steps and an IndexError traceback; expects the document to be available to the agent, actually it is not. | **Stage 5** |
| 10,000 stars | 112 | zylon-ai/private-gpt | 2261 | 2260 | Not kept: Labelled as a question asking for an Ollama config; the reporter is unsure their settings are right, so no wrong behaviour is pinned down. | Not a task |
| 10,000 stars | 120 | K-Dense-AI/scientific-agent-skills | 224 | 222 | Not kept: Proposal for a new skill; no bug or wrong behaviour reported. | Not a task |
| 10,000 stars | 126 | HKUDS/DeepTutor | 1113 | 994 | Not kept: Feature scope for guardian authorization; no wrong behaviour named. | Not a task |
| 10,000 stars | 126 | HKUDS/DeepTutor | 1041 | 1040 | Kept: Commands reproduce a TypeError, "unexpected keyword argument 'temperature'", with anthropic 1.x; calls should succeed. | **Stage 5** |
| 10,000 stars | 129 | run-llama/llama_index | 23405 | 23404 | Not kept: Written as a feature request to support a new model; no reproducer or concrete error, too vague to say what is wrong. | Not a task |
| 10,000 stars | 129 | run-llama/llama_index | 23223 | 23222 | Kept: Minimal code raises ValueError "Unknown model" for claude-opus-5-5; expects valid metadata including the context window. | **Stage 5** |
| 10,000 stars | 129 | run-llama/llama_index | 23208 | 23207 | Kept: Repro code: BedrockConverse rejects the Opus 5.5 IDs with ValueError, then structured_predict gets a 400 on the forced toolChoice; both should work. | **Stage 5** |
| 10,000 stars | 129 | run-llama/llama_index | 23212 | 23211 | Kept: Repro loop shows the GPT-6 IDs rejected with ValueError, then a 400 because temperature is sent; expects them to work. | Kept, after the project's two |
| 10,000 stars | 131 | Yuan1z0825/nature-skills | 281 | 279 | Not kept: One sentence saying OpenAlex ignores a parameter; it does not name the parameter or the expected behaviour, and has no reproducer. | Not a task |
| 10,000 stars | 134 | roboflow/supervision | 2786 | 2757 | Kept: Repro gives three YOLO lines from two annotations; expects one line per annotation and crowd regions skipped or warned. | **Stage 5** |
| 10,000 stars | 134 | roboflow/supervision | 2795 | 2794 | Kept: Repro shows a uint16 ndarray accepted as metres (3000.0); expects a ValueError, as with the wrapped form. | **Stage 5** |
| 10,000 stars | 134 | roboflow/supervision | 2793 | 2792 | Kept: Repro: a box_2d slightly outside 0-1000 with a mask raises a broadcast ValueError; it should degrade to an empty mask. | Kept, after the project's two |
| 10,000 stars | 134 | roboflow/supervision | 2748 | 2747 | Kept: Repro: as_pascal_voc writes 11,11,21,21 for fractional corners; expects rounding to 12,12,22,22. | Kept, after the project's two |
| 10,000 stars | 142 | exo-explore/exo | 2499 | 2464 | Kept: Repro via place_instance: runners stay RunnerIdle forever and the node is wedged; expects a bounded failure (RunnerFailed), not a hang. | **Stage 5** |
| 10,000 stars | 142 | exo-explore/exo | 2486 | 2430 | Kept: Steps given; the smaller node aborts with SIGABRT in a loop; expects placement to be rejected, shards weighted by memory, or the failure reported as OOM. | **Stage 5** |
| 10,000 stars | 143 | zhayujie/CowAgent | 3795 | 3793 | Kept: Steps: bash "echo hello" fails with exit_code 1 and no output; expected output is "hello". | **Stage 5** |
| 10,000 stars | 143 | zhayujie/CowAgent | 3774 | 3768 | Not kept: Feature request for domain or reverse-proxy support; no wrong behaviour named. | Not a task |
| 10,000 stars | 143 | zhayujie/CowAgent | 3776 | 3775 | Not kept: Feature request to support DeepSeek search/fetch APIs. | Not a task |
| 10,000 stars | 143 | zhayujie/CowAgent | 3744 | 3742 | Kept: Steps: cow install-browser crashes with FileNotFoundError for 'sudo'; expected to continue to step 3 or degrade gracefully. | **Stage 5** |
| 10,000 stars | 150 | mitmproxy/mitmproxy | 8516 | 8513 | Not kept: Enhancement asking for CRL/OCSP hosts to be blocked under --emudhra; no defect in mitmproxy's behaviour named. | Not a task |
| 10,000 stars | 150 | mitmproxy/mitmproxy | 8489 | 8488 | Kept: Repro: "Expires=0; Secure" parses with Secure swallowed into Expires; the expected attribute list is given. | **Stage 5** |
| 10,000 stars | 150 | mitmproxy/mitmproxy | 8471 | 8235 | Not kept: Only says "Listening does not work" after setting listen_port; the details are in screenshots, with no error or expected result stated. | Not a task |
| 10,000 stars | 150 | mitmproxy/mitmproxy | 8408 | 8407 | Not kept: Feature proposal to add launch_edge(); no wrong behaviour. | Not a task |
| 10,000 stars | 150 | mitmproxy/mitmproxy | 8326 | 8309 | Kept: In upstream mode the CONNECT line has the IPv6 host without brackets, and Burp rejects it. Expects the brackets the URI spec requires. | **Stage 5** |
| 10,000 stars | 151 | luongnv89/claude-howto | 222 | 219 | Kept: States current behaviour (the checker strips ZWNJ, so correct links fail) and expected behaviour (anchors match GitHub's ids). | **Stage 5** |
| 10,000 stars | 151 | luongnv89/claude-howto | 197 | 195 | Not kept: Improvement request to replace Mermaid.js with another tool; no bug. | Not a task |
| 10,000 stars | 159 | langchain-ai/langgraph | 9406 | 8218 | Kept: Repro shows interrupt() inside a tool emitted as a tool-error; the expected output without that event is given. | **Stage 5** |
| 10,000 stars | 159 | langchain-ai/langgraph | 9407 | 9284 | Kept: Repro: a @task inside a node returns None under astream_events but 42 under ainvoke; the value should be returned. | **Stage 5** |
| 10,000 stars | 159 | langchain-ai/langgraph | 9409 | 9180 | Kept: Repro: the streamed checkpoints miss the interrupt_after pause checkpoint; expects them to end at (1, ['two']). | Kept, after the project's two |
| 10,000 stars | 159 | langchain-ai/langgraph | 9400 | 9084 | Kept: Repro: tags double at each subgraph level (1x, 2x, 4x, 8x); expects each tag once, as in 1.2.2. | Kept, after the project's two |
| 10,000 stars | 161 | gradio-app/gradio | 13983 | 13982 | Kept: Repro app: after Stop the stream handler runs once more on the same last chunk (6 calls for 5 chunks). | **Stage 5** |
| 10,000 stars | 161 | gradio-app/gradio | 13985 | 13984 | Not kept: About other users' data being saved into the first user's private bucket, a cross-user data exposure; security issues are excluded. | Not a task |
| 10,000 stars | 161 | gradio-app/gradio | 13949 | 13950 | Kept: Curl repro: a Range past EOF gives Content-Range 0-3/3 and the request hangs; expects 0-2/3 and a completed response. | **Stage 5** |
| 10,000 stars | 161 | gradio-app/gradio | 13924 | 13923 | Kept: Repro loop raises OverflowError for int8/uint8 and flips the float16 endpoint; the expected int16 outputs are listed. | Kept, after the project's two |
| 10,000 stars | 162 | deepspeedai/DeepSpeed | 8909 | 8797 | Kept: Says "enabled": false inside comms_logger enables the logger instead of disabling it, with measured slowdown; the flag should be honoured. | **Stage 5** |
| 10,000 stars | 162 | deepspeedai/DeepSpeed | 8795 | 8756 | Not kept: Performance proposal to skip sync for unprofiled ops; no wrong result named. | Not a task |
| 10,000 stars | 162 | deepspeedai/DeepSpeed | 8882 | 8881 | Kept: The triage job fails with "no FAILED test files found" because ANSI codes break the grep; it should extract the failing tests. | **Stage 5** |
| 10,000 stars | 162 | deepspeedai/DeepSpeed | 8642 | 8639 | Kept: Gives a scalar-parameter reproducer that raises RuntimeError at init; expects the scalar to broadcast as raw bytes on every rank. | Kept, after the project's two |
| 10,000 stars | 163 | ifixai-ai/iFixAi | 350 | 349 | Kept: Reproducer shows an ungraded item labelled [FAIL]; expects a not-graded label that matches the HTML artifact. | **Stage 5** |
| 10,000 stars | 165 | agno-agi/agno | 11347 | 11344 | Kept: Steps show the default judge returning 404 and then an AttributeError on avg_score; expects a served default model and a returned score. | **Stage 5** |
| 10,000 stars | 165 | agno-agi/agno | 11327 | 11322 | Kept: Reproducer shows one upload's chunk_size staying on the shared reader; expects the chunker option to apply to that upload only. | **Stage 5** |
| 10,000 stars | 165 | agno-agi/agno | 11296 | 10505 | Kept: Reproducer shows create_reader ignoring chunk_size=200 and Knowledge objects sharing one reader; expects per-call options and isolated readers. | Kept, after the project's two |
| 10,000 stars | 165 | agno-agi/agno | 10602 | 10601 | Kept: Reproducer: page_size=0 on 11 rows returns 0 documents; expects a ValueError, as negative sizes already get. | Kept, after the project's two |
| 10,000 stars | 169 | ManimCommunity/manim | 5078 | 4945 | Kept: Gives scene code and a render command; the Line bends during the animation, and with path_arc=0 it should have no curvature. | **Stage 5** |
| 10,000 stars | 169 | ManimCommunity/manim | 5065 | 4983 | Not kept: Cleanup of hardcoded class names in error messages; no concrete expected behaviour, and the fix is left to 'your best judgment'. | Not a task |
| 10,000 stars | 169 | ManimCommunity/manim | 5040 | 4949 | Kept: Snippet plus command makes set_camera_orientation(zoom=...) raise AttributeError (no set_zoom) on OpenGL; zoom is expected to work. | **Stage 5** |
| 10,000 stars | 169 | ManimCommunity/manim | 5028 | 5026 | Kept: Reproducer lists the calls that should be accepted with warnings and the TypeErrors they actually raise under redirections. | Kept, after the project's two |
| 10,000 stars | 170 | wshobson/agents | 743 | 742 | Not kept: Documentation-only issue: two dangling Reference links in a SKILL.md file; no software misbehaviour. | Not a task |
| 10,000 stars | 170 | wshobson/agents | 711 | 704 | Not kept: Proposal for a new YAML validation check in the gardener; a tooling chore, not a bug report. | Not a task |
| 10,000 stars | 170 | wshobson/agents | 724 | 643 | Not kept: Repo and docs consistency drift (README counts, stale model column, divergent duplicate agent files); a doc and maintenance chore. | Not a task |
| 10,000 stars | 170 | wshobson/agents | 669 | 644 | Not kept: Feature request to support a new CLI harness; no wrong behaviour named. | Not a task |
| 10,000 stars | 170 | wshobson/agents | 668 | 660 | Kept: Command repro: the Model Usage section always says static-only, even after real LLM calls. Expects per-model token counts. | **Stage 5** |
| 10,000 stars | 170 | wshobson/agents | 666 | 663 | Kept: Steps show a failed install still deletes the pkg and marketplace-cache dirs. Expects them left alone on failure. | **Stage 5** |
| 10,000 stars | 170 | wshobson/agents | 662 | 661 | Kept: Regex repro: sub-skills/ refs are misparsed and flagged DEAD_CROSS_REF. Expects no match inside sub-skills/ and nested resolution. | Kept, after the project's two |
| 10,000 stars | 170 | wshobson/agents | 652 | 651 | Kept: Repro: an errored result comes out activated=True and errored=True. Expects activated=False and quality_score 0.0. | Kept, after the project's two |
| 10,000 stars | 174 | hpcaitech/ColossalAI | 6479 | 6471 | Not kept: Reports a security vulnerability (arbitrary code execution through unpickling a checkpoint), which the rule leaves out. | Not a task |
| 10,000 stars | 174 | hpcaitech/ColossalAI | 6469 | 6465 | Kept: Reproducer shows ZeRO grad norms that differ per rank and from the DDP norm; expects one value on every rank, equal to the correct norm. | **Stage 5** |
| 10,000 stars | 174 | hpcaitech/ColossalAI | 6468 | 6466 | Kept: Reproducer shows expert gradients far from the reference on each sp rank; expects them to match within bf16 noise, like the dense ones. | **Stage 5** |
| 10,000 stars | 174 | hpcaitech/ColossalAI | 6467 | 6464 | Kept: Reproducer prints a 2.000 expert gradient ratio against DDP; expects 1.000 for every parameter. | Kept, after the project's two |
| 10,000 stars | 181 | HKUDS/LightRAG | 4146 | 3891 | Kept: Says the fallback drops a healthy collection on any error, even 'already exists'; expects the existing collection to be validated and loaded, not dropped. | **Stage 5** |
| 10,000 stars | 181 | HKUDS/LightRAG | 4041 | 4040 | Kept: Reproducer shows an all-NaN case counted as a 0.0 success that skews the averages; expects it to be reported as a failed case. | Kept, past the stop |
| 10,000 stars | 181 | HKUDS/LightRAG | 4010 | 3603 | Not kept: Mainly a request for a new per-chunk transform hook. The bypass follows the documented contract, and the expected behaviour is left among options. | Not a task |
| 10,000 stars | 181 | HKUDS/LightRAG | 3992 | 3990 | Kept: Reproducer shows CJK and fullwidth delimiters turned into \left( \right); names the wrong glyph and expects mapping to the equivalent delimiters. | Kept, past the stop |
| 10,000 stars | 184 | VectifyAI/PageIndex | 188 | 163 | Kept: Gives the run command and a traceback: KeyError 'completed' and a context overflow on a large TOC, plus invented page entries. | Kept, past the stop |
| 10,000 stars | 185 | soxoj/maigret | 2947 | 2510 | Not kept: Suggestion to improve the graph's look and content; a feature request with no wrong behaviour. | Not a task |
| 10,000 stars | 185 | soxoj/maigret | 3109 | 3108 | Not kept: About traffic bypassing --proxy and leaking the user's real IP, a privacy and security vulnerability, which the rule leaves out. | Not a task |
| 10,000 stars | 185 | soxoj/maigret | 3216 | 3215 | Kept: Command reproducer crashes with ValueError 'Unknown check type' for sites with no checkType or engine; expects a status_code check. | Kept, past the stop |
| 10,000 stars | 185 | soxoj/maigret | 3056 | 3055 | Kept: Says a cp1252 stdout crashes the verbose scan and the spinner with UnicodeEncodeError; expects the same guard the banners got. | Kept, past the stop |
| 10,000 stars | 189 | google/langextract | 466 | 234 | Kept: Says the HTML shows garbled text when no charset is declared; expects it to render consistently as UTF-8. | Kept, past the stop |
| 10,000 stars | 189 | google/langextract | 556 | 558 | Kept: Reproducer: literal NA/NULL load as float NaN and tokenization raises TypeError; expects the literal strings to be kept. | Kept, past the stop |
| 10,000 stars | 189 | google/langextract | 571 | 529 | Kept: Reproducer shows temperature missing from the request when config= is passed; expects temperature=0.0 to be sent, or at least a warning. | Kept, past the stop |
| 10,000 stars | 189 | google/langextract | 567 | 565 | Not kept: The reproducer shows ungrounded empty extractions, but the expected behaviour is left open: drop them, or keep and document them. | Not a task |
| 10,000 stars | 193 | stanfordnlp/dspy | 10322 | 10321 | Kept: compile(seed=0) falls back to the default seed 9 because of 'or'; expects seed 0 to be used, with only None falling back. | Kept, past the stop |
| 10,000 stars | 193 | stanfordnlp/dspy | 10604 | 10603 | Kept: Reproducer shows that a 0.0 threshold rejects a score of 0.0 and accepts -0.1; expects a >= comparison against zero. | Kept, past the stop |
| 10,000 stars | 193 | stanfordnlp/dspy | 10644 | 10612 | Kept: Reproducer: tool_calls null or {} raises AdapterParseError; expects both to parse as empty, like []. | Kept, past the stop |
| 10,000 stars | 193 | stanfordnlp/dspy | 10597 | 10596 | Kept: Reproducer: CSV export raises ValueError after a handled failure; expects the result returned and both rows exported. | Kept, past the stop |
| 10,000 stars | 205 | Textualize/textual | 6592 | 6562 | Kept: Gives key steps in Kitty that display an escape sequence instead of '~/' in TextArea; names the wrong output and the expected text. | Kept, past the stop |
| 10,000 stars | 205 | Textualize/textual | 6594 | 6583 | Not kept: Framed as a support request for cmd+backspace in Kitty; too vague about what actually happens. | Not a task |
| 10,000 stars | 205 | Textualize/textual | 6593 | 6584 | Not kept: Feature request to bind option+backspace to delete-word; no wrong behaviour named. | Not a task |
| 10,000 stars | 209 | huggingface/pytorch-image-models | 2783 | 2782 | Kept: Reproducer: augmix raises a broadcasting ValueError on non-square and single-band images, while other augmentations are unaffected. | Kept, past the stop |
| 10,000 stars | 209 | huggingface/pytorch-image-models | 2775 | 2772 | Not kept: Feature request to add a new vision encoder model; no bug. | Not a task |
| 10,000 stars | 209 | huggingface/pytorch-image-models | 2765 | 2764 | Not kept: Feature request to add the Qwen vision encoder as a backbone; no bug. | Not a task |
| 10,000 stars | 209 | huggingface/pytorch-image-models | 2763 | 2762 | Not kept: Feature request to add Sapiens2 backbones; no bug. | Not a task |
| 10,000 stars | 209 | huggingface/pytorch-image-models | 2742 | 1597 | Kept: create_model with in_chans=1 on a pruned efficientnet raises a channel RuntimeError, while tf_efficientnet works. Expects in_chans to be honoured. | Kept, past the stop |
| 10,000 stars | 212 | blakeblackshear/frigate | 24591 | 10845 | Not kept: Support question asking whether an option exists to hold the Birdseye camera; no wrong behaviour named. | Not a task |
| 10,000 stars | 214 | virgiliojr94/book-to-skill | 311 | 310 | Kept: Reproducer shows a UTF-16 OPF giving the wrong spine order and 0 spine items; expects 'FIRST\n\nSECOND' and 2 items. | Kept, past the stop |
| 10,000 stars | 214 | virgiliojr94/book-to-skill | 270 | 268 | Kept: Reproducer: the preferred parser drops text inside w:sdt while the fallback keeps it; expects that text extracted, or the omission reported. | Kept, past the stop |
| 10,000 stars | 214 | virgiliojr94/book-to-skill | 296 | 295 | Kept: Reproducer gives actual 'A B' and expected 'A 😀 B': adjacent surrogate escapes should decode to one character instead of being dropped. | Kept, past the stop |
| 10,000 stars | 214 | virgiliojr94/book-to-skill | 301 | 299 | Kept: Repro command plus traceback: a successful pdf-inspector extraction aborts with KeyError 'fallback_reason' when it should complete. | Kept, past the stop |
| 10,000 stars | 217 | microsoft/graphrag | 2484 | 2478 | Kept: Reproducer shows the caller's event loop replaced and the internal loop leaked; expects the caller's loop kept and the internal loop always closed. | Kept, past the stop |
| 10,000 stars | 217 | microsoft/graphrag | 2434 | 2424 | Kept: Steps given; a blank line raises JSONDecodeError and the whole file is skipped. Expected: blank lines ignored and both records loaded. | Kept, past the stop |
| 10,000 stars | 223 | dgtlmoon/changedetection.io | 4624 | 4620 | Kept: Steps and log show the failure counter never resets when 'Ignore status codes' is on, so false alerts fire; expects any success to reset it to 0. | Kept, past the stop |
| 10,000 stars | 223 | dgtlmoon/changedetection.io | 4601 | 4506 | Not kept: Feature request for a new API call to recheck only errored watchers; it is framed as a wish, not a bug with a defined expected result. | Not a task |
| 10,000 stars | 223 | dgtlmoon/changedetection.io | 4484 | 4382 | Not kept: Feature request to mark a watch as unviewed again; no wrong behaviour named. | Not a task |
| 10,000 stars | 223 | dgtlmoon/changedetection.io | 4492 | 4477 | Kept: URL and XPath given; current output joins all matches into one line, expected output shows one match per line. | Kept, past the stop |
| 10,000 stars | 225 | agentscope-ai/agentscope | 3565 | 3492 | Kept: Reproducer prints a system message with content None, which endpoints reject with a 400; expects the system turn to always carry a string. | Kept, past the stop |
| 10,000 stars | 225 | agentscope-ai/agentscope | 3523 | 3518 | Kept: Says every default request sends thinking_budget 0, which the API rejects for Pro models; expects no thinking_config sent when it is unconfigured. | Kept, past the stop |
| 10,000 stars | 225 | agentscope-ai/agentscope | 3192 | 3191 | Kept: Reproducer shows enable_thinking=False injected though the caller set nothing; expected an empty extra_body. | Kept, past the stop |
| 10,000 stars | 225 | agentscope-ai/agentscope | 3510 | 3509 | Kept: Says think=false is always sent, overriding model defaults; expects think left out unless the user sets thinking_enable. | Kept, past the stop |
| 10,000 stars | 228 | google-research/timesfm | 459 | 457 | Kept: One-line reproducer shows the model is never an OptimizedModule despite torch_compile=True; compile should take effect but rebinding self is a no-op. | Kept, past the stop |
| 10,000 stars | 229 | NandhaKishorM/laya | 1409 | 1408 | Not kept: Feature proposal for opt-in diagnostics in the MCP route tools; it asks for interface agreement and names no wrong behaviour. | Not a task |
| 10,000 stars | 229 | NandhaKishorM/laya | 1394 | 1393 | Not kept: Security vulnerability: drive-anchored paths get past the unsafe-path guard and escape model_dir (path traversal). Excluded by the rule. | Not a task |
| 10,000 stars | 229 | NandhaKishorM/laya | 1314 | 1312 | Kept: Reproducer: malformed inputs return ACCEPT thresholds; they should raise before fitting instead of silently dropping evidence. | Kept, past the stop |
| 10,000 stars | 229 | NandhaKishorM/laya | 1289 | 1288 | Kept: Reproducer: a valid cutoff is lost and the actual result is (1.01, 0.0), and the rounded score replays as ACCEPT where the live decision was VERIFY; the two should agree. | Kept, past the stop |
| 10,000 stars | 231 | huggingface/diffusers | 14844 | 14719 | Kept: Reproducer: saving default weights deletes another variant's shards, and reloading that variant raises FileNotFoundError when it should still load. | Kept, past the stop |
| 10,000 stars | 231 | huggingface/diffusers | 15060 | 15014 | Not kept: Feature request for a new gfx1201 SageAttention backend that asks which delivery to use; no wrong behaviour named. | Not a task |
| 10,000 stars | 231 | huggingface/diffusers | 14521 | 14388 | Kept: Reproducer: required=True with a default runs at 50 steps rather than raising when the input is omitted. The fix direction is open, but the wrong result is named. | Kept, past the stop |
| 10,000 stars | 231 | huggingface/diffusers | 15128 | 15127 | Kept: Reproducer logs scales 0.5 and 1.0 for a broadcast dict; both adapters should get 0.5, as with scalar or explicit weights. | Kept, past the stop |
| 10,000 stars | 233 | OpenBMB/ChatDev | 641 | 638 | Not kept: Security vulnerability: arbitrary file write and delete through an unsanitized upload filename. Excluded by the rule. | Not a task |
| 10,000 stars | 234 | python-poetry/poetry | 11175 | 11174 | Kept: Command shown; an invalid --priority prints a bare 'BOGUS' KeyError when it should give a validation message listing the valid values. | Kept, past the stop |
| 10,000 stars | 234 | python-poetry/poetry | 10877 | 10144 | Kept: Reproducer TOML given: moving one source below [build-system] makes `poetry version` fail with 'Key "source" already exists' instead of working. | Kept, past the stop |
| 10,000 stars | 234 | python-poetry/poetry | 11099 | 9158 | Kept: Says concurrent installs of namespace packages interleave writes into mixed, invalid files; expected is the intended overwrite of one file by another, not a mix. | Kept, past the stop |
| 10,000 stars | 234 | python-poetry/poetry | 11093 | 9054 | Kept: Steps given: after an editable path dependency is removed, `poetry install --sync` fails with a path error instead of syncing; traceback included. | Kept, past the stop |
| 10,000 stars | 249 | ahujasid/mcp-for-blender | 381 | 380 | Kept: `setup` crashes with UnicodeDecodeError then AttributeError on German Windows because tasklist output is decoded with the wrong code page; it should decode and finish. | Kept, past the stop |
| 10,000 stars | 249 | ahujasid/mcp-for-blender | 368 | 365 | Not kept: Security report: a safe-mode blocklist entry never fires, so scripts can set persistent code-load paths. A security vulnerability is excluded. | Not a task |
| 10,000 stars | 255 | CloakHQ/CloakBrowser | 429 | 428 | Kept: Reproducer: clicking a locator inside an iframe with humanize=True raises ElementNotAttachedError; expected the button to be clicked. | Kept, past the stop |
| 10,000 stars | 256 | tirth8205/code-review-graph | 1116 | 1107 | Kept: Repro repo given; the rename leaves the import line unchanged and the code raises ImportError. Expected: the import line is edited and the code still imports. | Kept, past the stop |
| 10,000 stars | 256 | tirth8205/code-review-graph | 1099 | 977 | Kept: Generator and timings show parse time growing superlinearly and the build stalling for minutes on one header; expects under a second and continued progress. | Kept, past the stop |
| 10,000 stars | 256 | tirth8205/code-review-graph | 1086 | 986 | Not kept: Mainly asks for a test that checks node names against each grammar (a chore); the PowerShell call-edge gap is noted in passing with no expected result set out. | Not a task |
| 10,000 stars | 256 | tirth8205/code-review-graph | 1085 | 902 | Kept: Steps given; a component imported only by its .stories.tsx is never reported as dead code, though it should be. | Kept, past the stop |
| 10,000 stars | 258 | getzep/graphiti | 2030 | 2027 | Kept: Says add_episode_bulk saves entity_edges=[], so remove_episode misses the episode's facts; expects the fact UUIDs recorded as add_episode does. | Kept, past the stop |
| 10,000 stars | 258 | getzep/graphiti | 1999 | 1969 | Kept: Reproducer: the default model is being shut down and the named substitute returns 400 errors; expects a default install and small_model='gpt-5.6-luna' to work. | Kept, past the stop |
| 10,000 stars | 258 | getzep/graphiti | 1963 | 1947 | Kept: Repro table: Graphiti's fulltext index call errors on FalkorDB 6.0 ('expected at most 1') but works on 4.20; supported versions should work. | Kept, past the stop |
| 10,000 stars | 258 | getzep/graphiti | 1926 | 1651 | Kept: Steps: get_episodes says 'No episodes found' and delete_episode says 'not found' for an episode that exists; both should route to the group's graph. | Kept, past the stop |
| 10,000 stars | 259 | ScrapeGraphAI/Scrapegraph-ai | 1136 | 1102 | Kept: Reproducer returns 'NA' for every field on a page that plainly has the data; expected the founding year 1865 and similar values. | Kept, past the stop |
| 10,000 stars | 266 | Lightning-AI/pytorch-lightning | 21726 | 21725 | Kept: With devices=[3,4,5,6], memory is still allocated on GPU 0; expects devices not passed to stay unused. Trainer repro and cause given. | Kept, past the stop |
| 10,000 stars | 266 | Lightning-AI/pytorch-lightning | 21831 | 19803 | Not kept: Asks 'Is this intentional or a bug?' about the precision/scaler logic; no reproducer, and the expected behaviour is left open. | Not a task |
| 10,000 stars | 266 | Lightning-AI/pytorch-lightning | 21906 | 21900 | Not kept: Deprecation warnings from jsonargparse APIs, an API-update chore; the repro is empty and no wrong result or expected behaviour is stated. | Not a task |
| 10,000 stars | 266 | Lightning-AI/pytorch-lightning | 21799 | 21800 | Kept: Repro script: _atomic_save to a read-only directory returns silently when it should raise PermissionError. | Kept, past the stop |
| 10,000 stars | 276 | openai/openai-agents-python | 5444 | 5442 | Kept: Reproducer: the restored context loses fields changed in place, and to_string() raises TypeError on a UUID; the saved state should round-trip. | Kept, past the stop |
| 10,000 stars | 276 | openai/openai-agents-python | 5425 | 5414 | Kept: Reproducer prints GPT-5 default settings sent to a provider-chosen model; expected None None, the same as when the model is named. | Kept, past the stop |
| 10,000 stars | 276 | openai/openai-agents-python | 5419 | 5415 | Kept: Offline repro: the second turn raises UserError 'Unhandled item type'; the stored assistant turn should replay and the run complete, as in 0.18.0. | Kept, past the stop |
| 10,000 stars | 276 | openai/openai-agents-python | 5418 | 5392 | Kept: Reproducer shows invalid handoff args raise ModelBehaviorError and end the run; expects the error returned to the model as for function tools. | Kept, past the stop |
| 10,000 stars | 277 | assafelovic/gpt-researcher | 2193 | 2192 | Kept: Reproducer prints [], [], {} for configured MCP lists; expects the lists kept and the named server config returned. | Kept, past the stop |
| 10,000 stars | 277 | assafelovic/gpt-researcher | 2158 | 1495 | Kept: Says some multi-agent chapters repeat the same content; expects each chapter to have unique content. | Kept, past the stop |
| 10,000 stars | 277 | assafelovic/gpt-researcher | 2112 | 2097 | Kept: Test reproducer shows a failed second search carries the first query's metadata; expects its own query, no sources and the error. | Kept, past the stop |
| 10,000 stars | 277 | assafelovic/gpt-researcher | 2114 | 2096 | Kept: Reproducer shows uppercase HTTPS/WS URLs converted to stdio with URL and headers dropped; expects case-insensitive remote transport. | Kept, past the stop |
| 10,000 stars | 297 | srbhr/Resume-Matcher | 1042 | 1030 | Not kept: Error message given, but the steps are only a settings URL and "API Key" with no model named, and the expected behaviour is left blank. | Not a task |
| 10,000 stars | 297 | srbhr/Resume-Matcher | 977 | 975 | Kept: Reproduction steps and table show supported GPT-5.1/5.2 temperatures omitted after the PR; expects them preserved. | Kept, past the stop |
| 10,000 stars | 297 | srbhr/Resume-Matcher | 868 | 857 | Kept: Steps given; wizard fails with 400 because json_object is sent to LM Studio; expects all wizard turns to complete. | Kept, past the stop |
| 10,000 stars | 298 | reflex-dev/reflex | 7641 | 7640 | Not kept: Asks for a more restrictive CORS default, a security hardening and breaking-change request; no wrong result named. | Not a task |
| 10,000 stars | 298 | reflex-dev/reflex | 7582 | 7498 | Not kept: Names the silent non-detection, but leaves expected behaviour open between supporting the factory and rejecting or warning. | Not a task |
| 10,000 stars | 298 | reflex-dev/reflex | 7624 | 5220 | Not kept: Optimisation/refactor wish; says the current fix works, just not as efficient as it could be. | Not a task |
| 10,000 stars | 298 | reflex-dev/reflex | 7548 | 7536 | Kept: Table shows reversed() and foreach on "a😀b" yield lone surrogates and 4 items; expects Python's b😀a and 3 items. | Kept, past the stop |
| 10,000 stars | 298 | reflex-dev/reflex | 7599 | 7598 | Kept: Repro: get_router returns None for /About, so the 404 on_load runs. Expects the backend to resolve the route the frontend renders. | Kept, past the stop |
| 10,000 stars | 298 | reflex-dev/reflex | 7637 | 7567 | Kept: Repro app and steps: synced sibling vars stay stale in the tab that writes. Expects them to show the new value, as other tabs do. | Kept, past the stop |
| 10,000 stars | 298 | reflex-dev/reflex | 7621 | 7620 | Kept: Repro: an oversized CONNECT POST with polling stalls the page with no toast and no retry. Expects the error recorded, the toast and retries with backoff. | Kept, past the stop |
| 10,000 stars | 298 | reflex-dev/reflex | 7572 | 7571 | Kept: Test repro: the cached dependent is emitted only as stale after the correction. Expects the correction delta to carry the updated dependent. | Kept, past the stop |
| 10,000 stars | 299 | ArchiveBox/ArchiveBox | 1877 | 1876 | Not kept: Documentation suggestion for the troubleshooting wiki. Not a bug report with an expected behaviour. | Not a task |
| 10,000 stars | 299 | ArchiveBox/ArchiveBox | 1848 | 1847 | Kept: Steps given. The static export links to /snapshot/<id>/index.html, and the report names the archive/... path it should link to instead. | Kept, past the stop |
| 10,000 stars | 301 | huggingface/lerobot | 4144 | 3975 | Kept: Command repro: LoRA training on the base model fails trying to load the missing adapter_config.json, as if the repo were a PEFT adapter. | Kept, past the stop |
| 10,000 stars | 301 | huggingface/lerobot | 5049 | 5048 | Not kept: Feature request implementing Faster-WAM, with benchmarks. No wrong behaviour named. | Not a task |
| 10,000 stars | 301 | huggingface/lerobot | 4648 | 4647 | Kept: Snippet repro: from_pretrained leaves pretrained_path None, so empty normalizer stats and reward near 0. Expects the path backfilled. | Kept, past the stop |
| 10,000 stars | 301 | huggingface/lerobot | 4292 | 4205 | Kept: Repro: policy.forward with uint8 images raises NotImplementedError on ROCm. Expects an automatic float cast (preferred) or a clear error. | Kept, past the stop |
| 10,000 stars | 306 | PrefectHQ/fastmcp | 6161 | 6160 | Not kept: Performance report (slow listing, many sibling-merge calls). No expected behaviour or wrong result beyond the slowness. | Not a task |
| 10,000 stars | 306 | PrefectHQ/fastmcp | 6156 | 6155 | Kept: Repro output: the second client's timeout is ignored on a kept-alive transport. Expects each client's timeout to apply, or the mismatch guard to raise. | Kept, past the stop |
| 10,000 stars | 306 | PrefectHQ/fastmcp | 6027 | 6022 | Kept: Repro: a missing required prompt argument returns -32603. Expects -32602 with a message naming the argument. | Kept, past the stop |
| 10,000 stars | 306 | PrefectHQ/fastmcp | 6119 | 6117 | Kept: Repro shows the args param of FastMCPStdioTransport is dropped from the child argv. Expects it forwarded, as the CLI does. | Kept, past the stop |
| 10,000 stars | 307 | locustio/locust | 3557 | 3556 | Kept: Repro table: -r nan/inf is accepted and /swarm reports success with zero users. Expects a parse error and success false. | Kept, past the stop |
| 10,000 stars | 307 | locustio/locust | 3555 | 3554 | Kept: Table: a fractional spawn rate is truncated in the --html filename, e.g. results_10_0_5. Expects {r} to spell the actual value. | Kept, past the stop |
| 10,000 stars | 307 | locustio/locust | 3553 | 2280 | Kept: A directory in LOCUST_LOCUSTFILE logs a "Duplicate shape classes" error. Expects the shape class to load normally, without duplicates. | Kept, past the stop |
| 10,000 stars | 307 | locustio/locust | 3530 | 3529 | Kept: Repro: rest() and FastHttpSession mutate the caller's headers dict, which breaks a later form request. Expects headers to be a pure input. | Kept, past the stop |
| 10,000 stars | 330 | titanwings/distilly | 135 | 134 | Kept: The Work-only skill tells the model to refer to a Persona section it does not contain. Expects Work-only to be self-contained, with its own fallback. | Kept, past the stop |
| 10,000 stars | 335 | guillaumemeyer/watermarks-remover | 402 | 398 | Kept: Repro: multiline frontmatter values are never scanned, so an AI marker survives clean. Expects the full value to be scanned, as single-line ones are. | Kept, past the stop |
| 10,000 stars | 335 | guillaumemeyer/watermarks-remover | 385 | 321 | Not kept: Feature request for a DeepSeek harness or preset. No wrong behaviour named. | Not a task |
| 10,000 stars | 335 | guillaumemeyer/watermarks-remover | 380 | 378 | Kept: Repro script: web-extension parts holding claude.fileId survive clean and inspect reports clean. Expects them to be reported and removed. | Kept, past the stop |
| 10,000 stars | 335 | guillaumemeyer/watermarks-remover | 401 | 396 | Kept: Repro: a quoted '>' in meta content truncates the tag, giving a false positive, mangled output and a missed marker. Expects quote-aware matching. | Kept, past the stop |
| 10,000 stars | 336 | zulip/zulip | 39619 | 21819 | Not kept: Performance wish (topic moves are slow). Vague, with no reproducer or wrong result named. | Not a task |
| 10,000 stars | 336 | zulip/zulip | 40241 | 40239 | Kept: Steps: a GET with token FF returns HTTP 500 from a UnicodeDecodeError. Expects the existing Invalid token JSON error. | Kept, past the stop |
| 10,000 stars | 336 | zulip/zulip | 37212 | 35224 | Kept: Steps: after moving messages into a new topic the user does not follow it. Expects the follow status to carry over. | Kept, past the stop |
| 10,000 stars | 336 | zulip/zulip | 40037 | 39230 | Kept: A bare-hostname JITSI_SERVER_URL now makes the frontend fail to load. Expects it accepted, with https assumed. | Kept, past the stop |
| 10,000 stars | 342 | keon/algorithms | 2774 | 2773 | Kept: Repro: strip_url_params2 drops ac=2 as a duplicate of ab=1. Expected and actual URLs given. | Kept, past the stop |
| 10,000 stars | 342 | keon/algorithms | 2772 | 2771 | Kept: Repro: longest_palindrome("a#a") returns "aa", not "a#a", and "##" returns empty. | Kept, past the stop |
| 10,000 stars | 347 | danielgatis/rembg | 843 | 842 | Not kept: Feature request or question about a despill option. No wrong behaviour named. | Not a task |
| 10,000 stars | 348 | earthtojake/text-to-cad | 665 | 629 | Not kept: Error text only (node not on PATH). The 'what were you doing' field is blank, with no steps or expected behaviour. | Not a task |
| 10,000 stars | 348 | earthtojake/text-to-cad | 575 | 564 | Kept: Gives a reproducer script and steps; says 10-16 of 20 parts read STALE after a parallel build, when every part should read current. | Kept, past the stop |
| 10,000 stars | 348 | earthtojake/text-to-cad | 406 | 404 | Kept: Gives repro steps on fresh Windows 11 with SAC; names the wrong result: OCP import fails (CodeIntegrity 3077). Expects a signed wheel or a documented workaround. | Kept, past the stop |
