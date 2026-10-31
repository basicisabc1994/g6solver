# attrs-1509: scoring amendment

Issue python-attrs/attrs#1479, "Alias not available during field transformation". The task is the issue, its one comment before PR 1509 opened (2026-02-08T23:03:25Z, a request for attention with no content), and the repository at the parent commit.

The issue asks one thing: `alias` should not be `None` inside a `field_transformer`, and its example shows the values expected.

The fix adds a new field, `Attribute.alias_is_default`. Searched the parent's `src`, `docs` and `tests` for `alias_is_default`, and the issue text. No hits. The name is the fix's own, but what it means is not. The parent's `docs/extending.md` says explicit user-provided aliases can be detected in a transformer, and the parent's `_transform_attrs` gives a default only to an unset alias. So at the parent a transformer's rename treats the two kinds differently: an automatic alias follows the new name, and an explicit alias stays, even when it equals the default. Where a flag assertion carried that difference, it is replaced by that observable check, as rule 4(a) prefers. The other flag assertions are removed.

## tests/utils.py and four comparisons

**Pinned name.** `simple_attr()` and the expected `Attribute(...)` objects passed `alias_is_default=True` or a parametrised value. Dropping the argument is not enough: with the fix's extra field, an `Attribute` built with an explicit alias compares unequal to a real field. So a helper `fields_view()` is added to `tests/utils.py`, and the four comparisons go through it. It keeps what comparing tuples of `Attribute` checks at the parent: the collection is a tuple, each item is exactly an `Attribute`, and it lists the fields `Attribute.__eq__` compares (all except `inherited`). The tuple and class checks matter because the parent's `attrs.fields()` documents its return as a tuple of `attrs.Attribute`, and the generated `__eq__` refuses another class. A fix with no new field, or one under any other name, is treated the same.

What stays checked, in `test_fields`, `test_programmatic`, `test_these` and `test_can_unpickle_25_3_attributes`: a tuple of `Attribute`, and every field that existed at the parent, compared as before, including `alias`. `test_can_unpickle_25_3_attributes` keeps its parametrisation, so its test ids do not change, but it no longer uses the flag. Old pickles must still load.

## tests/test_hooks.py

**Pinned name.** `test_hook_alias_available` recorded `a.alias_is_default` next to the name and alias. It now records `(a.name, a.alias)`. The aliases stay checked, including the private-name stripping and the explicit alias from the issue's example. This is the test that must go from failing to passing.

**Pinned name.** A trailing `assert f.alias_is_default is ...` is removed from `test_hook_evolve_name_updates_auto_alias`, `test_hook_evolve_name_keeps_explicit_alias`, `test_hook_new_field_without_alias` and `test_hook_evolve_alias_sets_not_default`. In each one the `f.alias` assertion stays. These hold at the parent: a transformer that renames a field gets an `__init__` argument named after the new name. That is public behaviour at the parent, so it stays. It adds no new burden: a control that only moved the alias step also failed the older `test_hook_add_field`, which the fix did not touch.

**Pinned name, replaced by behaviour.** `test_hook_explicit_alias_matching_default` asserted that the flag is false for an explicit alias equal to the default. That assertion is replaced: a transformer renames such a field, and the alias must stay. This holds at the parent and with the fix. `test_hook_evolve_name_keeps_explicit_alias` cannot do this job, because its alias differs from the default.

**Not extended.** `test_hook_evolve_alias_sets_not_default` could also be rewritten, by renaming after `evolve(alias=...)`. It stays a plain removal. The docs' statement is about aliases the user gives on the field. Searched the parent's `docs` and `tests` for every `evolve(` and for a rename chained after an alias: nothing does this, so that would be a scenario I invented, not one the parent states.

## tests/test_hooks.py and tests/test_make.py, the conflicting-defaults message

**Pinned format.** `test_hook_conflicting_defaults_after_reorder` and `test_conflicting_defaults` compared the whole `ValueError` text, ending in `alias='x'` or `alias='y'`. The parent's text ends in `alias=None`. Which one appears depends on when the fix resolves aliases relative to this check, and with no transformer the issue says nothing at all. The fix's repr also hides its new field, which a different fix need not do.

The message embeds `repr(attribute)`, and that repr is built from `Attribute.__slots__` in order. So any exact text past the attribute's name also fixes where a new field may sit, which the task does not state. The check becomes `startswith` on the explanation through `Attribute(name='x'` (or `'y'`). What stays checked: the exception type, the project's wording, and which attribute is in question.

## tests/test_make.py

**Pinned name.** `test_alias_is_default_pickle_roundtrip` loses its flag assertion. The alias round trip stays.

**Pinned name, replaced by behaviour.** `test_alias_is_default` asserted the flag on four fields: two automatic, one explicit, one explicit and equal to the default. The same class now goes through a transformer that renames every field, and the four assertions read the resulting alias: the automatic ones follow the new name, and the explicit ones stay. The test keeps its id. Its name and docstring are the maintainers' and are not checks.

No test removed.

## Controls

- With the fix's new field renamed throughout its source, all the amended tests in the three modules pass, so no use of the name is left.
- With that field moved to the front of `__slots__` and made visible in the repr, both conflicting-defaults tests pass.
- `fields_view()` refuses a list, and refuses a look-alike object with the same attributes.
- A fix that guesses whether an alias is automatic by comparing it with the default fails exactly the two tests rewritten above, and nothing else. On the previous version of this diff it passed everything, which is the gap the second audit found. It breaks public behaviour: the constructor argument changes.
- The parent, with no fix, fails only `test_hook_alias_available`.

## Independent audit

Two blind audit rounds from another model family returned `revise`. Every finding held when tested against the parent, so all are accepted and none was rejected.

First round:

- **The helper dropped checks the parent states.** The first version of `fields_view()` accepted a list and any object with the right attributes, so the tuple-to-tuple comparisons lost their collection and class checks. Accepted: see the section on `tests/utils.py` above. The audit counted this one finding under over-relaxed, stated-check-could-stay and beyond-name-message-format.
- **Both message checks were still pinned.** The first version kept the exact repr through `alias=`, so a fix placing a new field earlier could fail. I had kept that text as the smallest diff and judged the case unlikely, but the placement is still unstated. Accepted: see the section on the conflicting-defaults message above. The audit counted this as two findings, one per test.

Second round:

- **Removing the flag also removed a stated distinction.** The first versions removed the flag assertion from `test_hook_explicit_alias_matching_default` and deleted `test_alias_is_default`, on the reasoning that nothing name-free could be checked. That reasoning was wrong. Run at the parent and with the fix, an explicit alias equal to the default survives a transformer's rename, and an automatic one does not. A fix guessing from the value passed the whole previous diff. Under rule 4 a test may be deleted only if nothing in it is stated, so both are now rewritten, and `lost` is 0. The audit counted this one finding, across those two places, under over-relaxed, stated-check-could-stay and beyond-name-message-format.
