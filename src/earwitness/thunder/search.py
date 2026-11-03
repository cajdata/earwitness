"""Thunder search: scoring observed against modeled envelopes.

M1 ships the plain cosine similarity used by the tests. M2 adds the sliding version over all time
offsets (FFT cross-correlation for the numerator, cumulative sum of m^2 for the denominator), the
coarse grid with the strike-count prefilter, and the refinement passes.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike


def cosine_similarity(observed: ArrayLike, modeled: ArrayLike) -> float:
    """Cosine of the angle between two envelopes of equal length; 0 if either is all zero."""
    o = np.asarray(observed, dtype=float)
    m = np.asarray(modeled, dtype=float)
    if o.shape != m.shape:
        raise ValueError(f"shape mismatch: {o.shape} vs {m.shape}")
    denom = np.linalg.norm(o) * np.linalg.norm(m)
    if denom == 0.0:
        return 0.0
    return float(np.dot(o, m) / denom)
