"""Client-mediated consent with a portable MCP schema; never infer human identity."""
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


def question(asks: str, binding: Mapping[str, Any], said: Sequence[str] = (), *, close: str) -> str:
    """The one way a person's dialog is written from what its act binds: what it asks, then every field of ``binding``
    as ``name: value``, whole, then what the act does and how to agree or refuse. An act shows what it binds by passing
    it here, so no field it stands on is left out of what the person reads."""
    def shown(value: Any) -> str:
        return value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)

    return "\n".join([asks, *(f"{name}: {shown(value)}" for name, value in binding.items()), *said, close])


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


#: COPY: What the person can do after each way a dialog ends without their agreement. None offers another way to agree,
#: since there is none: no terminal, no command, no setting and no agent's answer.
ASK = "Ask the person whether they want this before calling again, and offer no other way to agree: there is none."
CAN = {
    "declined": ("The person declined in the client's dialog; nothing was authorized.", ASK),
    "cancelled": ("The client cancelled the dialog. G6 cannot tell whether it was displayed.", ASK),
    "unavailable": ("This client showed the person no dialog (it has no MCP elicitation, or the request failed), so no "
                    "consent was received.",
                    "The person agrees only in the dialog of a client session that can show one (MCP elicitation), and "
                    "there is no other way: no command, setting or argument agrees for them. Tell the person; do not ask "
                    "for their name yourself."),
    "invalid": ("The client returned acceptance with no name, so nothing was authorized.",
                "Call again and ask the person to type their name in the dialog: the name is what counts, and there is "
                "no other way to agree."),
}


def refusal(consent: Consent, *, nothing: str = "No change was made.") -> dict:
    """The one refusal for a dialog that ended without a person's agreement: a code for each way it ended, what did not
    happen (``nothing``), and what the person can do."""
    from g6.metaharness import schema
    from g6.metaharness.api import _blocked

    codes = {"declined": schema.CONSENT_DECLINED, "cancelled": schema.CONSENT_CANCELLED,
             "unavailable": schema.CONSENT_NO_DIALOG, "invalid": schema.CONSENT_NO_NAME}
    message, can = CAN[consent.status]
    return _blocked(codes[consent.status], message, f"{nothing} {can}",
                    consent_status=consent.status, consent_error_type=consent.error_type)
