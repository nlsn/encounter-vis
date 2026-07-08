"""Ground-context data for the DC area map.

Two accuracy classes, kept deliberately separate:

* LANDMARKS: point features at well-known coordinates (good to ~100 m,
  which is well under a pixel at the map scales used here).

* Schematic polylines (rivers, highways): APPROXIMATE, hand-digitized
  centerlines good to roughly 0.5-2 km. They are for orientation only
  and are labeled "(approx.)" in the offline basemap. In satellite
  basemap mode the imagery itself shows the real roads/water; there we
  draw only the highway *labels* (at interchange/bridge anchor points)
  and skip the schematic lines by default.

If you need survey-grade ground features, pull OSM extracts instead --
see README "Improving the basemap".
"""

# (label, lat, lon, kind) -- kind controls the marker glyph
LANDMARKS = [
    ("Washington Monument", 38.8895, -77.0353, "monument"),
    ("US Capitol",          38.8899, -77.0091, "monument"),
    ("White House",         38.8977, -77.0365, "monument"),
    ("Lincoln Memorial",    38.8893, -77.0502, "monument"),
    ("Pentagon",            38.8719, -77.0563, "building"),
    ("Reagan National (DCA)", 38.8512, -77.0377, "airport"),
    ("Joint Base Andrews (ADW)", 38.8108, -76.8670, "airport"),
]

# Approximate river centerlines: list of (lat, lon, half_width_m).
POTOMAC = [
    (38.975, -77.200, 250),
    (38.945, -77.130, 250),
    (38.930, -77.105, 300),
    (38.903, -77.070, 350),
    (38.888, -77.050, 500),
    (38.870, -77.030, 700),
    (38.848, -77.022, 800),
    (38.820, -77.030, 800),
    (38.793, -77.037, 900),
    (38.760, -77.020, 1100),
    (38.715, -77.025, 1300),
    (38.680, -77.085, 1500),
    (38.650, -77.130, 1600),
]

ANACOSTIA = [
    (38.858, -77.020, 350),
    (38.866, -77.005, 300),
    (38.873, -76.992, 250),
    (38.882, -76.975, 220),
    (38.895, -76.955, 180),
    (38.910, -76.940, 130),
]

RIVERS = [("Potomac River", POTOMAC), ("Anacostia River", ANACOSTIA)]

# Approximate highway centerlines: (name, [(lat, lon), ...])
# Anchor points with the highest confidence are bridges/major
# interchanges; segments between them are smoothed guesses.
HIGHWAYS = [
    (
        "I-495 / I-95  Capital Beltway (approx.)",
        [
            (38.7929, -77.0391),  # Woodrow Wilson Bridge
            (38.796, -76.990),
            (38.803, -76.942),
            (38.812, -76.902),
            (38.822, -76.880),
            (38.848, -76.862),
            (38.880, -76.850),
            (38.912, -76.846),
            (38.948, -76.852),
            (38.978, -76.872),
            (39.000, -76.905),
            (39.012, -76.950),
            (39.020, -77.000),
            (39.022, -77.050),
        ],
    ),
    (
        "I-295 / DC-295 (approx.)",
        [
            (38.799, -77.026),
            (38.826, -77.014),
            (38.845, -77.008),
            (38.862, -76.998),
            (38.878, -76.972),
            (38.895, -76.948),
            (38.910, -76.935),
        ],
    ),
    (
        "Suitland Pkwy (approx.)",
        [
            (38.864, -76.995),
            (38.852, -76.955),
            (38.840, -76.915),
            (38.830, -76.892),
            (38.822, -76.878),
        ],
    ),
    (
        "MD-4 Pennsylvania Ave (approx.)",
        [
            (38.870, -76.975),
            (38.858, -76.935),
            (38.847, -76.895),
            (38.838, -76.855),
            (38.830, -76.815),
        ],
    ),
]

# Where to place each highway's text label (lat, lon, rotation_deg).
HIGHWAY_LABELS = [
    ("I-495 Capital Beltway", 38.892, -76.845, 80),
    ("I-295", 38.845, -77.013, 70),
    ("Suitland Pkwy", 38.845, -76.925, -35),
    ("MD-4", 38.851, -76.912, -25),
]
