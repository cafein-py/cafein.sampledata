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
RELEASE = "helsinki-2026.08.1"

ASSETS = {
    "osm_pbf": Asset(
        name="helsinki_capital_region.osm.pbf",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/helsinki_capital_region.osm.pbf",
        sha256="145ef0fed6909ede0b1d7e82ee29800975ff93e56c381dd9e16cc4503e09229a",
        size=71214563,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="Geofabrik finland-260810 Mon, 10 Aug 2026 23:31:58 GMT",
        release=RELEASE,
    ),
    "gtfs": Asset(
        name="hsl_gtfs.zip",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/hsl_gtfs.zip",
        sha256="9b87d6207ce345eb1a6064688f20da492e67454cd89d52b87ddc54da6974e6d3",
        size=56948603,
        license="CC BY 4.0",
        attribution="Helsinki Region Transport (HSL)",
        source_stamp="HSL GTFS feed_version=2026-08-11 03:52:16 fetched 2026-08-11T16:05:10+00:00",
        release=RELEASE,
    ),
    "dem": Asset(
        name="helsinki_dem_10m.tif",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/helsinki_dem_10m.tif",
        sha256="d763ff03bd33dbc40c795cd37dab77e5a76db1a3dbb871d0f41911c584c383c2",
        size=44670031,
        license="CC BY 4.0",
        attribution="National Land Survey of Finland elevation model 2 m, 2026",
        source_stamp="NLS korkeusmalli_2m over the capital region, fetched 2026-08-11T16:05:48+00:00..2026-08-11T16:15:19+00:00",
        release=RELEASE,
    ),
    "population_grid": Asset(
        name="hsy_population_grid_250m.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/hsy_population_grid_250m.gpkg",
        sha256="46b45af72528a7718931b370bf0dd7e71eb01fe7464d030b1f163d7a6e5c0ecb",
        size=1781760,
        license="CC BY 4.0",
        attribution="Helsinki Region Environmental Services HSY population grid 2025",
        source_stamp="HSY asuminen_ja_maankaytto:Vaestotietoruudukko_2025 fetched 2026-08-11T16:15:23+00:00, unknown-location dummy cell removed",
        release=RELEASE,
    ),
    "poi_library": Asset(
        name="helsinki_pois_library.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/helsinki_pois_library.gpkg",
        sha256="17a6a87f5a531d3e1431493e5cce574facb6c51a043d7a6011e3fa2167cefc14",
        size=147456,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=145ef0fed6909ede0b1d7e82ee29800975ff93e56c381dd9e16cc4503e09229a as library",
        release=RELEASE,
    ),
    "poi_kindergarten": Asset(
        name="helsinki_pois_kindergarten.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/helsinki_pois_kindergarten.gpkg",
        sha256="6d1deca9f0d54aa9f1b4489c90b61c24deeefc75da9f493dba0bbe9b1c502a91",
        size=262144,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=145ef0fed6909ede0b1d7e82ee29800975ff93e56c381dd9e16cc4503e09229a as kindergarten",
        release=RELEASE,
    ),
    "poi_university": Asset(
        name="helsinki_pois_university.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/helsinki_pois_university.gpkg",
        sha256="e4185315694686a6606057faa8a3ffdc0ac1cd6dbef5187017c5b1370b2913ab",
        size=106496,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=145ef0fed6909ede0b1d7e82ee29800975ff93e56c381dd9e16cc4503e09229a as university",
        release=RELEASE,
    ),
    "poi_supermarket": Asset(
        name="helsinki_pois_supermarket.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/helsinki_pois_supermarket.gpkg",
        sha256="f48a609c845d6a1f8af0e4b3b5444ad261818f99634a2b7adc403816cd1675df",
        size=180224,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=145ef0fed6909ede0b1d7e82ee29800975ff93e56c381dd9e16cc4503e09229a as supermarket",
        release=RELEASE,
    ),
    "poi_shopping_centre": Asset(
        name="helsinki_pois_shopping_centre.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/helsinki_pois_shopping_centre.gpkg",
        sha256="f4853f8ff742ff01dd315d632e6d6dbb82327d30e032d49ef40c4e321eaea184",
        size=135168,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=145ef0fed6909ede0b1d7e82ee29800975ff93e56c381dd9e16cc4503e09229a as shopping_centre",
        release=RELEASE,
    ),
    "poi_sports_centre": Asset(
        name="helsinki_pois_sports_centre.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/helsinki_pois_sports_centre.gpkg",
        sha256="3d8b05bad5670d094d1021ea18e343152bbd90b301cfbb31668a15d00691f517",
        size=192512,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=145ef0fed6909ede0b1d7e82ee29800975ff93e56c381dd9e16cc4503e09229a as sports_centre",
        release=RELEASE,
    ),
    "poi_swimming_hall": Asset(
        name="helsinki_pois_swimming_hall.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.1/helsinki_pois_swimming_hall.gpkg",
        sha256="b9d6696bbf3a751e4f86bd1afc100431f208cf6eed81929e83ded91d9a183f82",
        size=110592,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=145ef0fed6909ede0b1d7e82ee29800975ff93e56c381dd9e16cc4503e09229a as swimming_hall",
        release=RELEASE,
    ),
}
# --- END REGISTRY ---
