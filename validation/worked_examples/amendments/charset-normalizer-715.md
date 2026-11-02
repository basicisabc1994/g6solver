# charset-normalizer-715: no scoring amendment

Issue jawah/charset_normalizer#672, "[DETECTION]". The task is the issue, the comments made before PR 715 opened (2026-03-15T06:26:00Z), and the repository at the parent commit.

The stated behaviour: the reporter's file, attached to the issue and not in the repository, should be detected as `cp1250`, not `mac_latin2`.

**No diff is written.** The one pinned name can be freed, but freeing it leaves no test that fails on the parent and passes with the maintainers' fix, and such a diff is refused. This is not a case of the task determining the test. The task determines a behaviour that no maintainers' test checks.

## tests/test_base_detection.py, `test_alphabets_property`

**Pinned name.** `assert "Emoticons" in best_guess.alphabets`. At the parent the name is `"Emoticons range(Emoji)"`, in `UNICODE_RANGES_COMBINED` and in this same test. PR 715 is the 3.4.6 release, and its update of the Unicode block table renames the entry. Neither the issue nor its comments mention block names. A fix of the issue that leaves the table alone fails this test, so this is the name the audit found.

The weakest check available is that some block name starts with `Emoticons`. The parent's table already passes it.

## The other tests in the three modules

None of these is expected to fail on the parent, by reading the code. Nothing was run.

- `test_multiple_file_normalize` and `test_multiple_file_normalize_with_alternatives` in `tests/test_cli.py` come from #702, which is not this task. At the parent `x_[0].unicode_path` is overwritten on the second file, but each output is still written to its own path and the exit code is 0. The tests read neither `unicode_path` nor the JSON. The expected names follow from the parent's CLI and from `tests/test_full_detection.py`, which already maps `sample-arabic-1.txt` to `cp1256` and `sample-turkish.txt` to `cp1254`.
- `tests/test_thread_safety.py` is new, and its three cases, `sample-arabic-1.txt`, `sample-french-1.txt` and `sample-chinese.txt`, carry the same encoding and language as the parent's `tests/test_full_detection.py`.
- No file was added under `data/`, and the reporter's file is not used. The fix's change for #672, the threshold in `CharsetMatch.__lt__` going from 0.01 to 0.005, is not exercised by any assertion that changed.

## What was considered and not done

A new test could read the reporter's file and require `cp1250`. The issue's own verbose log suggests it would fail on the parent and pass with the fix: `cp1250` has chaos 0.0 and coherence 0.772, `mac_latin2` has chaos 0.6 percent and coherence 0.875, and that gap of 0.6 percent sits between the two thresholds. The reporter's second comment shows that another honest fix reaches `cp1250` too, adding `đ` to the Croatian list.

I did not write it. It adds an input the maintainers never used, so it is a new test and not the smallest diff that removes what the task does not state. Whether to add one is a decision about the instrument, for the experiment's lead. The attachment was not fetched.

Consequence for scoring: on this bug the maintainers' score measures the block rename, not the issue. With no amended test, this bug has no amended score.

## Controls

- With `test_alphabets_property` changed to accept either name, I expect every test in the three modules to pass on the parent. That is the reason for this note. If one still fails on the parent and passes with the fix, this note is wrong and the one-line diff can be written.
