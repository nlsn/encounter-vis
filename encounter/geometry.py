"""Geodesy helpers: Web Mercator projection, haversine, CPA search.

Methodology matches the original closest_approach.py: both tracks are
linearly interpolated onto a common 1 s grid, horizontal separation is
haversine great-circle distance, vertical separation is the difference
of the two uncorrected pressure altitudes, and 'closest approach' is
ranked by the combined 3D distance sqrt(h^2 + v^2).
"""

from dataclasses import dataclass

import numpy as np

R_EARTH = 6371000.0
R_MERC = 6378137.0  # WGS84 semi-major axis, used by Web Mercator tiles
M_PER_FT = 0.3048
M_PER_NM = 1852.0


def merc_xy(lon_deg, lat_deg):
    """Web Mercator (EPSG:3857) in meters. Vectorized."""
    lon = np.radians(np.asarray(lon_deg, dtype=np.float64))
    lat = np.radians(np.asarray(lat_deg, dtype=np.float64))
    x = R_MERC * lon
    y = R_MERC * np.log(np.tan(np.pi / 4.0 + lat / 2.0))
    return x, y


def merc_scale(lat_deg: float) -> float:
    """Meters of Mercator-plane distance per meter of ground distance."""
    return 1.0 / np.cos(np.radians(lat_deg))


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in meters. Vectorized."""
    p1 = np.radians(np.asarray(lat1, dtype=np.float64))
    p2 = np.radians(np.asarray(lat2, dtype=np.float64))
    dphi = p2 - p1
    dlmb = np.radians(np.asarray(lon2, dtype=np.float64) - np.asarray(lon1, dtype=np.float64))
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * R_EARTH * np.arcsin(np.sqrt(a))


@dataclass
class CPA:
    other: str          # registration of the non-reference aircraft
    t: float            # epoch seconds of minimum 3D separation
    d3_m: float
    horiz_m: float
    vert_m: float
    ref_lonlatalt: tuple
    other_lonlatalt: tuple

    @property
    def horiz_nm(self):
        return self.horiz_m / M_PER_NM

    @property
    def vert_ft(self):
        return self.vert_m / M_PER_FT


def compute_cpa(ref, other, dt: float = 1.0) -> CPA:
    """CPA of `other` relative to `ref` over their overlapping window."""
    t0 = max(ref.t0, other.t0)
    t1 = min(ref.t1, other.t1)
    if t0 >= t1:
        raise ValueError(f"Tracks {ref.name} and {other.name} do not overlap in time")

    tq = np.arange(t0, t1, dt)
    rlon, rlat, ralt = ref.sample(tq)
    olon, olat, oalt = other.sample(tq)

    h = haversine_m(rlat, rlon, olat, olon)
    v = np.abs(ralt - oalt)
    d3 = np.hypot(h, v)

    i = int(np.nanargmin(d3))
    return CPA(
        other=other.name,
        t=float(tq[i]),
        d3_m=float(d3[i]),
        horiz_m=float(h[i]),
        vert_m=float(v[i]),
        ref_lonlatalt=(float(rlon[i]), float(rlat[i]), float(ralt[i])),
        other_lonlatalt=(float(olon[i]), float(olat[i]), float(oalt[i])),
    )


def separation_series(ref, other, tq: np.ndarray):
    """Horizontal (m) and vertical (m) separation on grid tq (NaN outside overlap)."""
    rlon, rlat, ralt = ref.sample(tq)
    olon, olat, oalt = other.sample(tq)
    h = haversine_m(rlat, rlon, olat, olon)
    v = np.abs(ralt - oalt)
    return h, v


def ground_speed_kt(track, tq: np.ndarray, smooth_s: int = 7):
    """Ground speed (kt) from central differences of interpolated positions."""
    lon, lat, _ = track.sample(tq)
    x, y = merc_xy(lon, lat)
    scale = merc_scale(np.nanmean(track.lat))
    dt = np.gradient(tq)
    vx = np.gradient(x, edge_order=1) / dt / scale
    vy = np.gradient(y, edge_order=1) / dt / scale
    spd = np.hypot(vx, vy) * 1.943844  # m/s -> kt
    if smooth_s > 1:
        k = np.ones(smooth_s) / smooth_s
        valid = np.isfinite(spd)
        tmp = np.where(valid, spd, 0.0)
        num = np.convolve(tmp, k, mode="same")
        den = np.convolve(valid.astype(float), k, mode="same")
        with np.errstate(invalid="ignore", divide="ignore"):
            spd = np.where(den > 0, num / den, np.nan)
    return spd


def heading_deg(track, tq: np.ndarray, min_step_m: float = 3.0):
    """Track heading (deg true, 0=N, CW) from position deltas; holds last
    heading when nearly stationary (taxi pauses)."""
    lon, lat, _ = track.sample(tq)
    x, y = merc_xy(lon, lat)
    hdg = np.full(tq.shape, np.nan)
    last = 0.0
    for i in range(len(tq)):
        j0 = max(0, i - 2)
        j1 = min(len(tq) - 1, i + 2)
        dx = x[j1] - x[j0]
        dy = y[j1] - y[j0]
        if np.isfinite(dx) and np.isfinite(dy) and np.hypot(dx, dy) > min_step_m:
            last = np.degrees(np.arctan2(dx, dy))  # 0 = north, CW positive
        hdg[i] = last
    return hdg
