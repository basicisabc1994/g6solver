# SPDX-License-Identifier: Apache-2.0
"""The jobs family's shared duty-of-care code: one crisis trigger for the whole library, a dated and sourced helpline
directory, and the scoring instruments as data under one scorer.

``_``-prefixed, so it never registers as a component; every jobs member may import it. The registered door is
``g6.jobs.duty_of_care``. Modules:

* :mod:`~g6.jobs._care.crisis`: ``scan`` (every field, before anything else), ``crisis_result`` and ``CrisisLock``;
* :mod:`~g6.jobs._care.directory`: ``lookup`` by region, with each number's source, retrieval date and age;
* :mod:`~g6.jobs._care.instruments`: ``catalogue`` and ``score`` (PHQ-9 and GAD-7; the rest refused until their
  licence is settled);
* :mod:`~g6.jobs._care.sources`: the source register and the rule table;
* :mod:`~g6.jobs._care.envelope`: the stable codes and the labelled result.

Everything tops out at ``qualified-draft``; a crisis finding, a missing input and a refused instrument are
``blocked-escalated``. No qualified person has reviewed it (DECISIONS #39).
"""
from g6.jobs._care.crisis import CrisisFinding, CrisisLock, crisis_result, scan
from g6.jobs._care.directory import lookup
from g6.jobs._care.instruments import catalogue, score

__all__ = ["CrisisFinding", "CrisisLock", "catalogue", "crisis_result", "lookup", "scan", "score"]
