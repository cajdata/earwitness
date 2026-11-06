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
  within 10 s, both kept inside the time prior's clip-start window (`refine(...,
  offset_bounds)`): a camera clock makes that window seconds wide, and until M6's third pass the
  refinement searched a minute either side of it.
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
  gap) so a ball query finds flight pairs that meet in space in the right time order, keeping
  one hint per pair per meeting place (two aircraft working the same airport meet many times,
  and the tubes are only laid near each hint, so a single hint per pair would search the wrong
  place, and stopping at 100000 hints in flight-index order, which the run log reports when it
  happens); distance tubes laid across each flight at the lattice spacing between the lateral
  offsets the distance range allows for the aircraft's height above ground, each cell checked
  with the exact closest approach of the 1 s track on the pass that laid it (in M4, the track's
  nearest sample over the segment near the hint or inside the known-time window, which for
  pattern traffic answered with whichever circuit passed nearest; see the M6 notes); timing
  join on cell ids with the gap tolerance;
  silence filter counting
  other admitted flights whose slant range from the candidate (aircraft height against the
  candidate's ground height) drops under 4 km while the recording was silent, judged at the
  time the sound arrives, never the candidate's own event flights. In hard mode such
  candidates are dropped; in soft mode each offending flight costs 3 dB of score, which is the
  safer default when receiver coverage is thin or the silence marks are rough. Single-event
  mode replaces the pair with the known time plus tolerance.
- Track coverage: outside its sampled span a track continues in a straight line at the end
  sample's velocity; the cubic spline is never extrapolated, because a cubic run a minute past
  the data of a turning aircraft is tens of kilometers off. Reception times whose sound left
  before a leg's first sample or after its last are unexplained frames in the scorer (zero
  brightness, see Scoring), and the simulator renders nothing for them, so the two agree on
  what a leg can be heard to do.
- Scoring: the received frequency is f0 times a geometry-only ratio c / (c + v_r), computed once
  per candidate and flight on a 0.1 s grid; every (time shift, f0) pair in the sweep is a
  resampling. The score is the whitened brightness (dB above the per-frequency median) along
  the curve over a window around the measured closest approach, averaged over frames with
  weights (closest slant / slant): the tone is loudest and its curve steepest near closest
  approach, while in the faint far frames a wrong curve can wander across whatever else is
  playing, which is how a plain mean let far-away pairs outscore the truth by 4 dB in busy
  recordings. Frames whose sound the track does not cover count as zero brightness with their
  full weight, not as missing frames: a leg cut at touchdown a few seconds after closest
  approach would otherwise be scored on its three brightest frames alone, which is exactly
  how circuit legs at distant airports outscored the truth. The frame average is then averaged
  over the harmonics the user reports for the event (or the configured count, whichever is
  larger) with weights 1/n; higher harmonics move n times as far in frequency, so they
  separate curves that the fundamental alone cannot. Event 1 sweeps time and f0, which fixes the recording start;
  event 2 sweeps f0 only. A traced curve, when given in single-event mode, is scored by minus
  the RMS frequency error instead.
- Acceptance harness (`scripts/m4_acceptance.py`): sites are placed a few hundred meters beside
  real adsb.lol flights; every admitted flight that comes within 4.2 km of the site at any time
  in the clip is rendered on its real track for the stretch its samples cover, and silence
  windows are the recording-time gaps
  between the arrival-time spans in which any rendered source was inside that radius. The
  extra 200 m over the filter's 4 km keeps a careful user's silence marks conservative for a
  candidate cell up to half a lattice diagonal from the site. Event flyovers must close in at
  least 60 s from either end of the clip so the scoring window is complete.
- Refinement: the best cell of each of the top flight pairs is re-scored on a 100 m grid
  within 300 m, then on a 25 m grid within 100 m of that best point (a 50 m error already costs
  about a decibel near closest approach, where the ranking is decided); the heat map scores
  100 m cells within 1.5 km of the best pairs.
- Mirror ambiguity: one straight flyover is symmetric across its ground path, so a single
  event with a known time yields the site and its reflection with equal scores. Track
  curvature, terrain, other aircraft in the silence windows or the region prior break the tie;
  a second event always does.

## M5 implementation notes (desktop UI and audit)

- Architecture: a FastAPI app (`server/app.py`) over the engine. Every route is an adapter
  that validates a request and calls the same functions the CLI calls, in the same order
  (`server/runs.py`), so a run started from the UI lands in `runs/` as the same folder a CLI
  run writes and reproduces from it. Analyses run one at a time on a worker thread, and the
  pipelines' progress callable appends to a line buffer the page polls by index
  (`server/jobs.py`), so no connection stays open across a 20 minute search. The server binds
  to 127.0.0.1 on a free port, and every API request must carry the session token the
  launcher puts in the page URL (`server/launch.py`); a page in another tab, or a site using
  DNS rebinding, cannot drive it or read local files through it. pywebview opens the page in a
  native window with a small JavaScript bridge for file dialogs; `--browser` uses the system
  browser instead, with a typed path in place of the dialog.
- Front end (`ui/`): plain TypeScript built with Vite into `server/static/`, which is
  gitignored and shipped in the wheel. MapLibre draws OpenStreetMap raster tiles with
  attribution (the tile URL is a setting, blank for none), a two-click region rectangle, and
  the heat map as score-coloured points with the ranked answers as numbered markers. Plotly
  draws the spectrogram, the thunder envelope with wind-flagged spans shaded, the score
  histogram and the observed-versus-modeled overlays. The server cuts every spectrogram view
  to display size by block maxima, so a thin Doppler line survives the reduction, and
  computes a finer transform when the band is narrow.
- Doppler fit tool: auto-trace follows the line outward from the brightest frame in the box,
  accepting a frame when it is at least 6 dB bright, 2 dB above the box's typical 2 s
  brightness (since M6 the view's 25th-percentile frame, see below), and within 4% in
  frequency of the last accepted frame, and stopping once the
  line has been missing for 1.5 s. Taking every bright frame in the box instead let noise
  outnumber the line and dragged the fit to 22 m/s on the walk-through case. The fit is the
  straight flyover of `core.physics.straight_line_doppler` by bounded least squares with a
  soft-L1 loss (`aircraft/fit.py`). Its `events.json` ranges are each parameter plus or minus
  the largest of three sigma, a floor (3 Hz, 10 m/s, 100 m, 3 s) and a fraction of the value
  (30% of the speed, 50% of the distance): a straight line fitted to a real, curving track is
  biased, these are the widths the M4 acceptance validated, and on the walk-through case the
  fit gave 119.5 Hz, 44 m/s, 603 m and 547.4 s against a truth of 120.1 Hz, 46.9 m/s, 659 m
  and 548.6 s.
- Traced curves: a trace is saved with its event and reported per candidate as the RMS
  distance of the modeled curve from it, over the points the track covers. It no longer
  replaces the brightness score in single-event mode: that path was never exercised by the
  acceptance harness, and it went NaN whenever a trace point fell past the leg's end, which
  dropped the truth to rank 5 in the first UI run. The brightness score is what was validated.
- Audit (`audit/report.py`): four detectors with their evidence next to the verdict. Metadata
  reads container tags through ffprobe when it is installed and flags location and creation
  time tags. Thunder counts bursts of the 20 to 250 Hz envelope (1 s smoothing) above the
  median plus four robust sigmas, 0.75 to 90 s long. Aircraft follows the 55 to 190 Hz ridge,
  keeps stretches that stay above 6 dB in a 2 s mean for at least 10 s, splits them at 12%
  jumps, and counts those that fall by 2 to 60% with a time-frequency correlation below
  -0.6. Mains hum compares Welch lines at 50 and 60 Hz and their harmonics with the median
  power 1.2 to 6 Hz away. These are heuristics tuned on synthetic cases, not validated
  classifiers; the report says so.
- Compute estimate: the run panel scales the acceptance runs' timings (an aircraft two-event
  search over 1.8 million km2 and a day took a median of 12 minutes, a known-time search about
  a minute; the thunder coarse pass is cells times start times at about 2.5 microseconds
  each) to the region and dates, as a guide to narrowing the priors, not a promise.
- Limitations: a run cannot be cancelled (close the app); one run at a time; terrain must be
  off for synthetic cases rendered on flat ground, since real heights move the modeled curves;
  the coarse heat map's colour scale follows the coarse scores, so a thunder map looks
  uniformly warm and the numbered markers carry the answer; the UI requires a region, by the
  settled open decision, while the CLI can search the whole world.

## M6 implementation notes (real recordings)

- Heard crossing: an event's `t0_s` is the recording time at which the received frequency
  crosses f0, when the sound emitted at closest approach arrives. The straight-line fit's own
  t0 is the closest approach, d / c earlier; its exported ranges and speed profile carry the
  heard crossing (`DopplerFit.heard_t0_s`, `heard_valley`), the UI's sliders show the closest
  approach and add d / 343 m/s when saving, and Load subtracts it again. On the reference
  recording's 6 km flyover the difference was 18 s, enough to put the modeled curve 18 Hz off
  the ridge.
- Speed profile (`aircraft/fit.py`, `profile_speed`): the fit holds the speed at 28 values from
  15 to 300 m/s and refits f0, d and t0 at each; every row within 0.5 Hz rms of the best fit is
  part of the event's `valley`. A trace without the curve's wings fixes v^2 / d and little else
  (the reference's faint flyover fitted 60 to 230 m/s equally well while the Jacobian sigma said
  plus or minus 2.7 m/s), so the ranges span the whole profile and `FlyoverEvent.at_speed`
  narrows distance, fundamental and heard crossing to a flight's own speed: the funnel lays and
  checks tubes with it and requires both events to imply one recording start, and the scorer
  narrows f0 and t0 with the speed on the pass heard during the event (the closest approach
  among track times whose sound arrives inside the event's window, so pattern traffic that
  passes a point twice is narrowed with the right pass).
- Pair prefilter: each indexed sample implies a recording start for each event, were the
  closest approach at that sample: its time plus the event's distance at the sample's speed
  over c, minus the event's heard crossing at that speed (`funnel.sample_starts`). The two
  events' samples are matched in (x, y, implied start) with a time slack of the events'
  narrowed widths, the timing tolerance and one index step, about 20 to 45 s, where the M4
  prefilter matched raw times shifted by the gap with a slack that had to cover a sample
  anywhere within the reach of closest approach, the reach over the slowest speed (about 1000 s
  with honest speed ranges). The old mode stays as `FunnelConfig(prefilter="gap")`.
- Narrow tube and exact check (`funnel.pair_cells`): per hint only the event with the narrower
  distance band at its flight's speed is laid on the lattice, and its cells are checked against
  the other flight's closest approach on the pass heard when each cell's implied start says it
  must have been, so the wide tube of a distant fast aircraft is never laid; hints run in
  spatial order, the 1 s track cache is bounded, and the prefilter queries run in chunks with
  array reduction.
- Passes: an aircraft in a traffic pattern passes the same place every few minutes, on the near
  leg, the far leg and the turns, and only one of those passes was heard. The funnel took each
  cell's closest approach at the track's nearest sample, so a cell kept only the nearest pass:
  an event heard on the far leg failed the distance band at the near leg's distance, and one
  heard on a later circuit took the timing of whichever circuit flew nearest. Each laid cell now
  walks along the track from the sample that laid it to the closest approach of that pass
  (`funnel.descend_to_pass`), one row per cell and pass, and the other flight's pass is the
  nearest local minimum of distance among the samples whose sound arrives inside the window the
  narrow event's start allows (`funnel.heard_pass`); single-event mode uses the walk inside the
  known-time window. Both move or search four samples at a time before the 1 s polish
  (`funnel.PASS_STRIDE`): two passes of one point lie tens of seconds apart at the least, while
  ADS-B jitter dents the distance with minima a few seconds apart, which a walk one sample at a
  time stops at and a search of every sample takes for a pass. `cpa_to_points` (nearest sample
  overall) stays for the scripts' truth checks.
- Duplicates: a hint lays its tube within three reaches of its meeting place, wider than the
  coarse cells that tell meeting places apart, so the hints of one pair overlap and find the
  same cells again; about half of every two-event search's rows were such repeats, each scored
  and silence-checked anew. The funnel now returns one row per cell, flight pair and recording
  start to the second (`funnel.unique_hypotheses`); rows of one cell and pair with different
  starts, pattern traffic heard on another circuit, stay. The silence filter queries its
  neighbours in blocks of 2,000 candidates (`funnel.SILENCE_CHUNK`): the hit lists of every
  candidate at once reached tens of gigabytes beside busy airports.
- Clock: a known start bounds every flyover the clip can hold, so the pipeline admits only
  flights airborne within the clip, give or take the clock tolerance and two minutes
  (`pipeline.flight_window`), before the funnel; with two events it also keeps only candidates
  whose implied start sits within the clock tolerance, the timing tolerance and event 1's width.
- Auto-trace (`audio.spectrogram.trace_line`, used by the UI's ridge route and
  `scripts/m6_measure.py`): the noise level a frame's 2 s average must clear by 2 dB is the
  25th-percentile frame of the view. It was the median of the view's bright frames and every dim
  frame of the whole recording, which made the same view trace differently in a clip and in the
  full file, and a loud line filling most of the view raised it to the line itself, so a 60 s
  view of the reference's loud flyover kept 13 of its 310 trace points.
- Scorer window: each event is scored over a fixed 60 s either side of its heard crossing.
  Scoring only the traced span (`ScoreConfig.trace_window`) is an option, off by default after
  the evaluation in docs/VALIDATION.md (M6): it lifted a faint event's own score but lost the
  reference case, because the scorer integrates along the predicted curve and picks up line too
  faint to trace, and a trace can stop short of the heard crossing.
- Scoring speed: brightness lookups use a frame-major copy of the whitened spectrogram (the f0
  options at one frame sit side by side in memory), about 18% faster with identical scores, and
  `AircraftPipelineConfig.workers` (`--workers`) scores candidates in a process pool, a chunk of
  rows per task with only the flights it needs, identical to the serial scores. Sweeping all
  start-time options at once in numpy was three times slower: the arrays leave the cache.
