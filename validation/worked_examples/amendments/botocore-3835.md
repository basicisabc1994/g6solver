# botocore-3835: scoring amendment

Issue boto/botocore#3827, "RefreshableCredentials: concurrent get_frozen_credentials() can return expired credentials during a refresh". The task is the issue and the repository at the parent commit. Both comments came after PR 3835 opened (2026-10-19T00:55:00Z) and are not part of the task.

The stated behaviour: "the caller that arrives during the refresh gets `NEW`, or waits for it." The issue's script forces the race by pausing right after `_set_from_data`. Its suggested fix publishes the frozen credentials inside `_set_from_data`, before the new expiry.

Searched the parent's `botocore/credentials.py` and `tests/unit/test_credentials.py`, and the issue, for `_publish_refresh` and `_staged_expiry_time`. No hits. `_set_from_data` is in the parent's code and in the issue's script, so it is stated.

## tests/unit/test_credentials.py, `PausingRefreshableCredentials`

**Pinned name.** The helper class overrode `_publish_refresh`, the fix's own method. Any fix without that method never reaches the pause, so `publish_started` is never set and the test fails, including the fix the issue itself suggests. The override now wraps `_set_from_data` and pauses after it, which is exactly the issue's script. The wait is 5 seconds, also the issue's value, so the pause outlasts the wait below on a loaded machine. The helper's attribute names belong to the test and stay.

## `test_concurrent_reader_gets_refreshed_frozen_credentials`

**Pinned state.** `self.assertTrue(creds.refresh_needed())` during the pause requires the new expiry to be still unpublished at that point. That is true of the fix's staging. With the issue's suggested fix the credentials and the expiry are both published by then, so the assertion fails while the reader correctly gets `NEW`. It is removed.

**Replaced by a wait.** With the pause moved, the race is real at the parent, so the reader must get its chance to run during the pause. `wait([reader], timeout=0.5)` holds the pause open until the reader returns, or for half a second if it is blocked on the lock. Without it the release could run first, and the parent could pass by luck. `wait` is added to the `concurrent.futures` import.

What stays checked: the refreshing caller and the concurrent reader both get the `NEW` credentials, and the refresher is called once.

## Left unchanged

No other test in the module. Nothing pins a message here.

No test removed.

## Controls

Reasoning, as nothing here has been run.

- On the parent, the reader sees the new expiry, skips the lock and returns the `ORIGINAL` credentials during the pause, so `reader.result` differs from `NEW` and the test fails. This is the issue's defect and no longer a missing method.
- With the maintainers' fix the reader sees the old expiry, blocks on the lock and gets `NEW`. The test takes about half a second longer.
- With `_publish_refresh` renamed in the fix, the test passes, so the name is not required.
- With the issue's suggested fix, building and assigning the frozen credentials in `_set_from_data` before the expiry, the reader gets `NEW` at once and the test passes.
- A fix that makes every reader take the lock passes.
- A refresh that no longer goes through `_set_from_data` would time out on `publish_started`. The parent and the issue both route it there, so this is left as stated.
- The failure on the parent relies on the reader thread running within 0.5 seconds. Under extreme load it could run late and the test could pass by chance.
