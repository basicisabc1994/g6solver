# SPDX-License-Identifier: Apache-2.0
"""A Prolog refusal for a missing SWI-Prolog is the agent's to read, so its reason and evidence name no setup setting
(05718f35's rule, as Lean's refusal already reads): "a setting named in G6's documentation" stands in its place. The
install action, the person's steps, keeps the setting's name, as Lean's ``INSTALL_LEAN`` does."""
from pathlib import Path

import pytest

from g6.prolog import check_program, prove, query, swipl_version
from g6.prolog.envelope import SWIPL_ENV, SWIPL_MISSING

FAMILY = "parent(tom, bob).\n"


@pytest.mark.parametrize("run", [lambda: query(FAMILY, "parent(tom, X)"), lambda: prove(FAMILY, "parent(tom, bob)"),
                                 lambda: check_program(FAMILY), lambda: swipl_version()])
@pytest.mark.parametrize("named", [True, False])
def test_a_missing_swipl_refusal_names_no_setting_to_the_agent(run, named, tmp_path: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    if named:
        monkeypatch.setenv(SWIPL_ENV, str(tmp_path / "no-swipl-here"))
    else:
        monkeypatch.delenv(SWIPL_ENV, raising=False)
        monkeypatch.setenv("PATH", str(tmp_path))
    result = run()
    assert result.code == SWIPL_MISSING, (result.code, result.reason)
    assert SWIPL_ENV not in result.reason and "setting named in G6's documentation" in result.reason
    assert all(SWIPL_ENV not in str(item.get("reason", "")) for item in result.evidence), result.evidence
    assert SWIPL_ENV in result.warning_card["suggested_action"]      # the person's install steps keep the name
