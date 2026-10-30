"""Client-mediated consent with a portable MCP schema; never infer human identity."""
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Consent:
    status: str
    answer: str | None = None
    error_type: str = ""


async def request_consent(ctx: Any, question: str) -> Consent:
    # FastMCP's primitive wrapper adds a root `title`; Codex 0.164 rejects it.
    # Use the SDK directly with only standard form-schema fields. Keep a required
    # string: an empty-schema confirmation may be auto-accepted by client policy.
    schema = {"type": "object", "properties": {
        "value": {"type": "string", "description": "Your name (recorded as supplied, not authenticated)"}},
        "required": ["value"]}
    try:
        result = await ctx.session.elicit_form(
            message=question, requestedSchema=schema, related_request_id=ctx.request_id)
    except Exception as exc:  # noqa: BLE001 - unavailable consent must never authorize
        return Consent("unavailable", error_type=type(exc).__name__)
    if result.action == "decline":
        return Consent("declined")
    if result.action == "cancel":
        return Consent("cancelled")
    content = getattr(result, "content", None)
    value = content.get("value") if isinstance(content, dict) else None
    if result.action == "accept" and isinstance(value, str) and value.strip():
        return Consent("accepted", value.strip())
    return Consent("invalid")


def refusal(consent: Consent, *, next_action: str = "") -> dict:
    from g6.metaharness.api import _blocked
    from g6.metaharness.schema import APPROVAL_REQUIRED

    messages = {
        "declined": "The client returned declined consent; no change was authorized.",
        "cancelled": "The client cancelled the consent request. G6 cannot tell whether a prompt was displayed.",
        "unavailable": "Client elicitation failed or is unavailable; no consent was received.",
        "invalid": "The client returned no valid, nonempty name with acceptance; no change was authorized.",
    }
    return _blocked(APPROVAL_REQUIRED, messages[consent.status],
                    next_action or "No change was made. Inspect the request and client consent support before retrying.",
                    consent_status=consent.status, consent_error_type=consent.error_type)
