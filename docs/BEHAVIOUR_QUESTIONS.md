# Confirmed behaviour checks

A behaviour answer confirms one fresh import and one call, with copies of the displayed arguments. New executable
scenarios also display and retain the import roots (`.` followed by `src`), the resolved repository file, and its
SHA-256. Later caller import roots cannot replace these. A changed source file requires a new confirmation; a run
whose referent differs cannot count as a pass. This also means that a base control with different source bytes may
be unavailable rather than evidence against the current artefact.

G6 starts its own interpreter with `-I -S -B` in a folder written from the execution manifest. Repository pytest
configuration, plugins and `conftest.py` do not run the confirmed check. Exception outcome modules are never imported
to prepare a call: after the call, G6 resolves exception types among the modules the call itself loaded. Both offered
outcomes use this same setup. Portable example reproductions use the same observation bootstrap; running a wrapper
with pytest is a display convenience and its pytest status does not supply product evidence.

The repository's own module code necessarily executes when imported. It shares the child interpreter with the
bootstrap and can shape its own observed outcome, including the result channel. The channel is not an authentication
boundary against code inside that interpreter. G6 includes this limit in the behaviour reply and check report.

Historical answers without an available executable scenario remain diagnostic and need a new dialog before they
can supply support. Version 1 scenarios can still be read as reproductions; saving or loading them as confirmation
custody is refused because they lack the referent binding. Checks remain limited to the displayed call and do not
establish completeness of the task.
