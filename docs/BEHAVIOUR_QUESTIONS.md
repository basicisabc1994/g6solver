# Confirmed behaviour checks

A behaviour answer confirms one fresh import and one call, with copies of the displayed arguments. New executable
scenarios also display and retain the import roots (`.` followed by `src`) and the resolved repository file.
Every run must resolve the name through those roots alone to that same path. A patch that edits the function's
file is checked on its patched bytes. A patch that moves or removes the file is refused with
`G6_E_OBSERVATION_UNAVAILABLE` and cannot count as a pass. Attempts by a caller to change the import path are refused:
later caller roots never replace the confirmed roots or redirect the function. Older version-2 records carrying
a referent `sha256` field remain readable;
that field is ignored.

G6 starts its own interpreter with `-I -S -B` in a folder written from the execution manifest. Repository pytest
configuration, plugins and `conftest.py` do not run the confirmed check. Exception outcome modules are never imported
to prepare a call: after the call, G6 resolves exception types among the modules the call itself loaded. Both offered
outcomes use this same setup. Portable example reproductions use the same observation bootstrap; running a wrapper
with pytest is a display convenience and its pytest status does not supply product evidence.

An ordinary example retains the repository file and ordered import roots chosen when its bindings were resolved.
Its fresh interpreter keeps that order for the function and its dependencies, including the default top-folder/src
order and multiple custom roots. Changing the caller's import path after compilation cannot redirect the example
to a different same-named module.

The repository's own module code necessarily executes when imported. It shares the child interpreter with the
bootstrap and can shape its own observed outcome, including the result channel. The channel is not an authentication
boundary against code inside that interpreter. G6 includes this limit in the behaviour reply and check report.

Historical answers without an available executable scenario remain diagnostic and need a new dialog before they
can supply support. Version 1 scenarios can still be read as reproductions; saving or loading them as confirmation
custody is refused because they lack the referent binding. Checks remain limited to the displayed call and do not
establish completeness of the task.
