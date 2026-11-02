# setuptools-5352: scoring amendment

Issue pypa/setuptools#5351, "[BUG] cross compiling 3.15 abi3 wheels fails due to new multiarch support". The task is the issue, the three comments made before PR 5352 opened (2026-10-16T17:17:30Z), and the repository at the parent commit. The comment pointing at the PR came after it opened and is not part of the task.

The stated behaviour, in the issue's words: "The target architecture should be specified in the generated shared library or it should be agnostic and not specify the multiarch name." A comment adds that changing the pattern to `.abi3.` fixes the immediate problem, at the cost of multiarch wheels, and that abi3t "may" need thought. The issue also gives the build interpreter's `EXTENSION_SUFFIXES` and the environment of the cross build.

Searched the parent's `setuptools/command/build_ext.py`, `setuptools/tests/test_build_ext.py` and `setuptools/tests/test_bdist_wheel.py`, and the issue and comments, for `SOABI_PLATFORM`. No hits. The issue does not say which configuration variable gives the target's name. `EXT_SUFFIX`, `SOABI` and `MULTIARCH` would all serve.

## setuptools/tests/test_build_ext.py, fixture `host_multiarch` (new)

Neither maintainers' test reproduces the issue's cause. On any Python before 3.15 the real `EXTENSION_SUFFIXES` has no multiarch `abi3` entry, so the parent returns `.abi3.so`. Those tests failed there only because `.abi3-aarch64-linux-gnu.so` was required. Once `.abi3.so` is accepted, as the issue says it must be, nothing would fail on the parent. So the fixture reproduces what the issue states:

- `EXTENSION_SUFFIXES` is the issue's list, patched on `importlib.machinery` and, because the parent binds the name at import, on `setuptools.command.build_ext`.
- `_PYTHON_HOST_PLATFORM` and `_PYTHON_SYSCONFIGDATA_NAME` are set, as in the issue's build.
- `SOABI` and `MULTIARCH` are given the target's values, and stdlib `sysconfig` reads the same data as `distutils.sysconfig`, as it does in such a build. Without this a fix reading another source would see the host's values, which would pin `SOABI_PLATFORM` again.

## `test_abi3_filename_cross`

This is the test that goes from failing to passing.

**Pinned format.** It required exactly `eggs.abi3{flags}-aarch64-linux-gnu.so`. The issue allows the target's name or none. The check now accepts `eggs.abi3`, with or without `t`, followed by `-aarch64-linux-gnu` or nothing, then `.so`. `t` is optional because a comment only says abi3t "may" matter. What stays checked: only the target's name or none, so the host's `x86_64` name is refused, and the `spam.normal` line is unchanged.

## `test_abi3_suffix`

**Pinned format.** Every row compared `get_abi3_suffix()` exactly, and which rows gain a qualifier followed the fix's use of `SOABI_PLATFORM`. A fix that parses `EXT_SUFFIX`, or that picks `.abi3.so`, fails rows the issue permits. For each of the 16 rows left, the result must now be one of a closed list:

- the stable-ABI tag, `.abi3`, plus `.abi3t` only when the row's target is free-threaded;
- then no name, or the target's name from one of its sources: `EXT_SUFFIX`, the row's `SOABI_PLATFORM`, or the fixture's `SOABI` and `MULTIARCH`;
- then `.so` or the extension of the row's `EXT_SUFFIX`.

That is the issue's own rule, "target architecture or agnostic", applied to each row. It refuses the host's name, any other name, and an `.abi3t` suffix for a target that is not free-threaded. `expected` stays in the parametrisation so test ids do not change, and it is no longer read.

**Test cases removed: 17, the last rows of the parametrisation.** Windows `.pyd`, `_d` debug suffixes, PyPy, `.dll`, Cygwin, HP-UX, empty or missing `EXT_SUFFIX`, and `HAVE_DYNAMIC_LOADING`. Each exists only to pin how the fix decides a suffix from target configuration on platforms the issue does not mention. A fix that picks `.abi3.so` from `EXTENSION_SUFFIXES`, the issue's own "agnostic", returns `.abi3.so` for all of them, and nothing about them can be stated. The test itself stays, with its remaining ids unchanged.

## setuptools/tests/test_bdist_wheel.py, `test_abi3_filename_consistent`

**Pinned name.** `extension.abi3-target-platform{ext}` in the wheel. It depends on `SOABI_PLATFORM` setting the qualifier. What the test says it is for is unchanged: wheel creation must not change the build command's output filenames. So the file the wheel must contain is now the one `build_ext` reported, and that name must start with `extension.abi3` and end with the platform's extension. `assert build_ext.get_outputs() == outputs` stays. This test now passes on the parent, which it did not before.

No whole test removed. 17 parametrised cases removed.

## Left unchanged

`test_abi3_filename` is the parent's test. The `Py_GIL_DISABLED` rows of `test_abi3_suffix` stay as inputs, including the `VERSION` ones.

## Controls

Reasoning, as nothing here has been run.

- On the parent, with `host_multiarch`, `get_abi3_suffix()` returns `.abi3-x86_64-linux-gnu.so` on every Python version. So both `test_abi3_filename_cross` cases and all 16 `test_abi3_suffix` rows fail there.
- With the maintainers' fix they pass. The result is `.abi3-<SOABI_PLATFORM>.so`, `.abi3t-aarch64-linux-gnu.so`, or the unqualified suffix where `SOABI_PLATFORM` is unset, all of them in each row's list.
- With `SOABI_PLATFORM` renamed in the fix, the rows and tests still pass, now through the unqualified suffix, so the name is not required.
- A fix that changes the pattern to `.abi3.` passes. A fix that takes the qualifier from `EXT_SUFFIX`, `SOABI` or `MULTIARCH` passes.
- A fix that keeps the first `.abi3` entry of `EXTENSION_SUFFIXES` fails, on the host's name.
- A fix that picks `.abi3t` for a target that is not free-threaded fails.
- A fix that gates on the cross-build environment passes, because the fixture sets it.

## Independent audit

A blind audit from another model family returned `revise`, with one finding, counted under over-relaxed and stated-check-could-stay.

- **`test_abi3_suffix` only refused the host's name. Accepted.** My first version checked that the suffix starts with `.abi3` and does not carry `x86_64-linux-gnu`. That let through a wrong name for some other platform, and an `.abi3t` suffix for a target that cannot load it. The issue states which outcomes are right, the target's name or none, so the check is now that closed list. The audit suggests checking through `get_ext_filename()`. `test_abi3_filename_cross` already does that, and `get_abi3_suffix()` is the function the issue itself points at, so the rows keep calling it.
- The audit counts the removal of 17 cases as a change beyond names, messages and formats, and judges it justified. It is unchanged.
