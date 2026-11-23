"""Routing remedies follow the tools and refusal writers that ship."""
from g6.core import selector
from g6.mcp import registrations

def test_agentic_no_model_remedy_uses_the_declared_selector_step():
    from g6._agentic.kernel import NO_MODEL

    remedy = selector.remedy_for(NO_MODEL)
    assert remedy["fix_by"] == "tool:g6_select"
    assert remedy["call"]["tool"] == "g6_select"
    assert "yourself" in remedy["action"]


def test_registered_agentic_tool_is_not_still_declared_pending():
    assert "g6_agentic_run" in registrations.recorded()["g6"]
    assert "g6_agentic_run" not in selector.PENDING_TOOLS
    assert selector.REMEDIES["component_limit"]["available"] is True
