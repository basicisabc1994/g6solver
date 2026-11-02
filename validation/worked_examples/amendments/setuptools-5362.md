# setuptools-5362: scoring amendment

Issue pypa/setuptools#5360, "[BUG] Wheel tagging uses the build interpreter rather than the target when cross-compiling". The task is the issue and the repository at the parent commit. The issue's one comment came after PR 5362 opened (2026-10-19T20:57:07Z) and is not part of the task.

What the task states:

- `get_tag()` "derives the wheel's interpreter and ABI from the running interpreter". The tag, and the check on it, must describe the target. `tags.interpreter_name()`, `tags.interpreter_version()` and `tags.sys_tags()` are listed as the host-derived inputs.
- For CPython POSIX builds, `sysconfig.get_config_var("VERSION")` is the target's version. `sysconfig.get_python_version()`, `py_version_short` and `py_version_nodot` must not stand in for it.
- A `sys.version_info` comparison in `_validate_py_limited_api` should use target metadata.
- `_is_32bit_interpreter()` is "worth auditing", because the target's `SIZEOF_VOID_P` is the relevant value.
- Other implementations keep their separate handling.

The parent supplies a target source for the implementation and the ABI. `get_abi_tag()` reads `SOABI`. Its `get_flag("Py_DEBUG", ...)` reads `Py_DEBUG` first, falls back to the host, and warns with `RuntimeWarning` when the variable is unset.

Searched the parent's `setuptools/command/bdist_wheel.py`, `setuptools/command/build_ext.py` and `setuptools/tests/test_bdist_wheel.py`, and the issue, for `_interpreter_name`, `_python_version` and `_is_debug`. No hits.

## setuptools/tests/test_bdist_wheel.py, the import

**Pinned names.** The module imports `_interpreter_name`, `_is_debug` and `_python_version`. All three are the fix's own, so a fix without them fails at import, and with it every test in the module. They are removed from the import. `_is_32bit_interpreter` and `get_platform` exist at the parent and stay.

## `test_cross_tags`

This is the test that goes from failing to passing. The host stays a PyPy-like interpreter and the target stays CPython, so the implementation must still come from the target.

**Behaviour the task does not state.** `version_available=False` set `VERSION` to `None` and required the version to come from `SOABI`. The issue never mentions a missing `VERSION`. `VERSION` is now always given. The parametrisation stays, so test ids do not change, and those rows now repeat the `True` rows.

**Pinned value.** `tags.interpreter_version()` was patched to `'999'`. That also fails a fix which leaves the `abi_tag` property alone, because `stable_abi_tag` tests `impl_version[0] == '3'`. The patch now returns the mocked host's own version, which matches the mocked `sys.version_info`. Using it for the tag still gives the host's version, so that still fails whenever host and target differ.

**Pinned implementation.** `tags.sys_tags()` was replaced by a function that fails the test when called, which forbids calling it at all. The issue complains that validating against the host's tags describes the build machine. It does not forbid the call. A fix may keep host tags where host and target agree, or check both. `sys_tags()` now returns the host's own tag, which does not cover any target in the matrix. A fix that validates the target against it still raises `would build wheel with unsupported tag` and fails.

What stays checked: the tag equals the target's `cp` version, ABI and platform for each host and target, the implementation comes from the target, the `ValueError` on a free-threaded target before 3.15, and the extension filename. `struct.calcsize` stays reversed, so `SIZEOF_VOID_P` is still required on the `aarch64` rows.

## `test_get_abi_tag_windows`

**Behaviour the task does not state, in part.** The test required the `d` flag to come from the target in all eight cases, including the four where `Py_DEBUG` is unset and only `EXT_SUFFIX` hints at it. Reading `_d` from `EXT_SUFFIX` is the fix's own rule. Neither the issue nor the parent has it. In those four cases the assertions now accept the host's answer or the target's. Where `Py_DEBUG` is set, the target's value must win, as before. That variable is the parent's own source for the flag, and the tag's ABI is what the issue says must describe the target.

## `test_get_abi_tag_windows_without_soabi`

**Pinned format and behaviour the task does not state.** The test required `cp312` from `VERSION` given in the Windows form `312`, and a `d` read from `EXT_SUFFIX`. The issue speaks of the POSIX form only, and the `EXT_SUFFIX` rule is the fix's. `VERSION` is now given in the POSIX form for the running interpreter, and the result must be a `cp` tag.

What stays checked: the `RuntimeWarning` with the parent's own text when `Py_DEBUG` is unset on this path. That warning is in the parent's `get_flag`. The test keeps its id and its two cases.

## `test_is_debug_from_sysconfig`

**Pinned name.** The test called `_is_debug`, the fix's own helper. It now reads the same result through the public `get_abi_tag()`, on the path with no `SOABI`: the tag ends in `d` exactly when `Py_DEBUG` says so. `VERSION` is given, in the POSIX form, so the test does not also require a fallback for a missing version.

What stays checked: an explicit `Py_DEBUG` wins over an `EXT_SUFFIX` that says the opposite. That precedence is the parent's, in `get_flag`. The test keeps its id and its two cases.

## `test_config_fallback`

**Pinned names.** The parametrisation named the three private helpers. Those rows are removed. The `_is_32bit_interpreter` row stays, so its four test ids do not change.

## Tests removed

Both exist only to pin what the task does not state.

- `test_dist_files_target_version`, 2 cases. The version recorded in `dist_files`. The issue is about the wheel's tag. It does not mention `dist_files`, and the parent records the running version there.
- `test_target_python_version`, 15 cases. Calls `_python_version` and pins its fallback order: Windows forms, an integer, `SOABI` parsing for four implementations, and the host. The stated row, `VERSION` read from sysconfig, is covered by `test_cross_tags`.

With `test_config_fallback`, 29 parametrised cases are gone: 17 from these two tests and 12 from the three rows above.

## Left unchanged

`test_non_cpython_target` keeps the maintainers' form: a CPython host aimed at PyPy and GraalPy targets, with the parent's ABI formats `pypy311_pp73` and `graalpy231_311_native`. `test_limited_api_newer_than_target` keeps `AssertionError` and `unsupported tag`, the parent's text and behaviour. `test_platform_linux32` keeps requiring `SIZEOF_VOID_P`. The issue hedges with "worth auditing", but it names the value and says it is the relevant one. `test_limited_api` and `test_abi3_filename_consistent` use public behaviour and the parent's helpers.

## Controls

Reasoning, as nothing here has been run.

- On the parent, `test_cross_tags` fails on every row that reaches `get_tag()`, because the tag is built from the mocked PyPy host and validated against its tags. The `Py_DEBUG` rows of `test_get_abi_tag_windows` fail too. `test_platform_linux32` fails on Linux.
- On the parent, `test_get_abi_tag_windows_without_soabi` and `test_is_debug_from_sysconfig` pass. They guard parent behaviour.
- With the fix's three helpers renamed throughout, everything passes, so no name is left.
- A fix that reads the implementation from `SOABI`, the version from `VERSION` and the pointer size from `SIZEOF_VOID_P`, honours `Py_DEBUG`, and leaves `dist_files` alone should pass everything, whether or not it still calls `tags.sys_tags()` where host and target agree.
- A fix that keeps `tags.interpreter_name()` fails `test_cross_tags` and `test_non_cpython_target`. The issue lists that input as host-derived.
- A fix that uses `py_version_short` in place of `VERSION` fails `test_cross_tags`.
- A fix that validates the target against `tags.sys_tags()` fails `test_cross_tags`. One that drops the check fails `test_limited_api_newer_than_target`.
- A fix that leaves `_is_32bit_interpreter` alone fails the `aarch64` rows of `test_cross_tags` and `test_platform_linux32`.
- A fix that lets `EXT_SUFFIX` override an explicit `Py_DEBUG` fails `test_is_debug_from_sysconfig`. One that drops the warning fails `test_get_abi_tag_windows_without_soabi`.

## Independent audit

Two blind audit rounds from another model family returned `revise`. Every finding held when tested against the issue and the parent, so all are accepted and none was rejected.

First round, three over-relaxations and one pin left in place:

- **The host implementation in `test_cross_tags` was made to agree with the target. Accepted.** My first version patched the host to `cp` and called this a judgement call: the issue gives no target source for the implementation, and it says other implementations keep their own handling. The audit points to the issue's opening sentence and its list of host-derived inputs, which include `tags.interpreter_name()`. Checked against the parent, the target source is there too: `get_abi_tag()` already reads `SOABI`. The host is `pp` again.
- **The same in `test_non_cpython_target`. Accepted, for the same reason.** The maintainers' form is restored, and the test no longer appears in the diff.
- **The Windows debug flag was relaxed in every case. Accepted, in the form the audit proposes.** The issue's statement covers the tag's ABI, and the parent's `get_flag` already names `Py_DEBUG` as the source. The flag is exact again where `Py_DEBUG` is set. It stays open only where the target's debug status rests on the fix's `EXT_SUFFIX` rule.
- **Every call to `tags.sys_tags()` was forbidden. Accepted.** In my first version I kept this because the issue names that function. The audit is right that the issue's complaint is about validating against the host's tags, not about calling the function. The function now returns the host's tags, and validating against them fails.

Second round, two stated checks deleted with whole tests:

- **Deleting `test_is_debug_from_sysconfig` also deleted the precedence of an explicit `Py_DEBUG`. Accepted.** I had removed the whole test because it called a private helper. The parent's `get_flag` states that precedence, and the test sets `EXT_SUFFIX` against `Py_DEBUG` on purpose, which the Windows test does not. The test is back, reading the tag through `get_abi_tag()`.
- **Deleting `test_get_abi_tag_windows_without_soabi` also deleted the warning. Accepted.** I had called the warning incidental. It is the parent's own behaviour on this path, with the parent's own text. The test is back with that check and without the Windows version form or the `EXT_SUFFIX` rule.
- Across both rounds the audit counted the other removals beyond names, messages and formats as justified, and they are unchanged.
