"""Basemap layers for the animation.

Two modes:

* "satellite" / "osm": downloads XYZ raster tiles, caches them on disk,
  builds one mosaic per zoom level, and displays the mosaic that best
  matches the current camera width. Requires network access on first
  run; subsequent runs use the cache. Tile usage is subject to the
  provider's terms; attribution is drawn on the frame.

* "offline": no network needed. Draws a dark schematic basemap (land,
  approximate river ribbons, approximate highway lines, DC boundary
  diamond). Schematic line work is approximate (~0.5-2 km) and labeled
  as such; see landmarks.py.
"""

import math
import os
import time

import numpy as np

from .geometry import R_MERC, merc_xy
from . import landmarks as lm

TILE_SIZE = 256
WORLD_M = 2 * math.pi * R_MERC  # Web Mercator world width in meters

PROVIDERS = {
    "satellite": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/"
               "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Imagery: Esri World Imagery — Esri, Maxar, Earthstar Geographics, GIS User Community",
        "ext": "jpg",
    },
    "osm": {
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "Map data © OpenStreetMap contributors",
        "ext": "png",
    },
}


def _lonlat_to_tile(lon, lat, z):
    n = 2 ** z
    xt = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    yt = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return xt, yt


def _tile_bounds_merc(x, y, z):
    """Web Mercator bounds (x0, x1, y0, y1) of tile (x, y, z)."""
    n = 2 ** z
    x0 = x / n * WORLD_M - WORLD_M / 2
    x1 = (x + 1) / n * WORLD_M - WORLD_M / 2
    y1 = WORLD_M / 2 - y / n * WORLD_M
    y0 = WORLD_M / 2 - (y + 1) / n * WORLD_M
    return x0, x1, y0, y1


class TileMosaic:
    """A single-zoom-level mosaic covering a lon/lat bounding box."""

    def __init__(self, provider: str, zoom: int, bbox_lonlat, cache_dir: str):
        import requests  # local import: offline mode must not require it
        from PIL import Image
        import io

        self.provider = PROVIDERS[provider]
        self.zoom = zoom
        lon0, lat0, lon1, lat1 = bbox_lonlat  # west, south, east, north

        xt0, yt1 = _lonlat_to_tile(lon0, lat0, zoom)  # note: y grows southward
        xt1, yt0 = _lonlat_to_tile(lon1, lat1, zoom)
        self.tx0, self.tx1 = int(math.floor(xt0)), int(math.floor(xt1))
        self.ty0, self.ty1 = int(math.floor(yt0)), int(math.floor(yt1))

        nx = self.tx1 - self.tx0 + 1
        ny = self.ty1 - self.ty0 + 1
        if nx * ny > 900:
            raise ValueError(
                f"Refusing to fetch {nx*ny} tiles at z{zoom}; shrink the bbox or zoom."
            )

        os.makedirs(cache_dir, exist_ok=True)
        img = np.zeros((ny * TILE_SIZE, nx * TILE_SIZE, 3), dtype=np.uint8)
        session = requests.Session()
        session.headers["User-Agent"] = "f5-flyover-encounter/1.0 (trajectory visualization)"

        n_fetch = 0
        for iy, ty in enumerate(range(self.ty0, self.ty1 + 1)):
            for ix, tx in enumerate(range(self.tx0, self.tx1 + 1)):
                fname = os.path.join(
                    cache_dir, f"{provider}_z{zoom}_x{tx}_y{ty}.{self.provider['ext']}"
                )
                if not os.path.exists(fname):
                    url = self.provider["url"].format(z=zoom, x=tx, y=ty)
                    for attempt in range(3):
                        try:
                            r = session.get(url, timeout=20)
                            r.raise_for_status()
                            with open(fname, "wb") as f:
                                f.write(r.content)
                            n_fetch += 1
                            time.sleep(0.05)  # be polite to the tile server
                            break
                        except Exception as e:
                            if attempt == 2:
                                raise RuntimeError(f"Tile fetch failed: {url} ({e})")
                            time.sleep(1.5 * (attempt + 1))
                tile = Image.open(fname).convert("RGB")
                img[iy * TILE_SIZE:(iy + 1) * TILE_SIZE,
                    ix * TILE_SIZE:(ix + 1) * TILE_SIZE] = np.asarray(tile)

        if n_fetch:
            print(f"  fetched {n_fetch} new tiles at z{zoom} (rest from cache)")

        x0, _, _, _ = _tile_bounds_merc(self.tx0, self.ty0, zoom)
        _, x1, _, _ = _tile_bounds_merc(self.tx1, self.ty0, zoom)
        _, _, _, y1 = _tile_bounds_merc(self.tx0, self.ty0, zoom)
        _, _, y0, _ = _tile_bounds_merc(self.tx0, self.ty1, zoom)
        self.extent = (x0, x1, y0, y1)  # for imshow(origin='upper')
        self.image = img


class SatelliteBasemap:
    """Multi-zoom tile basemap. Chooses mosaic per camera width."""

    # (max_view_width_m, zoom)
    ZOOM_LADDER = [(1.0e9, 11), (26000.0, 13), (15000.0, 14)]

    def __init__(self, provider, bbox_wide, bbox_tight, cache_dir):
        self.attribution = PROVIDERS[provider]["attribution"]
        self.mosaics = []
        print(f"Building {provider} basemap mosaics (cache: {cache_dir})")
        for max_w, z in self.ZOOM_LADDER:
            bbox = bbox_wide if z <= 12 else bbox_tight
            self.mosaics.append((max_w, TileMosaic(provider, z, bbox, cache_dir)))
        # Desaturate OSM tiles so aircraft/trails stand out over the map detail.
        if provider == "osm":
            for _, mosaic in self.mosaics:
                img = mosaic.image.astype(np.float32)
                gray = img.mean(axis=2, keepdims=True)
                mosaic.image = (gray * 0.80 + img * 0.20).clip(0, 255).astype(np.uint8)
        self._overlay_alpha = 0.45 if provider == "osm" else 0.28
        self._artists = []

    def install(self, ax):
        """Add one imshow per mosaic; visibility toggled per frame."""
        self._artists = []
        for _, mosaic in self.mosaics:
            im = ax.imshow(
                mosaic.image,
                extent=mosaic.extent,
                origin="upper",
                interpolation="bilinear",
                zorder=0,
                visible=False,
            )
            self._artists.append(im)
        # Darkening overlay for label/aircraft contrast.
        x0, x1, y0, y1 = self.mosaics[0][1].extent
        ax.imshow(
            np.zeros((2, 2, 4), dtype=np.float32) + np.array([0, 0, 0, self._overlay_alpha], dtype=np.float32),
            extent=(x0, x1, y0, y1), origin="upper", zorder=0.5,
        )

    def update(self, view_width_m):
        best = 0
        for i, (max_w, _) in enumerate(self.mosaics):
            if view_width_m <= max_w:
                best = i
        for i, art in enumerate(self._artists):
            art.set_visible(i == best)


class OfflineBasemap:
    """Schematic dark basemap: land, OSM roads (cached), rivers, DC diamond."""

    # Road style by highway= type: (line width, hex color)
    ROAD_STYLE = {
        "motorway": (2.5, "#506070"),
        "trunk":    (2.0, "#445560"),
        "primary":  (1.8, "#3d4a58"),
        "secondary":(1.4, "#334050"),
        "tertiary": (1.0, "#2c3848"),
    }

    LAND = "#141b22"
    WATER = "#0e2a3d"

    def __init__(self, cache_dir, bbox_lonlat):
        from . import osm as _osm
        cache_path = os.path.join(cache_dir, "osm_roads.json")
        self._osm_roads = None
        self._hand_roads = False
        try:
            self._osm_roads = _osm.fetch_roads(bbox_lonlat, cache_path)
        except Exception as e:
            print(f"  OSM road fetch failed ({e}); falling back to schematic roads",
                  flush=True)
            self._hand_roads = True

    @property
    def attribution(self):
        if self._osm_roads is not None:
            return "Road data © OpenStreetMap contributors — schematic basemap"
        return "Schematic basemap: rivers/highways approximate (~0.5-2 km); landmarks at surveyed coordinates"

    def install(self, ax):
        from matplotlib.collections import LineCollection
        from matplotlib.patches import Polygon as MplPolygon

        ax.set_facecolor(self.LAND)

        # River ribbons: offset the centerline by half-width on each side.
        for _, pts in lm.RIVERS:
            lat = np.array([p[0] for p in pts])
            lon = np.array([p[1] for p in pts])
            hw = np.array([p[2] for p in pts])
            x, y = merc_xy(lon, lat)
            scale = 1.0 / np.cos(np.radians(lat.mean()))
            dx = np.gradient(x)
            dy = np.gradient(y)
            norm = np.hypot(dx, dy)
            nx, ny = -dy / norm, dx / norm
            off = hw * scale
            left = np.column_stack([x + nx * off, y + ny * off])
            right = np.column_stack([x - nx * off, y - ny * off])[::-1]
            poly = MplPolygon(
                np.vstack([left, right]), closed=True,
                facecolor=self.WATER, edgecolor="none", zorder=0.2,
            )
            ax.add_patch(poly)

        # Roads: OSM vector data when available, schematic fallback otherwise.
        if self._osm_roads is not None:
            # Draw each highway type as a LineCollection for efficiency.
            by_type = {k: [] for k in self.ROAD_STYLE}
            for road in self._osm_roads:
                hw = road["highway"]
                if hw not in by_type:
                    continue
                lons = [c[0] for c in road["coords"]]
                lats = [c[1] for c in road["coords"]]
                xs, ys = merc_xy(np.array(lons), np.array(lats))
                by_type[hw].append(list(zip(xs, ys)))
            for hw, segs in by_type.items():
                if not segs:
                    continue
                lw, color = self.ROAD_STYLE[hw]
                ax.add_collection(LineCollection(
                    segs, colors=color, linewidths=lw,
                    capstyle="round", zorder=0.3,
                ))
        else:
            # Hand-digitized fallback.
            ROAD = "#3d4a58"
            ROAD_LABEL = "#8798a8"
            for _, pts in lm.HIGHWAYS:
                lat = np.array([p[0] for p in pts])
                lon = np.array([p[1] for p in pts])
                x, y = merc_xy(lon, lat)
                ax.plot(x, y, color=ROAD, lw=2.2, zorder=0.3,
                        solid_capstyle="round")
            for text, lat, lon, rot in lm.HIGHWAY_LABELS:
                x, y = merc_xy(lon, lat)
                ax.text(
                    x, y, text, color=ROAD_LABEL, fontsize=8.5, rotation=rot,
                    ha="center", va="center", zorder=0.4, style="italic",
                )

        # Original federal district boundary diamond (10 mi square).
        corners_lat = [38.9959, 38.8927, 38.7882, 38.8929, 38.9959]
        corners_lon = [-77.0410, -76.9094, -77.0392, -77.1717, -77.0410]
        x, y = merc_xy(np.array(corners_lon), np.array(corners_lat))
        ax.plot(x, y, color="#2c3844", lw=1.0, ls=(0, (5, 4)), zorder=0.25)

    def update(self, view_width_m):
        pass


def make_basemap(mode, bbox_wide, bbox_tight, cache_dir):
    if mode == "offline":
        return OfflineBasemap(cache_dir, bbox_wide)
    if mode in PROVIDERS:
        return SatelliteBasemap(mode, bbox_wide, bbox_tight, cache_dir)
    raise ValueError(f"Unknown basemap mode: {mode}")
