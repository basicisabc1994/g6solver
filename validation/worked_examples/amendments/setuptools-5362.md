# setuptools-5362: scoring amendment

Issue pypa/setuptools#5360, "[BUG] Wheel tagging uses the build interpreter rather than the target when cross-compiling". The task is the issue and the repository at the parent commit. The issue's one comment came after PR 5362 opened (2026-10-19T20:57:07Z) and is not part of the task.

What the task states:

- The wheel's tag, and the check on it, must come from the target. `tags.sys_tags()`, `tags.interpreter_version()` and `tags.interpreter_name()` are listed as host-derived inputs.
- For CPython POSIX builds, `sysconfig.get_config_var("VERSION")` is the target's version. `sysconfig.get_python_version()`, `py_version_short` and `py_version_nodot` must not stand in for it.
- A `sys.version_info` comparison in `_validate_py_limited_api` should use target metadata.
- `_is_32bit_interpreter()` is "worth auditing", because the target's `SIZEOF_VOID_P` is the relevant value.
- Other implementations keep their separate handling.

Searched the parent's `setuptools/command/bdist_wheel.py`, `setuptools/command/build_ext.py` and `setuptools/tests/test_bdist_wheel.py`, and the issue, for `_interpreter_name`, `_python_version` and `_is_debug`. No hits. The parent has no target-version helper of any name, and debug status is not mentioned in the issue.

## setuptools/tests/test_bdist_wheel.py, the import

**Pinned names.** The module imports `_interpreter_name`, `_is_debug` and `_python_version`. All three are the fix's own, so a fix without them fails at import, and with it every test in the module. They are removed from the import. `_is_32bit_interpreter` and `get_platform` exist at the parent and stay.

## `test_cross_tags`

This is the test that goes from failing to passing, and it stays. Three things in it go beyond the task.

**Behaviour the task does not state.** `version_available=False` set `VERSION` to `None` and required the version to come from `SOABI`. The issue never names `SOABI` and says nothing of a missing `VERSION`. A fix that falls back to the host in that case fails. `VERSION` is now always given. The parametrisation stays, so test ids do not change, and those rows now repeat the `True` rows.

**Behaviour the task leaves open (judgement call).** The host's `tags.interpreter_name()` was patched to `pp` while the target is CPython, which requires the implementation to be read from `SOABI`. The issue does list `interpreter_name()` as host-derived. But it gives no target source for the implementation, it describes no build across implementations, and its suggested direction is `cpython_tags` "for the CPython path" with "separate handling for other implementations". A fix that keeps `tags.interpreter_name()` to choose that path, and reads the version from `VERSION`, is right for every build the issue describes. The patch now says `cp`.

**Pinned value.** `tags.interpreter_version()` was patched to `'999'`. That also fails a fix which leaves the `abi_tag` property alone, because `stable_abi_tag` tests `impl_version[0] == '3'`. The patch now returns the mocked host's own version, which matches the mocked `sys.version_info`. Using that value for the tag still gives the host's version, so a host-version tag still fails whenever host and target differ.

What stays checked: the tag equals the target's `cp` version, ABI and platform for each host and target, the `ValueError` on a free-threaded target before 3.15, the extension filename, and `tags.sys_tags()` never being called. That last one stays because the issue itself names `sys_tags()` as the input that describes the host. `struct.calcsize` stays reversed, so `SIZEOF_VOID_P` is still required on the `aarch64` rows.

## `test_get_abi_tag_windows`

**Behaviour the task does not state.** The test required the `d` flag to follow `Py_DEBUG` or `EXT_SUFFIX`, and to ignore `sys.gettotalrefcount`. The parent's version of this test asserted the opposite, and the issue never mentions debug builds. A fix that leaves this branch alone is honest. Each assertion now accepts the host's answer or the target's. So when host and target agree the tag is exact, and when they disagree either is allowed. What stays checked: the `cp313` or `cp313t` base and the `d` suffix when the two agree.

## `test_non_cpython_target`

**Behaviour the task leaves open (judgement call).** A CPython host aimed at a PyPy or GraalPy target required the implementation to come from `SOABI`, the same point as above. What the issue states is that other implementations keep working. The test now describes a host of the same implementation: `interpreter_name`, `interpreter_version` and `sys_tags` are patched to agree with the target. What stays checked: the implementation's tag and the parent's ABI format, `pypy311_pp73` and `graalpy231_311_native`. This holds at the parent, and keeps a fix honest about the other path.

## `test_config_fallback`

**Pinned names.** The parametrisation named the three private helpers. Those rows are removed. The `_is_32bit_interpreter` row stays, so its four test ids do not change. Removing them is the only way to lose the names: what each row checks is a private function's fallback order.

## Tests removed

All four exist only to pin what the task does not state.

- `test_dist_files_target_version`, 2 cases. The version recorded in `dist_files`. The issue is about the wheel's tag. It does not mention `dist_files`.
- `test_get_abi_tag_windows_without_soabi`, 2 cases. Debug status from `EXT_SUFFIX`, and `VERSION` in the form `312`. The issue speaks of POSIX builds only. The `Py_DEBUG` warning in it is there only because the warning fires.
- `test_is_debug_from_sysconfig`, 2 cases. Calls `_is_debug`, which is a pinned name, and checks the same unstated debug rule.
- `test_target_python_version`, 15 cases. Calls `_python_version` and pins its fallback order: Windows forms, an integer, `SOABI` parsing for four implementations, and the host. The only stated row, `VERSION` read from sysconfig, is covered by `test_cross_tags`. Its other point, `VERSION` winning over a disagreeing `SOABI`, is a case the issue does not describe.

With `test_config_fallback` that is 33 parametrised cases gone: 21 from these four tests and 12 from the three rows above.

## Left unchanged

`test_limited_api_newer_than_target` keeps `AssertionError` and `unsupported tag`: the parent's text and behaviour for a limited API newer than the interpreter. `test_platform_linux32` keeps requiring `SIZEOF_VOID_P`. The issue hedges with "worth auditing", but it names the value and says it is the relevant one, so this is kept as stated. `test_limited_api` and `test_abi3_filename_consistent` use public behaviour and the parent's helpers.

## Controls

Reasoning, as nothing here has been run.

- On the parent, `test_cross_tags` fails on the rows that reach `get_tag()`, because `tags.sys_tags()` is called and `pytest.fail` fires. `test_platform_linux32` fails too, on Linux.
- With the fix's three helpers renamed throughout, everything passes, so no name is left.
- A different fix that keeps `tags.interpreter_name()`, reads `VERSION`, calls `tags.cpython_tags` and reads `SIZEOF_VOID_P`, and leaves debug handling and `dist_files` alone, should pass everything.
- A fix that uses `py_version_short` in place of `VERSION` fails `test_cross_tags`, because that value stays the host's.
- A fix that keeps `tags.sys_tags()` on the CPython path fails `test_cross_tags`.
- A fix that drops the tag check fails `test_limited_api_newer_than_target`.
- A fix that leaves `_is_32bit_interpreter` alone fails the `aarch64` rows of `test_cross_tags` and `test_platform_linux32`.
