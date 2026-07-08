"""
Find closest point of approach (horizontal + vertical) between two
ADS-B Exchange KML tracks (gx:Track format, uncorrected pressure altitude).

Assumptions (please check these against your own understanding):
- Each KML may contain multiple <Placemark><gx:Track> blocks (this is how
  ADSBExchange splits a track when there are gaps). We concatenate all
  points from all tracks in a file, sorted by time.
- <gx:coord> is "lon lat alt" per the KML gx:Track spec. Altitude here is
  the "uncorrected pressure altitude" you selected on export, in meters
  (KML's native unit), so this script converts to feet for display.
- We interpolate both tracks onto a common, regular time grid (default:
  1-second steps) spanning their overlapping time range, using linear
  interpolation. Linear interpolation of lat/lon over a few seconds is a
  reasonable approximation for aircraft in cruise/maneuvering flight, but
  it will smear over any sharp turns between fixes if update rate is low.
- Horizontal distance uses the haversine formula (great-circle distance),
  which is a fine approximation at these scales (no need for geodesic
  precision over a few nautical miles).
- "Vertical distance" is simply the difference between the two pressure
  altitude values at each interpolated timestep. Since both aircraft are
  presumably referencing the same altimeter setting convention (pressure
  alt, not corrected to local QNH), differences here are more meaningful
  for RELATIVE separation than either aircraft's absolute geometric height
  would be -- but this assumes both feeds report pressure altitude
  consistently, which I have not independently verified.
- GPS/ADS-B position error and altitude quantization error are NOT
  accounted for here. Treat the resulting minimum separation as a rough
  estimate, not a precise or authoritative value.
"""

import re
import math
import sys
from datetime import datetime, timezone

KML_A = "/mnt/user-data/uploads/97-0400-track-press_alt_uncorrected.kml"   # SAM963 / C-37A
KML_B = "/mnt/user-data/uploads/N591EM-track-press_alt_uncorrected.kml"   # GHOST1 lead F-5

# NOTE: the a7a18e KML you referenced is labeled N591EM in your earlier
# messages -- if that's not actually the GHOST1 lead jet, swap the file
# below. I have not independently confirmed which registration is the
# lead aircraft; I'm relying on what you told me.

TIME_STEP_SECONDS = 1.0


def parse_kml_track(path):
    """
    Parse all <Placemark> blocks containing a <gx:Track>, and return a
    single list of (datetime_utc, lon, lat, alt_m) tuples, sorted by time,
    deduplicated on identical consecutive timestamps.
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    points = []

    # Split into Placemark blocks so we can pair <when> with <gx:coord>
    # blocks that belong to the same gx:Track (avoids cross-contamination
    # between separate track segments in the same file).
    placemark_blocks = re.findall(r"<Placemark\b.*?</Placemark>", content, re.DOTALL)

    if not placemark_blocks:
        print(f"WARNING: no <Placemark> blocks found in {path}", file=sys.stderr)

    for block in placemark_blocks:
        track_match = re.search(r"<gx:Track\b.*?</gx:Track>", block, re.DOTALL)
        if not track_match:
            continue
        track = track_match.group(0)

        whens = re.findall(r"<when\s*>([^<]+)</when>", track)
        coords = re.findall(r"<gx:coord\s*>([^<]+)</gx:coord>", track)

        if len(whens) != len(coords):
            print(
                f"WARNING: mismatched <when>({len(whens)}) vs "
                f"<gx:coord>({len(coords)}) counts in a track block of {path}; "
                f"truncating to the shorter length.",
                file=sys.stderr,
            )
        n = min(len(whens), len(coords))

        for i in range(n):
            ts_str = whens[i].strip()
            # KML timestamps here look like 2026-07-04T16:30:46.430Z
            ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc
            )
            parts = coords[i].strip().split()
            lon, lat, alt = float(parts[0]), float(parts[1]), float(parts[2])
            points.append((ts, lon, lat, alt))

    points.sort(key=lambda p: p[0])

    # Drop exact duplicate timestamps (keep first occurrence) -- these
    # showed up in the raw data (e.g. the same <when> twice in a row).
    deduped = []
    seen_ts = set()
    for p in points:
        if p[0] in seen_ts:
            continue
        seen_ts.add(p[0])
        deduped.append(p)

    return deduped


def interpolate_track(points, query_times):
    """
    Linearly interpolate lon/lat/alt at each datetime in query_times.
    query_times must be sorted and within [points[0][0], points[-1][0]]
    or results outside that range will be skipped (returned as None).
    """
    times = [p[0] for p in points]
    results = []

    idx = 0
    n = len(points)

    for qt in query_times:
        if qt < times[0] or qt > times[-1]:
            results.append(None)
            continue

        # advance idx so that times[idx] <= qt <= times[idx+1]
        while idx + 1 < n and times[idx + 1] < qt:
            idx += 1

        t0, lon0, lat0, alt0 = points[idx]
        if idx + 1 >= n:
            results.append((lon0, lat0, alt0))
            continue

        t1, lon1, lat1, alt1 = points[idx + 1]

        if t1 == t0:
            frac = 0.0
        else:
            frac = (qt - t0).total_seconds() / (t1 - t0).total_seconds()
            frac = max(0.0, min(1.0, frac))

        lon = lon0 + frac * (lon1 - lon0)
        lat = lat0 + frac * (lat1 - lat0)
        alt = alt0 + frac * (alt1 - alt0)

        results.append((lon, lat, alt))

    return results


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in meters."""
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(a))


def main():
    pts_a = parse_kml_track(KML_A)
    pts_b = parse_kml_track(KML_B)

    print(f"Track A ({KML_A.split('/')[-1]}): {len(pts_a)} points, "
          f"{pts_a[0][0].isoformat()} to {pts_a[-1][0].isoformat()}")
    print(f"Track B ({KML_B.split('/')[-1]}): {len(pts_b)} points, "
          f"{pts_b[0][0].isoformat()} to {pts_b[-1][0].isoformat()}")

    start = max(pts_a[0][0], pts_b[0][0])
    end = min(pts_a[-1][0], pts_b[-1][0])

    if start >= end:
        print("ERROR: tracks do not overlap in time -- cannot compare.")
        return

    print(f"\nOverlapping window: {start.isoformat()} to {end.isoformat()} "
          f"({(end - start).total_seconds():.0f} s)\n")

    # Build common query grid
    query_times = []
    t = start
    step_delta = None
    from datetime import timedelta
    step_delta = timedelta(seconds=TIME_STEP_SECONDS)
    while t <= end:
        query_times.append(t)
        t += step_delta

    interp_a = interpolate_track(pts_a, query_times)
    interp_b = interpolate_track(pts_b, query_times)

    best = None  # (horiz_dist_m, vert_dist_m, time, a_point, b_point)

    for qt, a, b in zip(query_times, interp_a, interp_b):
        if a is None or b is None:
            continue
        lon_a, lat_a, alt_a = a
        lon_b, lat_b, alt_b = b

        horiz_m = haversine_m(lat_a, lon_a, lat_b, lon_b)
        vert_m = abs(alt_a - alt_b)

        # "closest approach" ranked by combined 3D distance (horizontal
        # and vertical treated as orthogonal components of one distance).
        dist_3d = math.hypot(horiz_m, vert_m)
        if best is None or dist_3d < best[0]:
            best = (dist_3d, horiz_m, vert_m, qt, (lon_a, lat_a, alt_a), (lon_b, lat_b, alt_b))

    if best is None:
        print("No overlapping, valid interpolated points found.")
        return

    dist_3d, horiz_m, vert_m, qt, pa, pb = best
    horiz_ft = horiz_m * 3.28084
    horiz_nm = horiz_m / 1852.0
    vert_ft = vert_m * 3.28084
    dist_3d_ft = dist_3d * 3.28084

    print("=== Closest 3D approach (interpolated, 1s grid) ===")
    print(f"Combined 3D separation: {dist_3d:.1f} m ({dist_3d_ft:.0f} ft)")
    print(f"Time (UTC): {qt.isoformat()}")
    print(f"Horizontal separation: {horiz_m:.1f} m  "
          f"({horiz_ft:.0f} ft, {horiz_nm:.3f} NM)")
    print(f"Vertical separation (pressure alt): {vert_m:.1f} m ({vert_ft:.0f} ft)")
    print(f"Aircraft A (97-0400) at this time: lon={pa[0]:.6f}, lat={pa[1]:.6f}, "
          f"alt={pa[2]:.1f} m ({pa[2]*3.28084:.0f} ft)")
    print(f"Aircraft B (N591EM) at this time: lon={pb[0]:.6f}, lat={pb[1]:.6f}, "
          f"alt={pb[2]:.1f} m ({pb[2]*3.28084:.0f} ft)")

    # Also report the point of minimum vertical separation, in case it
    # differs meaningfully from the point of minimum horizontal separation.
    best_vert = None
    for qt2, a, b in zip(query_times, interp_a, interp_b):
        if a is None or b is None:
            continue
        _, _, alt_a = a
        _, _, alt_b = b
        v = abs(alt_a - alt_b)
        if best_vert is None or v < best_vert[0]:
            best_vert = (v, qt2)

    if best_vert and best_vert[1] != qt:
        print(f"\n(Note: minimum vertical separation of "
              f"{best_vert[0]*3.28084:.0f} ft occurred at a different time, "
              f"{best_vert[1].isoformat()}, than minimum horizontal separation.)")


if __name__ == "__main__":
    main()
