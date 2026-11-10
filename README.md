# Tube Sounds

Sonification of the London Underground: every train arrival and departure at
every station on the network makes a sound.

## POC status (2026-08-21)

Both pieces verified working against live TfL data:

- **`poll_events.py`** — terminal event stream. Polls the whole network,
  prints colour-coded `ARR`/`DEP` events. `python3 poll_events.py [seconds]`.
- **`index.html`** — browser sonification. Same detection logic plus Web Audio
  synthesis and a live map of the network with event flashes. Serve with
  `python3 -m http.server` and open http://localhost:8000 (talks to the TfL
  API directly from the browser; no backend, no key).

## How the data works

**Source:** TfL Unified API, no key needed at this volume (anonymous limit
~50 req/min; we use ~4/min).

- One call fetches the whole network:
  `GET https://api.tfl.gov.uk/Line/bakerloo,central,…,waterloo-city/Arrivals`
  → ~4 MB, ~4,000 predictions, ~450 live trains, ~3 s. (`/Line/Mode/tube/Arrivals`
  is a 404; the comma-separated line list is the way.)
- Each prediction carries `vehicleId`, `lineId`, `naptanId`/`stationName` (the
  station being predicted for), `timeToStation`, and — the key field —
  **`currentLocation`**: free text like `At Oxford Circus Platform 5`,
  `Between Stockwell and Vauxhall`, `Approaching Waterloo`, `Left Hornchurch`,
  `Departed Oval`. So enter/leave events can be read directly rather than
  inferred from countdown times.

**Event detection:** per `(lineId, vehicleId)`, keep only the nearest
prediction (min `timeToStation`), then diff `currentLocation` between polls:

- not-At → `At X`  ⇒ **arrival**
- `At X` → not-At  ⇒ **departure**

Measured rate: ~1.5–2 events/sec network-wide during evening service.

**Gotchas found:**

- `vehicleId "000"` is a shared placeholder (multiple trains) — skip it or you
  get ghost events.
- `currentLocation` is sometimes just `At Platform` with no station name —
  fall back to the nearest prediction's `stationName`.
- The API 403s Python's default `Python-urllib` user agent; set any custom
  `User-Agent` header. curl and browsers are fine.
- CORS is `Access-Control-Allow-Origin: *`, so a static page needs no proxy.
- Station coordinates come from `GET /Line/{id}/StopPoints` (one call per line
  at startup); its `naptanId`s match the arrivals feed exactly.

## Timing: why the refresh is ~60s, and why that no longer limits the sound

**The 60s is TfL's CDN, not a choice.** Arrivals come back with
`cache-control: max-age=30, s-maxage=60` and an `x-cache: HIT`. Polling faster
returns byte-identical bodies, so no amount of polling buys finer data.

**But the payload already contains the future.** Each prediction carries
`timeToStation` (and absolute `expectedArrival`) for every upcoming stop — a
median of **11.5 predictions per train, up to 18**. One payload therefore
describes ~280 arrivals spread across the next 150s. The app no longer diffs
snapshots; it builds a **schedule** and fires each arrival at its own predicted
moment, ~2–3 events/sec, continuously. Polling only refreshes the schedule.

That is what makes held notes move continuously rather than lurching once a
minute: measured counts now change every tick (e.g. 38 → 35 → 34 → 30 → 38 over
32s) instead of holding flat between refreshes.

Three things had to be right for this to work:

- **Subtract the `Age` header.** `timeToStation` is relative to when the payload
  was *generated*, and the CDN may have held it up to 60s (`Age: 58` observed).
  `Age` is exposed via CORS, so `at = now + (timeToStation - Age)` puts events on
  the real timeline with no clock comparison. Parsing `expectedArrival` against
  the browser clock is worse: the skew estimate only ever revises downward, and
  each revision shifts every pending event, causing a burst.
- **Never mark a past-due arrival as "fired".** A payload served 58s stale
  reports every imminent arrival as already past. Marking those keys fired
  poisoned them, so a *fresher* payload could never schedule them — producing a
  dead 10s window followed by a burst. They are now simply skipped.
- **De-quantise.** *TfL rounds `timeToStation` onto a 30-second grid.* Bucketing
  a real payload's arrivals into 10s bins gives
  `0, 34, 17, 0, 38, 24, 0, 41, 11, 0, 39, 11, 0, 50, 14` — **every third bin is
  empty**, and `timeToStation mod 10` piles up on 2–4 and 6–8. Played literally
  that grid is audible as a stutter. Each arrival is now spread uniformly across
  the 30s step it was rounded into, which is *within the feed's own precision*.

Measured ping rate per 10s window:

| | |
|---|---|
| original snapshot diffing | `+120, +18, +2, +1, +110` (clump, then silence) |
| schedule, quantised | `+66, +17, +0, +4, +54, +70, +87` |
| schedule, de-quantised | `+40, +32, +22, +20, +25, +42, +41, +36, +23` |

**Stay time.** A train's occupancy now ends solely via the "clear a train after
Ns" slider — an earlier build also had a separate shorter dwell timer, which made
every departure fire twice and let occupancy drift above what the feed shows
(137 → 210 and climbing). The default is **60s**, not 120s: occupancy settles at
roughly `arrival rate × stay`, so at ~2.5 arrivals/sec a 120s stay implies ~300
lit stations, double the ~147 the feed actually reports — which would also push
every held note to full and flatten the chord.

## Pitch mode 3: "Line key + position"

Every line draws from the **same** A-major-pentatonic collection, so any two
lines sounding together are consonant by construction — there is no way to pick
a clashing combination. What differs per line is the **rotation**: which degree
of the scale it starts on, giving each line its own mode and melodic colour.

Pitch within a line comes from position on a **south-west → north-east
diagonal**, normalised across the whole network, so Morden sits low and
Cockfosters high. Two resolutions:

- **Pings** are pitched by their own station's exact position.
- **Held notes** keep one voice per line (still 11 — the voice count never grows
  with traffic) whose pitch *glides* to the mean position of that line's
  dwelling trains. A whole line audibly drifts upward as its traffic moves
  north-east. Verified live: Northern's note moving B3 → A3 → B3 over 36s.

## Why held notes went static (and the fix)

Held notes stopped responding: measured over 45s across 15 groups there were
**zero on/off crossings**, with bands 4–9 pinned at 93–100% and mean bar movement
of 0.00–0.32%/s.

Root cause: the `typical` constants used to normalise each group were measured
under the *old* snapshot-diffing occupancy model. Occupancy is now derived from
scheduled arrivals held for the stay duration, which yields far higher counts —
re-measuring showed observed/typical ratios of **1.1× to 3.3×** (Piccadilly 3.3×,
band 2 3.2×, Jubilee 2.4×). A group saturates at 1.5× its typical, so those
groups sat clipped at full volume permanently and could not move.

Two changes:

1. **Re-measured the seeds** under the current model.
2. **Made normalisation adaptive.** Each group now learns its own normal with a
   ~10-minute time constant, seeded from the measured values and persisted across
   pitch-mode rebuilds. The constants had already gone stale twice, and moving the
   stay slider rescales every count, so a fixed number was always going to drift.

After: the same groups swing 61% → 72% → 37% and 68% → 71% → 20% over 30s.

Three layers of smoothing remain by design, and they are why held notes will
always evolve more slowly than pings: occupancy is a rolling window the length of
the stay setting (60s), one train is a small fraction of a busy group, and the
gain envelope has a 1.1s attack / 2.6s release. To make it more reactive, shorten
the stay, or raise "thin out" so notes sit near the gate and cross it more often.

## Scales

Twelve options, selectable live. Note count per octave is the real trade-off —
fewer notes means wider spacing and safer harmony, more means finer resolution
and more chance of two lines clashing:

| | scales |
|---|---|
| 5/oct | major pentatonic (default), minor pentatonic, hirajoshi |
| 6/oct | blues, whole tone |
| 7/oct | major, natural minor, dorian, lydian, phrygian |
| 8–12/oct | octatonic, chromatic |

The gapped scales have no semitone steps, so simultaneous notes cannot grind;
whole-tone and chromatic deliberately can. Across 15 latitude bands at a 4-octave
range: major pentatonic gives `A2 B2 E3 F#3 A3 C#4 E4 A4 …`, chromatic gives
`A2 C3 E3 G3 A#3 D4 F4 A4 …`.

## Pitch range

One control, in octaves (1.5–6, default 4), governing **all three** pitch modes
through a single mapping. Measured span of the 15 latitude bands:

| range | span | distinct notes |
|---|---|---|
| 1.5 oct | 110–277 Hz | only **8 of 15** — bands collide |
| 3 oct | 110–740 Hz | 15/15 |
| 4 oct (default) | 110–1480 Hz | 15/15 |
| 6 oct | 110–5920 Hz | 15/15, but the top is shrill |

The rotation that gives each line its own key now changes **which degree** of the
scale you land on without changing register. Previously it was added to the pitch
index, so it also nudged the line's whole register up — lines differed in height
as well as in key, which was not the intent.

Note names were also an octave low for everything from C upward (370 Hz printed
as "F#3"): the octave number was incremented from the A root instead of from C.
Now `110→A2, 130.81→C3, 185→F#3, 370→F#4, 440→A4`.

## Tone balance (EQ)

Three **bands** — low / mid / high — each flat across its third of the register
with a short crossfade between, applied as a gain multiplier to both held notes
and pings, drawn next to the sliders and log-spaced over the current pitch range.

It was first written as interpolation between three *points*, which meant a
slider at zero only silenced one exact frequency — turning "mid" down barely did
anything. With bands, a slider at zero mutes that whole part of the range:

| setting | 110 Hz | 220 Hz | 330 Hz | 440 Hz | 700 Hz | 1100 Hz | 1760 Hz |
|---|---|---|---|---|---|---|---|
| all 100% | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| **mid 0%** | 1.00 | 1.00 | **0.01** | **0.00** | 0.51 | 1.00 | 1.00 |
| **low 0%** | **0.00** | **0.00** | 0.99 | 1.00 | 1.00 | 1.00 | 1.00 |
| **high 0%** | 1.00 | 1.00 | 1.00 | 1.00 | 0.49 | **0.00** | **0.00** |

(The ~0.5 values are the crossfade edges between bands.) Defaults are
low 80 / mid 100 / high 45: the top of the register is perceptually much
brighter, and with a dozen notes running it is the highs that turn a chord into
a shriek — more so now the range reaches 1480 Hz.

## Look

White background, official TfL line colours used directly on the map — including
**Northern's black**, which on the previous dark theme had to be faked as white
and was unidentifiable. Two adjustments are still needed:

- Small text in Circle yellow or H&C pink is unreadable on white, so **labels**
  use a darkened variant (hue preserved, lightness dropped). Map geometry keeps
  the exact official values.
- The canvas paints its own white background rather than relying on the CSS
  colour, because the fading occupancy dots composite with `globalAlpha` and
  need real pixels beneath them.

The map is responsive (fills the width, up to 1300px, keeping the arrivals list
beside it on wide screens) and its aspect ratio is now **geographically correct**
at 1.77:1 — a degree of longitude is shorter than a degree of latitude at 51.5°N,
and without that `cos()` correction the old fixed 620×430 canvas rendered London
noticeably stretched vertically. The backing store is scaled by
`devicePixelRatio` for sharpness.

The arrivals/departures list now streams in **both** sound modes.

## Line inspector tab

A deliberately literal view of one line, for understanding what the feed actually
says. Stations are laid out in route order; every train is placed by parsing its
`currentLocation` string — a square sitting on a station for `At X`, a dot midway
for `Between X and Y`, offset dots for `Approaching X` and `Left X`.

**Click a station** and it plays that station's arrival sound, and shows:

- the raw predictions referencing it — train id, `timeToStation`, `expectedArrival`,
  platform, destination, and the verbatim `currentLocation` text
- what the sonification derives: lat/lon, latitude band, SW→NE position, the note
  it would sound under **all three** pitch modes, stereo pan, EQ gain at that
  pitch, and how many trains the Sound tab currently counts there
- buttons to hear arrival vs departure, and to A/B the three pitch mappings

**Branches matter.** Lines are not single sequences: Northern returns 10, the
longest only 12 stops, so laying out just the longest silently stranded 55 of 70
trains. There is a branch selector listing each with its endpoints, and the
status line reports how many trains are on other branches rather than hiding it.

## National Rail — data availability (researched 2026-08-22)

**There is no keyless, CORS-enabled live feed for GB National Rail.** Everything
routes back to National Rail's Darwin/OpenLDBWS, which needs a free Consumer key
from the Rail Data Marketplace (raildata.org.uk — free tier, instant approval, no
payment). Confirmed directly: both public Huxley2 instances
(`huxley2.azurewebsites.net`, `national-rail-api.davwheat.dev`) return HTTP 500
on departures without a token, though their non-live endpoints and CORS headers
(`access-control-allow-origin: *`) work fine.

Other routes checked: Realtime Trains (`api.rtt.io`) needs a Bearer token;
TransportAPI needs a key **and does not send CORS headers** (backend proxy
mandatory); no public keyless GTFS-RT feed for GB rail was found.

What *is* keyless and works today:

- **Station coordinates** — `davwheat/uk-railway-stations` `stations.json` on
  GitHub raw: 285 KB, ~2,500 stations with `lat`, `long`, `crsCode`.
- **Route geometry, UK-wide** — TfL's `/Line/{id}/Route/Sequence/outbound` covers
  the 25 National Rail operators including ScotRail (88 branches, returning real
  coordinates as far north as Glasgow Central). TfL knows the *shape* of the
  national network even outside London.
- **But not live arrivals**: `/Line/{operator}/Arrivals` returns `[]` for every
  National Rail operator, and `/StopPoint/{id}/Arrivals` only returns anything
  for London stops (Euston 8 predictions — and those are Overground, `lineId:
  lioness`, not National Rail). Glasgow, Manchester, Edinburgh and Birmingham all
  return 0.

### What the tab actually does

**Built and verified:** the whole GB network map — 2,432 stations and 1,097 route
sections across 25 operators, from Penzance (50.12°N) to northern Scotland
(58.59°N). Per-operator toggles. Geometry is pre-built into `nr-network.json`
(125 KB) by `tools/build_nr.py`, because fetching all 25 operators from TfL live
would be ~12 MB per page load.

**Built but UNVERIFIED:** live departures. There is a box to paste a Huxley2 base
URL and your own Darwin token (kept in `localStorage`), a "Save & test" button
that reports the real HTTP status back, and polling of 25 major stations that
flashes the map and plays the same bell instrument pitched by latitude. **I could
not test any of this — I have no token**, and the public Huxley2 instances were
returning HTTP 500. Treat it as a starting point, not a working feature.

Operator colours are *indicative* — chosen to tell routes apart, not official
brand palettes (unlike the tube side, which uses real TfL colours).

Projection note: GB spans 50°N–58.6°N, too much for the flat `cos(lat)`
correction used on the tube map, so this one uses Web Mercator. The `×180/π` on
the Mercator `y` matters — without it `y` is in radians while `x` is in degrees,
and the country renders as a 28:1 horizontal smear.

## Map: line paths

`GET /Line/{id}/Route/Sequence/outbound` returns `stopPointSequences`, each an
ordered list of stops whose `id`/`stationId` match the arrivals `naptanId`
exactly. Branched lines return several sequences (northern and metropolitan have
10 each; 46 across the network), so they are drawn as separate polylines — a
missing station breaks the path rather than drawing a wrong link. They are
stroked faintly under the station dots in line colour.

## Organising the stream

**The arrivals endpoint is edge-cached** — `cache-control: public,
max-age=30, s-maxage=60`, `x-cache: HIT`. Two consequences, both measured:

1. The payload only really changes every ~30–60 s, so all the movement across
   the network lands in one clump of ~120–150 changes. Polling faster just
   returns byte-identical bodies.
2. **Responses can arrive out of order.** Consecutive polls returned payload
   timestamps `09:48:10`, then `09:48:19`, then `09:48:10` again — a *stale*
   body served after a fresher one, because different edge nodes hold
   different ages. Diffing against that makes trains appear to move backwards
   and fires phantom arrival/departure events.

Fixes in the POC:

- **Staleness guard.** Every prediction carries a `timestamp` (payload
  generation time). Track the newest one processed and skip any response not
  strictly newer. In a 90 s run this correctly rejected 2–3 stale payloads.
- **Event queue with adaptive drain.** Rather than firing a batch at once,
  events go on a queue drained one at a time, with the delay set to spread the
  current backlog over `SPREAD_MS` (45 s), clamped to 70–600 ms. It is
  self-balancing: a large backlog speeds up, an empty one idles. The clamp
  matters — without the 600 ms ceiling a small queue trickles so slowly the
  next batch arrives before it clears, and the backlog grows without bound
  (observed: queue climbing to 142 while playing 1–3 sounds per 15 s).
- Batches are shuffled so a burst interleaves lines rather than marching
  through them line by line.

Measured before: `+120, +18, +2, +1, +110` sounds per 15 s (machine-gun burst
then ~45 s of silence). After: `+14, +40, +29, +24` — a steady ~2/sec.

## Station occupancy on the map

The map shows which stations are **currently holding a train**, not just a flash
per event. A station lights while at least one train reads `At <station>`, grows
with the number of trains there, and dims as it ages toward the hold timeout.
Roughly **100 stations are lit at once** (≈146 dwelling trains, so interchanges
hold several).

Start and end of a stay both come off the stream: the stay begins on the first
snapshot a train reads `At X`, and ends on the first snapshot it no longer does.

**The hold timeout (default 120 s, slider 30–300 s) is not optional polish.**
The dwell measurement found 22 trains reading `At Platform` across all 7
snapshots (~7 minutes) — stabled and terminating stock that would otherwise pin
a station lit forever. After the timeout the train is treated as departed.

Two bugs found while building it, both worth remembering:

- **Expire-and-relight.** Pruning a timed-out train is not enough: the next poll
  sees it still reading `At X` and re-adds it with a fresh timestamp, so a parked
  train blinks off and relights forever. Timed-out trains go on an `expired`
  list and are only allowed to light that platform again once the feed shows
  them somewhere else.
- **Delimiter collision.** The expired marker was first a Set of
  `` `${trainKey}|${naptanId}` `` — but the train key is *itself* `line|vehicle`,
  so `split('|')` destructured into the wrong fields, every lookup missed, and
  markers were dropped immediately. Symptom: bright-pixel count oscillating
  123 → 6336 → 302 → 7740 in lockstep with the poll cycle. It is now a
  `Map(trainKey -> naptanId)`, which sidesteps string joining entirely.

Verified: at a 30 s hold the map decays to 0 stations lit and *stays* cleared
across several polls, re-lighting only as trains genuinely move; at the 120 s
default it holds ~98 stations steadily.

## Why it used to go quiet / stop polling

Three separate causes, all found by a 4-minute instrumented browser soak:

1. **`fetch()` has no timeout.** A stalled connection (wifi handoff, mobile
   network, TfL holding the socket) leaves the `await` pending forever — it
   never resolves *and never rejects*, so neither the `catch` nor the
   next-poll `setTimeout` runs. The poll chain dies permanently and silently.
   Fixed with an `AbortController` (15 s) plus rescheduling in a `finally`, so
   every exit path re-arms the loop.
2. **A throw in `drain()` killed the drain chain**, muting the app for good
   while polls carried on. `fireEvent` is now wrapped in try/catch.
3. **"Stale payload, skipped" looked like a hang but was normal.** TfL's CDN
   holds a payload ~60 s, so with a 20 s poll two or three consecutive polls
   legitimately return nothing new. The status now says "TfL cache unchanged
   (refreshes ~60s)" instead of something that reads like an error.

Also added: a visibility handler (phones suspend timers on lock — on return it
resumes audio and re-polls immediately) and a watchdog that restarts the chain
if nothing has come back in 3 poll intervals.

Soak result: longest silence 33 s — **entirely the startup warmup**, since the
first poll only sets a baseline and the next few can be cache hits. After that,
every 3 s sample had 5–12 new sounds, 0 stalls, 0 page errors, 502 sounds in
4 minutes.

Known trade-off: the smoothing queue runs ~150–240 events deep, so a sound can
lag the real train movement by up to ~55 s. That is the price of a continuous
stream given a feed that only updates in ~60 s clumps.

## Sound design (current mapping)

- **Arrival** — bright synthesized bell (three partials, ~1.6 s decay).
- **Departure** — duller low pluck, one octave down, slight downward glide.
- **Pitch** — station latitude, quantized to a major pentatonic over ~3
  octaves (south = low, north = high). Pentatonic means any combination of
  simultaneous events stays consonant.
- **Stereo pan** — station longitude (west = left, east = right).
- **Colour/identity** — line colour drives the visuals; per-line filtering via
  checkboxes. Volume capped with a voice limit + compressor.

## Troubleshooting "I can't hear anything"

The first poll only establishes a baseline — events come from *diffing two*
polls, so there is a silent 15–30 s gap after pressing Start. Verified by
headless test: at t=10 s zero sounds had played; by t=40 s, 130 had.

Mitigations now in the page: a **🔊 Test tone** button that plays a chime with
no network data involved, an arpeggio fired the instant you press Start, and an
`audio:` status line showing AudioContext state, sample rate and a running
count of sounds played. If the counter climbs but you hear nothing, the problem
is device output, not the app. On iOS the hardware mute switch silences Web
Audio, so the page now plays a looping silent WAV via an `HTMLAudioElement` to
force the "playback" session category.

## The two axes (both switchable in the page)

`index.html` exposes the instrument as two independent choices, so all four
combinations are live:

| | **Pitch: station latitude** | **Pitch: tube line** |
|---|---|---|
| **Sound: Pings** | the original — bell per arrival, pluck per departure, south = low, north = high | one note per line, busiest lines centred |
| **Sound: Held notes** | 15 latitude bands, each a sustained note | 11 lines, each a sustained note |

(`chord.html` now just redirects here.)

### "Won't held notes be too much sound?"

It would be, done naively — **~147 trains are dwelling at any moment** (41–53%
of the ~295 running), and one voice per train would be unusable. The design
never does that. Trains are always **summed into a fixed set of notes** and the
note is played purely by ramping its gain:

- by line → **11** notes
- by latitude → **15** bands, of which only ~12 are ever non-empty (bands 13–14
  are empty; band 5, central London, alone holds ~50 dwelling trains)

So the worst case is 15 simultaneous oscillator groups regardless of how busy
the network gets. Measured: 12/15 notes sounding at once. Two further controls
keep it from turning into a wash:

- **Thin out** slider gates quiet notes off entirely. Measured effect:
  12/15 → 11/15 → 8/15 notes sounding at 0% / 30% / 55%.
- **Sparse-group weighting.** Relative normalisation alone lets a near-empty
  group blare — one train in a band whose typical is 0.4 saturated to 100% and
  shrieked a top E4. Groups are now weighted by `0.5 + 0.5·min(1, typical/8)`,
  which dropped that lone train to 53% while leaving busy central bands at
  66–98%.

**Measured inputs** (distinct payloads, Saturday morning):

- **~143 trains are dwelling at any moment** — 41–53% of the ~295 running. So
  one voice per train would mean ~143 voices over only 11 pitches. Instead each
  line gets a single always-running voice and is "played" purely by ramping its
  gain, which also means arrivals crescendo and departures fade rather than
  clicking on and off.
- **Business ranking** (avg trains): northern 69, central 49, jubilee 37,
  victoria 27, hammersmith-city 25, district 24, metropolitan 20, bakerloo 19,
  piccadilly 18, circle 5, waterloo-city 1.4. The busiest line takes the centre
  pitch and the rest fan outwards, so the quiet lines land at the extremes of
  the register.

**The tuning that mattered.** Normalising each line against its own typical
dwell count is necessary (otherwise Northern buries Waterloo & City), but the
obvious mapping — gain ∝ `count / typical` — is wrong. Counts hover right around
their average by definition, so nearly every line pinned at 100% and the chord
became a static wall; a `sqrt` made it worse by compressing further. It needs to
**expand** the range around typical instead: `(ratio - 0.25) / (1.5 - 0.25)`,
clamped. Measured before: bars at 96/100/100/88/100/100/93/100. After: lines
swinging independently, e.g. central 84→53→42%, district 53→83→90%,
metropolitan 75→32→23%.

Other details: three detuned oscillators per group plus a quiet octave give the
beating that stands in for many trains sharing one pitch; the lowpass opens with
level so a busier note is brighter, not just louder; a group with zero dwelling
trains goes fully silent. Waterloo & City is a two-train shuttle whose count
barely moves, so it sits high in its own range — the sparse-group weighting
keeps it from dominating as a constant top note.

Latitude bands, avg dwelling trains (0 = south, 14 = north):
`[2.4, 1.4, 1.0, 7.0, 12.2, 50.4, 26.4, 17.4, 12.0, 7.6, 4.6, 2.4, 1.6, 0.4, 0]`

## Ideas for later

- Per-line timbres (Victoria = glassy, Northern = dark, District = woody…).
- Use `timeToStation` to *schedule* sounds precisely instead of random jitter.
- Rhythm from headways; drone from the number of trains between stations.
- Night mode: quiet network = sparse, meditative; rush hour = dense texture.
- Include DLR/Overground/Elizabeth line (`modeName` supports them via the same
  API shape).
