# SPDX-License-Identifier: Apache-2.0
"""Compatibility facade for the pure lessons seam in g6.core.lessons.

The self-model reuses these exact objects without importing meta-harness
storage or execution. The algorithms and public names are unchanged.
"""
from g6.core.lessons import (
    ALPHA as ALPHA,
    Calibration as Calibration,
    FAILED as FAILED,
    HELD as HELD,
    Lesson as Lesson,
    OPEN as OPEN,
    Prediction as Prediction,
    ROOT_CAUSES as ROOT_CAUSES,
    Reading as Reading,
    calibrate as calibrate,
    gap as gap,
    hit_rate as hit_rate,
    repeats as repeats,
    score as score,
    stuck as stuck,
    trusted as trusted,
)

__all__ = ['FAILED', 'HELD', 'OPEN', 'ROOT_CAUSES', 'Calibration', 'Lesson', 'Prediction', 'Reading', 'calibrate', 'gap', 'hit_rate', 'repeats', 'score', 'stuck', 'trusted']
