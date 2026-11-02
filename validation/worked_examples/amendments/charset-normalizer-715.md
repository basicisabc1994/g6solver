# charset-normalizer-715: no scoring amendment

Issue jawah/charset_normalizer#672, "[DETECTION]". The task is the issue, the comments made before PR 715 opened (2026-03-15T06:26:00Z), and the repository at the parent commit.

The stated behaviour: the reporter's file, attached to the issue and not in the repository, should be detected as `cp1250`, not `mac_latin2`.

**No diff is written.** The one pinned name can be freed, but freeing it leaves no test that fails on the parent and passes with the maintainers' fix. The brief that set the rule says plainly that such a diff is refused. This is not a case of the task determining the test. The task determines a behaviour that no maintainers' test checks.

## tests/test_base_detection.py, `test_alphabets_property`

**Pinned name.** `assert "Emoticons" in best_guess.alphabets`. At the parent the name is `"Emoticons range(Emoji)"`, both in `UNICODE_RANGES_COMBINED` and in this same test. PR 715 is the 3.4.6 release, and its update of the Unicode block table renames the entry. Neither the issue nor its comments mention block names. A fix of the issue that leaves the table alone fails this test, so this is the name the audit found.

The weakest check available is that some block name starts with `Emoticons`. The parent's table already passes it.

## The other tests in the three modules

None of these is expected to fail on the parent, by reading the code. Nothing was run.

- `test_multiple_file_normalize` and `test_multiple_file_normalize_with_alternatives` in `tests/test_cli.py` come from #702, which is not this task. At the parent `x_[0].unicode_path` is overwritten on the second file, but each output is still written to its own path and the exit code is 0. The tests read neither `unicode_path` nor the JSON. The expected names follow from the parent's CLI and from `tests/test_full_detection.py`, which already maps `sample-arabic-1.txt` to `cp1256` and `sample-turkish.txt` to `cp1254`.
- `tests/test_thread_safety.py` is new. Its three cases, `sample-arabic-1.txt`, `sample-french-1.txt` and `sample-chinese.txt`, carry the same encoding and language as the parent's `tests/test_full_detection.py`.
- No file was added under `data/`, and the reporter's file is not used. The fix's change for #672, the threshold in `CharsetMatch.__lt__` going from 0.01 to 0.005, is not exercised by any assertion that changed.

## The diff that would be written

If the requirement that a test still fail on the parent no longer holds, this is the diff, built from the merge commit's bytes and checked with `git apply --check`. It is kept here and not as a `.patch` file, so that nothing applies it by accident.

```diff
diff --git a/tests/test_base_detection.py b/tests/test_base_detection.py
index c776206..659094a 100644
--- a/tests/test_base_detection.py
+++ b/tests/test_base_detection.py
@@ -165,7 +165,7 @@ def test_alphabets_property():
     best_guess = from_bytes("😀 Hello World! How affairs are going? 😀".encode()).best()
 
     assert "Basic Latin" in best_guess.alphabets
-    assert "Emoticons" in best_guess.alphabets
+    assert any(name.startswith("Emoticons") for name in best_guess.alphabets)
     assert best_guess.alphabets.count("Basic Latin") == 1
```

With it, no test in the three modules is expected to tell the parent from the fix, so every arm, and the parent itself, would pass this bug.

## What was considered and not done

A new test could read the reporter's file and require `cp1250`. The issue's own verbose log suggests it would fail on the parent and pass with the fix: `cp1250` has chaos 0.0 and coherence 0.772, `mac_latin2` has chaos 0.6 percent and coherence 0.875, and that gap of 0.6 percent sits between the two thresholds. The reporter's second comment shows that another honest fix reaches `cp1250` too, adding `đ` to the Croatian list.

I did not write it. It adds an input the maintainers never used, so it is a new test and not the smallest diff that removes what the task does not state. Whether to add one is a decision about the instrument, for the experiment's lead. The attachment was not fetched.

Consequence for scoring: on this bug the maintainers' score measures the block rename, not the issue. With no amended test, this bug has no amended score.

## Controls

- With the diff above applied, I expect every test in the three modules to pass on the parent. That is the reason for this note. If a test still fails on the parent and passes with the fix, this note is wrong and the diff above should be written as `charset-normalizer-715.patch`.

## Independent audit

A blind audit from another model family returned `revise`, with one finding.

- **Still pinned, the block name. Rejected, with its facts accepted.** The audit is right that the name is pinned and that a fix of the issue can keep the parent's label. This note said so from the start. The audit's remedy is to free the name, and it holds that losing the parent's failure does not justify keeping the pin. That is tested against the rule I was given, which includes this line: "At least one test must still fail on the parent and pass with the maintainers' fix. A diff that loses that is refused." The audit's own reading supports the premise. It finds every other assertion in the three modules to follow from the parent, so freeing the name leaves nothing failing on the parent. So the diff is shown above and not written as a patch. If the lead has dropped that line from the rule, the diff above is ready.
- The audit also marks that a shallow fix would pass. That is a coverage limit, as it says itself, and the section above on a new test is the only remedy I can see. It is not mine to add.
