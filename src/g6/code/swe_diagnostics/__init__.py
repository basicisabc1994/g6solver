# SPDX-License-Identifier: Apache-2.0
r"""g6.code.swe_diagnostics — show a coding agent an unfamiliar repository before it changes it: its layout, build, tests and files, and what a change touches.

An extension in the code family. ``repo_map`` walks a folder under a root the person who runs G6 declares
(``G6_CODE_CONFIG``) and lists its languages, build systems, test runners, key files, top folders and file counts;
``detect_build_env`` reads its marker files for the package manager, test runner and declared test command (never
run); ``test_analyze`` and ``code_summarize`` read one file, or text given inline: Python from its syntax tree, and
JavaScript, TypeScript and Go by pattern, each such result marked ``"method": "pattern"``. Walks and reads are capped
(files, bytes) and the caps are said; a link out of a root is not followed. ``patch_check`` reads a repository's change
since a base through ``g6.workforce.repo_tests`` (git, an outside program): the files, the test and runner files it
touches, changed Python that does not parse, and the tests it touches; it runs no test, and it is sensitive. Every
answer is a ``qualified-draft``: an observation of a repository, not a checked claim.

    from g6.core.registry import get_registry
    out = get_registry().invoke("swe_diagnostics", "code_summarize", {
        "source": "class Cart:\n    def total(self):\n        return 0\n", "name": "cart.py"}).unwrap()
    out["classes"][0]["methods"]   # ["total"]

Not ported from the archive's swe_diagnostics, and why: loading and running modules from the archive's benchmark tree
(whose location an environment variable chose), and that tree itself, which holds benchmark answer material; the
patch validator that ran git through a shell in the server's own folder (``g6.workforce.repo_tests`` instead); the
default-on model review and its verified label; source_health; and the pattern and skill catalogues.
"""
from g6.code.swe_diagnostics import read
from g6.code.swe_diagnostics.block import GIT, SENSITIVE_OPS, SweDiagnosticsBlock, SweDiagnosticsInput

__g6_component__ = True
__g6_block__ = "SweDiagnosticsBlock"
__g6_family__ = "code"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # patch_check runs git on your computer
__g6_requires__: tuple[str, ...] = ("workforce",)  # g6.workforce.repo_tests reads the change for patch_check
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a repository's
# names, commands and files are its own words, which can carry instructions an agent might follow).
__g6_mitigates__: tuple[str, ...] = ("blind_repo_change",)
__g6_introduces__: tuple[str, ...] = ("prompt_injection",)
__g6_outside__ = (GIT,)
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["GIT", "SENSITIVE_OPS", "SweDiagnosticsBlock", "SweDiagnosticsInput", "read"]
