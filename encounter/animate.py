"""Render the encounter animation to an MP4 via ffmpeg.

Frame layout (16:9):
  left  ~65%: Web Mercator map (basemap + trajectories + live labels)
  right ~35%: live readout table, altitude chart, separation chart,
              CPA summary, disclaimers

All altitudes shown are RAW UNCORRECTED PRESSURE ALTITUDE from the
ADS-B Exchange export (29.92 inHg reference), not geometric height and
not corrected to local QNH. ADS-B position/altitude error is not
modeled; separations are estimates.
"""

import subprocess
from datetime import datetime, timezone, timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon as MplPolygon

from .geometry import (M_PER_FT, M_PER_NM, compute_cpa, ground_speed_kt,
                       haversine_m, heading_deg, merc_xy, separation_series)
from .kml import parse_kml_track
from .timeline import (Camera, build_frame_times, default_camera_keys,
                       default_speed_profile)
from .basemap import make_basemap
from . import landmarks as lm

BG = "#0b0f14"
FG = "#e8eef5"
DIM = "#8fa1b3"
GRID = "#22303d"

EDT = timezone(timedelta(hours=-4), "EDT")

DISCLAIMER = ("Positions and altitudes from ADS-B Exchange KML exports; altitude is raw uncorrected pressure altitude "
              "(29.92 inHg reference), not geometric height. Positions/altitudes may be inaccurate; separations are estimates. "
              "Not an official reconstruction.")


class Aircraft:
    def __init__(self, reg, actype, color, path, callsign="", icao="",
                 is_ref=False, marker_scale=1.0):
        self.reg = reg
        self.callsign = callsign
        self.icao = icao
        self.actype = actype
        self.color = color
        self.is_ref = is_ref
        self.marker_scale = marker_scale
        self.track = parse_kml_track(path, reg)

    def precompute(self, tq):
        lon, lat, alt = self.track.sample(tq)
        self.x, self.y = merc_xy(lon, lat)
        self.alt_ft = alt / M_PER_FT
        self.gs_kt = ground_speed_kt(self.track, tq)
        self.hdg = heading_deg(self.track, tq)
        # Precompute Mercator positions of raw KML fixes for trail-dot overlay.
        self.track_x, self.track_y = merc_xy(self.track.lon, self.track.lat)


def _fmt_clock(t):
    du = datetime.fromtimestamp(t, tz=timezone.utc)
    dl = du.astimezone(EDT)
    return f"{du:%Y-%m-%d %H:%M:%S}Z   ({dl:%H:%M:%S} EDT)"


def _triangle(x, y, hdg_deg, size):
    """Aircraft glyph: arrowhead pointing along heading. Returns Nx2 verts."""
    shape = np.array([[0.0, 1.5], [-0.85, -1.0], [0.0, -0.45], [0.85, -1.0]]) * size
    h = np.radians(hdg_deg)
    c, s = np.cos(h), np.sin(h)
    # Clockwise-from-north rotation under v = shape @ rot: heading 90° (east)
    # maps +y (north-pointing tip) to +x (east). The prior [[c,s],[-s,c]] form
    # mirrored east/west by flipping the sign of the off-diagonals.
    rot = np.array([[c, -s], [s, c]])
    v = shape @ rot
    v[:, 0] += x
    v[:, 1] += y
    return v


class EncounterAnimation:
    DT = 0.5  # simulation grid step, seconds

    def __init__(self, aircraft, t_start, t_end, fps=30, size=(1920, 1080),
                 basemap_mode="offline", tile_cache="basemap_cache"):
        self.aircraft = aircraft
        self.ref = next(a for a in aircraft if a.is_ref)
        self.others = [a for a in aircraft if not a.is_ref]
        self.fps = fps
        self.size = size
        self.t0, self.t1 = t_start, t_end

        # Simulation grid and per-aircraft precomputation
        self.tq = np.arange(t_start, t_end + self.DT, self.DT)
        for a in aircraft:
            a.precompute(self.tq)

        # Separations to the reference aircraft
        self.sep_h = {}
        self.sep_v = {}
        self.cpa = {}
        for a in self.others:
            h, v = separation_series(self.ref.track, a.track, self.tq)
            self.sep_h[a.reg] = h
            self.sep_v[a.reg] = v
            self.cpa[a.reg] = compute_cpa(self.ref.track, a.track)
        self.cpa_min = min(self.cpa.values(), key=lambda c: c.d3_m)

        # Timeline (speed profile + camera)
        segs = default_speed_profile(t_start, t_end, self.cpa_min.t)
        self.frame_t, self.frame_speed = build_frame_times(segs, fps)
        map_w_frac = 0.648
        aspect = (size[1] * 1.0) / (size[0] * map_w_frac)
        self.map_w_frac = map_w_frac
        keys = default_camera_keys(
            t_start, t_end, self.cpa_min.t,
            self.cpa_min.ref_lonlatalt[1], self.cpa_min.ref_lonlatalt[0])
        self.camera = Camera(keys, aspect)

        # Basemap coverage boxes (west, south, east, north) with margin
        self.basemap = make_basemap(
            basemap_mode,
            bbox_wide=(-77.48, 38.50, -76.62, 39.10),
            bbox_tight=(-77.00, 38.78, -76.76, 38.98),
            cache_dir=tile_cache,
        )

        self._build_figure()

    # ------------------------------------------------------------------
    def _idx(self, t):
        i = int(round((t - self.t0) / self.DT))
        return max(0, min(len(self.tq) - 1, i))

    def _build_figure(self):
        w, h = self.size
        self.fig = plt.figure(figsize=(w / 100, h / 100), dpi=100)
        self.fig.patch.set_facecolor(BG)

        # ---- map axes ----
        ax = self.fig.add_axes([0.0, 0.0, self.map_w_frac, 1.0])
        self.ax_map = ax
        ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.set_facecolor("#10161d")
        self.basemap.install(ax)

        # landmarks (labels for the dense downtown monuments only appear
        # once zoomed in, to avoid a label pile-up at wide view)
        self.landmark_labels = []
        for name, lat, lon, kind in lm.LANDMARKS:
            x, y = merc_xy(lon, lat)
            mk = {"airport": "s", "monument": "^", "building": "D"}[kind]
            ax.plot([x], [y], marker=mk, ms=5, mfc="none", mec="#c9d6e2",
                    mew=1.1, zorder=3)
            txt = ax.annotate(name, (x, y), xytext=(6, 5),
                              textcoords="offset points", fontsize=8.5,
                              color="#c9d6e2", zorder=3,
                              path_effects=self._halo())
            max_w = 1e12 if kind == "airport" else 36000.0
            self.landmark_labels.append((txt, max_w))

        # reference-aircraft runway proxy: its on-ground points at ADW
        gnd = self.ref.track.alt_m < 20
        if gnd.any():
            gx, gy = merc_xy(self.ref.track.lon[gnd], self.ref.track.lat[gnd])
            ax.plot(gx, gy, color="#5a6a7a", lw=3.0, alpha=0.55, zorder=1,
                    solid_capstyle="round")

        # per-aircraft artists; label offsets fan out so a tight formation
        # doesn't stack its labels on top of each other
        label_offsets = {"97-0400": (12, 10), "N591EM": (12, 14),
                         "N593EM": (-72, 14), "N595EM": (-72, -14),
                         "N592EM": (12, -30)}
        for a in self.aircraft:
            a.trail_full, = ax.plot([], [], color=a.color, lw=1.1, alpha=0.35, zorder=4)
            a.trail_hot, = ax.plot([], [], color=a.color, lw=2.2, alpha=0.95, zorder=5)
            a.trail_dots, = ax.plot([], [], marker=".", ms=2.5, linestyle="none",
                                    color=a.color, alpha=0.55, zorder=4.5)
            a.glyph = MplPolygon(np.zeros((4, 2)), closed=True, facecolor=a.color,
                                 edgecolor="black", lw=0.6, zorder=7)
            ax.add_patch(a.glyph)
            a.label = ax.annotate("", (0, 0),
                                  xytext=label_offsets.get(a.reg, (10, 8)),
                                  textcoords="offset points", fontsize=9.5,
                                  color=a.color, fontweight="bold", zorder=8,
                                  path_effects=self._halo())

        # range line ref <-> nearest F-5
        self.range_line, = ax.plot([], [], color="#ffffff", lw=1.0, ls=(0, (4, 3)),
                                   alpha=0.85, zorder=6)
        self.range_text = ax.annotate("", (0, 0), fontsize=10, color="#ffffff",
                                      ha="center", zorder=8, fontweight="bold",
                                      path_effects=self._halo())

        # CPA marker (appears once passed)
        cx, cy = merc_xy(
            0.5 * (self.cpa_min.ref_lonlatalt[0] + self.cpa_min.other_lonlatalt[0]),
            0.5 * (self.cpa_min.ref_lonlatalt[1] + self.cpa_min.other_lonlatalt[1]))
        self.cpa_ring, = ax.plot([cx], [cy], marker="o", ms=16, mfc="none",
                                 mec="#ff3b3b", mew=2.0, zorder=6, visible=False)
        c = self.cpa_min
        tstr = datetime.fromtimestamp(c.t, tz=timezone.utc).strftime("%H:%M:%SZ")
        self.cpa_note = ax.annotate(
            f"CPA {self.ref.reg}\u2013{c.other}\n{c.horiz_nm:.2f} NM H / {c.vert_ft:.0f} ft V\n{tstr}",
            (cx, cy), xytext=(14, -34), textcoords="offset points", fontsize=9.5,
            color="#ff8a8a", zorder=8, visible=False, path_effects=self._halo())

        # overlays on the map
        self.clock_text = ax.text(0.015, 0.975, "", transform=ax.transAxes,
                                  fontsize=13, color=FG, va="top",
                                  family="DejaVu Sans Mono", zorder=10,
                                  path_effects=self._halo())
        self.speed_text = ax.text(0.015, 0.938, "", transform=ax.transAxes,
                                  fontsize=10.5, color=DIM, va="top", zorder=10,
                                  path_effects=self._halo())
        ax.text(0.008, 0.008, self.basemap.attribution, transform=ax.transAxes,
                fontsize=7, color=DIM, zorder=10, path_effects=self._halo())

        # title overlay for intro (bottom of map, semi-transparent dark box
        # instead of heavy halo so white text stays legible)
        self.title_overlay = ax.text(
            0.5, 0.06,
            "July 4, 2026 — Washington, DC\nF-5 flyover and 97-0400 arrival at Andrews",
            transform=ax.transAxes, fontsize=20, color=FG, ha="center", va="bottom",
            fontweight="bold", zorder=11,
            bbox=dict(facecolor="#0b0f14", alpha=0.65, edgecolor="none", pad=8))

        # ---- right panel ----
        fx = 1 - (1 - self.map_w_frac) + 0.012  # left edge of panel content
        self.fig.text(fx, 0.965, "F-5 flyover / 97-0400 encounter", fontsize=15,
                      color=FG, fontweight="bold")
        self.fig.text(fx, 0.935, "July 4, 2026 — ADS-B Exchange tracks", fontsize=10,
                      color=DIM)

        # readout table
        hdr = (f"{'CALLSIGN':<10}{'REG':<9}{'ICAO':<8}"
               f"{'TYPE':<6}{'ALT ft*':>7}{'GS kt':>7}{'DIST NM':>8}")
        self.fig.text(fx, 0.895, hdr, fontsize=10, color=DIM,
                      family="DejaVu Sans Mono")
        self.readout_rows = []
        for i, a in enumerate(self.aircraft):
            t = self.fig.text(fx, 0.868 - 0.026 * i, "", fontsize=10,
                              color=a.color, family="DejaVu Sans Mono")
            self.readout_rows.append(t)
        self.fig.text(fx, 0.868 - 0.026 * len(self.aircraft) - 0.004,
                      "*raw uncorrected pressure altitude   DIST = to 97-0400",
                      fontsize=8, color=DIM)

        # altitude chart
        axa = self.fig.add_axes([fx + 0.035, 0.485, 0.985 - fx - 0.04, 0.20])
        self.ax_alt = axa
        self._style_chart(axa, "Altitude — raw pressure alt (ft)")
        tmin = (self.tq - self.t0) / 60.0
        for a in self.aircraft:
            axa.plot(tmin, a.alt_ft, color=a.color, lw=1.2)
        axa.set_ylim(-150, 6200)
        axa.set_xlim(0, tmin[-1])
        self.alt_cursor = axa.axvline(0, color=FG, lw=0.9, alpha=0.7)
        self.alt_dots = [axa.plot([], [], "o", ms=4, color=a.color)[0]
                         for a in self.aircraft]
        self._time_ticks(axa)

        # separation chart
        axs = self.fig.add_axes([fx + 0.035, 0.195, 0.985 - fx - 0.04, 0.20])
        self.ax_sep = axs
        self._style_chart(axs, "Horizontal separation from 97-0400 (NM)")
        for a in self.others:
            axs.plot(tmin, self.sep_h[a.reg] / M_PER_NM, color=a.color, lw=1.2)
            c = self.cpa[a.reg]
            axs.plot([(c.t - self.t0) / 60.0], [c.horiz_m / M_PER_NM], "o",
                     ms=4, color=a.color)
        axs.set_ylim(0, 12)
        axs.set_xlim(0, tmin[-1])
        c = self.cpa_min
        axs.annotate(
            f"min {c.horiz_nm:.2f} NM / {c.vert_ft:.0f} ft vert",
            ((c.t - self.t0) / 60.0, c.horiz_m / M_PER_NM), xytext=(8, 14),
            textcoords="offset points", fontsize=8.5, color="#ff8a8a")
        self.sep_cursor = axs.axvline(0, color=FG, lw=0.9, alpha=0.7)
        self._time_ticks(axs)

        # CPA summary block (static)
        lines = ["Closest approach to SAM963 (3D min, 1 s grid):"]
        for a in self.others:
            c = self.cpa[a.reg]
            ts = datetime.fromtimestamp(c.t, tz=timezone.utc).strftime("%H:%M:%SZ")
            lines.append(f"  {a.callsign} {a.reg}: {c.horiz_nm:.2f} NM / "
                         f"{c.vert_ft:.0f} ft vert @ {ts}")
        self.fig.text(fx, 0.135, "\n".join(lines), fontsize=9.5, color=FG,
                      family="DejaVu Sans Mono", va="top")

        # disclaimer
        self.fig.text(fx, 0.052, DISCLAIMER, fontsize=7.4, color=DIM, va="top",
                      wrap=True)
        # matplotlib wrap needs a clip width; do it manually instead:
        self.fig.texts[-1].set_text(self._wrap(DISCLAIMER, 74))

    @staticmethod
    def _wrap(s, width):
        import textwrap
        return "\n".join(textwrap.wrap(s, width))

    @staticmethod
    def _halo():
        import matplotlib.patheffects as pe
        return [pe.withStroke(linewidth=2.4, foreground="#000000", alpha=0.85)]

    def _style_chart(self, ax, title):
        ax.set_facecolor("#0f151c")
        ax.set_title(title, fontsize=9.5, color=FG, loc="left", pad=4)
        ax.tick_params(colors=DIM, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(GRID)
        ax.grid(color=GRID, lw=0.5, alpha=0.7)

    def _time_ticks(self, ax):
        span_min = (self.t1 - self.t0) / 60.0
        first_tick = np.ceil(self.t0 / 300.0) * 300.0
        ticks, labels = [], []
        t = first_tick
        while t <= self.t1:
            ticks.append((t - self.t0) / 60.0)
            labels.append(datetime.fromtimestamp(t, tz=timezone.utc).strftime("%H:%M"))
            t += 300.0
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels)
        ax.set_xlim(0, span_min)

    # ------------------------------------------------------------------
    def _update_frame(self, frame_no):
        t = self.frame_t[frame_no]
        speed = self.frame_speed[frame_no]
        i = self._idx(t)
        (x0, x1, y0, y1), view_w = self.camera.view(t)
        self.ax_map.set_xlim(x0, x1)
        self.ax_map.set_ylim(y0, y1)
        self.basemap.update(view_w)
        for txt, max_w in self.landmark_labels:
            txt.set_visible(view_w <= max_w)
        glyph_size = view_w / np.cos(np.radians(38.85)) * 0.011

        # aircraft
        for a in self.aircraft:
            step = 4
            a.trail_full.set_data(a.x[:i + 1:step], a.y[:i + 1:step])
            j = max(0, i - int(60 / self.DT))
            a.trail_hot.set_data(a.x[j:i + 1], a.y[j:i + 1])
            # Raw ADS-B fix dots (show positions of actual data updates)
            dot_mask = (a.track.t >= self.t0) & (a.track.t <= t)
            a.trail_dots.set_data(a.track_x[dot_mask], a.track_y[dot_mask])
            if np.isfinite(a.x[i]):
                a.glyph.set_visible(True)
                a.label.set_visible(True)
                a.glyph.set_xy(_triangle(a.x[i], a.y[i], a.hdg[i],
                                         glyph_size * a.marker_scale))
                a.label.xy = (a.x[i], a.y[i])
                alt = a.alt_ft[i]
                gs = a.gs_kt[i]
                if alt < 80 and (not np.isfinite(gs) or gs < 60):
                    alt_s = "gnd"
                else:
                    alt_s = f"{alt:,.0f} ft"
                a.label.set_text(f"{a.callsign}\n{alt_s}")
            else:
                a.glyph.set_visible(False)
                a.label.set_visible(False)

        # range line to nearest F-5
        best = None
        for a in self.others:
            hsep = self.sep_h[a.reg][i]
            if np.isfinite(hsep) and (best is None or hsep < best[0]):
                best = (hsep, a)
        show = best is not None and best[0] < 8000 and np.isfinite(self.ref.x[i])
        self.range_line.set_visible(show)
        self.range_text.set_visible(show)
        if show:
            hsep, a = best
            self.range_line.set_data([self.ref.x[i], a.x[i]], [self.ref.y[i], a.y[i]])
            mx, my = 0.5 * (self.ref.x[i] + a.x[i]), 0.5 * (self.ref.y[i] + a.y[i])
            self.range_text.xy = (mx, my)
            self.range_text.set_position((mx, my + 0.03 * (y1 - y0)))
            self.range_text.set_text(f"{hsep / M_PER_NM:.2f} NM")

        # CPA marker after the fact
        passed = t >= self.cpa_min.t
        self.cpa_ring.set_visible(passed)
        self.cpa_note.set_visible(passed)

        # readouts
        self.clock_text.set_text(_fmt_clock(t))
        self.speed_text.set_text("paused" if speed == 0 else f"playback speed {speed:g}×")
        for row, a in zip(self.readout_rows, self.aircraft):
            alt = a.alt_ft[i]
            gs = a.gs_kt[i]
            pfx = f"{a.callsign:<10}{a.reg:<9}{a.icao:<8}{a.actype:<6}"
            if not np.isfinite(alt):
                row.set_text(pfx + f"{'—':>7}{'—':>7}{'—':>8}")
                continue
            alt_s = "gnd" if (alt < 80 and (not np.isfinite(gs) or gs < 60)) else f"{alt:,.0f}"
            gs_s = f"{gs:.0f}" if np.isfinite(gs) else "—"
            if a.is_ref:
                d_s = "—"
            else:
                hsep = self.sep_h[a.reg][i]
                d_s = f"{hsep / M_PER_NM:.2f}" if np.isfinite(hsep) else "—"
            row.set_text(pfx + f"{alt_s:>7}{gs_s:>7}{d_s:>8}")

        # chart cursors
        tm = (t - self.t0) / 60.0
        self.alt_cursor.set_xdata([tm, tm])
        self.sep_cursor.set_xdata([tm, tm])
        for dot, a in zip(self.alt_dots, self.aircraft):
            if np.isfinite(a.alt_ft[i]):
                dot.set_data([tm], [a.alt_ft[i]])
            else:
                dot.set_data([], [])

        # intro title fade
        intro_frames = int(2.0 * self.fps)
        fade_frames = int(1.2 * self.fps)
        if frame_no < intro_frames:
            self.title_overlay.set_alpha(1.0)
        elif frame_no < intro_frames + fade_frames:
            self.title_overlay.set_alpha(1.0 - (frame_no - intro_frames) / fade_frames)
        else:
            self.title_overlay.set_alpha(0.0)

    # ------------------------------------------------------------------
    def render(self, out_path, crf=18, preset="medium", frame_range=None):
        """Render all frames, or frames [a, b) if frame_range is given.

        Chunks rendered with identical settings can be joined losslessly:
            ffmpeg -f concat -safe 0 -i list.txt -c copy full.mp4
        """
        w, h = self.size
        f0, f1 = frame_range if frame_range else (0, len(self.frame_t))
        f1 = min(f1, len(self.frame_t))
        n = f1 - f0
        print(f"Rendering frames [{f0}, {f1}) of {len(self.frame_t)} at {w}x{h} "
              f"@ {self.fps} fps ({n / self.fps:.0f} s of video)", flush=True)
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{w}x{h}",
            "-r", str(self.fps), "-i", "-",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", str(crf), "-preset", preset,
            "-movflags", "+faststart",
            out_path,
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        canvas = self.fig.canvas
        import time as _time
        t_start = _time.time()
        for k, f in enumerate(range(f0, f1)):
            self._update_frame(f)
            canvas.draw()
            proc.stdin.write(canvas.buffer_rgba())
            if k and k % 250 == 0:
                rate = k / (_time.time() - t_start)
                eta = (n - k) / rate
                print(f"  frame {k}/{n}  ({rate:.1f} fps, ~{eta:.0f} s left)",
                      flush=True)
        proc.stdin.close()
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg failed")
        print(f"Wrote {out_path}")
