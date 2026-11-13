# click-3678: scoring amendment

Issue pallets/click#2819, "help not resolving automatically". The task is the issue, the comments made before PR 3678 opened (2026-07-08T12:19:10Z), and the repository at the parent commit.

Searched at the parent, in `src`, `docs`, `tests` and `CHANGES.md`: `_click_default_help`, `reserved for the automatic`, `is used by an argument`, `overwrite each other`. No hits. None of them appears in the issue or its comments either.

The docstrings in the amended tests still say what the maintainers wrote. They are not checks and were left alone.

## tests/test_basic.py, `test_param_named_help`

**Pinned name.** `assert help_option.name == "_click_default_help"`. The fix invented this private storage name. Removed: a parameter named `help` not clashing is checked directly by the lines that follow, through the public `runner.invoke`.

**Behaviour the task leaves open (judgement call).** For a command with a parameter named `help`, the test required `get_help_option()` to return an option and `--help` to print the help page. The parent's `docs/documentation.md` says the opposite: "if a command itself implements a parameter named `help` then the default help will not be run", and the issue quotes the older wording, "the default help parameter stops accepting it". The fix had to rewrite that sentence to say the help option survives. The issue's `helps` example and one of the two ideas in the first comment point the other way. So the task supports both outcomes, and a fix restoring the documented behaviour honours it.

The amended test requires a help option only when the command has no such parameter, and when one exists it must still work. What stays checked: the value reaches the callback in all four cases (`"value\n"`, `"\n"`), exit code 0, and with a help option present `--help` prints "Show this message and exit." and exits 0. The issue's failure, `--help` reaching the help option and ending in `Missing argument 'HELP'`, still fails.

## tests/test_basic.py, `test_param_squatting_help_option_name`

**Test removed.** It declares an argument called `_click_default_help` and expects a warning containing "reserved for the automatic help option" and exit code 2. Name, message and outcome all exist only because of the fix's reserved name. A fix without one has nothing to warn about. Nothing in it is stated, so it is deleted. This is the 1 in `lost`.

## tests/test_info_dict.py

**Pinned name.** `HELP_OPTION["name"]` was `"_click_default_help"`; at the parent it was `"help"`. Whether the help option's stored name changes is the fix's choice, so the value becomes `unittest.mock.ANY`. The rest of the `to_info_dict()` output stays compared exactly: the key exists and all the other fields match.

## tests/test_options.py, `test_argument_sharing_param_name_warning`

**Pinned warning.** `pytest.warns(UserWarning, match="'target' is used by an argument")`. The new warning is not in the issue. The first comment offers a warning only for a parameter named `help`, as one of two options, and it says nothing about an option and an argument sharing a name. Loosening to the warning type alone would still require a warning the task does not state, so the wrapper is removed, and `@pytest.mark.filterwarnings("ignore")` is added so a fix that does warn also passes under the project's `error` filter.

What stays checked: the output is `from_argument` and the exit code is 0, as at the parent.

## Left unchanged

`test_option_reusing_help_flag` uses only public API and the issue's third example. `test_options_sharing_name_no_warning` holds at the parent and covers documented feature switches.

## Controls

- With the fix's storage name and warning texts changed in its source, all the amended tests in the three modules pass, so none of them is still required.
- A different fix, written at the parent, that drops the automatic help option when a parameter takes its name also passes them all. That is the documented behaviour the judgement call above lets through.
