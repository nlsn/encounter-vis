#!/usr/bin/env python3
"""Render the July 4, 2026 F-5 flyover / 97-0400 encounter animation.

Examples:
    # OSM basemap, muted (default, needs network on first run; tiles cached)
    python render_video.py

    # Satellite imagery basemap
    python render_video.py --basemap satellite -o output/encounter_satellite.mp4

    # No network required
    python render_video.py --basemap offline -o output/encounter_offline.mp4
"""

import argparse
import os
from datetime import datetime, timezone

from encounter.animate import Aircraft, EncounterAnimation

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def utc_arg(s):
    return datetime.strptime(s, "%H:%M:%S").replace(
        year=2026, month=7, day=4, tzinfo=timezone.utc).timestamp()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--basemap", choices=["satellite", "osm", "offline"],
                   default="osm",
                   help="osm = OpenStreetMap tiles, muted (default); "
                        "satellite = Esri World Imagery; offline = schematic, no network")
    p.add_argument("-o", "--output", default="output/encounter.mp4")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--start", type=utc_arg, default=utc_arg("17:11:00"),
                   help="UTC HH:MM:SS (default 17:11:00, Mall approach)")
    p.add_argument("--end", type=utc_arg, default=utc_arg("17:57:00"),
                   help="UTC HH:MM:SS (default 17:57:00, after 97-0400 landing)")
    p.add_argument("--tile-cache", default="basemap_cache")
    p.add_argument("--crf", type=int, default=18, help="x264 quality (lower = better)")
    p.add_argument("--frame-range", default=None, metavar="A:B",
                   help="render only frames [A, B) -- chunks can be concatenated "
                        "losslessly with ffmpeg's concat demuxer")
    args = p.parse_args()

    # F-5 colors equally spaced red -> yellow-orange by callsign (GHOST1..4).
    aircraft = [
        Aircraft("97-0400", "C-37A", "#6fd3ff",
                 os.path.join(DATA, "97-0400-track-press_alt_uncorrected.kml"),
                 callsign="SAM963", icao="AE010D", is_ref=True, marker_scale=1.35),
        Aircraft("N591EM", "F-5", "#ff3b3b",
                 os.path.join(DATA, "N591EM-track-press_alt_uncorrected.kml"),
                 callsign="GHOST1", icao="A7A18E"),
        Aircraft("N593EM", "F-5", "#ff7a2b",
                 os.path.join(DATA, "N593EM-track-press_alt_uncorrected.kml"),
                 callsign="GHOST2", icao="A7A8FC"),
        Aircraft("N595EM", "F-5", "#ffb020",
                 os.path.join(DATA, "N595EM-track-press_alt_uncorrected.kml"),
                 callsign="GHOST3", icao="A7B06A"),
        Aircraft("N592EM", "F-5", "#ffd84d",
                 os.path.join(DATA, "N592EM-track-press_alt_uncorrected.kml"),
                 callsign="GHOST4", icao="A7A545"),
    ]

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    anim = EncounterAnimation(
        aircraft, args.start, args.end, fps=args.fps,
        size=(args.width, args.height), basemap_mode=args.basemap,
        tile_cache=args.tile_cache)

    print("\nClosest approach to SAM963 / 97-0400 (3D minimum, 1 s interpolation grid):")
    for reg, c in anim.cpa.items():
        ts = datetime.fromtimestamp(c.t, tz=timezone.utc).strftime("%H:%M:%S")
        print(f"  {reg}: {c.horiz_nm:.2f} NM horizontal, {c.vert_ft:.0f} ft vertical "
              f"(3D {c.d3_m:.0f} m) at {ts}Z")
    print()

    fr = None
    if args.frame_range:
        a, b = args.frame_range.split(":")
        fr = (int(a), int(b))
    anim.render(args.output, crf=args.crf, frame_range=fr)


if __name__ == "__main__":
    main()
