# pluggy-799: scoring amendment

Issue pytest-dev/pluggy#792, "Hook tracing crashes with specced Mock arguments and results". The task is the issue, the two comments made before PR 799 opened (2026-10-17T07:11:42Z), and the repository at the parent commit. One comment links a proposed fix in another PR. Its content is not part of the issue and was not used.

The stated behaviour: with tracing on, a `Mock(spec=str)` argument and a `Mock(spec=dict)` first result must not raise. "Tracing should log the mock without changing the hook result." The issue names the cause: `Mock(spec=...)` reports its spec through `__class__`, so `isinstance` routes these objects into the `str` and `dict` handling.

The parent states the trace format. `test_plugin_tracing` pins `  hello [hook]\n      arg: 3\n` and `  finish hello --> [4] [hook]\n`, and `_render_value` renders anything that is not a `str` with `str()`.

Searched the parent's `src/pluggy/_tracing.py`, `src/pluggy/_manager.py` and `testing/test_tracer.py` for `raised in`. The labels are `str` and `repr`. `format` does not appear.

## testing/test_tracer.py, `test_tracing_impostor_value_is_traced_as_itself`

This is the test that goes from failing to passing.

The `mock-spec-str` and `mock-spec-dict` rows are left as exact as they were. A Mock is not a `str`, so the parent's format applies, and for a Mock `str()` and `repr()` give the same text.

**Pinned format, `fake-class-str` row only.** The first trace line required `str(value)`, `fake str`. This object is not a Mock, and its `str()` and `repr()` differ. A fix that keeps it on the `str` path and falls back on `repr` is honest, and so is one that quotes the text as the parent does for a `str` with a space. The issue does not choose. The first line now accepts `str(value)`, `repr(value)` or `repr(str(value))`. What stays checked: the prefix and key, `is value` on the hook result, and the whole `finish` line, because `_format_message` renders positional arguments with `_safe_str` at the parent.

**Judgement call.** The `fake-class-str` row stays. It is not a specced Mock, but the issue names `__class__` as the cause, and this object does exactly that and nothing else. A fix that recognises `Mock` by name passes the issue's example and fails this row.

## `test_tracing_dict_result_is_not_read_as_arguments`

**Behaviour the task does not state.** The test required a real `dict` result to appear on the `finish` line. At the parent such a result is listed as call arguments, under `finish echo --> [hook]`. The fix changes that with a trailing `{}`. The issue is about a mock claiming to be a `dict` and says nothing of real ones, so a fix that leaves them as they are is honest. The check of the `finish` line is removed. What stays checked: the hook result and the first trace line, both as at the parent.

## Test removed

`test_broken_str_subclass_value_does_not_raise` is removed. The whole test exists only to pin what the task does not state. It requires the label `raised in format()`, a third label the fix invented, and it requires a `str` subclass whose `splitlines` raises not to raise from tracing. That object is a real `str` subclass and no `__class__` is involved. The issue does not mention it, the parent raises, and a fix based on `type()` still raises. Loosening the text would not help, because not raising is itself unstated.

`FakeStr` and `echo_pm` are used as before. No other test was changed.

## Controls

Reasoning, as nothing here has been run.

- On the parent all three `test_tracing_impostor_value_is_traced_as_itself` rows fail: `TypeError` for the two mocks, as the issue reports, and `AttributeError` for `FakeStr`.
- With the maintainers' fix they pass. `test_tracing_dict_result_is_not_read_as_arguments` passes on both.
- With the `{}` in `_manager.py` and the `_safe` guard both removed from the fix, leaving only `type()` in place of `isinstance()`, the amended module should pass, so neither is required.
- With the `format` label in the fix changed, the amended module should pass.
- A fix that catches the error and logs nothing for the value fails on the first or second line. So does one that logs a failure description for a Mock, because the parent's format for a non-`str` value applies.
- A fix that special-cases `str` only fails `mock-spec-dict`.
