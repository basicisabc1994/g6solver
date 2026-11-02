# python-dotenv-711: scoring amendment

Issue theskumar/python-dotenv#710, "`set_key` / `dotenv set` report a temp file path when the target's directory doesn't exist". The issue has no comments. The task is the issue and the repository at the parent commit.

The stated behaviour:

- "the library raises an error that names the `.env` path", where it now "names an internal temp file instead".
- The CLI "prints a full traceback" today. "The CLI prints a short error and exits with code 2, as `stream_file` does for `get`/`list`."
- "The same probably applies when the directory isn't writable."
- The cause names `set_value` and `unset` as not catching `OSError`.

Searched the parent's `src/dotenv/cli.py`, `src/dotenv/main.py`, `tests/test_cli.py` and `tests/test_main.py` for `Error writing` and for `.filename` on an exception. No hits. The parent's only wording here is `Error opening env file: {exc}` in `stream_file`, and that says opening.

## tests/test_cli.py, `test_set_missing_directory` and `test_unset_read_only_directory`

**Pinned message.** Both compared the whole output to `Error writing env file: [Errno 2] No such file or directory: '...'` or the `[Errno 13]` equivalent. The prefix "Error writing env file" is the fix's. A fix that reuses `stream_file`'s wording, or that prints its own short sentence, is what the issue asks for. So is a library error whose text differs from the operating system's.

The exact output is replaced by what the issue states, in the style of the parent's neighbouring tests:

- exit code 2;
- the `.env` path in the output, and no `.tmp_` name;
- no traceback;
- a short message, at most four lines.

Four lines allows click's own usage error, which `ctx.fail` prints in four lines with exit code 2. A traceback through `set_key` and `rewrite` is longer. `stream_file`'s message is one line.

The read-only test keeps `assert dotenv_path.read_text() == "a=b\n"`. Keeping a test of the read-only case is a judgement call, as the issue says "probably", but it names the case and says the same applies.

## tests/test_main.py, `test_set_key_missing_directory` and `test_set_key_read_only_directory`

**Pinned attribute and type.** `exc_info.value.filename == str(dotenv_path)` requires one way of naming the path: the attribute, as a `str`. The issue asks that the error names the path. A fix may raise a new `OSError` with the path in its message, or set `filename` to the `Path` it was given. The exact types `FileNotFoundError` and `PermissionError` fall the same way.

Each test now requires `OSError`, the type the issue's cause section uses and the base of the parent's `PermissionError` test nearby, with the `.env` path in `str(exc)` and no `.tmp_` name. What stays checked: `not dotenv_path.parent.exists()`, so a fix cannot create the missing directory, and in the read-only test the file's content and the absence of a temporary file left behind.

## Left unchanged

`test_set_key_permission_error` and every other test in the two modules.

No test removed.

## Controls

Reasoning, as nothing here has been run.

- On the parent, `set_key` raises with the `.tmp_` path, so `test_set_key_missing_directory` fails on the path check, and `test_set_missing_directory` fails on the exit code, 1 with a traceback.
- With the maintainers' fix all four pass, or the two read-only ones are skipped when run as root, as before.
- With the fix's message changed to `Error opening env file:` or `Cannot write ...`, the CLI tests pass, as long as the path is printed.
- With the fix raising `OSError(f"cannot write {path}") from err`, the library tests pass.
- A fix that catches `OSError` in the CLI but still reports the temporary path fails on `.tmp_`.
- A fix that prints the traceback itself and then exits with code 2 fails on the traceback check.
- A fix that creates the missing directory fails `test_set_key_missing_directory`.

## Independent audit

A blind audit from another model family returned `revise`, with one finding in each CLI test, counted under over-relaxed and stated-check-could-stay.

- **The CLI tests lost the short error without a traceback. Accepted, in both tests.** The issue states both: it complains that the CLI prints a full traceback, and asks for a short error. My first version kept the exit code and the path only. A fix that printed a traceback naming the path and then exited with code 2 would have passed. The tests now refuse a traceback and require a short message, with a bound loose enough for click's own usage error.
- The audit counts the widening of the exception types to `OSError` as a change beyond names, messages and formats, and judges it justified. It is unchanged.
