# Method

The method specification lives in [HANDOFF.md](HANDOFF.md), sections "Method spec: thunder" and
"Method spec: aircraft Doppler". This file becomes the maintained version once M2 (thunder) and
M4 (aircraft) have tuned the constants marked "per video" against synthetic and real cases.

Implementation notes that differ from or sharpen the handoff:

- Thunder loudness is parametrized as `A = (r_near / max(r, r_near))^p`. This equals the
  handoff's `min(A_cap, (R_0 / R)^p)` with `R_0 = r_near` and `A_cap = 1`. Because the score is
  cosine similarity, only the near-field radius matters, not the absolute scale.
- The thunder pulse kernel is `k(t) = (1 - exp(-t / rise)) * exp(-t / decay)` for `t >= 0`,
  normalized to a peak of 1. The decay grows linearly with distance (`decay_per_km_s`) so distant
  thunder rumbles longer. The synthesizer and the forward model share one config object so they
  agree by construction; M2 tuning may deliberately break that agreement.
- The emission-time solver is the fixed-point iteration from the handoff, started from
  `t_e = t_r - |p(t_r) - o| / c`. Its contraction factor is `|v_r| / c`, about 0.3 for a
  propeller aircraft, so five iterations leave a timing residual of order 10 ms at 5 km range
  and far less near closest approach. Convergence is checked in frequency, which is what gets
  scored, not in time. See `tests/test_physics.py`.
- A closed-form solution for the straight-line constant-velocity flyover
  (`physics.straight_line_doppler`) exists and is used to validate the iterative solver.

## M2 implementation notes (thunder search)

- Observed envelope: the M1 extraction, then subtraction of the 10th-percentile noise floor so
  quiet stretches score as silence, then resampling to the sweep step. A heuristic wind flag
  (sustained frames where 20 to 60 Hz dominates 60 to 250 Hz) is reported and can mask frames.
- Candidate grid: rows and columns at the requested spacing using the WGS84 radii of curvature,
  clipped to a GeoJSON polygon when given, then to Natural Earth land unless disabled.
- Prefilter: a point survives when at least N strikes within 25 km fall inside some window of
  clip length, with N = max(3, round(0.6 x observed peak count)); peaks are prominences above
  20% of the envelope maximum at least 10 s apart.
- Scoring: the model envelope of each candidate is rendered once over the whole offset range
  plus the clip length; one real FFT per batch of 256 candidates gives the cross-correlation
  with the observed envelope at every offset, and a cumulative sum of the squared model gives
  the per-window energy. The score is the cosine, with the best three offsets kept.
- Refinement: the top regions (greedy, at least 15 km apart) are re-searched on a 1 km grid
  within 5 km with offsets within 60 s at 1 s, then on a 200 m grid within 1 km with offsets
  within 10 s.
- Bearing-diversity weighting (off by default): strikes are binned into eight bearing sectors
  around the candidate and weighted by (mean count / sector count)^strength, so an isolated
  direction counts more than a crowded one.
- Ridge ambiguity: when every strike lies in one direction, moving the candidate along that
  bearing shifts all arrivals together and the start-time offset absorbs it, so the score has a
  ridge through the truth rather than a peak. Synthetic single-cell cases land within a few
  kilometers on that ridge; two cells in different directions pin the point to the 200 m grid.
