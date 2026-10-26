# SPDX-License-Identifier: Apache-2.0
"""g6.memory.ctx_cognee — add text to the Cognee server you run and follow the links its knowledge graph draws between facts, each result cited and untrusted.

An extension in the memory family. Cognee is a program you install and start yourself (``__g6_outside__``); G6
reaches it only through ``g6.core.outside.call_service``, plain HTTP on this machine at 127.0.0.1:8000 or the local
address ``G6_COGNEE_URL`` names, and never imports, installs, bundles or stands in for it (the cognee package requires
litellm, which G6 never uses). A server that is not answering is a ``blocked-escalated`` refusal with the install steps.

    from g6.core.registry import get_registry
    out = get_registry().invoke("ctx_cognee", "search", {"query": "who maintains the billing service"}).unwrap()
    out["results"], out["completion_state"]   # untrusted hits citing their dataset, "qualified-draft"

What it does:

* ``add`` sends text to a dataset and has Cognee build its graph; ``forget`` deletes one item. Both are dry runs unless
  ``dry_run`` is false, sent only when ``g6.action_gating`` lets them through (your Cognee token in your key file is
  your approval), and each is checked by listing the dataset's items before and after. An add is reported built only
  when Cognee's add and cognify replies each hold a finished run for that dataset and the item named for the text is
  listed; otherwise it is refused, saying what was already sent. A delete Cognee answers but does not carry out, or
  one G6 cannot check because the listing afterwards fails, is refused, never reported as done; after a delete the
  dataset's graph is read and the receipt says what it still holds of the item;
* ``search`` returns what Cognee finds as untrusted text citing its dataset and chunk, an answer its model wrote marked
  ``model_generated``; a ``*_COMPLETION`` search, which has Cognee's model write the answer, is a dry run unless asked
  and decided by the action gate like ``add``; an empty answer is said not to prove absence;
* ``graph`` returns the entities and links Cognee extracted for a dataset, drops a link to an entity the graph does
  not hold, and finds the shortest chain of links joining two entities, so a fact in one text can be followed to a
  related fact in another;
* ``status`` and ``datasets`` pass on what the server says about itself.

Every op but ``ops`` and ``help`` talks to your Cognee, which may send what it is given to the model provider it is set
up with, so every one is sensitive: it runs from Python, and over MCP only when the person who runs the server lists
it (or ``ctx_cognee``) in ``G6_MCP_SENSITIVE``. Keep Cognee's sign-in on and put its token in your key file
(``__g6_keys__``): G6 sends it only to that local Cognee, and without it each answer says sign-in is off and what that
exposes.

Not ported from the archive's ctx_cognee, and why: the import of the cognee package, which sent text to Cognee's
model whenever it was installed (CTX_COGNEE-01) and needs litellm (CTX_COGNEE-02); the capitalised-word fallback
presented as a knowledge graph; the reset that cleared only two lists in memory (CTX_COGNEE-03); the configure op that
let a caller redirect the model endpoint, the billing coupling, the sessions database in the home folder, the
default-on re-ranker and the scaffold.
"""
from g6.memory.ctx_cognee.block import (
    COGNEE,
    COGNEE_KEY,
    SEARCH_TYPES,
    SENSITIVE_OPS,
    URL_ENV,
    CogneeInput,
    CtxCogneeBlock,
)
from g6.memory.ctx_cognee.client import Cognee, CogneeError

__g6_component__ = True
__g6_block__ = "CtxCogneeBlock"
__g6_family__ = "memory"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # each one talks to the Cognee server you run
__g6_requires__: tuple[str, ...] = ("action_gating", "grounding", "knowledge")  # the gate; the knowledge family's reply
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unlinked_facts",)
__g6_introduces__: tuple[str, ...] = ("provenance_missing", "fabricated_fact")
__g6_outside__ = (COGNEE,)
__g6_keys__ = (COGNEE_KEY,)  # your Cognee token, from your key file only; sent only to that local Cognee
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["COGNEE", "COGNEE_KEY", "SEARCH_TYPES", "SENSITIVE_OPS", "URL_ENV", "Cognee", "CogneeError", "CogneeInput",
           "CtxCogneeBlock"]
