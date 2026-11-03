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
