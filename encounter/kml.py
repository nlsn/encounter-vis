"""Parse ADS-B Exchange KML exports (gx:Track format).

Handles multiple <Placemark><gx:Track> blocks per file (ADSBx splits
tracks at gaps), deduplicates identical consecutive timestamps, and
returns time-sorted points.

Altitude in <gx:coord> is whatever was selected at export time -- here,
UNCORRECTED PRESSURE ALTITUDE in meters (KML native unit). It is NOT
geometric height and NOT corrected to local QNH.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

_WHEN_RE = re.compile(r"<when\s*>([^<]+)</when>")
_COORD_RE = re.compile(r"<gx:coord\s*>([^<]+)</gx:coord>")
_PLACEMARK_RE = re.compile(r"<Placemark\b.*?</Placemark>", re.DOTALL)
_TRACK_RE = re.compile(r"<gx:Track\b.*?</gx:Track>", re.DOTALL)


@dataclass
class Track:
    """A single aircraft track on a uniform representation."""

    name: str                # e.g. registration
    t: np.ndarray            # epoch seconds (UTC), float64, sorted
    lon: np.ndarray          # degrees
    lat: np.ndarray          # degrees
    alt_m: np.ndarray        # uncorrected pressure altitude, meters

    @property
    def t0(self) -> float:
        return float(self.t[0])

    @property
    def t1(self) -> float:
        return float(self.t[-1])

    def sample(self, tq: np.ndarray):
        """Interpolate lon/lat/alt at epoch seconds tq.

        Uses cubic spline interpolation when there are enough data points
        (>=4); falls back to linear otherwise. Values outside the track's
        time span are returned as NaN so the caller can distinguish 'no
        data' from a real position.
        """
        if len(self.t) >= 4:
            if not hasattr(self, '_cs'):
                from scipy.interpolate import CubicSpline
                self._cs = (
                    CubicSpline(self.t, self.lon),
                    CubicSpline(self.t, self.lat),
                    CubicSpline(self.t, self.alt_m),
                )
            lon = self._cs[0](tq)
            lat = self._cs[1](tq)
            alt = self._cs[2](tq)
        else:
            lon = np.interp(tq, self.t, self.lon)
            lat = np.interp(tq, self.t, self.lat)
            alt = np.interp(tq, self.t, self.alt_m)
        mask = (tq < self.t[0]) | (tq > self.t[-1])
        lon[mask] = np.nan
        lat[mask] = np.nan
        alt[mask] = np.nan
        return lon, lat, alt


def _parse_when(s: str) -> float:
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    raise ValueError(f"Unrecognized KML timestamp: {s!r}")


def parse_kml_track(path: str, name: str) -> Track:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    rows = []
    for block in _PLACEMARK_RE.findall(content):
        track_match = _TRACK_RE.search(block)
        if not track_match:
            continue
        track = track_match.group(0)
        whens = _WHEN_RE.findall(track)
        coords = _COORD_RE.findall(track)
        n = min(len(whens), len(coords))
        for i in range(n):
            t = _parse_when(whens[i])
            parts = coords[i].split()
            rows.append((t, float(parts[0]), float(parts[1]), float(parts[2])))

    if not rows:
        raise ValueError(f"No gx:Track points found in {path}")

    rows.sort(key=lambda r: r[0])
    # Drop exact duplicate timestamps (keep first occurrence).
    dedup = []
    last_t = None
    for r in rows:
        if r[0] == last_t:
            continue
        last_t = r[0]
        dedup.append(r)

    arr = np.asarray(dedup, dtype=np.float64)
    return Track(name=name, t=arr[:, 0], lon=arr[:, 1], lat=arr[:, 2], alt_m=arr[:, 3])
