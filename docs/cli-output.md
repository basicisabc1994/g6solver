# Output from an invocation

The `g6` console entry point captures Python's stdout and stderr for `invoke` before resolving the operation and keeps that capture through
encoding and interpreter shutdown. Warnings, thread exceptions, unraisable exceptions, uncaught exceptions and
root logging records are counted without displaying their text, paths or tracebacks. Existing handlers on root,
named loggers (including non-propagating loggers) and `logging.lastResort` that were bound to entry stdout or stderr
are pointed at the routing pair until the capture owner exits, then returned to their exact original streams.
Their formatted text is discarded and counted as stream writes. Captured buffers are bounded
and never replayed. G6 writes its reply and diagnostics to the original streams it saved.
At the owned console entry, before argument parsing or optional dependency imports, G6 ignores
`DeprecationWarning` and `PendingDeprecationWarning` (including subclasses) attributed to source files outside
the `g6` package. Its warning display hook drops them even if a dependency installs its own `always` filter
during import. G6's own warnings and other warning categories retain their existing policy and display handler.
Python's warning filters are unchanged.
Invocation capture then reroutes only warnings Python would display. This startup policy belongs to the
console process; importing the entries or calling embedding `main()` does not install it.

After the reply, stderr receives one G6 line listing any captured counts. An atexit callback reports new counts
after the reply, only when there were any. An operation's atexit callbacks and non-daemon threads remain covered.
The console installs one routing pair before resolving the operation. Nested CLI calls and MCP helpers change
routing targets; worker completion never replaces process streams. After that callback's last line is flushed,
the owner restores its stream pair once. A stream whose reply or diagnostic write or flush failed is replaced
with a sink whose writes and flushes are no-ops, so Python's shutdown flush cannot change G6's exit code
or print another error. The console then points file descriptors 1 and 2 and Python's
`sys.__stdout__` and `sys.__stderr__` at the null device. Output during subsequent module teardown is
discarded, including finalisers; it cannot be counted after the final report.
`--json` writes one reply envelope to stdout; plain mode prints the result alone. Invocation failures keep their
exit codes, and KeyboardInterrupt propagates with the previous interrupt handling restored.

This capture covers Python streams and hooks. Writes below Python, including `os.write` on file descriptors 1 or 2,
C extensions writing directly to those descriptors, and child processes inheriting them, are outside the capture
and can still reach the terminal. This is output handling for cooperative Python code, not a sandbox against code
that deliberately changes the streams or hooks.
The descriptor limit applies before the console's final atexit callback seals the descriptors.

An in-process call to `g6.cli.main(argv)` captures for the duration of that call, including encoding. It restores
the streams that were current when called, warning filters and hooks, exception hooks, root logging handlers and
level, existing handlers' streams, and `logging.lastResort` when it returns or raises. The caller's stdout and stderr
objects remain identical even after a detached worker finishes. A thread that fails after this call returns belongs to
the embedding caller and uses that caller's hooks. Nested calls, including calls inside the console's installed
capture, reuse its original streams and add to the same counters. Each call writes its reply to those originals;
only the owner restores the capture. Concurrent `main()` calls wait on one process lock, including capture
installation and restoration; the CLI is not a concurrent API. Only the console entry point registers the
shutdown count line or redirects descriptors at shutdown. Restoring warning filters also invalidates Python's
warning caches, so an operation's temporary policy cannot suppress the caller's later warnings.

The stdio MCP server gives only the protocol writer a private, non-inherited duplicate of stdout's descriptor.
For the server's lifetime, descriptor 1 points at descriptor 2. An operation's `os.write(1, ...)`, native writes,
`sys.__stdout__`, and inherited child stdout therefore go to stderr, outside the protocol stream. These
descriptor-level writes are not masked and may display operation text on stderr. Direct Python helpers such as
`invoke_reply` used outside the stdio server do not redirect descriptors.
The console server leaves descriptor 1 on stderr after serving stops, so buffered dunder writes or exit callbacks
cannot regain the protocol pipe; the private writer descriptor is closed.

During each `g6_invoke` and `g6_compose` call, Python stdout goes to a bounded private buffer. A thread started
inside the call retains that buffer, including when it outlives the call. Reused or otherwise unattributed workers
cannot write to protocol stdout: unowned Python stdout is discarded and stderr receives at most one fixed G6
line per router lifetime. Attribution is captured at thread start, not at task submission to an existing pool.
The console server keeps its routing pair through non-daemon worker completion and operation atexit callbacks,
then restores it once in its own final atexit hook. Standalone MCP helpers retain an idle forwarding
pair after their targets finish, so late workers can release targets without changing stream bindings; once no
calls or attributed workers remain, unrelated output goes to the original streams. An embedding CLI call restores
the objects supplied by its caller at its own end, regardless of any worker still running.
The MCP entry module defers imports that pull in optional dependencies. Before the stdio runner imports anyio
or builds the server (which imports FastMCP), it installs the same external-deprecation display filter as the
console. Import-time dependency deprecations therefore never reach stderr. G6's own warnings retain their
existing treatment. No process-lifetime exception or logging hooks are installed in the server; other warnings,
those channels and direct stderr output remain outside this capture.
Deliberate replacement of streams, hooks or descriptors is outside cooperative output handling. The transport
uses FastMCP's lifespan and low-level server interfaces because its convenience runner takes no output stream.
Closing or breaking the protocol pipe, including closing the private writer, exits the server with code 1 and
one fixed `[G6_E_STDIO_CLOSED] G6: protocol pipe closed.` line if stderr is writable. I/O failures inside transport
exception groups are handled the same way. They display no exception text, traceback or path.

The Python library entry `g6.invoke` runs inside the caller's process and installs no process-wide capture or hooks.
