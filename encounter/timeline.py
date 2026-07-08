"""Video timeline: variable time compression and camera keyframes.

The animation plays simulation (UTC) time at a variable speed: fast
through taxi/transit, slower through the Mall pass, near-real-time
through the closest-approach window. The camera smoothly interpolates
between framed views (center + width) with smoothstep easing.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from .geometry import merc_xy


def utc(h, m, s=0):
    return datetime(2026, 7, 4, h, m, s, tzinfo=timezone.utc).timestamp()


@dataclass
class SpeedSegment:
    t0: float      # sim epoch seconds
    t1: float
    speed: float   # sim seconds per video second


def default_speed_profile(t_start, t_end, t_cpa):
    """Medium through Mall approach, slow through the encounter, fast through
    the 97-0400 holding pattern to its landing."""
    slow0, slow1 = t_cpa - 55.0, t_cpa + 45.0
    # Clamp each segment start to t_start so non-default start times work cleanly.
    def clamp(t):
        return max(t_start, t)
    segs = [
        SpeedSegment(t_start,           clamp(utc(17, 10, 30)), 40.0),
        SpeedSegment(clamp(utc(17, 10, 30)), clamp(utc(17, 13, 55)), 12.0),
        SpeedSegment(clamp(utc(17, 13, 55)), clamp(slow0), 8.0),
        SpeedSegment(clamp(slow0),       slow1, 3.0),
        SpeedSegment(slow1,              utc(17, 22, 30), 30.0),
        SpeedSegment(utc(17, 22, 30),    utc(17, 50, 0),  90.0),  # holding loops
        SpeedSegment(utc(17, 50, 0),     t_end,           50.0),  # final approach + landing
    ]
    return [s for s in segs if s.t1 > s.t0]


def build_frame_times(segments, fps, intro_hold_s=2.0, outro_hold_s=5.0):
    """Return (sim_times, speeds) arrays, one entry per video frame."""
    sim_times = []
    speeds = []
    n_intro = int(round(intro_hold_s * fps))
    sim_times.extend([segments[0].t0] * n_intro)
    speeds.extend([0.0] * n_intro)

    for seg in segments:
        t = seg.t0
        while t < seg.t1:
            sim_times.append(t)
            speeds.append(seg.speed)
            t += seg.speed / fps

    n_outro = int(round(outro_hold_s * fps))
    sim_times.extend([segments[-1].t1] * n_outro)
    speeds.extend([0.0] * n_outro)
    return np.asarray(sim_times), np.asarray(speeds)


@dataclass
class CameraKey:
    t: float               # sim epoch seconds at which this view is fully reached
    center_lat: float
    center_lon: float
    width_m: float         # ground meters visible across the map axes


def default_camera_keys(t_start, t_end, t_cpa, cpa_lat, cpa_lon):
    """Wide intro -> Mall approach -> tight on encounter -> medium -> hold -> touchdown."""
    # At 17:11:00 SAM963 is at (~39.09, -76.96) and F-5s are at (~38.89, -77.31),
    # both outside the mall view. intro is wide enough to contain both.
    intro  = (38.97, -77.115, 45000.0)
    mall   = (38.878, -76.995, 30000.0)
    tight  = (cpa_lat - 0.004, cpa_lon + 0.002, 20000.0)  # wider: keeps 5-ship in frame
    medium = (38.870, -76.930, 42000.0)
    # SAM963 holds NW of Andrews (~39.09, -77.15); hold view covers both.
    hold     = (38.95, -77.02, 70000.0)
    approach = (38.88, -76.92, 45000.0)  # tighter for final approach corridor
    keys = [
        CameraKey(t_start,              *intro),
        CameraKey(utc(17, 12, 0),       *intro),
        CameraKey(utc(17, 12, 30),      *mall),
        CameraKey(utc(17, 13, 55),      *mall),
        CameraKey(t_cpa - 55.0,        *tight),
        CameraKey(t_cpa + 60.0,        *tight),
        CameraKey(utc(17, 18, 0),      *medium),
        CameraKey(utc(17, 22, 30),     *medium),
        CameraKey(utc(17, 23, 30),     *hold),
        CameraKey(utc(17, 50, 0),      *hold),
        CameraKey(utc(17, 52, 0),      *approach),
        CameraKey(t_end,               38.81, -76.87, 20000.0),
    ]
    # Drop keys before t_start (handles non-default start times).
    keys = [k for k in keys if k.t >= t_start]
    if not keys or keys[0].t > t_start:
        keys.insert(0, CameraKey(t_start, *mall))
    return keys


def _smoothstep(u):
    u = np.clip(u, 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


class Camera:
    def __init__(self, keys, aspect):
        """aspect = map axes height / width (pixels)."""
        self.keys = sorted(keys, key=lambda k: k.t)
        self.aspect = aspect

    def view(self, t_sim):
        """Return (x0, x1, y0, y1) Web Mercator view bounds and ground width."""
        ks = self.keys
        if t_sim <= ks[0].t:
            k = ks[0]
            lat, lon, w = k.center_lat, k.center_lon, k.width_m
        elif t_sim >= ks[-1].t:
            k = ks[-1]
            lat, lon, w = k.center_lat, k.center_lon, k.width_m
        else:
            for i in range(len(ks) - 1):
                if ks[i].t <= t_sim <= ks[i + 1].t:
                    a, b = ks[i], ks[i + 1]
                    u = _smoothstep((t_sim - a.t) / max(b.t - a.t, 1e-9))
                    lat = a.center_lat + u * (b.center_lat - a.center_lat)
                    lon = a.center_lon + u * (b.center_lon - a.center_lon)
                    # interpolate width in log space for a natural zoom feel
                    w = float(np.exp(np.log(a.width_m) + u * (np.log(b.width_m) - np.log(a.width_m))))
                    break

        cx, cy = merc_xy(lon, lat)
        scale = 1.0 / np.cos(np.radians(lat))   # ground m -> mercator m
        half_w = 0.5 * w * scale
        half_h = half_w * self.aspect
        return (float(cx - half_w), float(cx + half_w),
                float(cy - half_h), float(cy + half_h)), w
