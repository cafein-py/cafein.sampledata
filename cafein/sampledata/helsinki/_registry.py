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
RELEASE = "helsinki-2026.08"

ASSETS = {
    "osm_pbf": Asset(
        name="helsinki_capital_region.osm.pbf",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08/helsinki_capital_region.osm.pbf",
        sha256="bd67d36a0bb1dd90b9891ee31b364b04b12952362be81d5c71509a8add58d24f",
        size=71201516,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="Geofabrik finland-260809 Mon, 10 Aug 2026 10:21:16 GMT",
        release=RELEASE,
    ),
    "gtfs": Asset(
        name="hsl_gtfs.zip",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08/hsl_gtfs.zip",
        sha256="bbe0b5025790598182ae6db1d498c0cc6a5c1101c03cd6e174564a37b2d8f42c",
        size=84457050,
        license="CC BY 4.0",
        attribution="Helsinki Region Transport (HSL)",
        source_stamp="HSL GTFS feed_version=2026-08-08 06:56:25 fetched 2026-08-10T21:22:20+00:00",
        release=RELEASE,
    ),
    "dem": Asset(
        name="helsinki_dem_10m.tif",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08/helsinki_dem_10m.tif",
        sha256="d763ff03bd33dbc40c795cd37dab77e5a76db1a3dbb871d0f41911c584c383c2",
        size=44670031,
        license="CC BY 4.0",
        attribution="National Land Survey of Finland elevation model 2 m, 2026",
        source_stamp="NLS korkeusmalli_2m over the capital region, fetched 2026-08-10T21:23:09+00:00..2026-08-10T21:27:23+00:00",
        release=RELEASE,
    ),
    "population_grid": Asset(
        name="hsy_population_grid_250m.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08/hsy_population_grid_250m.gpkg",
        sha256="02259abd796b10400a55fa62928209465dcde8903eb3d234cf5d3378b3fe0fcd",
        size=1777664,
        license="CC BY 4.0",
        attribution="Helsinki Region Environmental Services HSY population grid 2025",
        source_stamp="HSY asuminen_ja_maankaytto:Vaestotietoruudukko_2025 fetched 2026-08-10T21:27:27+00:00",
        release=RELEASE,
    ),
}
# --- END REGISTRY ---
