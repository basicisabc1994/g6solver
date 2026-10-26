# SPDX-License-Identifier: Apache-2.0
"""g6.lean — check a Lean 4 proof of a named theorem: declared in your file, no sorry, only Lean's standard axioms, and the statement you expect.

The owner, 2026-10-20: formal methods ported into the library, one package per engine; this is the Lean one, with the
earlier version's worst audit finding fixed (audit 017, FM-001: any file that elaborated was labelled ``verified``, even
one holding only a comment, a ``def`` or an ``axiom``).

Capability label: ``implemented``.

    from g6.lean import check_proof
    r = check_proof("theorem two_add : 2 + 2 = 4 := rfl", "two_add", expected_statement="2 + 2 = 4")
    r.completion_state, r.statement, r.axioms        # ("qualified-draft", "2 + 2 = 4", [])
    check_proof("-- nothing here", "two_add").code   # "G6_E_LEAN_THEOREM_MISSING"

Lean 4 is yours, out of process, never installed by G6: install elan, Lean's toolchain manager, as the Lean project
describes at lean-lang.org, which puts ``lean`` on PATH, or set ``G6_LEAN`` to a ``lean`` executable. Only ``Init`` and
``Std``, which ship with Lean, may be imported; G6 never installs Lean, a toolchain, Lake or Mathlib, and never
downloads anything. No pip extra: there is no Python package to install.

``check_proof`` (:mod:`g6.lean.proof`) takes the source, the theorem's full name and, optionally, the statement you
expect. A proof passes only when every one of these holds:

* the source passes the screen (:mod:`g6.lean.screen`), which refuses, each with its own code, line and column:
  ``sorry``, ``admit`` and ``stop``; what runs code (``#eval``, ``run_cmd``, ``run_tac``, the IO monad, ``unsafe``,
  ``implemented_by``, ``extern``, ``initialize``, ``native_decide``); ``axiom`` and ``opaque`` declarations; new syntax
  (``macro``, ``syntax``, ``elab``, ``notation``, ``macro_rules`` and the like); attributes outside a short list;
  ``set_option`` outside a short list with caps; imports other than ``Init`` and ``Std``, and ``prelude``; ``#``
  commands other than ``#check``, ``#check_failure``, ``#reduce`` and ``#synth``; G6's own marker; control and
  invisible characters. The screen is not the guard of soundness; Lean's report below is;
* the source declares a ``theorem`` by that full name;
* Lean elaborates the file in a fresh directory with no error, under a time limit (60 seconds by default, at most
  600), an output cap, a memory cap and a minimal environment, its process tree killed when a limit is passed; the
  source reaches Lean as a file, never on a command line (:mod:`g6.lean.runner`);
* Lean itself reports, on lines G6 appends after the source and marks with a nonce the source cannot know
  (:mod:`g6.lean.lean_output`): that the name was unknown before the source (``#check_failure``), so a theorem from an
  import cannot pass as yours; that it is a ``theorem``, not a ``def`` (``#print``); its statement (``#check``, also
  with every name in full and every numeral's type); and its axioms (``#print axioms``), which must all be among
  ``propext``, ``Classical.choice`` and ``Quot.sound``: ``sorryAx`` is ``G6_E_LEAN_SORRY`` and any other axiom
  ``G6_E_LEAN_NONSTANDARD_AXIOM``;
* when an expected statement is given, the statement Lean prints matches it with runs of whitespace collapsed, and Lean
  accepts the theorem as a proof of the expected statement read on its own (``example : (statement) := @name``): a
  statement that prints the same but means another thing, such as ``2 + 2 = 4`` over ``Fin 3``, is
  ``G6_E_LEAN_STATEMENT_MISMATCH``.

A pass is ``qualified-draft`` (``G6_E_LEAN_PROOF_CHECKED``), never ``verified``: Lean's kernel accepted a proof of
exactly this statement, using only the axioms listed, and whether the statement says what you mean, with the
definitions and instances it uses from your file, is for a person to inspect. A failure is ``blocked-escalated`` with
Lean's messages (file, line, column and text) on your own line numbers. ``lean_version`` reads ``lean --version``.
Both ops are sensitive, because Lean runs your source as you: callable from Python, and over MCP only when the person
who runs the server lists them in ``G6_MCP_SENSITIVE``. ``ops`` and ``help`` answer over MCP. Codes are ``G6_E_LEAN_*``.

Proof search (:mod:`g6.lean.search`, the archive's ``lean_prover`` rewritten): ``prove`` writes the goal itself from
your statement, tries ``rfl``, ``decide``, ``simp`` and ``omega``, then proofs an injected ``g6.core.llm`` caller
proposes, and takes the first candidate ``check_proof`` passes against your statement, ``qualified-draft`` at best; it
runs Lean, so it is sensitive too. ``sketch`` is a model's outline in words, a draft Lean never checked. The archive's
verdict path (any file without the word ``sorry`` was "proved"), its stepping op that applied nothing, six of its seven
strategies, Pantograph (its licence is not established), the theory store, Lake and Mathlib setup, research, the billing
context and its 29-op server did not come across.

Not ported from the archive's ``lean_mcp``, ``lean_bridge`` and ``lean_proof_index``, and why:

* the ``verified`` label on any file Lean exited 0 on (FM-001), and the artifact whose "replay" repeated that check;
* ``run_code`` and ``eval_expr``, which ran ``#eval`` on caller text, and ``elaborate``, ``type_check``,
  ``infer_type``, ``unfold_definition`` and ``tactic_apply``, which ran caller text through Lean with no screen;
* the micro-kernel, a string heuristic that answered when Lean was absent, and the analysis ops built on it
  (``validate_proof``, ``compare_proofs``, ``inspect_term``, ``explain_tactic``, ``suggest_tactic``, ``find_lemma``,
  ``check_termination``);
* the theorem store and its search and export, and the proof index, which stamped ``verified`` on any text that
  matched a pattern (FM-016);
* the thread-pool bridge with its billing context (FM-019), the standalone server, the Windows-only install hint and
  the installer script;
* the translation layers (autoformalisation, triangulation, intent fidelity), which the design leaves for their own
  decision; Lake projects, Mathlib and any download.
"""
from g6.core.outside import OutsideProgram
from g6.lean.envelope import INSTALL_LEAN
from g6.lean import lean_output, proof, runner, screen, search
from g6.lean.envelope import LEAN_ENV, STANDARD_AXIOMS, Refusal, finish
from g6.lean.lean_block import SENSITIVE_OPS, LeanBlock, LeanInput
from g6.lean.proof import check_proof, lean_version
from g6.lean.schema import LeanResult
from g6.lean.screen import declarations, screen_statement
from g6.lean.search import SearchResult, prove, sketch

__g6_component__ = True
__g6_block__ = "LeanBlock"
__g6_model__ = "llm"  # the block's model keyword: g6_invoke passes the model the person configured, if any
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # they run Lean on your source
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause.
__g6_mitigates__: tuple[str, ...] = ("unfinished_proof", "misstated_theorem", "unchecked_maths_claim")
__g6_introduces__: tuple[str, ...] = ()
__g6_outside__ = (OutsideProgram(
    name="Lean", purpose="check_proof, lean_version and prove run Lean 4 on your source", install=INSTALL_LEAN,
    licence="Apache-2.0", command="lean", env=LEAN_ENV),)

__all__ = ["LEAN_ENV", "LeanBlock", "LeanInput", "LeanResult", "Refusal", "SENSITIVE_OPS", "STANDARD_AXIOMS",
           "SearchResult", "check_proof", "declarations", "finish", "lean_output", "lean_version", "proof", "prove",
           "runner", "screen", "screen_statement", "search", "sketch"]
