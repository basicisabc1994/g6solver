# Output from an invocation

The `g6` console entry point captures Python's stdout and stderr for `invoke` before resolving the operation and keeps that capture through
encoding and interpreter shutdown. Warnings, thread exceptions, unraisable exceptions, uncaught exceptions and
root logging records are counted without displaying their text, paths or tracebacks. Captured buffers are bounded
and never replayed. G6 writes its reply and diagnostics to the original streams it saved.
Warning filters retain their existing policy: capture reroutes only warnings Python would display.

After the reply, stderr receives one G6 line listing any captured counts. An atexit callback reports new counts
after the reply, only when there were any. An operation's atexit callbacks and non-daemon threads remain covered.
After that callback's last line is flushed, the console points file descriptors 1 and 2 and Python's
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
level, and `logging.lastResort` when it returns or raises. A thread that fails after this call returns belongs to
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
The router remains installed throughout the stdio server lifetime. No process-lifetime exception, warning or
logging hooks are installed in the server; those channels and direct stderr output remain outside this capture.
Deliberate replacement of streams, hooks or descriptors is outside cooperative output handling. The transport
uses FastMCP's lifespan and low-level server interfaces because its convenience runner takes no output stream.

The Python library entry `g6.invoke` runs inside the caller's process and installs no process-wide capture or hooks.
