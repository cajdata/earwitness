# Validation

Results of the acceptance checks, synthetic first (M2, M4) and real recordings later (M6).
Every number here comes from a run that can be repeated with the command shown.

## M2: thunder pipeline on real GLM storm data (synthetic recordings)

Data: GOES-19 GLM flashes for 2025-05-16, 21:00 to 24:00 UTC, over the central and eastern
United States (region `-97,33,-82,42`, about 1,300 by 1,000 km), the evening of a severe
weather outbreak with 50,000 flashes inside the region in those three hours. Ten land sites were
drawn at random where at least 12 flashes fell within 25 km during some 30-minute window; each
site's busiest window became a 30-minute synthetic recording built from the real flashes
(pink noise at 10 dB SNR, 8 kHz). The search was given the whole region and the whole three-hour
range, a 5 km coarse grid, land mask, default constants, and 20 regions to refine.

Acceptance criterion (HANDOFF M2): the true location ranks first and within 5 km in at least 8 of
10 cases.

```bash
uv run python scripts/m2_acceptance.py --date 2025-05-16 --hours 21-23 --bbox=-97,33,-82,42 --cases 10 --seed 1 --out runs/m2 --score cosine
```

### Cosine score (the handoff's scoring, default): 9 of 10

| case | strikes in window | rank-1 error (m) | offset error (s) | score | runner-up | rank within 5 km | candidates | runtime (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 69 | 5889 | -15.4 | 0.977 | 0.975 | 2 | 12091 | 16 |
| 2 | 316 | 1007 | -1.1 | 0.989 | 0.909 | 1 | 12938 | 16 |
| 3 | 160 | 2196 | -4.8 | 0.992 | 0.872 | 1 | 11572 | 17 |
| 4 | 37 | 103 | +0.3 | 0.990 | 0.840 | 1 | 12777 | 15 |
| 5 | 804 | 188 | +0.7 | 0.988 | 0.953 | 1 | 12777 | 16 |
| 6 | 29 | 1875 | -3.1 | 0.986 | 0.879 | 1 | 11868 | 16 |
| 7 | 31 | 2279 | -4.2 | 0.987 | 0.915 | 1 | 12091 | 16 |
| 8 | 318 | 1445 | -2.5 | 0.958 | 0.873 | 1 | 12221 | 16 |
| 9 | 66 | 92 | +1.0 | 0.989 | 0.766 | 1 | 11491 | 16 |
| 10 | 46 | 243 | -0.2 | 0.995 | 0.787 | 1 | 12491 | 13 |

Median rank-1 error 1.2 km. Case 1 is a ridge: its busiest window (23:52 UTC, 275 strikes
within 25 km, nearly all to one side) puts the rank-1 answer 5.9 km along the storm bearing with
a 15 s start-time error, and the region holding the truth ranks second at 0.975 against 0.977.
The runner-up column is the best other region at least 15 km away. Runtime is the whole
pipeline per case on this desktop; the coarse search of about 12,500 candidates over 2,160
offsets takes about one second of it.

### Pearson score (window mean removed): 10 of 10

Same cases with `--score pearson`. Margins over the runner-up are larger where strikes are
sparse (cases 4, 6, 9, 10) and smaller where they are dense (cases 3, 5); it resolves the case 1
ridge to 1.4 km. Across both versions of the harness it has scored 20 of 20 to cosine's 19 of 20.
Cosine stays the default per the handoff until M6 shows which one holds up on real recordings.

| case | strikes in window | rank-1 error (m) | offset error (s) | score | runner-up | rank within 5 km |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 69 | 1403 | -1.4 | 0.951 | 0.944 | 1 |
| 2 | 316 | 879 | -1.1 | 0.977 | 0.811 | 1 |
| 3 | 160 | 2196 | -4.8 | 0.978 | 0.924 | 1 |
| 4 | 37 | 103 | +0.3 | 0.987 | 0.785 | 1 |
| 5 | 804 | 1484 | +0.7 | 0.969 | 0.931 | 1 |
| 6 | 29 | 1244 | -2.1 | 0.968 | 0.698 | 1 |
| 7 | 31 | 2279 | -4.2 | 0.961 | 0.913 | 1 |
| 8 | 318 | 1445 | -2.5 | 0.953 | 0.777 | 1 |
| 9 | 66 | 92 | +1.0 | 0.979 | 0.501 | 1 |
| 10 | 46 | 242 | -0.2 | 0.994 | 0.721 | 1 |

### Far rumble the model does not render: 7 of 10

A first version of the harness synthesized each recording from every flash within 50 km while
the model only renders flashes within 25 km (`R_max`). In dense storms the hundreds of faint
far flashes add up to a rumble the model never predicts, the exact truth then scores only 0.75
to 0.90, and three cases went to impostors hundreds of kilometers away with scores of 0.94 to
0.96 (cases 4, 6 and 8). This table comes from that first harness version, whose clip windows
were clamped to end inside the time range; `--synth-radius-km 50` reproduces the effect:

| case | rank-1 error (m) | score | runner-up | rank within 5 km |
| --- | --- | --- | --- | --- |
| 1 | 2855 | 0.992 | 0.983 | 1 |
| 2 | 1442 | 0.983 | 0.898 | 1 |
| 3 | 3872 | 0.984 | 0.862 | 1 |
| 4 | 342518 | 0.941 | 0.938 | none |
| 5 | 188 | 0.988 | 0.953 | 1 |
| 6 | 532414 | 0.957 | 0.955 | none |
| 7 | 1452 | 0.998 | 0.949 | 1 |
| 8 | 342629 | 0.937 | 0.932 | none |
| 9 | 3678 | 0.956 | 0.846 | 1 |
| 10 | 243 | 0.995 | 0.785 | 1 |

What this says about the real world: the audible radius is a first-order constant. If distant
thunder is audible at a site, the model must render it (raise `--rmax-km`), or the recording
must be treated as a dense-storm case where the fingerprint is weak. M6 should measure the
effective radius on real recordings before `R_max` is trusted.

### Bearing-diversity weighting (off by default): 7 of 10

Same ten sites with `--bearing-weight 0.5`, which boosts strikes in sparsely populated bearing
sectors around each candidate. On synthetic cases this can only hurt, because the recording is
rendered from the unweighted model, and it did: in cases 5 and 10 the truth's own score fell
(0.957 and 0.794) below distant impostors. The option exists for the ridge geometry of real
single-cell storms (handoff video 2) and should be judged in M6 on real recordings, not here.

| case | strikes in window | rank-1 error (m) | offset error (s) | score | runner-up | rank within 5 km |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 69 | 7056 | -15.4 | 0.972 | 0.880 | none |
| 2 | 316 | 387 | -0.1 | 0.985 | 0.916 | 1 |
| 3 | 160 | 3174 | -7.8 | 0.988 | 0.872 | 1 |
| 4 | 37 | 635 | -0.7 | 0.972 | 0.835 | 1 |
| 5 | 804 | 84631 | +9612.7 | 0.957 | 0.945 | none |
| 6 | 29 | 3413 | -6.1 | 0.958 | 0.871 | 1 |
| 7 | 31 | 4293 | -7.2 | 0.977 | 0.934 | 1 |
| 8 | 318 | 1788 | -3.5 | 0.962 | 0.872 | 1 |
| 9 | 66 | 655 | +0.0 | 0.977 | 0.757 | 1 |
| 10 | 46 | 683839 | +9062.8 | 0.794 | 0.777 | none |

### Continental-US, one full day: 114 s

Runtime criterion (HANDOFF M2): a one-day continental-US search finishes in under 30 minutes on
an 8-core desktop. One synthetic case (30 minutes, 29 real flashes within 25 km, site near
45.49 N 73.06 W) searched against every GLM flash of 2025-05-16 in the box `-125,24,-66,50`:

```bash
uv run python scripts/m2_acceptance.py --date 2025-05-16 --conus-day --bbox=-125,24,-66,50 --cases 1 --seed 2 --out runs/m2-conus
```

| stage | size | time |
| --- | --- | --- |
| flashes in the box and day | 682,694 | loaded from the Parquet cache |
| grid at 5 km | 600,855 points, 405,567 on land | 9 s including the land mask |
| prefilter, N >= 9 within 25 km | 80,475 candidates | 5 s |
| coarse search, 17,281 offsets at 5 s | 80,475 candidates | 88 s |
| refinement, 20 regions, 1 km then 200 m | | 11 s |
| whole pipeline | | 114 s |

Result: rank 1, 221 m from the truth, clip start within 0.2 s, score 0.997 against 0.900 for
the next region. Downloading the day's GLM files (4,320 files, about 2.6 GB) took 15 minutes on
this connection and is a one-time cost per day of data; the 24 cached Parquet files total 28 MB.

Machine: Intel Core Ultra 7 270K Plus, 31 GB RAM. The pipeline is single-threaded numpy and
scipy, so the core count does not matter yet; an 8-core desktop of the same generation would
take about as long.

### Observations

- Single-direction storms produce a ridge: range along the storm bearing trades against the
  clip start time, so errors of 1 to 6 km come with offset errors of a few seconds in the same
  direction (cases 1, 3 and 7). Two or more cells in different directions pin the point to the
  refinement grid (cases 4, 9 and 10, around 100 to 250 m).
- The prefilter default N = max(3, 0.6 x peak count) is weak for continuous rumbles, where the
  envelope has few distinct peaks; it leaves 11,000 to 13,500 of 52,000 grid points. That is
  fine for runtime but means the strike-count prefilter is not doing much on stormy evenings.
- Scores of 0.93 to 0.96 appear at wrong places and times whenever the storm is dense, because
  smooth positive envelopes always correlate. The margin over the runner-up, not the absolute
  score, is the confidence signal to surface in the UI.
