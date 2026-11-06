# Countermeasures

What does and does not stop a recording from being located by its thunder or its propeller
flyovers. The evidence comes from three places, marked in each row:

- **video 3**: colsto's tests in [I discovered a sound that leaks your exact
  location](https://www.youtube.com/watch?v=UytYrhYLOKI), as summarized in
  [HANDOFF.md](HANDOFF.md) (not re-run here).
- **M6**: Earwitness on real recordings, [VALIDATION.md](VALIDATION.md).
- **sweep**: Earwitness on its own demo cases, edited the way a person might edit a clip before
  sharing it (`scripts/m7_countermeasures.py`, results below). These are synthetic cases, one
  storm and one pair of flyovers, so they show mechanisms rather than odds.

Check your own recording with `earwitness audit <file>` (or the app's Audit page). It reports
which of these clues a recording holds.

## In short

- **The timing between sounds gives the place away.** Edits that change that timing, mainly
  cuts, are what help. Filters and compression don't.
- **A clock is the biggest gift to a search.** An on-screen clock, a file's own timestamps, a
  spoken time or a creation date turns a hard search into an easy one. Remove them.
- **Strip the metadata.** Phone videos often carry GPS coordinates and a creation time, which
  give the answer away without any sound analysis.
- **Shorter helps, but less than you'd think.** With a clock, 150 s holding two flyovers located
  a phone clip to 130 m, and 90 s around one distinctive aircraft was enough.

## What did not work

| Edit | Thunder | Aircraft | Source |
| --- | --- | --- | --- |
| Low-cut (high-pass) filter, then the level turned back up | Still located: a 150 Hz low-cut changed nothing (242 m off before and after). | Still located: 13 m off before, 44 m after a 300 Hz low-cut. Even a steep 300 Hz filter, which removes the fundamental near 100 Hz, left the site first (23 m); scored on four harmonics, it was 13 m again. | video 3 (the curve came back once the gain was boosted); sweep |
| Lossy compression | AAC at 64 kbit/s and MP3 at 16 kbit/s changed nothing (242 m). | No change (13 m) at either. | sweep |
| Phone-call compression | | The curve survived. | video 3 |
| Talking over it | | The curve survived. | video 3 |
| Keeping the clip short | An 8 minute clip with two loudness peaks was located 2.2 km off: its nearest clap was 0.6 km away, and lightning was rare that day. | With a clock, two flyovers located a 150 s phone clip (130 m off), and one distinctive aircraft needed about 90 s around its flyover. A 1-minute clip with one flyover and a known time was enough in video 3. | M6; video 3 |

Why filters and codecs fail: the aircraft search compares each frequency against that same
frequency's own background (per-frequency whitening), so a filter that turns down a band
turns down the noise in it equally. And a Doppler curve has harmonics above any low-cut. Codecs
keep loud tonal lines, which is what a propeller's whine is. The thunder search compares the
shape of the loudness over time, and the cosine score ignores overall level.

## What helps

| Edit | Effect | Source |
| --- | --- | --- |
| **Cuts between flyovers** | The two-flyover search needs the real gap between two aircraft: after a 60 s cut between the flyovers it found nothing at all. | video 3; sweep |
| **Removing the clock** | Without a clock, one flyover can't be placed: the search needs two, and two without a clock failed for a phone clip in a metro full of training flights, where the clock had located it to 190 m. A clock and five claps brought a 110 s security-camera clip of a storm to sixth place, 4.3 km off. | M6 |
| **Many cuts in thunder** | One 60 s cut barely mattered (237 m), because the claps on either side of it still fit. Three 20 s cuts moved the answer from 242 m to 4.5 km. | sweep |
| **Heavier noise reduction** | Removes faint curves and the quieter claps. | video 3 (not measured here) |
| **Stripping metadata** | Removes GPS tags and creation times. `ffmpeg -i in.mp4 -map_metadata -1 -c copy out.mp4` does it for most video files. | M6 protocol; audit |

What a cut does not do: it leaves each flyover intact. With a clock, the flyover before a cut
still locates the site (37 m off in the sweep), because a clock pins the time of each part on
its own, as long as the silence check is the soft one (`--silence soft`, the default to use on
real recordings). With the hard silence check, the stretch after the cut looked to the clock
like silence while another aircraft could be heard, and every candidate was dropped. Either
way a clock is only right up to the first cut.

## What the clue needs to work (M6)

These come from the real recordings in [VALIDATION.md](VALIDATION.md), so they are as much
about what makes a recording safe as what makes it findable:

- A clock plus two flyovers located a 150 s phone clip.
- A distinctive aircraft alone, with a clock, needed about 90 s.
- A common trainer alone was ambiguous at any length, even with a clock: other trainers flying
  nearby leave nearly the same curve.
- A thunder clip needs claps within about 10 km, rare lightning in the region and day searched,
  or a clock. Dense storms, where claps overlap, and distant rumble both failed. So did a single
  clap with no clock.

## Not tested

These seem likely to help, but nobody has measured them here. Treat them as ideas, not advice:
pitch shifting or speeding the audio up (both move a Doppler curve and its timing), replacing
the soundtrack with music, and real noise-reduction tools (the sweep did not model one).

## The sweep

```bash
uv run python scripts/m7_countermeasures.py --out runs/m7-countermeasures
```

The demo's thunder case (two storm cells 9 and 12 km away, 15 minutes) and aircraft case (two
light aircraft 4 minutes apart, plus two decoys), rendered at 16 kHz, edited, then searched like
`earwitness demo` does: thunder over a 120 km box and an hour of start times, aircraft over a
30 km box and 20 minutes without a clock, unless noted. Rank 1 within 5 km (thunder) or 200 m
(aircraft) is a success. Margin is the lead over the next region at least 15 km away; the
aircraft cases formed only the true pair, so they have none. Run on 2026-11-05 with ffmpeg 9.0.

| method | edit | rank-1 error | rank within tolerance | score | margin |
| --- | --- | --- | --- | --- | --- |
| thunder | as recorded | 242 m | 1 | 0.997 | 0.047 |
| thunder | 150 Hz low-cut, 2nd order | 242 m | 1 | 0.997 | 0.046 |
| thunder | AAC 64 kbit/s | 242 m | 1 | 0.997 | 0.047 |
| thunder | MP3 16 kbit/s | 242 m | 1 | 0.997 | 0.047 |
| thunder | one 60 s cut at 7.5 min | 237 m | 1 | 0.891 | 0.040 |
| thunder | three 20 s cuts | 4.5 km | 1 | 0.854 | 0.049 |
| thunder | one 60 s cut, clock to 5 s | 663 m | 1 | 0.891 | 0.223 |
| aircraft | as recorded | 13 m | 1 | 27.33 | |
| aircraft | 300 Hz low-cut, 2nd order | 44 m | 1 | 27.35 | |
| aircraft | 300 Hz low-cut, 8th order | 23 m | 1 | 7.27 | |
| aircraft | the same, scored on four harmonics | 13 m | 1 | 13.60 | |
| aircraft | AAC 64 kbit/s | 13 m | 1 | 27.40 | |
| aircraft | MP3 16 kbit/s | 13 m | 1 | 26.95 | |
| aircraft | 60 s cut between the flyovers | no answer | none | | |
| aircraft | the same cut, first flyover alone, clock to 2 s, hard silence check | no answer | none | | |
| aircraft | the same, soft silence check | 37 m | 1 | 11.76 | |

Aircraft scores are decibels of whitened brightness along the modeled curves; thunder scores
are cosine similarities.
