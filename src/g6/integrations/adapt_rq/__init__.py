# SPDX-License-Identifier: Apache-2.0
"""Background JSON jobs on an operator-owned Redis queue, with registered functions and sensitive operations."""
from g6.integrations.adapt_rq.block import SENSITIVE_OPS, AdaptRQBlock, RQInput
from g6.integrations.adapt_rq.config import REDIS, WORKER, register_function

__g6_component__ = True
__g6_block__ = "AdaptRQBlock"
__g6_requires__ = ()
__g6_mitigates__ = ("unseen_job_failure",)
__g6_introduces__ = ("unsafe_autonomous_action", "prompt_injection")
__g6_sensitive_ops__ = SENSITIVE_OPS
__g6_outside__ = (REDIS, WORKER)
__g6_extras__ = {"rq": ("rq", "redis")}

__all__ = ["AdaptRQBlock", "RQInput", "register_function"]
