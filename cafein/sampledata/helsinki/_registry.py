"""The Helsinki asset registry: the pins this package release carries.

The block between the REGISTRY markers is rewritten by
``pipeline/registry.py`` from a data release's ``manifest.json`` —
edit the pins there, never by hand. An empty ``RELEASE`` means this
build pins no data release yet: the remote assets raise with guidance
instead of downloading.
"""

from cafein.sampledata import Asset

DOWNLOAD_BASE = "https://github.com/cafein-py/cafein.sampledata/releases/download"

# --- REGISTRY (generated; do not edit by hand) ---
RELEASE = ""

ASSETS = {
    "osm_pbf": Asset(
        name="helsinki_capital_region.osm.pbf",
        url="",
        sha256="",
        size=0,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="",
        release=RELEASE,
    ),
    "gtfs": Asset(
        name="hsl_gtfs.zip",
        url="",
        sha256="",
        size=0,
        license="CC BY 4.0",
        attribution="Helsinki Region Transport (HSL)",
        source_stamp="",
        release=RELEASE,
    ),
    "dem": Asset(
        name="helsinki_dem_10m.tif",
        url="",
        sha256="",
        size=0,
        license="CC BY 4.0",
        attribution="National Land Survey of Finland elevation model 10 m",
        source_stamp="",
        release=RELEASE,
    ),
    "population_grid": Asset(
        name="hsy_population_grid_250m.gpkg",
        url="",
        sha256="",
        size=0,
        license="CC BY 4.0",
        attribution="Helsinki Region Environmental Services HSY population grid",
        source_stamp="",
        release=RELEASE,
    ),
}
# --- END REGISTRY ---
