# pluggy-799: scoring amendment

Issue pytest-dev/pluggy#792, "Hook tracing crashes with specced Mock arguments and results". The task is the issue, the two comments made before PR 799 opened (2026-10-17T07:11:42Z), and the repository at the parent commit. One comment links a proposed fix in another PR. Its content is not part of the issue and was not used.

The stated behaviour: with tracing on, a `Mock(spec=str)` argument and a `Mock(spec=dict)` first result must not raise. "Tracing should log the mock without changing the hook result." The issue names the cause: `Mock(spec=...)` reports its spec through `__class__`, so `isinstance` routes these objects into the `str` and `dict` handling.

The parent states the trace format. `test_plugin_tracing` pins `  hello [hook]\n      arg: 3\n` and `  finish hello --> [4] [hook]\n`, and `_render_value` renders anything that is not a `str` with `str()`.

Searched the parent's `src/pluggy/_tracing.py`, `src/pluggy/_manager.py` and `testing/test_tracer.py` for `raised in`. The labels are `str` and `repr`. `format` does not appear.

## testing/test_tracer.py, `test_tracing_impostor_value_is_traced_as_itself`

This is the test that goes from failing to passing.

The `mock-spec-str` and `mock-spec-dict` rows keep their exact first line, `str(value)`. A Mock is not a `str`, so the parent's format applies.

**Pinned format, `fake-class-str` row only.** The first trace line required `str(value)`, `fake str`. This object is not a Mock, and its `str()` and `repr()` differ. A fix that keeps it on the `str` path and falls back on `repr` is honest, and so is one that quotes the text as the parent does for a `str` with a space. The issue does not choose. For this row only, the first line accepts `str(value)`, `repr(value)` or `repr(str(value))`. The row is picked by `type(value) is FakeStr`, which the object's `__class__` cannot fool. What stays checked: the prefix and key, `is value` on the hook result, and the whole `finish` line, because `_format_message` renders positional arguments with `_safe_str` at the parent.

**Judgement call.** The `fake-class-str` row stays. It is not a specced Mock, but the issue names `__class__` as the cause, and this object does exactly that and nothing else. A fix that recognises `Mock` by name passes the issue's example and fails this row.

## `test_tracing_dict_result_is_not_read_as_arguments`

**Pinned format.** The test required a real `dict` result to appear inline on the `finish` line. At the parent such a result is listed as call arguments, as `  finish echo --> [hook]` followed by `      a: 1`. The fix changes that with a trailing `{}`. The issue is about a mock claiming to be a `dict` and says nothing of real ones, so a fix that leaves them as they are is honest. The exact text of the `finish` line is replaced by what holds in both forms: there are exactly two records, the call and its `finish` line, and the `finish` line starts with the parent's `  finish echo -->`, carries the `[hook]` tag and shows the returned key and value. What stays checked as well: the hook result and the first trace line, both as at the parent.

## Test removed

`test_broken_str_subclass_value_does_not_raise` is removed. The whole test exists only to pin what the task does not state. It requires the label `raised in format()`, a third label the fix invented, and it requires a `str` subclass whose `splitlines` raises not to raise from tracing. That object is a real `str` subclass and no `__class__` is involved. The issue does not mention it, the parent raises, and a fix based on `type()` still raises. Loosening the text would not help, because not raising is itself unstated.

`FakeStr` and `echo_pm` are used as before. No other test was changed.

## Controls

Reasoning, as nothing here has been run.

- On the parent all three `test_tracing_impostor_value_is_traced_as_itself` rows fail: `TypeError` for the two mocks, as the issue reports, and `AttributeError` for `FakeStr`.
- With the maintainers' fix they pass. `test_tracing_dict_result_is_not_read_as_arguments` passes on both, with the inline form and with the parent's form.
- With the `{}` in `_manager.py` and the `_safe` guard both removed from the fix, leaving only `type()` in place of `isinstance()`, the amended module should pass, so neither is required.
- With the `format` label in the fix changed, the amended module should pass.
- A fix that drops the `finish` line for a `dict` result, drops the result or the tag from it, or writes an extra record, fails `test_tracing_dict_result_is_not_read_as_arguments`.
- A fix that catches the error and logs nothing for the value fails on the first or second line. So does one that logs a failure description for a Mock, because the parent's format for a non-`str` value applies.
- A fix that special-cases `str` only fails `mock-spec-dict`.
- A fix that quotes a Mock's text as if it were a `str` fails the two mock rows.

## Independent audit

Three blind audit rounds from another model family returned `revise`, each with one finding, counted under over-relaxed and stated-check-could-stay. All three held against the parent, so all are accepted and none was rejected.

- **First round: the `dict` result test no longer checked the completion trace. Accepted.** My first version kept only the first line. That let the `finish` line disappear for this case, or lose the result, and the parent states both: `_manager.py` logs a `finish` line for every call, and `_tracing.py` shows a `dict` as key and value. The test then required that line, its prefix, and the returned key and value, and accepted the parent's form and the fix's.
- **Second round: that check still dropped the number of records and the tag. Accepted.** The parent emits exactly one call record and one `finish` record through the `hook` tracer, and its tests compare both. The test now requires exactly two records and the `[hook]` tag on the second, and it still accepts both renderings of the `dict`.
- **Third round: the rendering allowance reached the mock rows too. Accepted.** My note said those rows stayed exact, but the list of renderings applied to every row, and for a Mock `repr(str(value))` is a quoted text the parent never prints. The parent renders a Mock argument with `str()`, so those rows are exact again. The allowance applies to `FakeStr` alone.
- All three rounds count the removed test as a change beyond names, messages and formats, and judge it justified. It is unchanged.
