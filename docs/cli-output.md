# Output from an invocation

The `g6` console entry point captures Python's stdout and stderr for `invoke` before resolving the operation and keeps that capture through
encoding and interpreter shutdown. Warnings, thread exceptions, unraisable exceptions, uncaught exceptions and
root logging records are counted without displaying their text, paths or tracebacks. Captured buffers are bounded
and never replayed. G6 writes its reply and diagnostics to the original streams it saved.

After the reply, stderr receives one G6 line listing any captured counts. An atexit callback reports new counts
after the reply, only when there were any. An operation's atexit callbacks and non-daemon threads remain covered.
`--json` writes one reply envelope to stdout; plain mode prints the result alone. Invocation failures keep their
exit codes, and KeyboardInterrupt propagates with the previous interrupt handling restored.

This capture covers Python streams and hooks. Writes below Python, including `os.write` on file descriptors 1 or 2,
C extensions writing directly to those descriptors, and child processes inheriting them, are outside the capture
and can still reach the terminal. This is output handling for cooperative Python code, not a sandbox against code
that deliberately changes the streams or hooks.

An in-process call to `g6.cli.main(argv)` captures for the duration of that call, including encoding. It restores
the streams that were current when called, warning filters and hooks, exception hooks, root logging handlers and
level, and `logging.lastResort` when it returns or raises. A thread that fails after this call returns belongs to
the embedding caller and uses that caller's hooks. Nested calls, including calls inside the console's installed
capture, reuse its original streams and add to the same counters. Each call writes its reply to those originals;
only the owner restores the capture. Only the console entry point registers the shutdown count line.

The stdio MCP server captures stdout during each `g6_invoke` and `g6_compose` call, isolating concurrent calling
threads and leaving the protocol thread's output alone. It installs no process-lifetime exception, warning or
logging hooks. Output from detached threads after a server call is outside that per-call capture.

The Python library entry `g6.invoke` runs inside the caller's process and installs no process-wide capture or hooks.
