# SPDX-License-Identifier: Apache-2.0
"""Build web pages and design tokens; check syntax, declared contrast and optional overflow."""
from g6.code.adapt_webpage.block import SENSITIVE_OPS, AdaptWebpageBlock
from g6.code.adapt_webpage.checks import BROWSER
from g6.code.adapt_webpage.schema import AdaptWebpageInput

__g6_component__ = True
__g6_block__ = "AdaptWebpageBlock"
__g6_family__ = "code"
__g6_sensitive_ops__ = SENSITIVE_OPS
__g6_requires__ = ()
__g6_mitigates__ = ("broken_responsive_layout", "design_quality_failed")
__g6_introduces__ = ("broke_existing_functionality",)
__g6_outside__ = (BROWSER,)
__g6_extras__ = {"web": ("tinycss2",), "render": ("playwright",)}

__all__ = ["SENSITIVE_OPS", "AdaptWebpageBlock", "AdaptWebpageInput"]
