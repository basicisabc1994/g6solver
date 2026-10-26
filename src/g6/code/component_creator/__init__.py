# SPDX-License-Identifier: Apache-2.0
"""g6.code.component_creator — scaffold a new G6 component in the library's own shape, and check a package against the library's standard before anyone calls it done.

An extension in the code family: the library's one scaffolder. ``spec_from_module`` reads a Python module or package
as text with ``ast`` (never imported) and maps each public function to an op by its name's prefix; ``spec_from_text``
asks a model caller you give the block to draft a spec and checks it against the spec schema; ``render`` turns a spec
into the library's shape: declarations, a typed input that refuses extra fields, ``ops`` and ``help``, handlers that
refuse until they are written (or call the library function they wrap and say nothing checked its result), one
failing "prove it fails" test and a skill whose Ops match the block's. It writes only when asked, into a new folder
under a root the person who runs G6 declares (``G6_CODE_CONFIG``), and is sensitive. ``check_conformance`` holds a
package to static checks (installed checks also import the package and run ops): declarations, failure modes
(``g6.core.failure_modes``), the block's input, the skill (``g6.core.skills``) and its tests.

    from g6.core.registry import get_registry
    out = get_registry().invoke("component_creator", "check_conformance", {"component": "ctx_library_mapper"}).unwrap()
    out["static_clean"], out["problems"]

Merged in from the archive: auto_component_integrator's interface reader and name-prefix mapping
(``spec_from_module``) and auto_engineer's spec drafting (``spec_from_text``). Not ported, and why: the 37- and 32-op
MCP blocks, their stores and flywheel tables, the exemplar store fed with unreviewed output, git rollback (it threw away
uncommitted work), the generation pipelines and the model chain (a hidden paid fallback), the 25 default MCP ops a
scaffold was padded to, the archive layout and tiers it wrote, handlers that answered "completed" with tests that only
checked that, and every result it labelled verified, a reviewer's signature string included.
"""
from g6.code.component_creator import conformance, render, spec
from g6.code.component_creator.block import SENSITIVE_OPS, ComponentCreatorBlock, ComponentCreatorInput
from g6.code.component_creator.spec import ComponentSpec, FieldSpec, OpSpec

__g6_component__ = True
__g6_block__ = "ComponentCreatorBlock"
__g6_model__ = "caller"  # the block's model keyword: g6_invoke passes the model the person configured, if any
__g6_family__ = "code"
__g6_sensitive_ops__: tuple[str, ...] = SENSITIVE_OPS  # render writes files when asked
__g6_requires__: tuple[str, ...] = ()
# Failure-mode codes (g6.core.failure_modes): what it guards against, and what its own use can cause (a model's draft
# of a spec can describe a different component from the one meant).
__g6_mitigates__: tuple[str, ...] = ("incomplete_component",)
__g6_introduces__: tuple[str, ...] = ("wrong_problem",)
__g6_outside__: tuple = ()
__g6_extras__: dict[str, tuple[str, ...]] = {}

__all__ = ["SENSITIVE_OPS", "ComponentCreatorBlock", "ComponentCreatorInput", "ComponentSpec", "FieldSpec", "OpSpec",
           "conformance", "render", "spec"]
