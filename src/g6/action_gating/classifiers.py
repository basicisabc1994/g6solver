# SPDX-License-Identifier: Apache-2.0

"""Deterministic reversibility and impact classification."""
from __future__ import annotations

from g6.action_gating.normalizer import has_profile
from g6.action_gating.schema import ActionCapsule, ImpactAssessment, Reversibility

# Domains this assessment rates high impact. They raise the aggregate whatever the reach: a declared
# security domain once left a password change at low risk (study V2, finding V2-F1).
_HIGH_IMPACT_DOMAINS = frozenset({"security", "identity", "financial", "privacy"})


def classify_reversibility(capsule: ActionCapsule) -> Reversibility:
    if capsule.reversibility == "reversible":
        return "fully_reversible"
    if capsule.reversibility == "partially_reversible":
        return "partially_reversible"
    if capsule.reversibility == "irreversible" or capsule.irreversible_or_costly_to_reverse:
        return "irreversible"
    return "unknown"


def classify_impact(capsule: ActionCapsule, reversibility: Reversibility) -> ImpactAssessment:
    rationale: list[str] = []
    domains = set(capsule.safety_domains)
    undeclared = capsule.reversibility == "unknown" or capsule.blast_radius == "unknown"
    # An action type with no profile has only its caller's declaration, a claim (audit 002, AG-008):
    # declared reversible and local, it is still never read as low risk.
    unknown = undeclared or not has_profile(capsule.action_type)
    high = (
        capsule.real_world_effect
        or capsule.external_side_effect
        or reversibility in {"irreversible", "compensatable_only"}
        or capsule.blast_radius in {"organization", "external_party", "public"}
    )
    critical = capsule.action_type in {"permission_escalation", "plc_write", "gcode_execute"}
    sensitive = sorted(_HIGH_IMPACT_DOMAINS & domains)

    aggregate = "low"
    if unknown:
        aggregate = "unknown"
        rationale.append("Unknown reversibility or blast radius is not treated as low risk." if undeclared else
                         "An action type with no profile is only as safe as its declared facts, which are claims; "
                         "it is not treated as low risk.")
    if high:
        aggregate = "high"
        rationale.append("External, real-world, broad, or irreversible effects require stronger gating.")
    if sensitive:
        aggregate = "high"
        rationale.append(f"An action in the {', '.join(sensitive)} domain requires stronger gating, whatever its reach.")
    if critical:
        aggregate = "critical"
        rationale.append("This action type requires global safety approval before execution.")

    level = "high" if aggregate in {"high", "critical"} else ("unknown" if aggregate == "unknown" else "low")
    return ImpactAssessment(
        financial_impact="high" if "financial" in domains else "none",
        privacy_impact="high" if "privacy" in domains else "none",
        security_impact="high" if {"security", "identity"} & domains else "none",
        legal_impact="medium" if "legal" in domains else "none",
        reputational_impact="medium" if "reputation" in domains else "none",
        data_integrity_impact=level if "data_integrity" in domains else "none",
        operational_impact=level if {"infrastructure", "physical_world"} & domains else "none",
        aggregate_risk=aggregate,
        confidence=None,  # deterministic rules provide no confidence estimate
        rationale=rationale or ["Low local reversible action with bounded impact."],
    )
