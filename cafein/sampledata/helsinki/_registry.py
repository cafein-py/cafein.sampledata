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
RELEASE = "helsinki-2026.08.2"

ASSETS = {
    "osm_pbf": Asset(
        name="helsinki_capital_region.osm.pbf",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_capital_region.osm.pbf",
        sha256="5a3cb8995c7568f73c85c7c83db2042d374d1ae8e643c71e2899319ec943bdbf",
        size=71397794,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="Geofabrik finland-260817 Mon, 17 Aug 2026 23:30:33 GMT",
        release=RELEASE,
    ),
    "gtfs": Asset(
        name="hsl_gtfs.zip",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/hsl_gtfs.zip",
        sha256="346477944f94b9fcee19f557c8f75947d6f1a4bab525e58c4554e2d04f7b70b0",
        size=64637249,
        license="CC BY 4.0",
        attribution="Helsinki Region Transport (HSL)",
        source_stamp="HSL GTFS feed_version=2026-08-18 04:13:28 fetched 2026-08-18T16:51:28+00:00",
        release=RELEASE,
    ),
    "dem": Asset(
        name="helsinki_dem_10m.tif",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_dem_10m.tif",
        sha256="d763ff03bd33dbc40c795cd37dab77e5a76db1a3dbb871d0f41911c584c383c2",
        size=44670031,
        license="CC BY 4.0",
        attribution="National Land Survey of Finland elevation model 2 m, 2026",
        source_stamp="NLS korkeusmalli_2m over the capital region, fetched 2026-08-18T16:52:07+00:00..2026-08-18T16:57:58+00:00",
        release=RELEASE,
    ),
    "population_grid": Asset(
        name="hsy_population_grid_250m.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/hsy_population_grid_250m.gpkg",
        sha256="164b1949048c5a78fa454fafcddcf2f358a0aaa06fc087d9f9c30f4d580f6e62",
        size=1781760,
        license="CC BY 4.0",
        attribution="Helsinki Region Environmental Services HSY population grid 2025",
        source_stamp="HSY asuminen_ja_maankaytto:Vaestotietoruudukko_2025 fetched 2026-08-18T16:58:03+00:00, unknown-location dummy cell removed",
        release=RELEASE,
    ),
    "air_quality": Asset(
        name="helsinki_air_quality.tif",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_air_quality.tif",
        sha256="ed7a996ebbf99f30f59655d8269c154c242b0fc678196032e23de538c1719b1d",
        size=45008076,
        license="CC BY 4.0",
        attribution="Finnish Meteorological Institute, FMI-ENFUSER",
        source_stamp="FMI-ENFUSER valid hour 2026-08-18T15:00:00+00:00, model origin 2026-08-18T02:00:00+00:00 (bound by the validated download reference), fetched 2026-08-18T16:28:01+00:00 from https://opendata.fmi.fi/download?producer=enfuser_helsinki_metropolitan&param=AQIndex,NO2Concentration,O3Concentration,PM10Concentration,PM25Concentration,LungDepositedSurfaceArea,BlackCarbonConcentration,ParticleNumberConcentration&bbox=24.58,60.1321,25.1998,60.368&levels=0&origintime=2026-08-18T02:00:00Z&starttime=2026-08-18T15:00:00Z&endtime=2026-08-18T15:00:00Z&format=netcdf&projection=EPSG:4326, native grid 2631x2018 cells, 0.000236x0.000117 deg, EPSG:4326, values and units unchanged",
        release=RELEASE,
    ),
    "green_view": Asset(
        name="helsinki_green_view.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_green_view.gpkg",
        sha256="d3d1fd727bc4e16670660118ecd61e073206339eab0c4ef6f613f2cc10d9f8e7",
        size=28790784,
        license="CC BY 4.0",
        attribution="Akseli Toikka, Elias Willberg, Ville Mäkinen, Tuuli Toivonen & Juha Oksanen: The green view dataset for the capital of Finland, Helsinki. Data in Brief 30 (2020) 105601, doi:10.1016/j.dib.2020.105601",
        source_stamp="publisher supplement bytes (sha256 330e215a8d84…, 8b591f416890…) from the sources-green-view mirror https://github.com/cafein-py/cafein.sampledata/releases/download/sources-green-view/1-s2.0-S2352340920304959-mmc2.zip and https://github.com/cafein-py/cafein.sampledata/releases/download/sources-green-view/1-s2.0-S2352340920304959-mmc3.zip, fetched 2026-08-18T16:28:04+00:00, layers normalized to EPSG:3067 with columns verbatim",
        release=RELEASE,
    ),
    "noise": Asset(
        name="helsinki_noise_2022.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_noise_2022.gpkg",
        sha256="b836b2c7ff6e2a90829212208c7063a5eb576f98c306a3056dd7c21908c9a15f",
        size=57098240,
        license="CC BY 4.0",
        attribution="City of Helsinki, meluselvitys 2022",
        source_stamp="kartta.hel.fi WFS layers avoindata:Meluselvitys_2022_Helsinki_metro_Lden, avoindata:Meluselvitys_2022_Helsinki_metro_Ln, avoindata:Meluselvitys_2022_Helsinki_rautatiet_Lden, avoindata:Meluselvitys_2022_Helsinki_rautatiet_Ln, avoindata:Meluselvitys_2022_Helsinki_kadut_ja_maantiet_Lden, avoindata:Meluselvitys_2022_Helsinki_kadut_ja_maantiet_Ln, avoindata:Meluselvitys_2022_Helsinki_raitiotie_Lden, avoindata:Meluselvitys_2022_Helsinki_raitiotie_Ln fetched 2026-08-18T16:31:06+00:00, zones normalized to source/metric/db_low/db_high in EPSG:3067, geometry unchanged",
        release=RELEASE,
    ),
    "poi_library": Asset(
        name="helsinki_pois_library.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_pois_library.gpkg",
        sha256="2e775b140fae28a2d29963ba281fd4b999cfa1b32573d0f584467efb6a08ecbe",
        size=147456,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=5a3cb8995c7568f73c85c7c83db2042d374d1ae8e643c71e2899319ec943bdbf as library",
        release=RELEASE,
    ),
    "poi_kindergarten": Asset(
        name="helsinki_pois_kindergarten.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_pois_kindergarten.gpkg",
        sha256="29d368c02159c21cf63ee831b80569224e410a986487806ac3d2262fe2f37cd6",
        size=262144,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=5a3cb8995c7568f73c85c7c83db2042d374d1ae8e643c71e2899319ec943bdbf as kindergarten",
        release=RELEASE,
    ),
    "poi_university": Asset(
        name="helsinki_pois_university.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_pois_university.gpkg",
        sha256="3e1481eee169519303676eeef545a8415828f21061ef4fd602923e42b95b5a17",
        size=106496,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=5a3cb8995c7568f73c85c7c83db2042d374d1ae8e643c71e2899319ec943bdbf as university",
        release=RELEASE,
    ),
    "poi_supermarket": Asset(
        name="helsinki_pois_supermarket.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_pois_supermarket.gpkg",
        sha256="49126c7098bcf48b292a2aa6efa1f7264bc0c04776de25d1019532e7358c414c",
        size=180224,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=5a3cb8995c7568f73c85c7c83db2042d374d1ae8e643c71e2899319ec943bdbf as supermarket",
        release=RELEASE,
    ),
    "poi_shopping_centre": Asset(
        name="helsinki_pois_shopping_centre.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_pois_shopping_centre.gpkg",
        sha256="8bbaf3f2baed43cd5205cf33adf8a134924a90944182e26516d1a8d283619b86",
        size=135168,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=5a3cb8995c7568f73c85c7c83db2042d374d1ae8e643c71e2899319ec943bdbf as shopping_centre",
        release=RELEASE,
    ),
    "poi_sports_centre": Asset(
        name="helsinki_pois_sports_centre.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_pois_sports_centre.gpkg",
        sha256="df97afa8e93fa00becd714f40b7ad29861beb15a23d8beade3e114bff5cc6dd5",
        size=192512,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=5a3cb8995c7568f73c85c7c83db2042d374d1ae8e643c71e2899319ec943bdbf as sports_centre",
        release=RELEASE,
    ),
    "poi_swimming_hall": Asset(
        name="helsinki_pois_swimming_hall.gpkg",
        url=f"{DOWNLOAD_BASE}/helsinki-2026.08.2/helsinki_pois_swimming_hall.gpkg",
        sha256="9e01480cd260dccb0281d3804a3365b016c381e0609106ddc563f87786677c26",
        size=110592,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="extracted from helsinki_capital_region.osm.pbf sha256=5a3cb8995c7568f73c85c7c83db2042d374d1ae8e643c71e2899319ec943bdbf as swimming_hall",
        release=RELEASE,
    ),
}
# --- END REGISTRY ---
