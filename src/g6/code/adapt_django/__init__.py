# SPDX-License-Identifier: Apache-2.0
"""Generate Django source without Django; check full projects in a separate, time-limited process, not a security boundary."""
from g6.code.adapt_django.block import SENSITIVE_OPS, AdaptDjangoBlock
from g6.code.adapt_django.schema import AdaptDjangoInput

__g6_component__ = True
__g6_block__ = "AdaptDjangoBlock"
__g6_family__ = "code"
__g6_sensitive_ops__ = SENSITIVE_OPS
__g6_requires__ = ()
__g6_mitigates__ = ("handwritten_boilerplate",)
__g6_introduces__ = ("broke_existing_functionality",)
__g6_outside__ = ()
__g6_extras__ = {"django": ("django",)}

__all__ = ["SENSITIVE_OPS", "AdaptDjangoBlock", "AdaptDjangoInput"]
