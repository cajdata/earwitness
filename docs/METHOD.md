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
- Score modes: `cosine` is the handoff's sliding cosine. `pearson` removes the mean of the
  observed clip and of each model window first, so a continuous rumble shared by many places and
  times earns no credit and only the timing structure counts. Dense storms are where the two
  differ; the M2 acceptance report compares them.

## M3 implementation notes (aircraft data)

- Legs: an aircraft's day is cut at readsb's new-leg flag, at gaps longer than 60 s, and where
  a ground point is followed by an airborne one. Ground points are dropped. Legs with fewer
  than five airborne points are discarded.
- Vertical datum: geometric altitude (GNSS, WGS84 ellipsoid) is used where broadcast. Where only
  barometric altitude exists, it is shifted by the leg's median geometric-minus-barometric
  offset, or kept uncorrected and flagged when the leg never broadcast geometric altitude.
  Ground height for a candidate point is Copernicus GLO-30 (orthometric) plus the EGM96 geoid
  undulation, so aircraft and ground share the ellipsoid.
- Interpolation: positions in local ENU about the candidate are resampled to 1 s with a
  shape-preserving PCHIP interpolant, then wrapped in the M1 `Track` (cubic spline) that the
  Doppler model evaluates; velocity comes from the spline derivative.
- Engine class: the ICAO type designator from the trace is looked up in the dump1090 table
  (L1P piston, L1T turboprop, L2J jet, H.. helicopter). Designators missing from that table
  classify as unknown; the M4 funnel decides how to treat them.

## M4 implementation notes (aircraft funnel and scoring)

- Events: `events.json` stores ranges, not points, for each flyover's heard closest-approach
  time, rest frequency, speed and closest-approach slant distance, plus silence windows and an
  optional known recording start. `events_from_truth` turns a simulator truth into such ranges.
- Frames: all geometry runs in ECEF with absolute Unix times, so no tangent plane has to cover
  the search region. Candidates live on a latitude/longitude lattice with metric spacing whose
  integer cell ids make the two-event spatial join a sorted-array intersection.
- Funnel order: speed gate on the indexed samples; pair prefilter in (x, y, time shifted by the
  gap) so a ball query finds flight pairs that meet in space in the right time order; distance
  tubes laid across each flight at the lattice spacing between the lateral offsets the distance
  range allows for the aircraft's height above ground, checked with the exact closest approach
  of the 1 s track; timing join on cell ids with the gap tolerance; silence filter counting
  other admitted flights within 4 km during the silent windows, never the candidate's own event
  flights. Single-event mode replaces the pair with the known time plus tolerance.
- Scoring: the received frequency is f0 times a geometry-only ratio c / (c + v_r), computed once
  per candidate and flight on a 0.1 s grid; every (time shift, f0) pair in the sweep is a
  resampling. The score is the mean whitened brightness (dB above the per-frequency median)
  along the curve over a window around the measured closest approach. Event 1 sweeps time
  and f0, which fixes the recording start; event 2 sweeps f0 only. A traced curve, when given
  in single-event mode, is scored by minus the RMS frequency error instead.
- Refinement: the best cell of each of the top flight pairs is re-scored on a 100 m grid
  within 300 m; the heat map scores 100 m cells within 1.5 km of the best pairs.
- Mirror ambiguity: one straight flyover is symmetric across its ground path, so a single
  event with a known time yields the site and its reflection with equal scores. Track
  curvature, terrain, other aircraft in the silence windows or the region prior break the tie;
  a second event always does.
