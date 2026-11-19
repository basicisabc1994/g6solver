# SPDX-License-Identifier: Apache-2.0
"""Source terms transcribed from the owner's study design; no remote lookup."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LicenceRow:
    source: str
    licence_id: str
    licence: str
    url: str
    train_ok: bool
    eval_ok: bool
    notes: str
    date_checked: str = "2026-11-18"
    redistribute: bool = False

    def __post_init__(self) -> None:
        if any(type(v) is not bool for v in (self.train_ok, self.eval_ok, self.redistribute)):
            from g6.rsi.records import TaskError

            raise TaskError("G6_E_RSI_LICENCE_REFUSED")


ROWS = (
    LicenceRow("mbpp", "mbpp-cc-by-4", "CC-BY-4.0", "https://github.com/google-research/google-research/tree/master/mbpp",
               False, True, "MBPP underlying data terms. Retention only in this study; EvalPlus additions are Apache-2.0."),
    LicenceRow("apps", "apps-mit", "MIT", "https://huggingface.co/datasets/codeparrot/apps", True, True,
               "Only the train split may train. Public tests need later augmentation; older data may be contaminated."),
    LicenceRow("codecontests", "codecontests-apache-cc-by", "Apache-2.0 AND CC-BY-4.0",
               "https://github.com/google-deepmind/code_contests", True, True,
               "Only train may train. Parts retain their own terms; attribution and upstream restrictions still apply."),
    LicenceRow("livecodebench", "livecodebench-eval-only", "MIT-code; data-cc-unspecified",
               "https://github.com/LiveCodeBench/LiveCodeBench", False, True,
               "Owner permits evaluation only, aggregate scores only, no redistribution. Data card says cc; "
               "LeetCode terms restrict scraping. Held-out dates must be after September 2024."),
    LicenceRow("generated", "generated-local", "Repository terms retained", "local:generated", True, True,
               "Local bug-injection tasks only. Family metadata must retain repository attribution and permissive "
               "licence terms. Training repositories and held-out repositories stay separate."),
)


def licence_for(source: str) -> LicenceRow:
    """Return the fixed source policy; callers cannot grant extra uses."""
    for row in ROWS:
        if source == row.source:
            return row
    from g6.rsi.records import TaskError

    raise TaskError("G6_E_RSI_SOURCE_UNKNOWN")
