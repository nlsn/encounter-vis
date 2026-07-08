# F-5 Flyover / 97-0400 Encounter Visualization

Animated reconstruction of the July 4, 2026 encounter near Joint Base
Andrews between the C-37A **97-0400** (SAM963, arriving from the north) and the
four-ship F-5 formation **N591EM / N593EM / N595EM / N592EM** (GHOST1–4,
recovering from a National Mall flyover), built from ADS-B Exchange KML track
exports.

Output: 1920x1080, 30 fps MP4 with:

* OSM (muted) or satellite basemap with labeled landmarks and highways
* Trailing trajectories with raw ADS-B fix markers, aircraft glyphs
  oriented to track heading, live callsign + altitude labels
* Live range line and distance readout between 97-0400 and the nearest F-5
* Closest-point-of-approach markers and per-aircraft CPA summary
* Altitude and separation strip charts with a time cursor
* Variable playback speed (medium through Mall approach, ~3× through the
  closest-approach window, fast through the 97-0400 holding pattern)
* Persistent data-quality disclaimer (raw uncorrected pressure altitude)

## Quick start

```bash
pip install -r requirements.txt
# ffmpeg must be on PATH (apt install ffmpeg / brew install ffmpeg)

# OSM basemap, muted (default; network needed on first run; tiles cached
# in basemap_cache/ so later runs are offline-capable):
python render_video.py -o output/encounter_osm.mp4

# Satellite imagery basemap instead:
python render_video.py --basemap satellite -o output/encounter_satellite.mp4

# Fully offline schematic basemap (no network, no requests/Pillow needed):
python render_video.py --basemap offline -o output/encounter_offline.mp4
```

Useful options: `--fps`, `--width/--height`, `--start/--end`
(UTC HH:MM:SS on 2026-07-04), `--crf` (x264 quality), `--tile-cache`.

## What the numbers mean (and their limits)

**Methodology**: each pair of tracks is interpolated onto a common 1 s grid
using cubic spline interpolation; horizontal separation is haversine
great-circle distance; vertical separation is the difference of the two
reported pressure altitudes; "closest approach" minimizes the combined 3D
distance sqrt(h² + v²).

Computed CPA of each F-5 relative to 97-0400:

| Callsign | Aircraft | Horizontal | Vertical (baro delta) | Time (UTC) |
|----------|----------|-----------:|----------------------:|------------|
| GHOST1   | N591EM   | 0.52 NM    | 416 ft                | 17:15:09   |
| GHOST2   | N593EM   | 0.59 NM    | 245 ft                | 17:15:08   |
| GHOST3   | N595EM   | 0.53 NM    | 362 ft                | 17:15:10   |
| GHOST4   | N592EM   | 0.58 NM    | 649 ft                | 17:15:12   |

**Caveats -- treat all separations as estimates:**

* Altitude is **uncorrected pressure altitude** (29.92 inHg reference)
  as exported from ADS-B Exchange, in meters in the KML, converted to
  feet for display. It is not geometric height and not corrected to
  local QNH. Baro *differences* between aircraft are more meaningful
  than either absolute value, assuming both feeds report pressure
  altitude consistently -- which has not been independently verified.
* ADS-B position error, altitude quantization, receiver timing jitter,
  and multi-receiver merge artifacts are not modeled.
* Cubic spline interpolation smooths between fixes but can slightly
  over- or undershoot during sharp turns when the update rate is low.
* Ground speed and heading shown in the video are derived by finite
  differences of the interpolated positions (smoothed ~7 s), not taken
  from the ADS-B velocity messages.
* Aircraft type labels: 97-0400 is labeled C-37A and the N59xEM
  aircraft F-5 based on prior notes accompanying the data, not
  independently verified from the track files themselves (KML carries
  no type field).

## Aircraft roster

| Callsign | Reg     | ICAO   | Type  |
|----------|---------|--------|-------|
| SAM963   | 97-0400 | AE010D | C-37A |
| GHOST1   | N591EM  | A7A18E | F-5   |
| GHOST2   | N593EM  | A7A8FC | F-5   |
| GHOST3   | N595EM  | A7B06A | F-5   |
| GHOST4   | N592EM  | A7A545 | F-5   |

## Basemap accuracy

* **Landmark points** (monuments, airports) use well-known surveyed
  coordinates, good to ~100 m -- sub-pixel at these map scales.
* **Offline mode rivers and highways are schematic approximations**
  (roughly 0.5-2 km), hand-digitized through high-confidence anchor
  points (bridges, interchanges). They are labeled "(approx.)" and are
  for orientation only. See `encounter/landmarks.py`.
* In **satellite/osm modes** the imagery itself provides the ground
  truth; schematic lines are not drawn, only landmark labels.
* The runway-like line at Andrews is derived from 97-0400's own
  on-ground track points (data-driven, not an airport diagram).

Improving the basemap: for survey-grade roads/water, replace the
schematic polylines with an OSM extract (e.g. `osmnx` or a Geofabrik
shapefile clip) and plot in Web Mercator alongside the tiles.

Tile usage: Esri World Imagery and OSM tiles are fetched at zoom 11/13/14
for the two coverage boxes (a few hundred tiles total), cached under
`basemap_cache/`, and attributed on-frame. Respect the providers' terms
for anything beyond light personal use.

## Project layout

```
render_video.py            CLI entry point
closest_approach.py        original standalone CPA script (reference)
data/                      ADS-B Exchange KML exports (5 aircraft)
encounter/
  kml.py                   gx:Track parser (multi-segment, dedup, cubic spline)
  geometry.py              Mercator, haversine, CPA, speed/heading
  timeline.py              variable-speed playback + camera keyframes
  landmarks.py             landmark points + schematic line data
  basemap.py               tile fetch/cache/mosaic + offline schematic
  animate.py               figure/artists + ffmpeg pipe renderer
output/                    rendered videos
```

## Timeline in the video (all UTC)

* 17:11 -- visualization opens on the Mall approach (F-5s already airborne)
* 17:13:30 -- ~1,000 ft (raw baro) pass over the Washington Monument
* 17:14-17:16 -- 97-0400 descends toward Andrews from the north; CPA
  ~0.52 NM / ~416 ft at 17:15:09; 97-0400 descends to ~1,350 ft then
  climbs away to the east and holds ~1,900 ft
* 17:20-17:22 -- F-5s land at Andrews
* 17:22-17:56 -- 97-0400 holds east of Andrews (video plays at 70×)
* ~17:56 -- 97-0400 lands at Andrews
