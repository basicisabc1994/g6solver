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

#: How each refusal this component writes is repaired (``g6.core.refusals``): code -> who fixes it, and
#: G6's words where they are not the class's own. A census test holds every code it writes to one entry.
__g6_refusals__ = {
    "G6_E_RQ_BACKEND_FAILED": "none",
    "G6_E_RQ_DRY_RUN": "none",
    "G6_E_RQ_FUNCTION_REFUSED": "none",
    "G6_E_RQ_INFO": "none",
    "G6_E_RQ_JOB_MISSING": "none",
    "G6_E_RQ_METADATA_REFUSED": "none",
    "G6_E_RQ_NOT_INSTALLED": "person",
    "G6_E_RQ_PARTIAL_FAILURE": "none",
    "G6_E_RQ_PAYLOAD_REFUSED": "none",
    "G6_E_RQ_QUEUE_MISMATCH": "none",
    "G6_E_RQ_REPORT": "none",
    "G6_E_RQ_REQUEUE_FAILED": "none",
    "G6_E_RQ_WORKER_REFUSED": "none",
}
