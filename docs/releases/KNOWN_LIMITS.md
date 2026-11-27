# G6 0.3.0 known limits

Each line below is a finding reproduced on G6's own code. Most were found by an independent reviewer, who wrote a
test that shows it; the `limit-` and `rc030-` lines were found in use, in G6's own tests, or in the release review.
Each one ships as a known limit. It fails in the safe direction (G6 refuses, withholds or reports less, or raises a
false alarm rather than a false pass), needs code already running in G6's own process or a second program running as
the same user, is a narrow platform edge that ends in an error, is a narrowed race, or sits in repository tooling
rather than the installed package.

Two marks are used:

- **(repair landed, not re-checked)**: a later repair touched this code, and no fresh review has run the finding's
  test again. Treat the finding as open.
- **(fix needs a certificate-bound file)**: the fix waits for G6's next certificate issuance window.

The machine-readable ledger, with each finding's evidence and follow-up, is
[`v0.3.0-ledger.tsv`](v0.3.0-ledger.tsv).

## Open before the tag

None. Each must-close finding on code that ships was closed before the tag:

- `review-sfu2#2`: the storage check followed a mount point to a named pipe and connected to it before refusing. It
  now refuses a link or mount point to a device or network target before following it (d24f40ac). The file is
  certificate-bound, and its certificates are issued again before the tag.
- `review-splitfu4#1`: the skill's safety census dropped a bold caption with no authority keyword. It now keeps it
  (22a7b1b3).
- `review-reply4#1`, `review-reply4#2`, `review-reply4#3`: deeply nested JSON read through the sharing check, or in a
  saved code configuration, ended in a bare error instead of G6's refusal. Closed by the reply follow-up (bb750f15)
  and by routing the release's other JSON readers through G6's guarded decoder (158eeefc). None of the follow-up's
  certificate-bound hunks was needed.
- A network-share path (`\\host\share` and its other spellings) given as a repository was checked on disk before it
  was refused, which on Windows opens the share. It is now refused on its words, before any file system call
  (cb107cba).
- The plugin's Stop hook tested nothing by default while its README said a reviewer reads the patch. The hook now runs
  G6's model-free patch review by default, and the README says what runs (12335883).
- A provider's error text could be read as a consent refusal, and a review refused after a request went out could be
  reported as not sent. G6 now reads a consent refusal from its own record, and says "not sent" only when no request
  was counted (705fd2d0).

## Platforms and scope

- `platform-macos`: macOS is out of scope for 0.3.0. G6 has no operating-system boundary there, so patch review and
  the completion loop refuse; other tools that run untrusted code refuse unless the person allows unconfined runs
  once, and code allowed that way runs with the person's own rights.
- `platform-python`: Patch review and the completion loop test Python repositories with pytest only.
- `platform-windows-ci`: Hosted Windows CI runners cannot create the AppContainer patch review needs, so the
  patch-review smoke test is skipped on Windows CI. Windows patch review was exercised on a local machine only.
- `platform-windows-git`: On Windows, tests that run git fail inside G6's sandbox. The sandbox cannot read the
  folders above the temporary folder, and git checks every folder from the drive down. `g6 doctor` warns of this and
  prints a one-time grant for each such folder, which the person runs (read-attributes only: no files, no listing,
  nothing inherited below the folder); G6 never runs it. Until the person runs it, such a failure is still reported as
  `defect_detected`, and the reply quotes the failure's own line (masked, at most 300 characters) so the cause can be
  seen. A separate outcome for failures of the computer rather than the patch is planned for 0.3.1.
- `platform-trusted-host`: Local settings assume a trusted host. An agent with the account's unrestricted shell can
  change G6's settings, state and hooks.

## Patch review and behaviour checks (16)

- `limit-environmental-failure`: A stated check that fails for a reason of the computer, not of the patch (such as git
  that cannot run inside the sandbox), is reported as `defect_detected`. The reply quotes the failure's own line as
  data, and no outcome is decided from it. A separate outcome is planned for 0.3.1.
- `review-efu4#3`: A baseline run of the original code with no record of what it loaded can still establish a
  regression, so a defect can be claimed without that evidence (a false alarm, not a false pass).
- `review-maskfu2r1#3`: On Windows, a new file whose name holds two or more characters outside the Basic
  Multilingual Plane is misread, and the review refuses it as another file. (repair landed, not re-checked)
- `review-qs4#2`: A control error after a confirmed defect was observed can turn that defect into insufficient
  evidence in the reply. (repair landed, not re-checked)
- `review-qs4#3`: After a non-confirming answer, the reply can say nothing is enforced while an earlier confirmed
  answer is still enforced. (repair landed, not re-checked)
- `review-qsfur4#3`: Recording an exception can run a metaclass hook from the repository, inside the child process
  that already runs the code under test.
- `review-tmfu3r2#9`: For a module present only as compiled bytecode (`.pyc`, no source), G6's refusal to run it is
  an ImportError, so a confirmed check that expects an ImportError can read as passed, or an expected return as
  failed, from that refusal.
- `limit-change-map-collection`: The advisory change map's `exercised` answer can be shaped by repository code that
  runs during test collection. The map is a lead, never evidence or a decision, and a static named call can be
  reachable without running.
- `limit-change-map-hierarchy`: Without test collection, the change map does not check a file selection for
  hierarchy skips.
- `limit-tier2-collection`: One test file that cannot be collected turns the whole batch of tests that touch the
  change into a collection error.
- `limit-receipt-path-tail`: G6 issues no test receipt when a module loaded from outside the repository has the same
  name as a file in it, such as a folder named like a standard-library or installed module. G6 matches the two by
  the end of their paths. The run then counts as caller-declared, never as observed by G6. This fails safe.
- `rc030-mustclose#B1`: The coding gate's `verify_answer` can return the verdict `pass` on a test run the caller
  reports. That pass is `qualified-draft` with the evidence basis `caller-declared`, never a result G6 observed.
- `rc030-mustclose#B3`: Requirement evidence takes the run, control and reference positions it is handed, without a
  receipt from G6's own store. In this release only G6's own patch review hands it runs, so a caller reaches this
  only through the Python API, in G6's process.
- `rc030-mustclose#B4`: The tests that "touch the changed modules" are the test files that name a changed module's
  file name anywhere in their text, and the run does not record whether a selected file loaded the module.
- `rc030-mustclose#B5`: Where pytest's console text and its JUnit report disagree, a run's class can follow one of
  them alone. Every such case gives a non-pass, and both come from the process under test.
- `rc030-mustclose#B6`: A caller's proposed check may take an id in the `g6p-` namespace G6's own proposer uses, so
  the two can be attributed to each other. Proposed checks never pass a requirement.

## Completion loop and self-improvement loop (25)

- `review-l4#3`: Through the plain Codex caller, a Codex usage limit during confirmation ends as a failed case and
  can spend an evaluation. The loop's watched Codex reads the limit and waits.
- `review-frzfu4#2`: A later pass that sent nothing can record an earlier refusal again and pause the loop again (a
  false pause). (repair landed, not re-checked)
- `review-frzfu4#3`: If writing a provider-limit note fails with an unexpected error, the limit is lost and one more
  request is sent into it. (repair landed, not re-checked)
- `review-frzfu4#4`: An interrupt that lands while a pause is written on a failing tick can leave neither a pause nor
  a stop on disk (narrowed race). (repair landed, not re-checked)
- `review-frzfu4#5`: A file swapped for a folder between two reads is left out of the frozen-file listing, with no
  error, until the next reading (race). (repair landed, not re-checked)
- `review-frzfu4#6`: A folder swapped for a junction during the walk is followed, so a file from outside the root can
  be pinned (race). (repair landed, not re-checked)
- `review-refufu2r1#2`: Two spellings of the same input path collide while the private copy is built, so a valid
  request fails with FileExistsError before anything starts. (repair landed, not re-checked)
- `review-refufu2r3#2`: Private-copy cleanup checks a folder's identity and then deletes by name, so a folder swapped
  in between by a program running as the same user is deleted.
- `review-refufu2r4#2`: A reply whose outcome is unknown is recorded as a classifier refusal and pauses the loop; the
  same inputs are later described as refused. (repair landed, not re-checked)
- `review-refufu2r4#3`: The test guarding admission outside the lock names two facts and would not notice a third
  fact read outside the lock. (repair landed, not re-checked)
- `review-wksfu2#3`: An error raised while a usage callback's exception is rendered escapes the unread-account
  handling.
- `review-wksfu2#8`: A process status that cannot be rendered escapes the refusal reader as a RuntimeError.
- `review-wksfu2#9`: A process exception that cannot be rendered escapes the refusal reader as a RuntimeError.
- `review-wksfu2#12`: An error rendering a reservation callback escapes budget-refusal handling as a RuntimeError.
- `review-wksfu4#2`: A charge that another meter on the same file settles after a reading was taken is counted
  inside that reading (110 of 100); it needs a second process or meter. (repair landed, not re-checked)
- `review-wksfu4#3`: The meter and the reader decide separately whether a figure with no reset time still stands, so
  the meter can refuse until a re-read the reader never makes. (repair landed, not re-checked)
- `review-wksfu4#4`: Amounts that overflow the exact sum raise OverflowError on every later reservation and reading,
  and the append-only ledger never clears it. (repair landed, not re-checked)
- `review-wksfu4#5`: One reservation reads the account twice, decides on the first reading and records the second.
  (repair landed, not re-checked)
- `review-wksfu4#6`: A reading stamped at the largest representable time makes every later look at that provider
  raise. (repair landed, not re-checked)
- `review-lr4#1`: A wrapped strategy's measured winner is counted as not kept, so the diagnosis can name tuning as
  the bottleneck when selection is.
- `review-lr4#2`: An exception during selection erases the candidates already raced, so the count reads zero.
- `review-lr4#3`: Generated strategies with the same name overwrite each other's counts.
- `review-lr4#4`: A refused later proposal hides the count of a valid earlier proposal.
- `review-maskfu2r1#4`: In the meta-harness coding check, a folder whose name ends in a dot, beside one without
  the dot, can make the mutation check read the wrong file version. (repair landed, not re-checked)
- `limit-record-tuple`: A test-run record handed to the reviewer loop in memory with a tuple where JSON has a list
  reads as valid; records that arrive as JSON cannot carry one.

## Stopping and starting the loop (6)

- `review-cfufu2r1#1`: A stop reason that cannot be written can abort every stop write and leave the start live.
  (repair landed, not re-checked)
- `review-stopfu4#2`: Printing a stop reason the terminal cannot show can replace the stop code with CANNOT_OPEN;
  the run still ends.
- `review-stopfu4#3`: Refusing a start with an invalid name changes the records and revokes an existing start.
- `review-stopfu4#4`: When the client's start dialog fails, the refusal leaves out that the stop file could not be
  read; the start stays refused.
- `rc030-mustclose#improve-stop`: A self-improvement runner enrolled on a network share by an earlier version cannot
  be stopped over MCP. Write its `STOP` file locally instead.
- `rc030-mustclose#stop-rereview`: Once the Stop hook's default model-free review has checked a patch, adding the
  reviewer line to the key file does not review that same patch again; the reviewer loop starts with the next change.

## Consent and cloud model calls (10)

- `review-mvp-consent#2`: The native urllib send route is resolved twice, so code in G6's process can make a route
  other than the one checked send the request.
- `review-mvp-consent#3`: A host hook running in G6's process can rewrite a request after consent recorded it, so
  the record shows the earlier request.
- `limit-runtime-lookup-order`: On a machine without the Claude Code or Codex program, an ungranted agent run says
  the program is missing, not that consent is needed. Nothing is sent either way.
- `limit-nested-send-wait`: A send made on another thread from inside an admitted send waits for that send's
  admission to end.
- `limit-observer-threads`: Admission and refusals reach threads and thread-pool tasks a backend starts during a
  call. They do not reach threads started with `_thread.start_new_thread`, threads already running, pool
  initializers, future callbacks, or tasks given to other kinds of executor.
- `limit-double-send`: A sender in G6's process that sends twice from one frame, the first refused and the second
  raising a new error, reads that error back to its second send, so the refused request is not the one barred.
- `limit-first-review-withheld`: The first review of a repository G6 has not seen before is withheld when the agent
  polls for it, until the person shares the repository in the dialog. The review then has to run again.
- `limit-consent-surrogate`: A model reply value that holds half of a UTF-16 character is refused
  (`blocked-escalated`), not returned. (fix needs a certificate-bound file)
- `limit-consent-held-target`: On Windows, a reader that holds a retained file open while it is published ends the
  run as unreadable or as a failed review. Nothing is sent. (fix needs a certificate-bound file)
- `limit-consent-library-recursion`: A model reply nested past Python's recursion limit raises a plain RecursionError
  to library code that catches only G6's model errors (bundles, agents), instead of a G6 refusal code. It fails
  closed: nothing is sent. (fix needs a certificate-bound file)

## Model overrides, `g6_agentic_run` (7)

- `review-agk3#L`: The host caller allows one session per override call and refuses a Retry-After over one second
  instead of waiting.
- `review-kf2r1#3`: A pending override reachable from its responder stays in memory until it leaves the 256-entry
  seal table.
- `review-kf2r1#4`: Named tuples and other tuple subclasses that `g6.invoke` accepts are refused for overrides.
- `review-kf2r2#3`: Swapping a bound method's owner, or changing a closure's container in place after prepare, is not
  caught (Python callers in G6's process).
- `review-kf2r3#1`: A function held in a default value, a dictionary value or a partial's arguments can be swapped
  after prepare and then run (Python callers in G6's process).
- `review-kf2r3#4`: Code in G6's process can reach and rewrite an override's seal through the garbage collector;
  module globals are not pinned either.
- `review-kfu1#6`: A proxy variable set in the person's own process between the last check and the connection could
  send a loopback request off the machine (in-process race).

## Allowances and the status line (8)

- `review-allow1#1`: A profile name that starts with a hyphen stops the status-line renderer from starting.
- `review-allow1#4`: Writing to a stalled observer can delay the renderer past its time budget.
- `review-allow1#6`: Under PowerShell, the renderer's own exit code is replaced by 1.
- `review-allow2#2`: A renderer exit code of 2^31 or more is reported as 4294967295.
- `review-allow2#3`: After the clock is set back, a reading dated ahead outranks every current observation, and a
  later figure is discarded. (repair landed, not re-checked)
- `review-allow2#4`: An interpreter in a folder whose name has a typographic quote passes the preview, but the
  installed PowerShell command does not parse and the renderer never starts. (repair landed, not re-checked)
- `review-allow2#5`: With the interpreter removed, the PowerShell form exits 0 with nothing rendered. (repair landed,
  not re-checked)
- `review-allow2#6`: The tests guarding the observer list spellings and this platform's branch only, so a changed
  copy can still pass them. (repair landed, not re-checked)

## Context Engine, `g6_knowledge_*` (27)

All but the last are (repair landed, not re-checked).

- `review-s12a#5`: An interrupted complete deletion can never finish: deleted text stays in the index and nothing
  more can be added.
- `review-s12a#6`: A snapshot of an expired source can still be reconstructed.
- `review-s12a#7`: A credential folder (`.git`, `.ssh`, `.claude`, `.aws`, `.g6`, `.codex`), or a folder inside one,
  named as the folder to add is walked and hashed.
- `review-s12a#8`: Passages the caller may not use can fill the candidate window, so a usable source's matches come
  back as none.
- `review-s12a#9`: A file that became an offline placeholder after indexing is opened again by search and fetch.
- `review-s12a#10`: A commit refused with "nothing was indexed" leaves staged passages and audit copies behind.
- `review-s12a#11`: A snapshot stops reconstructing exactly after two unrelated publications.
- `review-s12a#12`: Engine errors other than G6's refusals (a non-Unicode file name, a full disk) reach the client as
  "Invalid tool call" with no G6 error code.
- `review-s12a#13`: Listings from declined or uncommitted previews are never removed, and survive complete deletion.
- `review-s12a#14`: Built-in ignored folders are matched case-sensitively, so `NODE_MODULES` is walked.
- `review-s12b#6`: A complete deletion gives its receipt while a saved context, a preview listing or a job record can
  still hold the source's text or file names.
- `review-s12b#7`: A failed refresh leaves the audit copy it wrote; a refused commit leaves a listing it could not
  remove.
- `review-s12b#8`: A complete deletion can remove the index of a generation published meanwhile, so later searches
  answer empty; a refresh can publish over a source added while it ran.
- `review-s12b#9`: A refresh checks permission once, so a source revoked during it is indexed afterwards, and one
  completely deleted during it can reappear.
- `review-s12b#10`: The graph build checks permission on one path of several.
- `review-s12b#11`: A search reply with no passage is bound to no source, so its counts and change denials for a
  shared source leave with no disclosure row.
- `review-s12b#12`: Changed passages can fill a small result window, so an intact note is never returned while the
  backend reports an answer.
- `review-s12c#5`: A saved pack that holds some but not all passages of a file is refused at once (false refusal).
- `review-s12c#6`: A pack can return a changed file's old text as intact (no MCP tool reads a pack; in-process callers
  only).
- `review-s12c#7`: An empty list of retrieval routes asks no backend and returns an empty result, read as no match.
- `review-s12c#8`: The graph build re-reads files of an expired source and keeps their file and heading names; search
  does not return them.
- `review-s12c#10`: A grant that names one passage gives its authority to every passage of that file version.
- `review-s12c#14`: The test inventory of time-checked reads misses four functions that return source content.
- `review-s12c#15`: A negative `top_k` returns hits instead of being refused.
- `review-s12c#17`: A lexical backend that returned nothing cancels the fallback's vote (not reachable until the
  meaning route ships).
- `review-s12c#18`: A job that a stopped server left running is reported as running by a new server, with nothing
  doing the work.
- `review-rfu4#1`: A saved context pack nested deeper than the JSON decoder allows is not used, but the reply gives
  a plain decoding error instead of G6's usual refusal code.

## Workflow record, on by default (14)

The record is on by default; `G6_WORKFLOW_RECORD=off`, set where the server is registered, turns it off. Its view in
review replies stays off unless the person names a level in `G6_WORKFLOW_VIEW` or sets `G6_WORKFLOW_RECORD=on`.

- `review-mvp-s17#1`: A feedback write can finish after its store was cleared but before deletion returns.
- `review-mvp-s17#2`: The writer inventory test still passes against a copied package that adds a writer it has not
  seen.
- `review-mvp-s17#3`: Feedback recorded by task alone cannot be deleted by task once its chain is unreadable.
- `review-mvp-s17#4`: Receipt totals count files plus entries removed from files that remain, as the published policy
  says.
- `review-s17a#3`: An entry past its retention period, in a file still in use, is still read.
- `review-s17a#4`: A loop run's duration is clocked from the first step G6 saw, so a loop that stops in that step
  records 0.0 seconds instead of "not measured".
- `review-s17a#5`: G6's own cost reads 0 where nothing was observed.
- `review-s17a#6`: The Stop hook's adapter records a report that was not returned, and adds one entry per turn end.
- `review-s17a#7`: The admitted level in `decisions.json` is taken on the file's word; the running code uses its own
  constant, and a test pins the shipped file.
- `review-s17a#8`: The slice gate reads a placeholder as an answer once another word stands beside it (repository
  tooling).
- `limit-export-opaque-source`: A withheld `g6_workflow_export` reply names its source only by a hash, and granting
  that hash does not release the reply. It fails closed: nothing is shared.
- `limit-no-g6-record`: 0.3.0 shows the card at done and an export writes `RECORD.md`, but there is no read-only
  `g6_record` tool for an agent to read a session's events back over MCP. It is planned for 0.3.1.
- `limit-card-withheld-over-mcp`: In an MCP reply, the card at done reads the workflow record only once the person
  has granted the `workflow:<project>` source to that client's provider. Until then it says the record is withheld
  and gives no counts. It fails closed: nothing is shared.
- `limit-card-collection-repair`: A repair that changes a product value from which a test's own parameters are
  built at collection counts as a new check, not a repair, so the card at done reports fewer repairs than were made.

## Tool routing, the skill and the routing instrument (27)

The routing instrument under `validation/routing` ships in the repository and has not run a trial. Every line here
except `review-split3#pbr` and the three `review-mvp-s14` lines is (repair landed, not re-checked).

- `review-split3#pbr`: Without a model, lexical selection no longer offers the earlier top route for 10 reviewer
  tasks.
- `review-s14a#4`: The server scan finds MCP servers by the spelled name `FastMCP`, so an import alias, a subclass,
  the low-level server or a second construction is not seen.
- `review-s14a#5`: Stale tool names are caught only with a `g6_` or `metaharness_` prefix, so the separate
  abstraction server's tools are outside the check.
- `review-s14a#6`: The op inventory is each component's own reply, not the ops its typed input accepts.
- `review-s14a#7`: The routing manifest decides what a route's own section is, so one section can own every mention.
- `review-s14a#9`: The routing manifest sets the ceiling its own version entries are checked against.
- `review-s14a#11`: The dialog word list is six hand-kept words, not the dialogs' fields.
- `review-s14b#11`: The server scan resolves names against four spelled module names.
- `review-s14b#12`: The stale-name check for the second server reads one file and one written form.
- `review-s14b#13`: Ops are checked against the typed input only where it is a plain literal; three shipped
  components rest on their own reply.
- `review-s14b#14`: Two routing checks decide by different rules whether text names a tool.
- `review-s14b#16`: The guidance checks read neither the tool descriptions nor the second server's instructions.
- `review-s14b#18`: The behaviour form's fields are read from a variable by its name.
- `review-mvp-s14#1`: In the instrument, a strategy that never ran can earn a successful trial using only appendable
  files.
- `review-mvp-s14#2`: In the instrument, authentic reply bytes can be graded again against another case's kept
  oracle.
- `review-mvp-s14#3`: The instrument's audit authenticates neither the split nor the critical-case flag.
- `review-s14a#3`: The pin of model-calling runners skips folders named `fixtures`, and two worked-example runners
  build a model caller outside it.
- `review-s14a#10`: A route's digest leaves out what the route says.
- `review-s14a#12`: The trial server cannot run what one positive review task expects, so a correct agent cannot
  complete it.
- `review-s14a#13`: Withholding a tool from the second trial server removes it from the product's own server object
  in the same process.
- `review-s14b#4`: The release reading can read pass for a report with unattributed rows or loosened bars (in-process
  calls).
- `review-s14b#6`: The event-log digest names bytes deleted with the session's temporary folder.
- `review-s14b#7`: Parts of the installed skill and `g6_help`'s text are in no digest.
- `review-s14b#8`: The trial home copies consent events without their checkpoint, so trial servers read every source
  as not granted.
- `review-s14b#9`: One G6 home serves every session of a run and its repeats.
- `review-s14b#15`: A session that ran can leave no row, and the cohort can then be started again.
- `review-s14b#17`: A trial server imports whichever G6 the interpreter has installed, and no transcript says which.

## Refusals, repair hints and the MCP transport (13)

- `review-wrp1#6`: A call the MCP framework refuses on type validation, before the tool runs, carries no repair
  block.
- `review-wrpfu1#3`: Exit can wait up to 10 seconds for the repair-log writer, and rows still queued when it gives up
  are lost without being counted.
- `review-wrpfu2#3`: A repair-log row dropped at the lock limit in a short-lived process is missing from another
  process's repair statistics.
- `review-wrpfu2r4#2`: A component value with a custom serializer is refused rather than returned (the fix for this
  finding is the refusal).
- `review-sel4#8`: The action gate's fill hint fills only what its next step names, not the act the gate covers (by
  design).
- `review-reply4#4`: Caller JSON nested too deep for the decoder does not get the input-refusal envelope in
  `g6_invoke`, `g6_compose` and two meta-harness tools.
- `review-reply4#5`: The decoder census misses two modules that decode JSON another way.
- `review-wcfu4r4#1`: When standard input is wrapped with `newline=""` and its text ends in a carriage return, G6's
  input cache refuses it.
- `review-wcfu4r4#2`: An interrupt at the moment the input placeholder file is opened leaks a file descriptor and its
  temporary name.
- `review-wcfu4r4#3`: When standard input is a regular file that G6 refuses, the refusal loses the record of earlier
  line ends.
- `limit-collection-compile-decoder`: The confined child that compiles a test collection reads its parent's JSON
  request with the plain JSON decoder. A request past the decoder's limits ends the child, and the parent reads that
  as a failed collection.
- `limit-source-reader-valueerror`: The certified components' source-record readers report a record nested past the
  JSON decoder's limits as a plain ValueError, not as a JSON decoding error, so a handler that catches only decoding
  errors does not turn it into G6's refusal. (fix needs a certificate-bound file)
- `limit-refused-request-id`: A refused reply can carry an empty `request_id`.

## Setup and first-use hints (8)

- `review-ent1#11`: There is no first-call hint outside a git repository that has a commit.
- `review-ent2#6`: Accepting the `AGENTS.md` rewrite loses the file's hidden attribute and access control list.
- `review-ent4#3`: Accepting the `AGENTS.md` rewrite does not keep NTFS alternate data streams, mode or other
  attributes.
- `review-ent4#1d`: A valid G6 block after a code fence G6 cannot read for certain is refused, never rewritten.
- `review-ent4#2`: If setup's temporary file became a read-only hard link, setup leaves it read-only and names it.
- `review-ent4#5a`: An operation that raises over MCP gives the transport's masked error, not a labelled G6
  envelope.
- `review-entfu1#3`: Another program writing in the Codex folder between setup's two calls on its temporary file can
  race its removal.
- `review-entfu4#3`: Output written below Python during `g6 invoke` (direct writes to the console, C extensions,
  child processes) is not captured.

## Paths and storage (9)

- `review-stid2#1`: A declared root's own name is not checked; it is taken as the person's declaration.
- `review-stid2#4`: A path through an alias above the root is refused, never admitted.
- `review-stid3#f`: A hard link made between the walk and the read refuses the whole package.
- `review-stid4#2`: A rooted path with no drive letter is classified differently by different entry points. (fix
  needs a certificate-bound file)
- `review-sfu2#3`: A program running as the same user that swaps a folder during a write can make the write land
  outside the root.
- `review-sfu2#L4`: An outside file held open by another program is opened before it is refused.
- `review-sfu3#spelling`: A root declared through a junction, an 8.3 short name or another letter case is refused.
- `review-iss2#r4-2`: The rule for folders pending deletion assumes other file systems behave like the one it was
  built on.
- `review-hafx4#2`: A file whose name starts with `COM0` or `LPT0` (such as `COM0.py`) is refused as a Windows device
  name, although Windows and Linux can both hold such a file (a false refusal). (fix needs a certificate-bound file)

## Component door, composition and the cognitive bridge (22)

- `review-pr8fu3r4#1`: An estimator's feature count is expanded into default feature names before the visit budget
  applies.
- `pr8fu4r1-offered#1`: A maker with no size contract runs once before its output's size is refused.
- `review-pr8fu4r3#7`: Releasing a pinned buffer runs an array subclass's own release code (Python 3.12).
- `review-pr8fu4r4#1`: The copier takes a whole collection before reading its size (16 to 32 MB before the refusal).
  (repair landed, not re-checked)
- `review-pr8fu4r4#2`: A declared bound spends the copier budget three to five times, so plans within the stated caps
  are refused. (repair landed, not re-checked)
- `review-pr8fu4r4#3`: Claim admission reads each model under its own budget. (repair landed, not re-checked)
- `review-pr8fu4r4#4`: Naming a refused type runs that class's own formatting code, or ends as AttributeError.
  (repair landed, not re-checked)
- `review-pr8fu4r4#5`: A Python caller passing a collection of the wrong kind gets AttributeError, not the boundary
  refusal. (repair landed, not re-checked)
- `review-pr8fu4r4#6`: An array of one-character items is refused where it converted before. (repair landed, not
  re-checked)
- `review-pr8fu4r4#7`: A model that grows between admission and the copy is copied and encoded once before the cap
  refuses it (in-process race). (repair landed, not re-checked)
- `review-pr8fu4r4#8`: A record type that declares slots cannot be made; no shipped record uses slots. (repair
  landed, not re-checked)
- `review-pr8fu5r1#1`: The documented plan size holds only for a model that is one long text; the binding limit is
  100,000 reaches per pass.
- `review-pr8fu5r1#2`: A claims or hypotheses list grown between admission and the copy is copied and encoded once
  before it is refused (in-process race).
- `review-pr8fu5r1#3`: A record type with slots under a base without them cannot be made.
- `review-pr8fu5r1#4`: A variant model of exactly 250,000 bytes with a non-default operation is refused by
  `compile`, though it compiles under the default operation.
- `review-pr8fu5r1#5`: Plain data where a record is due ends in AttributeError in four metacognition functions
  (Python callers only).
- `limit-navigator-copy`: The navigator copies a plan step's arguments before any size budget applies.
- `limit-record-id-subclass`: A record id that is a subclass of `str` stops evidence issuance as a whole instead of
  being refused alone. Nothing is issued.
- `limit-compose-typeerror`: Python's `compose()` raises a plain TypeError for a stage's `params` or `inputs` that is
  not a mapping; over MCP the same input is refused with `G6_E_INPUT_VALIDATION`.
- `limit-door-containers`: The checked door reads and corrects mappings, lists and tuples in an answer; a set, deque
  or dataclass passes as it is, so a `verified` word inside one is not corrected (the reply's own state still is).
- `limit-bytes-tool-names`: An agent tool call whose name or argument key is bytes, which only adapter code can
  pass, raises out of the gated call or fails as the tool runs.
- `limit-windows-confirmed-checks`: On the Windows machine the 0.3 line was tested on, confirmed behaviour checks
  ended `insufficient_evidence` or `not_run` in two test files, as they did before the line's changes; this has not
  been run on Linux.

## Distillation and the learning library (9)

- `review-dfufu3r4#1`: After an interrupted restore, the reply can say recovery completed while G6's intent file is
  still on disk, so the next request is refused until it is cleared.
- `review-dfufu3r4#2`: A restore whose activation was already recorded can be answered as blocked, with no decision
  shown, when a later read fails.
- `review-dfufu3r4#3`: A request that finishes an earlier deferred close and then cannot tidy up says its undo is
  incomplete about a refusal row that was already published.
- `review-dfufu3r4#4`: On the server's job path, a distillation that recorded its concept and then had a later write
  refused still says it was blocked before any state change.
- `review-dfufu3r4#5`: When no file can be written at all (a full disk), G6 cannot record a deferred close, and the
  reply still says recovery completed.
- `review-dfufu3r4#6`: After the first deferred close in a G6 home, every request opens the acceptance journal; while
  another program holds that file, failure intake is refused.
- `release-030#g6_distill_start`: `g6_distill_start` learns only from cases that carry the full case fields and that
  a confined oracle run labels; a record without them is refused (`G6_E_SYNTH_TRAINING_ONLY`), and proposing a
  concept needs a host model caller (`G6_E_SYNTH_NO_CALLER`).
- `release-030#g6_research_start`: `g6_research_start` refuses while research authority is off, the default, and
  while no host runner is attached (`G6_E_RESEARCH_AUTHORITY`, `G6_E_RESEARCH_HOST_REQUIRED`).
- `release-030#g6_simulate`: `g6_simulate` forecasts from G6's own import graph and recorded actions; with no
  earlier action of that kind, the forecast stays unknown.

## Release copy checks, repository tooling (3)

- `review-copy4#2`: An unquoted Mermaid state label is refused as a prose claim (false refusal).
- `review-copy4#5`: A blank line hides later MkDocs extensions from the coverage guard.
- `review-copyfur1#2`: Copy after a sentence that ends in a single capital letter is read as one sentence with it,
  so independent copy can be refused along with the claim before it.

## Not yet measured

These are not findings; they are measurements that have not run or were too small to count, and no claim is made
from them:

- `release-030#routing-trials`: fresh-session routing trials, in which Claude Code and Codex meet tasks where G6
  could help without being told to call it. Before any session ran, the study's pins were re-recorded by dated
  amendments (2026-11-26, and 2026-11-27 for review) for the review, learn, question and workflow routes, and three
  development tasks were added for learn. No result changed, because none existed.
- `release-030#dogfood`: G6 used on ten patches from its own release work, by one person on one Windows machine
  (n=1 dogfooding, not a study). Before the one-time access grant, no review could run. With the grant, the bar of
  three catches was not met: G6 alone made one catch, on a branch deferred to 0.3.1, and with an agent's proposed
  checks one more, on code that ships. G6 alone also raised false alarms on correct patches: five from tests that
  fail only inside its Windows sandbox (`platform-windows-git`), and two that flagged test edits the change needed as
  weakened tests.
- `release-030#distill-study`: the three-arm distillation study (static checks, reflection only, compiled
  checks) on weakened tests. Two pilots stopped before a label could be read.

## Appendix A: deferred with 0.3.1

These findings belong to features that are not in 0.3.0. They ship with those features.

- **Template behaviour checks for methods (9):** `review-tmfu3r1#3`, `review-tmfu3r1#4`, `review-tmfu3r1#5`,
  `review-tmfu3r3#3`, `review-tmfu3r3#4`, `review-tmfu3r3#5`, `review-tmfu3r3#6`, `review-tmfu3r3#7`,
  `review-tmfu3r3#8`.
- **Typed completion-loop records and prompt reservations (6):** `review-wtffu4r4#1` to `review-wtffu4r4#6`.
- **Tool families and optional-dependency routing (1):** `review-rfu6r4#1`.
- **The record of which code each test depended on (1):** `review-evidfu3#5`.
- **Learning from recorded outcomes (1, not measured):** `release-030#learning-loop`.
- **Model selection by meaning (1, must-close):** `review-wsemfu3#1`.
- **A model by default through the person's own sign-in (1, must-close):** `review-mdr3#sdk-retries`.
- **No open findings listed:** `g6_record`, the tool that reads back the record of what G6 checked, evidence custody,
  the tests a change touches with the ones not reached named, LangExtract grounding, clarity reports, intent
  candidates, typed test-output parsers, unknown costs in graph reasoning, and discriminating diagnosis.

## Appendix B: code left out of 0.3.0

These findings are on code the installed package does not hold (it holds `src/g6` only), so they stay with that
code:

- **The worked-examples study's execution, capture and replay code** (the host-session runner, its watchdog and
  workspace packing, the container K loop, the replay cohort's capture, and the study's saved-row and change-identity
  checks): research tooling under `validation/worked_examples`, or not in the tree at all. `review-hafx4#1`,
  `review-ha1#23`, `review-hk34#9`, `review-k34#1`, `review-k34#2`, `review-k34#3`, `review-k34#4`, `review-k34#5`,
  `review-k34#6`, `review-replay4#1`, `review-replay4#2`, `review-replay4#3`, `review-replay4#4`, `review-rpf4#1`,
  `review-stfu3#2`, `review-stfu4#1`, `review-stfu4#2`, `review-stfu4#3`, `review-stfu4#4`, `review-v2#1`.
- **The intent-to-formal-property tool** (section 15): not built into 0.3.0; its code is not in the tree.
  `review-s15a#1`, `review-s15a#2`, `review-s15a#3`, `review-s15a#4`, `review-s15a#5`, `review-s15a#6`,
  `review-s15a#7`.

`review-hafx4#2` was found in the study's packing code, and the same device-name rule is in G6's shipped path check,
so it has its own entry under "Paths and storage".
