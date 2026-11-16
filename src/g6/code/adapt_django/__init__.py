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

#: How each refusal this component writes is repaired (``g6.core.refusals``): code -> who fixes it, and
#: G6's words where they are not the class's own. A census test holds every code it writes to one entry.
__g6_refusals__ = {
    "G6_E_DJANGO_CHECK": "none",
    "G6_E_DJANGO_INPUT": "input",
    "G6_E_DJANGO_MIGRATIONS": "none",
}
