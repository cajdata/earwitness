# Validation

Results of the acceptance checks, synthetic first (M2, M4, M5) and real recordings last (M6).
Every number here comes from a run that can be repeated with the command shown.

## M6: real-world recordings

Acceptance criterion (HANDOFF M6): at least one real aircraft case and one real thunder case
documented, failures included, with error distance, rank and runtime, from personal recordings
made under the section 8 protocol and from colsto's published test recording.

How a case runs: the recording is analysed the way a user runs it, with `earwitness analyze` or
the UI, with no truth in sight. The true site, and the clock reading if one was taken, live in
a private file under `data/` (gitignored), such as `data/m6/<case>/truth.json` with `lat`,
`lon` and optionally `start_utc` and `notes`. `scripts/m6_score.py --run <run folder> --truth
<truth.json>` then scores the run folder: distance of the rank-1 answer from the site, its score
and margin over the runner-up, the first rank within the tolerance (200 m for aircraft, 5 km for
thunder, the M4 and M2 acceptance widths), the start-time error, and the rank of the best
candidate within the tolerance in the final result stage. The row it prints carries no
coordinates and is pasted here as it is.

Off season, `scripts/m6_storm_watch.py` polls the newest GLM files on AWS and reports where
lightning is right now, around named points (a livestream camera) or in the busiest half-degree
cells of a region, as the trigger the protocol describes for recording public livestreams within
their terms. On 2026-11-04 at 15:09 UTC it saw 213 flashes in the previous 15 minutes between
105 W and 75 W, most of them over south Florida and a few over northwest Georgia, in 14 s.
With `--day` it reads the GLM hour cache for a past date instead and prints flashes per hour
around a point and its busiest half hour, which picks the hour of a webcam archive to pull.

| case | method | rank-1 error (m) | rank within tolerance | score, margin | start error (s) | truth rank in the final stage | counts | runtime (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| colsto, metro region, run 1 | aircraft two-event pair | 41252 | none (200 m) | 15.36, margin 0.06 | -32104.5 | 351304 of 362958 (coarse; nearest 55 m) | flights 3549, pairs 130522, candidates 362958, after_silence 362958 | 5290 |
| colsto, metro region, run 2 (a PC-12, then a King Air 350) | aircraft two-event pair | 173 | 1 (200 m) | 19.88, margin 1.61 | +0.4 | 1 of 355189 (coarse; nearest 55 m) | flights 3549, pairs 131274, candidates 355189, after_silence 355189 | 4364 |
| personal-1, known time +-5 s, metro box | aircraft single-event known-time | 31603 | 5 (200 m) | 16.03, margin 0.01 | +7.9 | 45 of 1918 (coarse; nearest 93 m) | flights 1859, candidates 1918 | 470 |
| personal-1, known time +-2 s, metro box, envelope sweep (bug) | aircraft single-event known-time | 31603 | none (200 m) | 16.03, margin 0.01 | +7.9 | 28 of 1192 (coarse; nearest 93 m) | flights 1859, candidates 1192 | 383 |
| personal-1, known time +-2 s, metro box, corrected sweep | aircraft single-event known-time | 11510 | 5 (200 m) | 16.00, margin 0.02 | -0.5 | 27 of 1192 (coarse; nearest 93 m) | flights 1859, candidates 1192 | 68 |
| personal-1, known time +-2 s, metro box, silence 880 to 1240 s | aircraft single-event known-time | 11510 | 3 (200 m) | 16.00, margin 0.02 | -0.5 | 23 of 1192 (coarse; nearest 93 m) | flights 1859, candidates 1192, 775 with silence hits | 67 |
| personal-1, known time +-5 s, state box | aircraft single-event known-time | 79712 | none (200 m) | 16.20, margin 0.20 | +7.0 | 46 of 9213 (coarse; nearest 79 m) | flights 3300, candidates 9213 | 481 |
| personal-1, two events, metro box, no clock | aircraft two-event pair | 46410 | none (200 m) | 19.75, margin 0.13 | -23826.5 | 4307 of 208887 (coarse; nearest 93 m) | flights 1859, pairs 156212, candidates 208887 | 3268 |
| personal-1, two events (PC-12 re-traced), known time +-2 s, metro box | aircraft two-event pair | 190 | 1 (200 m) | 17.53, margin 12.32 | +0.5 | 4 of 147 (coarse; nearest 93 m) | flights 1859, pairs 166302, candidates 147 after the clock | 1445 |
| colsto, metro region, run 4 (second pass: implied-start prefilter, 12 scoring processes) | aircraft two-event pair | 173 | 1 (200 m) | 19.88, margin 1.61 | +0.4 | 1 of 244711 (coarse; nearest 55 m) | flights 3549, pairs 44480, candidates 244711 | 510 |
| colsto, two events, clock assumed +-2 s, metro box | aircraft two-event pair | 173 | 1 (200 m) | 19.88, margin 14.43 | +0.4 | 1 of 493 (coarse; nearest 55 m) | flights 336, pairs 7695, candidates 493 | 86 |
| personal-1, two events, known time +-2 s, metro box, flights limited to the clip window | aircraft two-event pair | 190 | 1 (200 m) | 17.53, margin 12.32 | +0.5 | 4 of 147 (coarse; nearest 93 m) | flights 92, pairs 5919, candidates 147 | 86 |
| thunder-1, public field recording (30 min, rural night), state-sized box, local date | thunder | 479316 | none (5 km) | 0.842, margin 0.000 | n/a | none of 10 (final; nearest 29928 m) | grid 46057, on_land 44257, prefilter 36089 | 51 |
| thunder-2, public field recording (60 min, front porch), state-sized box, one day | thunder | 73170 | none (5 km) | 0.788, margin 0.031 | n/a | none of 10 (final; nearest 55377 m) | grid 8721, prefilter 4000 | 20 |
| colsto, metro region, run 5 (third pass: every pass, repeats dropped, 12 scoring processes) | aircraft two-event pair | 173 | 1 (200 m) | 19.88, margin 1.61 | +0.4 | 1 of 129236 (coarse; nearest 55 m) | flights 3549, pairs 44480, candidates 129236 | 546 |
| personal-1, two events, known time +-2 s, metro box, every pass | aircraft two-event pair | 190 | 1 (200 m) | 17.53, margin 3.33 | +0.5 | 2 of 99 (coarse; nearest 93 m) | flights 92, pairs 2769, candidates 99 | 65 |
| personal-1, two events (PC-12 re-traced), metro box, no clock, previous funnel | aircraft two-event pair | 46385 | none (200 m) | 19.81, margin 0.18 | -23826.5 | 1093 of 256778 (coarse; nearest 93 m) | flights 1859, pairs 68760, candidates 256778 | 1061 |
| personal-1, two events (PC-12 re-traced), metro box, no clock, every pass | aircraft two-event pair | 46281 | none (200 m) | 19.63, margin 0.00 | -19149.4 | 1031 of 173627 (coarse; nearest 93 m) | flights 1859, pairs 68760, candidates 173627 | 849 |
| personal-1, known time +-2 s, metro box, every pass | aircraft single-event known-time | 11510 | 5 (200 m) | 16.00, margin 0.02 | -0.5 | 27 of 1289 (coarse; nearest 93 m) | flights 92, candidates 1289 | 79 |
| thunder-3, public field recording (19 min, city window, rain), state-sized box, a day around the stated start | thunder | 439172 | none (5 km) | 0.837, margin 0.000 | n/a | none of 10 (final; nearest 74639 m) | grid 22327, on_land 20039, prefilter 5242 | 14 |
| thunder-4, public field recording (18 min, stream and road), state-sized box, a day | thunder | 74343 | none (5 km) | 0.746, margin 0.019 | n/a | none of 10 (final; nearest 6513 m) | grid 10790, prefilter 1387 | 6 |
| thunder-5, the same storm 13 min later (13 min, 3 loudness peaks), state-sized box, a day | thunder | 23028 | none (5 km) | 0.936, margin 0.001 | n/a | none of 10 (final; nearest 23028 m) | grid 10790, prefilter 1922 | 6 |
| thunder-6, public field recording (80 min, in a tent), state-sized box, a day | thunder | 142297 | none (5 km) | 0.625, margin 0.023 | n/a | none of 10 (final; nearest 142297 m) | grid 9327, on_land 8271, prefilter 6735 | 24 |
| thunder-7, public field recording (55 min, shoreline park, date in doubt), state-sized box, a day | thunder | 330090 | none (5 km) | 0.760, margin 0.002 | n/a | none of 10 (final; nearest 179481 m) | grid 9327, on_land 8271, prefilter 6993 | 21 |
| thunder-8, public field recording (8 min, city window, hail), province-sized box, a day | thunder | 2172 | 1 (5 km) | 0.928, margin 0.084 | n/a | 1 of 10 (final; nearest 2172 m) | grid 85155, on_land 56689, prefilter 106 | 6 |
| thunder-8, the same clip over western North America, a day | thunder | 2076 | 1 (5 km) | 0.927, margin 0.000 | n/a | 1 of 10 (final; nearest 2076 m) | grid 415872, on_land 255021, prefilter 1414 | 11 |
| thunder-9, a security camera's clip of a strike next door, from a news upload (30 s, one clap, no clock), state-sized box, the reported time +-1 h | thunder | 53178 | none (5 km) | 0.999, margin 0.000 | n/a | none of 10 (final; nearest 18328 m) | grid 10790, prefilter 2141 | 4 |
| thunder-9, the same strike on the same house's doorbell camera (21 s) | thunder | 381207 | none (5 km) | 0.984, margin 0.000 | n/a | none of 10 (final; nearest 41992 m) | grid 10790, prefilter 1786 | 4 |
| thunder-10, a security camera's clip of a storm over a published street, its clock on screen: 25 s of real time, two claps, state-sized box, the clock +-3 s | thunder | 67447 | none (5 km) | 0.986, margin 0.002 | n/a | none of 10 (final; nearest 23069 m) | grid 10790, prefilter 677 | 2 |
| thunder-10, 110 s of the same storm, five claps, its clock gaining 7% on the video, the clock +-3 s | thunder | 32827 | 6 (5 km) | 0.924, margin 0.007 | n/a | 6 of 10 (final; nearest 4310 m) | grid 10790, prefilter 1212 | 3 |
| thunder-10, the same 110 s stretched to the clock's length | thunder | 209215 | none (5 km) | 0.944, margin 0.029 | n/a | none of 10 (final; nearest 7461 m) | grid 10790, prefilter 1227 | 3 |

Run 2 is the reference case solved: the two flyovers measured with the project's own trace
and fit tools, searched blind over a 1.8 by 1.0 degree box around the metropolitan area the
video's last minute reveals, rank the published site first, 173 m off, with a margin of
1.6 over the runner-up, the clip's start within 0.4 s of the clock the ADS-B tracks imply,
and the same two aircraft colsto found; 73 min on one core (prefilter 3 min, funnel 18 min,
scoring 54 min). The start error is measured against a start derived from those tracks
and the fitted closest-approach time, since the video gives no clock. This is the aircraft
half of the acceptance criterion; the thunder half, two public field recordings that were
not located, is in the second pass below. Run 1, the same search a day
earlier in the session, is the failure the bullet on the heard crossing below explains:
the true pair's cells sat 55 m from the site and scored below zero because the search
clock started 18 s late. The continental search colsto himself ran is not reproduced, for
the reasons the bullet on honest ranges gives.

personal-1 is the first personal recording under the section 8 protocol: 21 minutes of
phone video (Samsung Galaxy S25 Ultra, AAC 48 kHz) recorded on 2026-11-04 with the start
noted against an NTP clock, metadata stripped before analysis without reading the
location tag, the truth in a private file read only at scoring. The audit found two
falling sweeps; the strong one, 690 to 760 s, is a two-blade piston single about 1 km off
with a fundamental near 85 Hz and five visible harmonics (fit 69 m/s at 1.4 km, rms 0.44 Hz,
trace without the receding wing, so the profile spans 36 to 240 m/s). The recorder's own
notes, read afterwards, name that aircraft, a Cessna 172, and its minute. The known-time
runs use the phone's own clock (the file's end stamp minus its duration, within 2 s of the
NTP note) and a 1.0 by 0.8 degree box around the metropolitan area; the recorder's general
area was known to the analyst from earlier conversation, so the test is a search within a
declared region, as for the reference case. Four runs tell one story: the correct aircraft
is found and its cell sits 167 m from the site with the clip start within 0.6 s of the
clock, but it ranks 5th (3rd once a silence window is marked) behind other Cessna 172s
airborne over the metro at that minute, all within half a point. The clock fixes where
along each track the flyover happened, not which aircraft it was, and every trainer at
pattern speed about 1 km from some cell traces a curve within a spectrogram bin of the
measured one; one flyover of a common type in a training-dense metro is ambiguous by
construction, which the synthetic M4 cases, with few look-alikes, never showed. The second
run exposed a bug on the way: in known-time mode the scorer swept the clock tolerance plus
the event envelope's width, 31 s for this profiled trace, so a 2 s tolerance changed
nothing and the leader stretched the clock by 6.9 s; the sweep now adds the width at the
flight's own speed, which also made the run ten times faster.

The two-event search without a clock failed (rank 1 a pair six and a half hours earlier,
the true pair 4,307th of 208,887). Scoring the site by hand with the true flights showed
the Cessna's track reproducing its trace to 0.8 Hz rms at 14.6 dB, and the second aircraft,
a PC-12 that passed at 3.5 km and 108 m/s while slowing from 127, scoring below zero: its
line had been traced on a 220 to 275 Hz fragment, and the profile's row at that speed put
the fundamental 10 Hz and the timing 2.6 s off. Re-tracing the same line over 150 to
320 Hz gave 98.5 m/s at 2.9 km (rms 1.0 Hz) and +1.8 dB at the site, still weak because a
line visible for 22 s of the scorer's 120 s window cannot score high, and the site's 16.4
trailed the false leader's 19.8. The pipeline had treated a known time and a second event
as alternatives; it now lets the clock constrain a two-event search as well, keeping only
pairs whose implied start sits within the clock tolerance, the timing tolerance and event
1's width. With that, the re-traced PC-12 and the clock to 2 s, the search keeps 147 of
319,149 candidates and ranks the site first: 190 m off, margin 12.3 over the runner-up,
the start within 0.5 s, the two aircraft the notes name, in 24 min (prefilter 12, funnel
13, scoring 1 s). The state-wide known-time run, the stretch case, loses the site among the
state's trainers as expected. So one strong flyover of a common type is not enough in a
trainer-dense metro, and a faint second aircraft is not enough on its own, but the two
together with the phone's own clock locate a 21 minute phone video to 190 m within a
metro-sized region.

Status of the inputs and the findings on 2026-11-04:

- colsto's reference recording for video 3 is published as an unlisted YouTube video, "Unknown
  location - raw audio" (one hour, uploaded 2026-10-31). The app downloads no media, so the audio
  was fetched by hand with yt-dlp (Opus, 48 kHz, 3600 s) into `data/m6/colsto/`. Video 3 gives
  the recording day (September 8), a 200 m grid, a 920 s offset between the two flyovers and a
  search over the whole continental US, and its last minute shows the located point on a map;
  that point, read from the frames, is the private truth. The two flyovers were traced and
  fitted with the project's own ridge and fit tools, the UI's route: a loud one at 2137 s
  (fundamental 113 Hz with a second harmonic, 139 m/s at 6.2 km) and a faint one at 3062 s
  (144 Hz, 67 m/s at 1.2 km). The fitted gap is 925 s against colsto's 920 +- 5 s.
- First continental two-event run, the US-wide prior colsto himself used: stopped after
  2 h 22 min with 2,500 of 100,000 flight-pair hints done. The funnel's exact closest-approach
  step built a dense points-by-samples distance matrix per hint; with this recording's wide loud
  tube (a 56 km hint radius, 95 lateral offsets at 200 m) and loitering aircraft that matrix ran
  to tens of gigabytes, the process swapped (31 to 39 GB committed on a 32 GB machine, a third
  of a core busy), and hints in flight-index order re-read terrain tiles across the country
  (30 GB of file reads). The prefilter had also truncated its hint list at the cap, in index
  order, which the M4 notes had already flagged at the old cap. The funnel was reworked before
  the rerun: the nearest track sample comes from a k-d tree, only the narrower tube is laid per
  hint and its cells are checked against the other flight's exact closest approach, hints run
  in spatial order, the 1 s track cache is bounded, and `--max-pairs` sets the hint cap.
- Why the funnel then found nothing at the truth: the faint flyover's trace covers 25 s
  around closest approach and none of the curve's wings, so its straight-flyover fit is
  degenerate along d ~ v^2 (the slope at closest approach fixes v^2 / d and little else).
  Holding the speed at anything from 60 to 230 m/s refits the trace within 0.1 Hz rms of
  the best, while the fit's Jacobian reported a one-sigma speed error of 2.7 m/s. The fit
  said 67 m/s at 1.2 km; the aircraft, found afterwards in the ADS-B day by the 925 s gap,
  flew 104 m/s at 3.5 km, and ranges built from k sigma and the 30% and 50% floors excluded
  it (colsto, measuring the same curve: "pretty sure it wasn't going 70"). The fit now
  profiles the speed and keeps every held speed within 0.5 Hz rms of the best with its own
  distance, fundamental and closest-approach time (`DopplerFit.valley`, an optional
  `valley` per event in events.json); the funnel lays each tube and checks each cell at
  the flight's own speed and requires both events to imply the same recording start, and
  the scorer narrows f0 and t0 per candidate the same way. The loud flyover's 40 s trace
  profiles to 90 to 300 m/s; its fit, 139 m/s at 6.2 km, matched its aircraft (137 m/s at
  6.1 km) by the luck of a longer trace. With the profiled events, a check that ran every
  hint meeting within 61 km of the site (74,666 from an 80 km box) kept 21 cells within
  300 m of it, the nearest 89 m away, all from the true pair, among 246,896 candidates.
- The second cause, found by scoring the true site by hand with the true flights: the fit
  tool reported the model's closest-approach time, when the aircraft was nearest, while
  events, the funnel, the synthetic harness and the handoff's timing pair use the heard
  crossing, one acoustic delay later. Every event measured with the fit tool therefore
  started the search clock late by d / c: 1.5 s for the 500 m flyover of the M5 acceptance
  case, invisible inside the scorer's 3 s sweep, and 18 s for this recording's 6 km flyover,
  which put the modeled curve 18 Hz off the ridge (model-to-trace rms 0.45 Hz at the right
  start, 17.9 Hz at the late one). The fit's exported ranges and valley now carry the heard
  crossing, with a test on a 6 km flyover, and the UI's manual sliders convert the same way.
  At the true site with the corrected start, the true pair scores 19.5 (11.6 dB and
  7.9 dB) against the 15.4 that won run 1; the faint event's profile row at the flight's
  speed puts its fundamental at 126 to 132 Hz, and the audio's best answer there is 125.5,
  so the profile helps that event too (its original 141 to 148 Hz range scores below zero).
- The price of honest ranges: with speed envelopes of 62 to 309 and 29 to 309 m/s the
  prefilter on a 200 km box alone returns 919 million sample pairs and 282,091 hints in
  514 s, so a continental two-event search for this recording is beyond the v0.1 funnel on
  one machine (millions of hints at 5 to 20 ms each, then millions of candidates at 10 ms
  each). The reference case therefore runs with a region prior, the city-sized one colsto
  himself calls the typical case and which his video reveals in its last minute; the
  continental search stays on the list with the two changes it needs, a vectorized
  candidate scorer and a prefilter that indexes samples by the recording start they imply
  at their own speed (both built in the second pass below, which cut this search from 73 to
  8.5 minutes with the same answer).
- Personal recordings: personal-1 above, an aircraft case. No personal thunder recording yet,
  out of storm season; the thunder cases in the second pass are public field recordings with
  published locations.
- ffmpeg 9.0 is installed on the development machine, so phone and camera files (AAC in MP4 or
  M4A) and the reference recording's Opus decode; without it only WAV, FLAC and OGG do.

### Second pass, 2026-11-05: review, M4 rerun, thunder, short clips and scale

**Review of the M6 commit.** A review of a913b1d (funnel, fit, scorer, pipeline, flight
preparation, events, terrain, CLI, tests and UI) found five defects, each fixed with a test. The
scorer narrowed a profiled event with the speed at the track's nearest pass to the candidate,
not the pass heard during the event, so an aircraft passing a point twice at different speeds,
as pattern traffic does, was narrowed with the wrong row: a test track heard on a 40 m/s pass
was narrowed to its later, nearer 90 m/s pass, 6 Hz and 10 s off. It now finds the closest
approach among track times whose sound arrives inside the event's window. In the UI, Load put a
fitted event's range midpoints on the sliders (185 m/s at 25 km for the reference recording's
139 m/s, 6.2 km fit), Update right after Load replaced the event's ranges with slider defaults
and dropped its speed profile, and after Add the list's re-sort left the selection on the
neighbouring event, so the next Update overwrote it; Load now restores the fit's best profile
row, Update keeps an event's own ranges and profile until a slider moves, and the selection
follows the saved event. The fit panel's help text still called the t0 slider the heard
crossing, and the sliders stopped at 160 m/s and 5 km, short of the reference's loud flyover.
The clock filter treated an event with an empty profile as profiled, and a run with no
candidates wrote an untyped column. Checked and found correct: unprofiled events (the synthetic
path), single-event mode with and without profiles, an empty set after the clock filter, the
chunked prefilter (the same hints in the same order as the loop it replaced, at chunk sizes of
7, 113 and 20,000 rows, with and without the cap), the heard-time conversions in the fit and the
UI, terrain tile grouping at tile edges (20,000 random points and the edge cases) and
track-cache eviction (a cache of one track reproduces a run exactly). The true sites of both
solved cases score exactly as before the fixes.

Two further changes came from the review. A known recording time now limits the flights to those
airborne within the clip, before the funnel (`pipeline.flight_window`): personal-1's clocked
two-event search keeps the same 147 candidates and the same answer in 86 s instead of 24 min.
And the auto-trace's noise level is now the view's 25th-percentile frame
(`audio.spectrogram.trace_line`): it was the median of the view's bright frames and every dim
frame of the whole file, so the same view traced differently in a clip and in the full
recording, and a loud line filling most of the view kept only its brightest seconds (in a 60 s
excerpt, 13 points of the reference's loud flyover against 312 with the new level).

**M4 rerun.** The acceptance harness, rerun after the fixes with the commands of the M4 section,
again places 10 of 10 two-event and 10 of 10 single-event cases first within 200 m. Two-event
mode matches its table with every pair correct and the same rank-1 errors except case 2 (12 m,
was 16 m) and margins within 0.6 dB, keeps as many or more truth cells (the exact check finds
band-edge cells the two-tube join missed) and ran in 36 min against 2 h 33 min, the funnel
rework's doing. Single-event mode matches case for case, with 0.2 to 3.6% fewer candidates (each
cell's speed at closest approach is now checked), in 8 min 26 s. With the implied-start
prefilter below as the default, the two-event harness ran once more: 10 of 10 again, every pair
correct, with the same rank-1 errors and margins, in 26 min 35 s; it keeps fewer of the cells
around the site in three cases (9 to 15 where the run before kept 18 to 20), not the answers.

**Thunder cases.** Two public field recordings from the radio aporee archive on the Internet
Archive, both under a public-domain mark, both with the recordist's position (GPS in one, a
street address in the other) and a stated local start, and both inside GOES-East coverage with
GLM lightning within 25 km at their stated times, were analysed blind: the date the recordist
gives (a day around the stated time for thunder-2) as the time prior and a box the size of the
region the title names as the region prior (about 1.1 million km2 and 220,000 km2), default
settings, the truth in private files read only at scoring. Neither was located (rows thunder-1
and thunder-2 above). thunder-1, 30 minutes of a rural night with 18 loudness peaks, ranks a
point 479 km away first; the runner-up, 0.001 lower, sits 30 km from the site. thunder-2, an
hour on a front porch as a storm line arrives, with 30 peaks under building rain, ranks a point
73 km away first; the nearest coarse cell to the site ranks 601st of 4,000. Scoring each true
site by hand over the same prior (a 5 km box around it) shows why: the truth scores below the
impostors, 0.78 against 0.84 for thunder-1 and 0.71 against 0.79 for thunder-2, and their own
scores barely move with a 50 km audible radius (0.79 and 0.71) and fall with the Pearson score
(0.53 and 0.47). Both storms were dense. thunder-2's true site finds its best start at 05:05:55
UTC, six minutes after the recordist's stated midnight, with 1,186 GLM flashes within 25 km
during the hour; thunder-1's evening held up to 571 flashes an hour within 25 km, and restricted
to the recordist's stated evening the true site matches nothing (0.58 at the window's first
minute). At 20 or more flashes a minute the claps overlap into a rolling envelope that the model
renders for any dense patch of the storm, and the observed envelope, with rain in the same 20 to
250 Hz band, has nothing distinctive left to match: the regime the M2 notes flagged with
synthetic dense storms, now seen on real ones. The thunder half of the criterion is documented
with these two failures; a success needs a sparse storm, a few distinct claps a minute, and the
storm watch is the way to catch one.

**Short clips.** `scripts/m6_clip_sweep.py` cuts each aircraft recording to excerpts centered on
a flyover (or on the midpoint of two), fits the events from the analyst's own trace cut to the
excerpt, and searches each excerpt over the same metro box with the clock: the recording's start
plus the excerpt's offset, to plus or minus 2 s. For the reference recording the clock is
assumed, the start the ADS-B tracks imply, standing in for a camera's timestamp; personal-1's is
the phone's own. Rank 1 within 200 m, or the rank-1 error when the first rank within 200 m is
not in the top ten:

personal-1 (the two flyovers pass 97 s apart):

| clip (s) | both flyovers + clock | loud flyover alone + clock | faint flyover alone + clock |
| --- | --- | --- | --- |
| 1274 | rank 1, 190 m | rank 5 | none (55.5 km) |
| 600 | rank 1, 190 m | rank 4 | none (4.8 km) |
| 300 | rank 1, 190 m | rank 5 | none (4.8 km) |
| 180 | rank 1, 190 m | rank 5 | none (4.8 km) |
| 150 | rank 1, 130 m | none (11.5 km) | none (4.8 km) |
| 120 | none (235 m) | none (11.5 km) | none (4.8 km) |
| 105 | does not fit | none (43.4 km) | none (4.8 km) |
| 90 | does not fit | none (43.4 km) | none (55.8 km) |
| 60 | does not fit | rank 4 | none (55.8 km) |
| 45 | does not fit | none (43.5 km) | none (46.2 km) |
| 30 | does not fit | none (31.9 km) | none (55.8 km) |
| 20 | does not fit | none (50.8 km) | none (55.9 km) |

Reference recording (the two flyovers pass 924 s apart):

| clip (s) | both flyovers + clock | loud flyover alone + clock | faint flyover alone + clock |
| --- | --- | --- | --- |
| 3600 | rank 1, 173 m | none (675 m) | none (211 m) |
| 1800 | rank 1, 173 m | none (675 m) | rank 1, 155 m |
| 1200 | rank 1, 173 m | none (675 m) | none (211 m) |
| 1000 | rank 1, 164 m | none (675 m) | none (211 m) |
| 600 | does not fit | none (675 m) | rank 1, 177 m |
| 300 | does not fit | none (675 m) | none (211 m) |
| 180 | does not fit | none (675 m) | none (211 m) |
| 120 | does not fit | none (675 m) | rank 1, 136 m |
| 90 | does not fit | none (669 m) | rank 1, 95 m |
| 60 | does not fit | none (457 m) | none (511 m) |
| 45 | does not fit | none (5.0 km) | none (6.7 km) |
| 30 | does not fit | none (12.2 km) | none (6.7 km) |
| 20 | does not fit | none (93.3 km) | none (38.7 km) |

The two flyovers with the clock locate personal-1 in every excerpt that holds both, down to 150
s (130 m off, margin 12.7); at 120 s the excerpt edge cuts the loud flyover's trace and the
answer, the right pair, lands 235 m off. The reference recording's two flyovers are 15 minutes
apart, so that mode needs 1,000 s or more, and there it is 164 to 173 m off at rank 1. A single
flyover with a clock depends on the aircraft: the reference's faint turboprop, 3.5 km out,
stands out among the region's flights at that minute (a lead of 0.9 to 1.7 over the next), and
alone with the clock it places the site 95 to 211 m off in every excerpt from 90 s to the full
hour (first within 200 m in four of those nine lengths, by 11 m or less outside it in the other
five), 511 m off at 60 s and lost below that; its loud flyover, 6 km out, finds the right
aircraft but a point about 670 m off at every length from 90 s up, because a distant pass
constrains the distance poorly. personal-1's loud flyover, a trainer at pattern speed in a
training-dense metro, ranks 4th or 5th from 180 s up and at 60 s, outside the top ten at the
other lengths and never first, behind other trainers with nearly the same score, and its faint
second aircraft alone is never found. So the shortest clip is set by the mode: with a clock, a
distinctive aircraft alone needs about 90 s around its flyover, and a common trainer is
ambiguous at any length unless a second flyover is in the clip, in which case the clip needs
only to span both with about 25 s on either side. Without a clock, two flyovers must be in the
clip anyway, and the search is the slower two-event one.

Re-tracing each excerpt with the UI's auto-trace, as fixed in this pass, instead of cutting the
analyst's trace gives the same picture for two flyovers with a clock (rank 1 at 134 to 180 m
from the full clip down to 150 s for personal-1, 164 to 194 m for the reference) and places the
reference's faint flyover alone 261 to 366 m off in every excerpt from 60 s to the full hour,
where the analyst's checked trace gave 95 to 211 m: the auto-trace takes 215 to 219 points of
that faint line against the analyst's 180, and its fit, and with it the position, is looser.
With the auto-trace as it was before this pass, the reference's loud flyover traced to 25 to 76
points in every excerpt of 1,800 s and shorter, and its single-event rows failed there (run
folders `runs/m6-*-clips`, `-clips-cut` and `-clips-retrace`).

**A scorer window that follows the visible span.** personal-1's faint second event scored only
+1.8 dB because its line is visible for 22 s of the scorer's 120 s window, so scoring each
traced event only over its trace's span plus a margin was built (`ScoreConfig.trace_window`) and
tried on the clocked searches of both recordings and on the reference search:

| case and mode (lead of the true pair over the best other pair) | fixed 120 s window | traced span + 10 s | + 20 s | + 30 s | + 45 s |
| --- | --- | --- | --- | --- | --- |
| personal-1, both flyovers + clock | rank 1, 190 m, margin 12.9 | rank 1, 134 m, 17.7 | rank 1, 143 m, 15.3 | rank 1, 130 m, 13.3 | rank 1, 190 m, 11.8 |
| personal-1, loud flyover + clock | rank 5 | pair 3rd, refined >200 m | pair 3rd, >200 m | pair 3rd, >200 m | pair 4th, >200 m |
| reference, both flyovers + clock | rank 1, 173 m, margin 14.0 | 318 m | rank 1, 185 m | 206 m | rank 1, 129 m |
| reference, faint flyover + clock | rank 1, 155 m, margin 1.3 | 65.8 km | 45.2 km | rank 1, 191 m | rank 1, 95 m |
| reference, loud flyover + clock | 675 m, margin 3.6 | 571 m, 1.0 | 598 m, 2.3 | 675 m, 3.2 | 695 m, 3.6 |
| reference, both flyovers, no clock (metro search) | rank 1, 173 m | 318 m (truth 4th) | | | |

The narrower window lifts a faint event's own score, as intended, but costs more than it gives:
the reference search without a clock loses its answer, and the reference's faint flyover alone
is lost entirely at 10 and 20 s margins. Its trace ends at 3,067 s while the aircraft's heard
crossing is at 3,079 s, so a window built from the trace cuts out closest approach itself; the
scorer integrates brightness along the predicted curve, so it gains from line too faint to
trace, and the untraced wings are where look-alike aircraft differ. The fixed window stays the
default and the option stays off.

**Continental scale.** The two changes the first pass named are built. The pair prefilter now
indexes each sample by the recording start it implies at its own speed (its time plus the
event's distance at that speed over c, minus the event's heard crossing at that speed), so the
two events' samples are matched within the events' narrowed widths, the timing tolerance and one
index step, about 20 to 45 s, instead of within the reach over the slowest speed, about 1,000 s
with honest speed ranges. On the reference search over the metro box it returns 18.9 million
sample pairs and 44,480 hints in 9 s against 381 million pairs and 131,274 hints in 176 s; the
funnel then takes 314 s instead of 1,072 s and the true pair still ranks first. On a box 200 km
on a side around the reference site, the first pass's measure of continental cost, the new mode
returns 27 million sample pairs and 62,037 hints in 13 s against 553 million and 171,198 in 273
s for the old mode on the same 4,968 flights; on personal-1's metro box, 63 million pairs and
68,760 hints in 36 s against 1.37 billion and 166,302 in 715 s, after which the funnel keeps the
true pair's cells beside the site (10 within 300 m, the nearest 93 m) among 256,778 candidates.
The old mode stays as `FunnelConfig(prefilter="gap")`. The scorer was profiled at 9 to 16 ms a
candidate, most of it in brightness lookups that strided across the whitened spectrogram; a
frame-major copy makes them 18% faster with identical scores, while sweeping every start option
in one numpy call was three times slower (the arrays leave the cache). Candidates are
independent, so `--workers N` scores them in a process pool, a chunk of rows per task with only
the flights it needs, with scores identical to the serial ones: 244,711 candidates in 160 s on
12 processes, 0.65 ms a candidate of wall time against 9.1 ms on one core (run 2 scored 355,189
in 54 minutes). The reference search over the metro box now takes 8.5 minutes instead of 73,
with the same answer.

### Third pass, 2026-11-05: pattern traffic in the funnel, and a sparse-storm shortlist

**Pattern traffic.** The funnel took each candidate cell's closest approach to a flight at the
track's nearest sample (a k-d tree over the track segment near the hint), so a cell kept only
the nearest of the flight's passes. An aircraft in a traffic pattern passes a place every few
minutes, on the near leg, the far leg and the turns, and only one of those passes was heard: an
event heard on the far leg failed the distance band at the near leg's distance, and one heard on
a later circuit took the timing of whichever circuit flew nearest. A synthetic racetrack (3 km
legs 1.4 km apart at 50 m/s, flown five times) shows both. Heard on the third circuit's far leg,
1,726 m from the site while the near leg passes 424 m away, the site was lost in two-event mode
with a partner of narrower or wider band and in known-time mode; heard on the fourth circuit of
a circuit drifting 40 m a lap, it was lost in two-event mode with either partner. Each laid cell
now walks along the track from the sample that laid it to the closest approach of that pass
(`funnel.descend_to_pass`), one row per cell and pass, and the other flight's pass is the
nearest local minimum of distance among the samples whose sound arrives inside the window the
narrow event's implied start allows (`funnel.heard_pass`). Both move four samples at a time
before a 1 s polish: two passes of one point lie tens of seconds apart at the least, while ADS-B
jitter dents the distance with minima a few seconds apart. A walk one sample at a time stopped
at such a dent 4.6 s short of a test pass's closest approach (0.7 s with the coarse walk), and a
search of every sample took dents for passes: in 600 sampled hints of the reference search it
accepted 28 such candidates, an aircraft receding at 2.6 km whose real closest approach was
heard 10 s before the window, which the coarse search and the old funnel both reject. On the
reference case's real tracks the tubes hold 1.04 rows per cell, and 90% of the extra rows are
passes a minute or more apart; per hint the funnel costs what it did (6.0 s against 5.9 s for
those 600 hints) and keeps 4% more candidates.

**Repeats.** The rerun showed what the hint layout had always cost: a hint lays its tube within
three reaches of its meeting place, wider than the coarse cells that tell meeting places apart,
so the hints of one pair overlap and find the same cell, pair and start again. About half of
every two-event search's rows were such repeats (52% of the M4 case 4 rows after the silence
filter, 54% of personal-1's no-clock search), each scored and silence-checked anew, and the
"candidates", "after silence" and "truth cells kept" columns of the M4 tables count them. The
funnel now returns one row per cell, pair and start to the second (`funnel.unique_hypotheses`),
and the silence filter queries its neighbours 2,000 candidates at a time: on the M4 case with
the most pattern traffic, whose rows doubled with the pass fix, the filter took 8 minutes
instead of 44 s while the harness process grew to 49 GB, and takes 41 s with both changes.

**M4 rerun.** Both harnesses rerun on the final code, with the commands of the M4 section.
Single-event mode: 10 of 10 first within 200 m, every rank-1 error and margin as before, 0.2 to
10% more candidates and as many distinct cells of the true flight within 300 m of the site or
more (case 7: 4 to 7, case 10: 4 to 5), 8 min 30 s in all. Two-event mode, 10 of 10 again,
before (second pass) and after, counting distinct hypotheses and cells for both:

| case | rank-1 error (m), before / after | margin (dB) | distinct hypotheses after silence | distinct truth cells | runtime (s) |
| --- | --- | --- | --- | --- | --- |
| 1 | 19 / 19 | 5.83 / 5.83 | 3,021 / 4,398 | 4 / 4 | 155 / 99 |
| 2 | 12 / 12 | 4.85 / 4.85 | 1,049 / 2,148 | 4 / 4 | 84 / 71 |
| 3 | 14 / 14 | 7.47 / 7.47 | 978 / 1,577 | 5 / 5 | 93 / 70 |
| 4 | 11 / 11 | 3.40 / 3.26 | 10,084 / 17,232 | 6 / 6 | 256 / 231 |
| 5 | 23 / 23 | 3.85 / 3.85 | 1,938 / 3,373 | 6 / 6 | 131 / 98 |
| 6 | 17 / 17 | 4.07 / 4.07 | 3,030 / 3,780 | 6 / 6 | 98 / 93 |
| 7 | 12 / 12 | 7.95 / 7.95 | 6,950 / 10,957 | 5 / 6 | 223 / 273 |
| 8 | 10 / 10 | 6.80 / 6.80 | 3,341 / 4,871 | 5 / 5 | 222 / 176 |
| 9 | 16 / 16 | 2.08 / 2.08 | 7,726 / 11,581 | 6 / 6 | 258 / 295 |
| 10 | 13 / 13 | 3.64 / 3.51 | 930 / 2,003 | 6 / 6 | 75 / 113 |

Every answer and pair is the same; the funnel forms 1.2 to 2.2 times as many distinct
hypotheses, the passes the old one dropped, and two cases' margins fall by 0.13 dB to runner-ups
it never formed. The ten cases took 25 min 21 s against 26 min 35 s.

**Real recordings.** The reference search over the metro box (run 5) ranks the published site
first again, 173 m off, 19.88 with a margin of 1.61 and the start within 0.4 s, as run 4 did,
from 129,236 distinct hypotheses (run 4 scored 244,711 rows, repeats included): the funnel took
405 s against 314 s, the cost of the extra passes, and scoring 97 s against 160 s on 12
processes, 9 minutes in all. personal-1 changes more, because its loud flyover is a trainer at
pattern speed in a metro full of training traffic. With the clock (plus or minus 2 s) and both
flyovers the answer is the same, 190 m off, but the margin falls from 12.32 to 3.33: three pairs
of other light aircraft 40 to 46 km away (a Cessna 206 with a Cessna 172, and two pairs of
172s), which the old funnel never paired on the passes that fit, now meet the clock and score
11.6 to 14.2, where its best rival scored 5.2; the faint second flyover (+2.0 for the true pair
against -1.9 to +1.8 for these) decides. The 12 dB margins of the second pass were the old
funnel not forming those rivals. Without the clock the search fails as before, 46 km off: the
funnel forms 173,566 distinct hypotheses against 117,550, and the true pair's best cell within
300 m, which scores 17.34 either way, ranks 313th instead of 184th, behind 112 other pairs
instead of 66. The loud flyover alone with the clock is unchanged (11.5 km, the truth 5th).
Counting every pass of the region's trainers makes the field fuller; it is the clock that thins
it.

**Short clips, again.** Both clip sweeps were rerun on the new funnel with the same cut traces
and clocks. The reference recording's 30 runs give the same answer as before, every one, with
the same two-flyover margins. personal-1's two flyovers with the clock are first at every length
that holds both, down to 150 s, at the same distances (190 m, 130 m at 150 s, and 235 m at 120
s), with margins of 2.6 to 3.5 dB instead of 12 to 13, for the reason above; its loud flyover
alone is unchanged within two ranks. So the clip-length findings of the second pass stand, with
personal-1's margins restated. A slip in the first rerun showed how much one flyover leans on
the clock: started 0.18 s later than the assumed clock, which the tracks themselves imply and
the two-flyover search puts 0.4 s early, the reference's faint flyover alone landed 88 to 169 m
off and first within 200 m at every length from 60 s to the hour, against 95 to 511 m and first
at four of ten lengths with the assumed clock.

**A shortlist of sparse storms.** `scripts/m6_thunder_shortlist.py` searches the Internet
Archive's radio aporee mirror for items that mention thunder or storms (1,116 hits), keeps those
with a published position inside GLM coverage on a date GLM was operational (97), whose title or
description mentions thunder under a public-domain mark or a Creative Commons licence (48), and
rates each from one GLM file in six (20 s every 2 minutes) over the stated recording time and 45
minutes either side. Ten had sampled flashes within 25 km during the recording at a sparse rate,
4 a minute or fewer; twelve had flashes within 25 km only before or after it; eight sat in dense
storms, above 4 a minute (both failed cases, at 10 to 20 a minute, would have been among them);
two saw flashes only beyond 25 km and sixteen none within 50 km, among them rain and wind
recordings that only mention storms. The five sparse items with the most nearby flashes during
the recording were analysed blind, with the protocol of the two failed cases: a day around the
stated start as the time prior, a box the size of the state the item names as the region prior
(the state of the recordist's other recordings where the title names none), default settings,
the truth read only at scoring.

None of the five was located (rows thunder-3 to thunder-7 above). Each true site was then scored
by hand over the same prior, a 5 km box around it, and GLM's flashes were counted during the
stated recording with every file:

| case | recording | GLM flashes while recording, within 10 / 25 km | blind rank 1 | true site's own best |
| --- | --- | --- | --- | --- |
| thunder-3 | 19 min from a city window, in rain; 21 loudness peaks | 2 / 22 (1.2 a minute within 25 km) | 439 km off, 0.837, margin 0.000 | 0.763, at a start 84 min before the stated one |
| thunder-4 | 18 min beside a stream and a road; 11 peaks | 1 / 32 (1.7 a minute) | 74 km off, 0.746, margin 0.019; the true area ranks 10th, 6.5 km off | 0.681, 8 min before the stated start |
| thunder-5 | the same storm, 13 min later, 1 km away; 3 peaks | 0 / 16 | 23 km off, 0.936, margin 0.001 | 0.896, 138 min before |
| thunder-6 | 80 min in a tent; 18 peaks | 4 / 36 (0.45 a minute) | 142 km off, 0.625, margin 0.023 | 0.544, 13 min after |
| thunder-7 | 55 min at a shoreline park, its file name dated three months after its published date; 10 peaks | 4 / 11 | 330 km off, 0.760, margin 0.002 | 0.546, 41 min before |

The storms were sparse, but they were not near: no recording had more than 4 flashes within 10
km, so what it heard was rumble from 10 to 25 km, which the 5 s smoothed envelope turns into
broad bumps much like the rain, stream and traffic it rides on. With a day of a state's
lightning to choose from, some other place and start always fits such an envelope better than
the truth (0.63 to 0.94 against 0.54 to 0.90), and the published starts did not pin the truth
either: one is a round hour and two more are rounded to ten or fifteen minutes, and the truth's
own best start lies 8 to 138 minutes from the stated one. thunder-4 came nearest: its true area
found its own start 8 minutes before the stated one and ranked 10th, 6.5 km off, behind
impostors 0.02 to 0.09 higher. A success needs claps within about 10 km, a few a minute and not
dozens, and a recording whose start is known, so the shortlist now ranks each class by the
flashes within 10 km during the recording first.

**A thunder case located.** Ranked that way, the shortlist's first item is an 8 minute clip of
hail and occasional thunder from a city window, with traffic, on a day when the province around
it saw 121 GLM flashes in all; one flash during the clip fell 0.6 km from the recordist, one
more within 10 km, three within 25 km. Analysed blind with the same protocol, a box the size of
the province the item names and a day around the stated start, it ranks a point 2.2 km from the
site first (row thunder-8 above), with a score of 0.928 and a margin of 0.084 over the runner-up
16 km away, and a start 4 minutes before the stated round hour; the true site's own best is
0.912 at the same start. It is the first real thunder recording the method has located, and a
short one: two loudness peaks in eight minutes. What sets it apart is the lightning inside its
prior, a province over a day: 121 flashes, against 1,252 for thunder-4 and -5, 9,922 to 20,962
for thunder-3, -6 and -7, and 41,579 and 198,917 for the two dense-storm failures. The same clip
searched over all of western North America for that day (871 flashes, 1,414 candidate cells
after the prefilter) still ranks the site first, 2.1 km off, but a storm 1,984 km away ties it
(0.9275 against 0.9273). So a thunder clip can be short when its claps are near and the
lightning in its prior is rare: a few flashes leave few places and starts that fit two claps. A
near clap alone is not enough where storms are common: thunder-3 heard two flashes within 10 km,
the nearest 2.6 km away, and failed against the 9,922 in its prior.

**Online clips of a single strike.** Doorbell and security cameras that catch a strike next
door seemed the way to a known clock, so three such news uploads with the street published were
fetched. None carries a clock: the camera maker's export shows its logo and no time. One, a
doorbell in a news package, has the anchors talking over the strike and no clap left in the 20
to 250 Hz band. The other two hold the same strike from two cameras on one house, a single clap
and its rumble each (thunder-9). Searched over the state with the time the reports give (the
fire call), plus or minus an hour, they rank points 53 and 381 km away first at 0.999 and 0.984
with no margin: 8,102 flashes fell in the box in those two hours, and a single clap fits any of
them. The true site's own best is 0.966, at a start that puts the clap on one of the 13 flashes
GLM saw within 3 km in the half hour before the call. A thundersnow clip from a doorbell, with
its time to the minute and its neighbourhood published, was not fetched: GLM saw no flash within
100 km in the two hours around it, the optical sensor's known blind spot for shallow winter
storms. A clip of one clap can be located only with a clock that singles out its flash.

**Clips with a clock.** Uploads whose camera prints its clock on the picture were looked for
next, among owners' and stations' doorbell, security, dashcam and webcam clips, by title,
description and preview frame, and each lead was checked against GLM before anything was
fetched; most named only a city or nothing, a nest camera's clock fell on a day GLM saw no
flash within 5 km, and a dashcam that prints time and position sat in a storm of 2,841 flashes
within 25 km in an hour. Two were fetched. One, a strike on a named bar, is a broadcaster's
edit whose captions cover the clock and whose audio holds no distinct clap; it was not
analysed. The other (thunder-10) is an owner's edit of a storm over a published street from
several cameras: 25 s of real time around one strike, 110 s of a later stretch whose clock
gains 7% on the video (seconds dropped from the recording), slow-motion replays, and views with
no clock. Run with the clock to 3 s over the state, the 25 s piece fits everywhere (0.98, rank 1
67 km off); the 110 s piece, with five claps from flashes 5 to 10 km away in a storm of 9 a
minute within 25 km, ranks a point 33 km away first and one 4.3 km from the street sixth (0.893
against 0.924), the true site's own best 0.866; stretched to the clock it does no better (7.5
km). The first run of these showed the refinement leaving the time prior, searching start
offsets a minute either side of the coarse best and returning starts 19 to 99 s outside the
clock's window; it now stays inside the prior. A clock and a handful of claps bring the truth
into the top ten; they do not yet beat a storm's worth of impostors.

## M5: desktop UI, from a file to a heat map without the CLI

Acceptance criterion (HANDOFF M5): a user can go from a file to a heat map for both methods
without the CLI.

Walk-through on 2026-11-04 in the UI alone (`earwitness ui`), with every input made through
the pages: open the file by path, choose the method, set the date and draw or type the region,
measure the flyover with the fit tools, run, read the results. Both run folders reproduce from
their contents like any CLI run.

| method | recording | set in the UI | rank-1 error | score, margin over rank 2 | runtime |
| --- | --- | --- | --- | --- | --- |
| aircraft, single-event known-time | M4 single-event case 1: synthetic flyovers rendered on real 2026-09-08 tracks beside a site at 40.0190, -86.2447 | date; known start 16:16:31.950Z; a 1.4 by 1.15 degree region (15,200 km2); terrain off, since the case was rendered on flat ground; silence soft, no windows | 12 m | 7.98 dB, 1.02 dB | 42 s |
| thunder | a synthetic 30 minute clip beside the real 2025-05-16 storm (2,117 strikes heard) at 37.3750, -89.7450, start 23:00 UTC | date; a 2 by 1.95 degree region (38,300 km2); defaults | 320 m, start time exact | 0.990, 0.044 | 8 s |

The aircraft event came from the UI's own tools: zoom to 90 to 140 Hz and 520 to 620 s,
auto-trace (367 points along the sweep), fit (f0 119.5 Hz, 44 m/s, 603 m, t0 547.4 s, rms
0.52 Hz, against a truth of 120.1 Hz, 46.9 m/s, 659 m and 548.6 s), then add the event with
the fit's ranges. The audit report on the same file found the aircraft clue as likely (four
falling sweeps, the brightest 123 to 107 Hz at 542 s) and thunder as weak (the flyovers also
lift the low band); on the thunder clip the envelope view showed the storm's claps from 800 s
on.

Defects found and fixed during the walk-through, all in the code the UI exercised for the
first time:

- Auto-trace took every bright frame in the box, so noise outnumbered the line and the fit
  gave 22 m/s. The tracer now follows the line outward from its brightest frame and stops
  where it breaks.
- The fit's `t0` range missed the truth by 0.4 s with 1 s floors. Range floors are now the
  widths the M4 acceptance used (3 s, 3 Hz, 30% of the speed, 50% of the distance).
- A traced curve replaced the brightness score in single-event mode and went NaN whenever a
  trace point fell past the leg's end; the truth sat at rank 5 with a NaN total while flights
  40 km away ranked first. The trace is a diagnostic now, and the brightness score decides.
- Run files carried NaN values, which browsers reject; the server replaces them with null.
- With terrain on, the real heights put this flat-ground synthetic case 2.8 dB lower at the
  truth. That is an input mismatch, not a defect: real recordings keep terrain on.

Not shown by this check: real recordings (M6), the pywebview window on Linux, and the ADS-B
day download from the data panel, which was exercised only through the cached day.

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
