# black-5144: scoring amendment

Issue psf/black#4296, "Standalone comments still partially broken". The task is the issue, the one comment made before PR 5144 opened (2026-05-20T23:24:42Z), and the repository at the parent commit.

## tests/test_format.py, `test_simple_format[comments_in_comprehensions]`

The maintainers' case lives in `tests/data/cases/comments_in_comprehensions.py`, an input followed by the exact expected output. The test file itself was not changed by the fix; the amendment adds a branch for this one case, so the test keeps its id and the data file stays as it is.

**Pinned format.** The whole expected output, line by line. For most of the inputs it is the layout the fix happens to produce, and the fix's own comment calls it a last resort that is "at least valid Python". Neither the issue nor the comment gives an expected layout. The issue says "The code should be formatted successfully and correctly" and calls the first-pass style "pretty weird". The comment adds that, at minimum, Black could leave such a block untouched rather than produce invalid code. So several layouts honour the task. Searched: the issue, the comment, and the parent's `docs/the_black_code_style/` for any rule on where these comments or keywords go. None found.

**What stays checked.** `assert_format(source, None, mode)`, the repository's own helper, on the same input:

- no crash in the default mode, in preview mode, or at line length 1;
- the output is AST-equivalent to the input;
- the output is stable on a second pass.

Two more checks keep the parts of the expected output that are stated:

- Every `# comment` survives. The issue names the broken comment as a defect, and the parent's stability policy calls accidentally removing comments a bug. The parent's docs allow comments to move, so only their number is checked. AST equivalence alone would not catch a lost comment.
- The `for (a, b), c in [...]` block stays exactly as written. The parent formats it that way already, and the stability policy at the parent covers it, so this part of the expected output is existing behaviour rather than something the fix chose.

All four inputs that crash at the parent stay in the test: the three from the issue and comment, and the `async for` variant of the first.

No test removed.
