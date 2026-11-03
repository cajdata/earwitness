# Clue sources to consider after v0.1

Earwitness v0.1 uses two clue sources: thunder matched against lightning records, and propeller
aircraft matched against ADS-B. The sources below are candidates for later versions, ranked by
value to the project, highest first. Each entry says what the clue would give us, what public
data exists and where it falls short, how the source would plug into the pipeline v0.1 builds,
and what is still unknown. Nothing here is scheduled or implemented, and nothing here changes the
v0.1 milestones in [HANDOFF.md](HANDOFF.md).

## 1. Mains hum (electrical network frequency, ENF)

### What the clue gives us

A time, not a place. Mains hum rides on recordings made near powered equipment, as the 50 or
60 Hz fundamental and its harmonics (100 and 150 Hz on 50 Hz grids, 120 and 180 Hz on 60 Hz
grids). The grid frequency wanders by a few hundredths of a hertz as load and generation change,
and that wander is the same everywhere on one interconnection. In the US that means one pattern
for the Eastern Interconnection, one for the Western, and one for ERCOT in Texas. Extracting the
hum frequency over time from a recording and matching it against a reference log of the grid
frequency gives the absolute start time of the recording, usually to about a second, and tells
which interconnection the recording was made on.

An absolute time is exactly what the aircraft single-event known-time mode needs. In colsto's
third video, one flyover plus a known clock time located a 1-minute clip. With ENF supplying the
time, recordings that contain only one flyover, or that are too short for the two-event timing
pair, become solvable. For thunder, a known time collapses the offset sweep to a narrow window
and removes most false matches.

### Public data and its limits

- Great Britain: the National Energy System Operator publishes historic system frequency at
  1-second resolution as monthly files going back years. Useful for GB recordings and as a
  development dataset for the matcher.
- United States: FNET/GridEye at the University of Tennessee measures all three
  interconnections, but its public site has hidden the actual frequency values since May 2018.
  There is no public historical US grid-frequency series the app could download at runtime.
- Practical US route: a reference logger of our own, a small mains-sensing recorder (a
  transformer or optocoupler on a wall outlet feeding a sound card or microcontroller, logging
  zero crossings) that runs continuously. It covers only recordings made after logging starts,
  and one logger covers one interconnection, so three are needed for the lower 48 states.
- Other operators publish frequency data with varied terms and resolutions, and forensic labs
  keep private ENF databases that an open-source tool cannot use.

### How it plugs into the existing pipeline

1. New audio feature: a hum tracker. Narrow-band STFT around the fundamental or a harmonic (the
   second harmonic is often cleaner than the fundamental in consumer recordings), fine frequency
   estimation per 1-second frame, output a frequency-versus-time series.
2. New data adapter: reference frequency series per supported grid, cached per day like the
   lightning and ADS-B data.
3. New matcher: normalized cross-correlation of the recording's ENF series against each grid's
   reference across the date range. Output the best start time, the margin over the runner-up,
   and the grid identity.
4. Hand-off: the estimated time becomes the `--known-time` prior for the aircraft single-event
   mode and a narrow time window for the thunder search. The audit mode reports "mains hum
   present" as a clue.

### Open questions

- How often do target recordings contain usable hum? Outdoor phone recordings often have none;
  indoor recordings and recordings near lighting usually do.
- How many minutes of clean hum are needed for a unique match across a day, a week, or a month
  of reference data, given each grid's typical variability?
- How to build and validate the US reference logger, and whether any US operator will share
  historic data under terms an open-source project can accept.
- Whether video flicker (rolling-shutter ENF) is worth adding for recordings whose audio has
  no hum.
- Whether reference data terms allow redistribution, or whether users must fetch it themselves
  as with the other sources.

## 2. Ships (AIS)

### What the clue gives us

The closest analog to the aircraft method. Large vessels are logged by AIS with position, speed
and course, and they make two kinds of sound that carry over water to the shore: horn signals,
and engine and propeller tones with harmonics. Ships are slow. A vessel at 10 m/s shifts a tone
by about 3 percent each way between approach and recession, a few hertz on a 100 Hz tone, so the
Doppler curve is shallow and takes minutes to unfold. Timing carries more information than the
curve shape: the interval between passes of different ships, and the moment a horn is heard
against where that ship was.

### Public data and its limits

- MarineCadastre publishes US coastal AIS from the Coast Guard's receiver network as bulk
  downloads covering 2009 to 2023, downsampled to one position per vessel per minute. It covers
  US coastal waters, large lakes and major rivers only, and the archive lags the present.
- One-minute sampling means a 20-knot vessel moves about 600 m between points. Tracks must be
  interpolated, which is fine for straight passages and poor for maneuvering in harbors.
- Horn soundings are not logged anywhere. Small craft without AIS, and class B transponders
  that report less often, are invisible.
- Live aggregators such as MarineTraffic and AISHub have terms that do not suit an open-source
  tool, the same situation as OpenSky and Blitzortung for the v0.1 sources.

### How it plugs into the existing pipeline

The aircraft funnel and Doppler model carry over almost unchanged. A vessel adapter turns AIS
records into the same track format (time, latitude, longitude, height, speed, course), with
height set to the waterline plus a stack height. The speed gate uses vessel speeds, the distance
tube uses the audible range of ship engines over water, and the timing pair works across two
vessel passes or one pass plus an ENF or known time. Exact curve scoring runs on the same
whitened spectrogram with a narrower frequency band.

### Open questions

- How far engine tones are audible on shore, and which tone to track: the firing frequency of a
  slow-speed diesel and its harmonics, or the propeller blade rate, which for large ships sits
  below the audible band.
- Whether a Doppler shift of a few hertz over several minutes is measurable against wind and
  surf noise.
- Whether one-minute AIS positions support the 100 m class of answers the aircraft method
  reaches, or only a few hundred meters.
- Whether the archive lag makes recent recordings unsolvable with public data.

## 3. Trains

### What the clue gives us

In the United States, trains must sound the horn starting 15 to 20 seconds before entering a
public grade crossing, in a long-long-short-long pattern, unless the crossing is in a quiet zone.
A recording with train horns therefore says: within earshot of a railway, near a crossing where
horns are sounded, at a time when a train passed. The horn's Doppler curve also gives the train's
speed and closest-approach distance, and the known track geometry constrains where the listener
can be.

### Public data and its limits

- The Federal Railroad Administration's crossing inventory is public and includes crossing
  locations, train horn rule records and trains per day.
- Freight train positions are not public. Passenger operators publish GTFS and GTFS-Realtime
  feeds, but historical archives of the realtime feeds are patchy. The Transport for NSW archive
  is an example of one that exists.
- Railway geometry is available from OpenStreetMap under the ODbL.

### How it plugs into the existing pipeline

A candidate filter, not a standalone locator. Candidates farther than the horn's audible range
from any crossing with horns sounded are dropped or down-weighted, and the count of horn events
over the clip is checked against trains per day. Where a passenger realtime archive exists, a
horn can be tied to a specific train and the aircraft-style timing logic applies. The audit mode
reports "train horns present" as a clue.

### Open questions

- Audible range of a train horn in the recording's environment, and how to tell train horns
  from truck and ship horns automatically.
- Coverage of quiet zones and whether the inventory's horn records are current.
- Which regions have usable passenger realtime archives.

## 4. Helicopters

### What the clue gives us

Helicopters are already in the ADS-B data the aircraft pipeline downloads, and many civil ones
broadcast. Their sound is a main-rotor blade-pass tone in the low tens of hertz with strong
harmonics and heavy amplitude modulation, plus a tail-rotor tone roughly between 70 and 200 Hz.
The Doppler physics is identical to fixed-wing aircraft. Helicopters often fly low and slow over
populated areas, which puts them in earshot of recordings that no fixed-wing aircraft passes.

### Public data and its limits

- adsb.lol traces include rotorcraft; the ICAO type description letter H identifies them in the
  aircraft-type lookup.
- Many police and military helicopters do not broadcast, and some others use anonymized
  identities or are filtered out of community feeds. A heard helicopter with no track is
  unusable, and it weakens the silence filter the same way a missing fixed-wing track does.
- HANDOFF.md lists helicopters as excluded from v0.1. This entry does not change that.

### How it plugs into the existing pipeline

Keep type class H in flight prep instead of dropping it, add a rotor harmonic model to the
aircraft forward model (blade-pass fundamental from rotor speed and blade count, a harmonic
series, modulation), and let the funnel accept hovering and very slow segments, which defeat the
speed gate as written for fixed-wing aircraft. Exact curve scoring and refinement stay the same.

### Open questions

- Fraction of helicopters broadcasting usable ADS-B in the target regions.
- Whether blade slap and modulation need their own scoring, or whether the harmonic series alone
  is enough.
- How to handle hovering, where there is no Doppler curve but a long, loud, fixed-position
  source.

## 5. Rain onset and stop versus weather radar

### What the clue gives us

Rain starting and stopping is audible as a broadband noise step in most outdoor and many indoor
recordings. Weather radar records where rain was falling every few minutes. Matching the
recording's rain start and stop times against radar reflectivity over each candidate gives a
coarse, kilometer-level constraint. On its own it is weak. As a cross-check on thunder results it
is nearly free, because thunder cases happen during storms.

### Public data and its limits

- The NEXRAD Level II archive on AWS covers about 160 US sites from 1991 to the present, with
  volume scans roughly every 5 minutes. In 2025 the archive moved to the
  `unidata-nexrad-level2` bucket; a loader must use the current bucket.
- Radar sees rain aloft, not at the ground. Beam height, kilometer-scale resolution, the
  5-minute cadence and the minutes rain takes to fall blur the timing to several minutes and the
  position to kilometers.
- This archive is US only. Other countries run radar networks with varied openness.

### How it plugs into the existing pipeline

A new audio feature detects rain onset and stop times. A new data adapter fetches the radar
sweeps for the date and region into the cache and derives a per-candidate "rain present" time
series from reflectivity above a threshold. The thunder search gains an optional filtering or
re-scoring term that penalizes candidates whose radar rain times disagree with the recording.
The audit mode reports "rain onset or stop present" as a clue.

### Open questions

- How reliably rain onset can be detected in audio against wind, traffic and running water.
- The right reflectivity threshold, and how to account for fall time and beam height.
- Whether the constraint ever changes a thunder result in practice, or only confirms it.

## 6. Rare one-off events

### What the clue gives us

Some events are logged with precise time and position and are heard or felt over wide areas:
earthquakes, the sonic booms of returning rocket boosters, and the booms of large fireballs.
When a recording captures one, the delay between the catalog time and the heard arrival gives a
distance from the source, exactly like a single thunder strike with a known time. These are
precise when they happen and rare enough to be treated as bonus clues, not a method.

### Public data and its limits

- Earthquakes: the USGS catalog gives origin time, location, depth and magnitude. Shaking
  arrives through the ground at several kilometers per second, so the delay is short and the
  distance estimate needs seismic travel-time models, not the speed of sound.
- Rocket booster booms: launch and landing times are public, booster trajectories only partly.
  The boom reaches the ground along a path that depends on the trajectory and the atmosphere, so
  the time-of-arrival model is more involved than a point source.
- Fireballs: the NASA CNEOS fireball table lists time, location and energy for large events.
  Booms arrive minutes later from an airburst at tens of kilometers altitude, so a high-altitude
  point source with a sound-speed profile fits.

### How it plugs into the existing pipeline

The thunder machinery already handles point events with known time and position. These sources
need a generic "known event" adapter that supplies time, position and source height, plus a
propagation model per event type (sound speed varying with altitude for airbursts and booms,
seismic velocities for earthquakes). The search then treats the event as one more strike with
its own propagation speed.

### Open questions

- Timing precision of each catalog, and how to model propagation through a layered atmosphere.
- Whether enough real recordings of such events exist to test against.
- How to present a result built on one event, which gives a distance ring rather than a point
  unless combined with another clue.

## Sources

- NESO system frequency data (Great Britain, 1-second historic): https://neso.energy/data-portal/system-frequency-data
- FNET/GridEye public display (US, values hidden since May 2018): https://fnetpublic.utk.edu/tabledisplay.html
- Robert Heaton on ENF matching: https://robertheaton.com/enf/
- University of Maryland MAST lab, ENF research: https://www.mast.umd.edu/research.php?t=enf
- MarineCadastre AIS data (US coastal, 2009 to 2023): https://marinecadastre.gov/ais
- FRA crossing inventory listing: https://catalog.data.gov/dataset/crossing-inventory-listing-8-01-8-08
- FRA train horn rule, 49 CFR Part 222: https://www.ecfr.gov/current/title-49/subtitle-B/chapter-II/part-222
- Transport for NSW historical GTFS and GTFS-Realtime archive: https://opendata.transport.nsw.gov.au/data/dataset/historical-gtfs-and-gtfs-realtime
- NEXRAD Level II on AWS: https://registry.opendata.aws/noaa-nexrad/
- USGS earthquake catalog search: https://earthquake.usgs.gov/earthquakes/search/
- NASA CNEOS fireball and bolide data: https://cneos.jpl.nasa.gov/fireballs/
