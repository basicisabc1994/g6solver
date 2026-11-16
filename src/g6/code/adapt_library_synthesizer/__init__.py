# SPDX-License-Identifier: Apache-2.0
"""Draft Python from a paper spec and check independently supplied examples."""
from g6.code.adapt_library_synthesizer.block import SENSITIVE_OPS, AdaptLibrarySynthesizerBlock
from g6.code.adapt_library_synthesizer.schema import AdaptLibrarySynthesizerInput

__g6_component__ = True
__g6_block__ = "AdaptLibrarySynthesizerBlock"
__g6_model__ = "caller"  # the block's model keyword: g6_invoke passes the model the person configured, if any
__g6_family__ = "code"
__g6_sensitive_ops__ = SENSITIVE_OPS
__g6_requires__ = ()
__g6_mitigates__ = ("unfaithful_method_code",)
__g6_introduces__ = ("wrong_problem", "broke_existing_functionality")
__g6_outside__ = ()
__g6_extras__ = {"docs": ("pypdf",)}

__all__ = ["SENSITIVE_OPS", "AdaptLibrarySynthesizerBlock", "AdaptLibrarySynthesizerInput"]

#: How each refusal this component writes is repaired (``g6.core.refusals``): code -> who fixes it, and
#: G6's words where they are not the class's own. A census test holds every code it writes to one entry.
__g6_refusals__ = {
    "G6_E_PAPER_FETCHED": "none",
    "G6_E_PAPER_HOST": "person",
    "G6_E_PAPER_PDF": "none",
    "G6_E_PAPER_SIZE": "none",
    "G6_E_PAPER_TYPE": "none",
    "G6_E_SYNTH_INPUT": "input",
    "G6_E_SYNTH_MODEL": "person",
    "G6_E_SYNTH_NO_MODEL": ("tool:g6_select", "no_model"),
    "G6_E_SYNTH_NO_ORACLE": "none",
    "G6_E_SYNTH_ORACLE_FAILED": "none",
    "G6_E_SYNTH_ORACLE_SOURCE": "none",
    "G6_E_SYNTH_SPEC": "input",
    "G6_E_SYNTH_SYNTAX": "input",
}
