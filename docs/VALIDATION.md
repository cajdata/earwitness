# Validation

Results of the acceptance checks, synthetic first (M2, M4) and real recordings later (M6).
Every number here comes from a run that can be repeated with the command shown.

## M4: aircraft Doppler pipeline (synthetic recordings on real flights)

Acceptance criterion (HANDOFF M4): synthetic two-event cases recover the truth within 200 m
ranked first in at least 8 of 10; single-event known-time cases do so in at least 6 of 10.
Result: two-event 10 of 10, single-event 10 of 10.

Harness: `scripts/m4_acceptance.py`. Each case places a site 150 to 700 m beside a real
propeller flight from the cached adsb.lol day 2026-09-08 (the reference recording's date) in
the region `-100,30,-85,42` (Texas to the Great Lakes, 53,576 legs, 30,533 admitted as piston,
turboprop or unknown), starts a 20 minute clip so that the flight passes 2 to 18 minutes in,
and renders every admitted flight that comes within 4.2 km of the site at any time in the clip
on its real 1 s track, for the stretch its samples cover: exact Doppler curves with six
harmonics and 1/r loudness at a random fundamental of 80 to 130 Hz, mixed with pink noise at
-15 dB SNR. The measured events are the truth widened the way a careful user would set
sliders: heard closest approach plus or minus 3 s, fundamental plus or minus 3 Hz, speed plus
or minus 15%, distance plus or minus 50%, three harmonics reported, and silence windows
wherever no rendered source was within earshot for at least 30 s (track-based, in arrival
time). Two-event mode pairs the two closest flyovers whose closest approaches lie at least
60 s inside the clip and at least 90 s apart; single-event mode takes the closest one and the
true start time with a 1 s tolerance. The pipeline then searches the whole region and the
whole day with default settings (hard silence filter at 4 km, flat ground), hiding the truth.

Scores per case: distance of the rank-1 answer from the site; distance from the site's mirror
image across the event flight's ground path (one straight flyover cannot tell the two apart);
whether the right flights were paired; the number of candidate cells before and after the
silence filter; how many cells of the true flight within 300 m of the site survived the
funnel; the margin, which is the rank-1 score minus the best other flight pair's score in dB
of whitened brightness; and runtime on one laptop core while other runs shared the machine.

### Two-event mode: 10 of 10

```bash
uv run python scripts/m4_acceptance.py --date 2026-09-08 --bbox=-100,30,-85,42 --cases 10 --seed 1 --mode two --out runs/m4-two
```

| case | sources rendered | event flights | rank-1 error (m) | mirror (m) | pair correct | rank within 200 m | candidates | after silence | truth cells kept | margin (dB) | runtime (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 10 | a742ad P208 + a1e67a P208 | 19 | 452 | yes | 1 | 38,152 | 8,265 | 10 | 5.8 | 449 |
| 2 | 11 | a35682 RV10 + aca944 (no type) | 16 | 250 | yes | 1 | 18,690 | 2,672 | 7 | 4.7 | 208 |
| 3 | 6 | a27abf S22T, twice | 14 | 100 | yes | 1 | 33,137 | 2,383 | 7 | 6.9 | 224 |
| 4 | 10 | a22079 C172 + a789f6 C172 | 11 | 715 | yes | 1 | 80,146 | 23,286 | 20 | 3.4 | 974 |
| 5 | 2 | a9e5c3 C172 + a2a436 P28A | 23 | 307 | yes | 1 | 58,589 | 5,323 | 28 | 3.9 | 666 |
| 6 | 4 | a208cf BE33 + a1fb42 C172 | 17 | 1,641 | yes | 1 | 25,560 | 7,094 | 20 | 4.1 | 729 |
| 7 | 3 | ac3f13 PA46 + a9279f P28A | 12 | 299 | yes | 1 | 113,098 | 16,776 | 16 | 8.0 | 2,351 |
| 8 | 4 | a1cbc9 C182, twice | 10 | 608 | yes | 1 | 97,282 | 9,237 | 11 | 6.8 | 711 |
| 9 | 5 | a9e92e C172 + ad1cfe DA40 | 16 | 76 | yes | 1 | 137,031 | 20,118 | 17 | 2.1 | 1,862 |
| 10 | 7 | a1a78b C172 + a3ad2f C172 | 13 | 1,211 | yes | 1 | 17,637 | 2,036 | 17 | 3.6 | 925 |

Rank 1 within 200 m in 10 of 10, every answer within 25 m of the site and every pair
correct. The margin over the best other flight pair runs from 2.1 dB (case 9) to 8.0 dB
(case 7). The ten cases took 2 h 33 min together, 3.5 to 39 minutes each.

The same ten recordings scored with a plain mean along the curve (every frame the track covers
weighted equally, frames it does not cover skipped) and a single 100 m refinement reached 6 of
10:

| case | plain mean: rank-1 error (m) | plain mean: rank | final scoring: rank-1 error (m) |
| --- | --- | --- | --- |
| 1 | 962,378 | 3 | 19 |
| 2 | 1,474,291 | 2 | 16 |
| 3 | 38 | 1 | 14 |
| 4 | 775,502 | 5 | 11 |
| 5 | 77 | 1 | 23 |
| 6 | 42 | 1 | 17 |
| 7 | 12 | 1 | 12 |
| 8 | 64 | 1 | 10 |
| 9 | 90,459 | 2 | 16 |
| 10 | 43 | 1 | 13 |

Three of the four plain-mean winners were one aircraft paired with itself: short circuit legs
at an airport hundreds of kilometres away whose track covered only a few seconds of each
scoring window, so the leg was judged on its few brightest frames, and in a recording with ten
rendered sources some tone is always bright somewhere. The truth, meanwhile, was diluted over
faint far frames where a wrong curve wanders across whatever else is playing. Two changes to
the score fixed this (docs/METHOD.md, Scoring): frames are weighted by (closest slant / slant),
so the loud, steep stretch around closest approach decides, and a frame the track does not
cover counts as zero brightness with full weight, so a leg explains a flyover only if it covers
it. A second, 25 m refinement stage around the best 100 m cell took the typical error from
about 40 m to about 15 m.

Defects found on the way, all fixed before the runs above:

- Track extrapolation. `Track` continued its cubic spline beyond the sampled span, which a few
  minutes out placed the aircraft at absurd positions and frequencies. The synthesizer then
  found no harmonic under the Nyquist frequency and silently rendered nothing for any source
  whose leg began inside the clip far from the site, so two cases had an event sitting in
  noise. The track now continues in a straight line at its end velocity, the simulator renders
  only sound whose emission the track covers, and the scorer treats frames the track does not
  cover as unexplained.
- Pair hint cap. The pair prefilter returned its first 20,000 hints in index order, and two
  cases' true pairs fell beyond that. The cap is now 100,000, the run log says when it is
  reached, and the tube step computes closest approach over the track segment near each hint,
  which paid for the extra hints.
- Harness silence. The first single-event run scored 4 of 10 because the harness rendered only
  flights whose closest approach fell inside the clip and marked silence from a straight-line
  formula, so circling aircraft and aircraft closing in from just outside the clip were within
  4 km during "silence" and the hard filter dropped the true site. The harness now renders
  every flight within earshot and marks silence from the real tracks in arrival time; the
  pipeline's filter was changed from ground distance at emission time to slant range at
  arrival time, and a soft mode (`--silence soft`, 3 dB per offending flight) was added for
  real recordings, where silence marks will never be exact.

### Single-event known-time mode: 10 of 10

```bash
uv run python scripts/m4_acceptance.py --date 2026-09-08 --bbox=-100,30,-85,42 --cases 10 --seed 2 --mode single --out runs/m4-single
```

| case | sources rendered | event flight | rank-1 error (m) | mirror (m) | rank within 200 m | candidates | after silence | truth cells kept | margin (dB) | runtime (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 5 | a36c89 SR20 | 17 | 1,116 | 1 | 1,916 | 681 | 3 | 1.0 | 53 |
| 2 | 1 | aaf094 C172 | 162 | 41 | 1 | 1,748 | 897 | 6 | 3.8 | 54 |
| 3 | 5 | a8db4a RV7 | 3 | 13 | 1 | 3,014 | 1,480 | 7 | 1.7 | 62 |
| 4 | 1 | a27312 S22T | 140 | 129 | 1 | 1,287 | 997 | 7 | 0.5 | 54 |
| 5 | 5 | a7b11f DA40 | 37 | 661 | 1 | 3,597 | 1,345 | 3 | 1.3 | 64 |
| 6 | 2 | a6d579 C172 | 14 | 663 | 1 | 990 | 426 | 6 | 4.3 | 48 |
| 7 | 4 | a0a3ab C140 | 20 | 448 | 1 | 787 | 225 | 4 | 2.7 | 49 |
| 8 | 2 | a2a688 C172 | 20 | 532 | 1 | 5,883 | 2,157 | 5 | 0.8 | 70 |
| 9 | 1 | a579ca C182 | 9 | 1,195 | 1 | 3,777 | 1,808 | 7 | 4.7 | 65 |
| 10 | 16 | ac881e P28A | 18 | 529 | 1 | 1,071 | 1,071 | 4 | 2.5 | 54 |

Rank 1 within 200 m in 10 of 10; the ten cases took 10 min 20 s together. Cases 2 and 4 are
single straight flyovers, and the answer sits between the site and its mirror (162 m from one
and 41 m from the other, then 140 m and 129 m): one straight pass cannot tell the two sides of
the track apart, and both appear in the top list. Three wins (cases 1, 4 and 8) are by less
than 1 dB over a flight elsewhere in the region, so one event with a known start time narrows
the search to a short list rather than a point, and the margin is the number to read. With
the plain mean the same seed scored 8 of 10 (cases 5 and 8 lost to flights 275 and 432 km
away, at rank 2); the frame weighting took it to 9 (case 8 then lost to a circuit leg
1,175 km away) and the coverage rule to 10.

What these runs do not show: the only interference rendered is other propeller flights, so
wind, traffic, speech, reverberation, engine speed changes and recorder clock drift wait for
real recordings in M6; silence windows are exact here, which makes the hard filter safe, while
on real recordings the soft mode should be the default; and the two-event search over a 15 by
12 degree region and a whole day took 3.5 to 40 minutes per case on one core, nearly all of it
in the funnel, where a known start time or a narrower region brings it to about a minute.

## M3: aircraft data, one continental-US day (2026-09-08)

Acceptance criterion (HANDOFF M3): one continental-US day processes end to end, and spot-checked
flights match the adsb.lol replay.

Day: 2026-09-08, the date of colsto's reference recording (HANDOFF section "Testing and
validation"), from adsb.lol release `v2026.09.08-planes-readsb-prod-0`: two tar parts, 3.87 GB.
Region: the continental-US box `-125,24,-66,50`.

```bash
uv run earwitness data adsb --date 2026-09-08 --bbox=-125,24,-66,50 --keep-raw
```

| stage | size | time |
| --- | --- | --- |
| download, two parts | 3.87 GB | 2 min on this connection |
| stream, parse and clip every trace | 47,384 aircraft, 58.9 million points kept | 5 min 19 s |
| Parquet written | 1.48 GB (zstd) | |
| flight preparation | 188,423 airborne legs | about 1 min |

Engine classes of the prepared legs, from the aircraft type designators in the traces:

| class | legs |
| --- | --- |
| piston | 90,585 |
| turboprop | 11,955 |
| jet | 52,637 |
| helicopter | 18,016 |
| other | 110 |
| unknown | 15,120 |

Before the built-in supplement of nineteen designators newer than the dump1090 table (B38M,
A21N, B39M, A20N, E295, C700 and others) the unknown count was 24,995. Of the 15,120 that
remain, 14,407 carry no designator at all: aircraft absent from readsb's database, including
every non-ICAO `~` address. The rest are gliders, balloons and experimental types.

Height sources: 98,857 legs geometric (GNSS, WGS84), 84,358 barometric shifted by the leg's
median geometric-minus-baro offset, 5,208 barometric with no correction available.

Terrain check at known places (Copernicus GLO-30 plus EGM96):

| place | DEM (m) | geoid (m) | ellipsoidal (m) |
| --- | --- | --- | --- |
| Oklahoma City | 364.4 | -27.7 | 336.6 |
| Denver | 1596.5 | -17.0 | 1579.5 |
| Miami Beach | 5.1 | -28.0 | -22.8 |
| Gulf of Mexico (no tile) | 0.0 | -27.1 | -27.1 |

### Spot check

adsb.lol's site does not hold per-aircraft trace files for 2026-09-08 (the files and the
replay heat map both return 404 for that day, while 2025-09-08 and 2026-11-01 are served), so
the replay itself cannot be opened for this date. The release is the archive the replay is
built from, and the reference used instead is adsb.lol's second instance, `readsb-staging-0`,
from the same day's staging release: its traces are extracted by the same streaming parser and
compared by interpolating the cached prod flight at the staging timestamps.

```bash
uv run python scripts/m3_spotcheck.py --date 2026-09-08 --bbox=-125,24,-66,50 --sample 8 --seed 1
```

| aircraft | cached points | staging points | compared | median (m) | p95 (m) | max (m) | type code agrees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| c001eb | 1082 | 1081 | 904 | 0.0 | 1.6 | 5.7 | yes |
| a69e60 | 1289 | 1289 | 1142 | 0.0 | 0.0 | 0.6 | yes |
| 86953e | 1107 | 1107 | 1076 | 0.0 | 1.6 | 2.7 | yes |
| aed4a2 | 380 | 380 | 380 | 0.0 | 0.4 | 0.6 | yes |
| ac6576 | 775 | 775 | 775 | 0.0 | 0.4 | 0.8 | yes |
| a18ff8 | 1079 | 1079 | 1079 | 0.0 | 0.0 | 6.1 | yes |
| ab4c78 | 217 | 217 | 217 | 0.0 | 0.0 | 1.4 | yes |
| a73a1c | 3837 | 3837 | 3501 | 0.0 | 1.2 | 6.8 | yes |

Eight of eight agree. The two instances turn out to carry almost identical traces for these
aircraft (same point counts, sub-meter medians), so this confirms the archive, the streaming
extraction, the clipping and the timestamps rather than receiver independence. The compared
column counts staging points inside the cached leg's time span and off the ground.

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
