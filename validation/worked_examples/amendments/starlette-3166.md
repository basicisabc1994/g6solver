# starlette-3166: scoring amendment

Issue Kludex/starlette#2019, "SessionMiddleware sends a new set-cookie for every request, with unintended results". The task is the issue, the comments made before PR 3166 opened (2026-03-01T17:39:22Z), and the repository at the parent commit. The maintainer's comment announcing the PR came after it opened and is not part of the task.

The stated behaviour: a request that does not change the session gets no new `Set-Cookie`. One comment puts it as "We now only send the cookie if the session dict was modified".

Searched the parent's `starlette`, `docs` and `tests` for `class Session`, `.accessed`, `.modified`, `mark_accessed` and `mark_modified`, and for `Vary` in session code. The issue's comments describe "a dict like object" with no name and never mention `Vary`. The only `Vary` handling at the parent is in the CORS and GZip middleware, static files and the generic header helper.

## tests/middleware/test_session.py

**Pinned name, in the import line.** The fix's test imported `Session` from `starlette.middleware.sessions`. A fix without that class fails at import, and with it every test in the module. The name is removed from the import.

**Pinned name and attribute.** `test_session_tracks_modification` built `Session` objects directly and read `.modified`. The class and attribute are the fix's own. The test is rewritten to check the same seven cases through the public API: a real app, `request.session`, and the response headers. After a session is seeded, setting an item, deleting one, clearing, popping an existing key and `setdefault` on a missing key must each send `Set-Cookie`. Popping a missing key and `setdefault` on an existing key must not. "Modified" becomes the observable result the task names. The test keeps its id and takes no new fixture.

**Behaviour the task does not state.** `test_vary_cookie_on_access` required `Vary: Cookie` after writing and after reading a session. That is a new header the task never mentions, so those two assertions and their comments are removed. The third assertion stays: not touching the session adds no `Vary: Cookie`, which is what the parent does. The two requests stay too, so that request is made with a session cookie present.

## Left unchanged

`test_set_cookie_only_on_modification` is the issue's own behaviour, stated exactly, and uses only public API. Every older test in the module is unchanged.

No test removed.

## Control

With the fix's class and attributes renamed and its `Vary: Cookie` lines deleted, all the amended tests pass, so none of those names or that header is still required.
