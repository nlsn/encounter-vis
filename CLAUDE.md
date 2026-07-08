# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (ffmpeg must also be on PATH)
pip install -r requirements.txt

# Render with satellite imagery (network on first run; tiles cached in basemap_cache/)
python render_video.py --basemap satellite -o output/encounter_satellite.mp4

# Make targets
make video          # satellite basemap
make video-offline  # no network required
make video-osm      # OpenStreetMap tiles

# Render a subset of frames (for fast iteration; chunks can be joined losslessly)
python render_video.py --basemap offline --frame-range 0:300 -o output/test.mp4

# Print CPA statistics without rendering
python closest_approach.py
```

Key CLI flags: `--fps`, `--width/--height`, `--start/--end` (UTC `HH:MM:SS` on 2026-07-04), `--crf` (x264 quality, lower = better), `--tile-cache`.

## Architecture

The pipeline has three phases: **data → simulation grid → render loop**.

### Data layer (`encounter/kml.py`)
`parse_kml_track()` reads ADS-B Exchange `gx:Track` KML exports into a `Track` dataclass (arrays of epoch-seconds, lon, lat, alt_m). `Track.sample(tq)` linearly interpolates onto any time grid, returning NaN outside the track's span. Altitude is raw uncorrected pressure altitude in meters from the KML — not geometric height, not QNH-corrected.

### Simulation layer (`encounter/geometry.py`)
All geometry uses Web Mercator (EPSG:3857) for display and haversine for separation distances. `compute_cpa()` finds the closest point of approach between two tracks on a 1 s interpolation grid. `separation_series()` and `ground_speed_kt()` / `heading_deg()` produce per-frame arrays used by the renderer.

### Timeline (`encounter/timeline.py`)
`default_speed_profile()` defines variable playback speed (40× during taxi, 3× through the close-approach window). `build_frame_times()` converts this into per-frame sim-time arrays. `Camera` smoothly interpolates between `CameraKey` waypoints using smoothstep easing, with zoom interpolated in log-space.

### Render loop (`encounter/animate.py`)
`EncounterAnimation.__init__()` does all pre-computation and builds the matplotlib figure once. `_update_frame(n)` mutates only the existing artist data (trails, glyphs, labels, cursors) — no figure rebuild per frame. `render()` pipes raw RGBA from `canvas.buffer_rgba()` directly into an ffmpeg subprocess.

Frame layout: left ~65% is the Web Mercator map axes; right ~35% is a static panel containing a live readout table, altitude chart, separation chart, CPA summary, and disclaimer text.

### Basemap (`encounter/basemap.py`, `encounter/landmarks.py`)
Three modes: `satellite` (Esri World Imagery tiles), `osm` (OpenStreetMap tiles), `offline` (schematic dark basemap from hand-digitized polylines in `landmarks.py`). Tile modes fetch at zoom levels 11/13/14 into `basemap_cache/`, then `SatelliteBasemap.update()` switches between zoom-level mosaics per frame based on current camera width. Offline mode draws river ribbons (offset polygon from centerline + half-width), highway lines, and a DC boundary diamond — all approximate (±0.5–2 km).

### Reference script (`closest_approach.py`)
Standalone script that computes and prints CPA for all aircraft pairs. The methodology in `geometry.py` intentionally matches this script exactly — keep them consistent if modifying the CPA algorithm.

## Key constraints

- **Altitude caveat**: all altitudes are raw uncorrected pressure altitude (29.92 inHg). The disclaimer text in `animate.py` and the README must stay accurate if the data or computation changes.
- **Tile terms**: Esri and OSM tiles are for light personal use. The `--tile-cache` flag lets users reuse tiles across renders; don't fetch tiles unnecessarily.
- **ffmpeg dependency**: the renderer uses a raw RGBA pipe to ffmpeg. ffmpeg must be on PATH; `libx264` must be available in the build.
