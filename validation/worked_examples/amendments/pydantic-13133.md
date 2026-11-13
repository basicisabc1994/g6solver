# pydantic-13133: scoring amendment

Issue pydantic/pydantic#10565, "Unhelpful `AssertionError` when encountering a `TypeError` when evaluating a forward annotation since 2.9". The issue has no comments. The task is the issue and the repository at the parent commit.

The stated behaviour: for a forward reference nested in a generic, such as `list["1 + 'a'"]`, evaluation must not end in that `AssertionError`. It must end in the helpful error that #10030 introduced.

## tests/test_forward_ref.py, `test_invalid_forward_ref`

**Pinned format.** The second block required, on Python 3.11 and later, the exact text `Unable to evaluate type annotation list['CustomType[int]'].`, and a different text on 3.10. The `list[...]` rendering comes from the fix formatting the whole value with `repr`. The issue gives no format for the nested case. Reporting the inner reference, `'CustomType[int]'`, would be just as good. At the parent the error path can only print a bare `ForwardRef`, which is why the `assert` is there.

Searched the parent's `pydantic`, `tests` and `docs` for `Unable to evaluate type annotation`. It appears in the code on this exact error path and in the older test directly above. The parent's docstring there promises "a helpful error", and its message names the annotation that failed. So the wording and naming the annotation are existing convention. How a nested annotation is rendered is what the fix invented.

The version branch and the exact text are replaced by a pattern that requires the project's wording followed by `CustomType[int]`. What stays checked: `pytest.raises(TypeError)`, so the `AssertionError` still fails, the project's wording for this error, and that the failing annotation is named. Either the inner reference or the outer container may be shown. The first block of the test, for a plain forward reference, keeps its exact message, because the parent already determines it.

## Left unchanged

The maintainers' other test changes here remove tests of `eval_type_backport` or restate one, and they pin nothing beyond behaviour that already held.

No test removed.

## Controls

- With the fix's message changed to show the inner reference instead of the outer container, `test_invalid_forward_ref` passes.
- With the message changed to the project's wording alone, naming no annotation, the test fails on the pattern.

## Independent audit

A blind audit from another model family returned `revise` with one finding, counted under over-relaxed and stated-check-could-stay. My first version kept only the wording, which let through a generic error that names no annotation. The finding held against the parent's docstring, message and older test, so it is accepted and the annotation is now required, as above. No finding was rejected.
