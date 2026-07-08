"""Fetch road geometries from the Overpass API and cache them locally.

Roads are returned as a list of dicts::

    {"highway": "primary", "coords": [(lon, lat), ...]}

The cache file is JSON so the network is only hit on the first run.
"""

import json
import os
import time

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# highway= values to fetch, from most to least prominent
ROAD_TYPES = ("motorway", "trunk", "primary", "secondary", "tertiary")


def fetch_roads(bbox_lonlat, cache_path, road_types=ROAD_TYPES):
    """Return OSM road segments for *bbox_lonlat* (west, south, east, north).

    Results are loaded from *cache_path* if it exists; otherwise fetched
    from Overpass and written to *cache_path*.
    """
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    import requests

    west, south, east, north = bbox_lonlat
    type_re = "|".join(road_types)
    query = (
        f'[out:json][timeout:60];'
        f'(way["highway"~"^({type_re})$"]({south},{west},{north},{east}););'
        f'out geom;'
    )
    print(f"  fetching OSM road data for bbox {bbox_lonlat} …", flush=True)
    headers = {
        "Accept": "application/json",
        "User-Agent": "f5-flyover-encounter/1.0 (trajectory visualization)",
    }
    for attempt in range(3):
        try:
            r = requests.post(OVERPASS_URL, data={"data": query},
                              headers=headers, timeout=90)
            r.raise_for_status()
            break
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"Overpass fetch failed: {e}")
            time.sleep(5 * (attempt + 1))

    data = r.json()
    roads = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        hw = el.get("tags", {}).get("highway", "")
        if hw not in road_types:
            continue
        coords = [(nd["lon"], nd["lat"]) for nd in el.get("geometry", [])]
        if len(coords) >= 2:
            roads.append({"highway": hw, "coords": coords})

    os.makedirs(os.path.dirname(os.path.abspath(cache_path)), exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(roads, f)
    print(f"  cached {len(roads)} road segments → {cache_path}", flush=True)
    return roads
