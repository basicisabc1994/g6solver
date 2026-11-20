"""M1b-only construction hook; labelling remains with the funnel caller."""
from __future__ import annotations

from dataclasses import dataclass

from .. import funnel
from .erasure import indexed_erasures


@dataclass(frozen=True)
class Construction:
    variant_id: str
    kind: str
    case: funnel.CaseRecord | None
    reason: str = "ok"


def m1b_variants(case: funnel.CaseRecord) -> list[Construction]:
    """Return multi-target constructions followed by indexed erasures.

    Entries with no CaseRecord retain the reason for their refusal and must not
    be labelled. Equal test sources from different constructions are retained:
    validity counts are by construction kind, not by unique source text.
    """
    constructions = []
    for name in funnel.VARIANTS:
        kind = "reversed_maintainer" if name == "reversed_maintainer" else "operator:" + name
        try:
            built = funnel._variant(case, "multi_target", name)
        except funnel._Stop as exc:
            constructions.append(Construction(name, kind, None, exc.reason))
        except ValueError:
            constructions.append(Construction(name, kind, None, "case_record_invalid"))
        else:
            constructions.append(Construction(name, kind, built))
    for variant in indexed_erasures(case.before_tests, case.after_tests):
        try:
            built = funnel.CaseRecord.model_validate({
                **case.model_dump(), "before_tests": case.after_tests, "after_tests": variant.after_tests,
            })
        except ValueError:
            constructions.append(Construction(variant.variant_id, variant.kind, None, "case_record_invalid"))
        else:
            constructions.append(Construction(variant.variant_id, variant.kind, built))
    return constructions
