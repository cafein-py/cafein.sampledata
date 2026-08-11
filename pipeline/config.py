"""Pipeline configuration: sources, extent, and asset identities."""

# Source endpoints — one line each, so a moved URL is a one-line fix.
# A release run passes an explicit YYMMDD snapshot date so the source is
# an immutable dated file and a rerun fetches identical bytes; `latest`
# exists for exploratory runs only.
GEOFABRIK_FINLAND_URL = "https://download.geofabrik.de/europe/finland-latest.osm.pbf"
GEOFABRIK_FINLAND_DATED_URL = (
    "https://download.geofabrik.de/europe/finland-{date}.osm.pbf"
)
HSL_GTFS_URL = "https://dev.hsl.fi/gtfs/hsl.zip"

# A generous bounding box over Helsinki, Espoo, Vantaa, and Kauniainen —
# the HSY capital region the population grid covers — as
# (west, south, east, north) in EPSG:4326, outer islands included.
CAPITAL_REGION_BBOX = (24.40, 59.95, 25.27, 60.41)

OSM_ASSET = "helsinki_capital_region.osm.pbf"
GTFS_ASSET = "hsl_gtfs.zip"

OSM_LICENSE = "ODbL 1.0"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
GTFS_LICENSE = "CC BY 4.0"
GTFS_ATTRIBUTION = "Helsinki Region Transport (HSL)"

# ERROR-severity notice codes the published HSL feed is known to carry.
# The feed ships as published — these ride along in the validation
# report instead of blocking; cafein's smoke build remains the
# functional gate. Any code not listed here still blocks the release.
TOLERATED_GTFS_NOTICES = frozenset(
    {
        # stations (location_type 1) that themselves name a parent_station
        "station_with_parent_station",
        # duplicated shape_dist_traveled at different shape points
        "equal_shape_distance_diff_coordinates",
        # references to IDs absent from the target file
        "foreign_key_violation",
    }
)

# Sources are hundreds of megabytes; allow slow mirrors before giving up.
DOWNLOAD_TIMEOUT = 300.0

# The largest source is the Finland extract (~600 MB); anything past
# this cap is a misbehaving endpoint, aborted before it fills the disk.
# Smaller endpoints get correspondingly smaller caps at their call
# sites, so no anomalous-but-successful endpoint can add up to much.
MAX_DOWNLOAD_BYTES = 4 << 30
MAX_GTFS_BYTES = 2 << 30
MAX_DEM_CHUNK_BYTES = 100 << 20
MAX_WFS_XML_BYTES = 10 << 20

# --- DEM (NLS 10 m elevation model) -----------------------------------------

# The OGC WCS of the National Land Survey's open elevation data. Requires
# a free NLS api key (https://www.maanmittauslaitos.fi/en/rajapinnat/
# api-avaimen-ohje), passed by the release workflow as MML_API_KEY.
NLS_WCS_URL = (
    "https://avoin-karttakuva.maanmittauslaitos.fi" "/ortokuvat-ja-korkeusmallit/wcs/v2"
)
# The WCS serves only the 2 m elevation model; the 10 m asset is the
# server's own resampling of it (SCALEFACTOR 0.2 -> 10 m pixels).
NLS_DEM_COVERAGE = "korkeusmalli_2m"
NLS_DEM_SCALEFACTOR = 0.2

# The capital-region bbox in the DEM's native EPSG:3067, the 4326 bbox's
# corner envelope rounded outward to 100 m (recomputed by the test suite
# when pyproj is available, so drift fails loudly):
# (east_min, north_min, east_max, north_max).
CAPITAL_REGION_BBOX_3067 = (354700, 6647100, 404800, 6699900)
# The projected CRS the region's metric work happens in.
CAPITAL_REGION_CRS = "EPSG:3067"

# One WCS GetCoverage per chunk; the server caps elevation requests at
# 10 x 10 km, and 10 km at 10 m is a 1000 px tile, a few MB.
DEM_CHUNK_METERS = 10000

# The bbox includes open sea, so all-nodata chunks are legitimate; the
# validity policy is a minimum valid share plus finite elevations at
# known-land probes (the four municipal centres), which a missing land
# chunk cannot satisfy.
DEM_MIN_VALID_FRACTION = 0.35
# North of the coastal strip the capital region is land: every chunk
# lying fully north of this northing must carry valid elevations, which
# catches a single-chunk hole no sparse probe would.
DEM_LAND_NORTH_OF = 6_660_000
DEM_MIN_CHUNK_VALID_FRACTION = 0.5
DEM_LAND_PROBES = (
    (385700, 6671900),  # central Helsinki
    (372500, 6677800),  # Leppävaara, Espoo
    (393200, 6685900),  # Tikkurila, Vantaa
    (371800, 6672900),  # Kauniainen
)

DEM_ASSET = "helsinki_dem_10m.tif"
DEM_LICENSE = "CC BY 4.0"
DEM_ATTRIBUTION = "National Land Survey of Finland elevation model 2 m"

# --- Population grid (HSY 250 m) --------------------------------------------

# HSY's open geoserver; the population grid is published per year as
# Vaestotietoruudukko_<year>, discovered from GetCapabilities.
HSY_WFS_URL = "https://kartta.hsy.fi/geoserver/wfs"
HSY_GRID_LAYER_PREFIX = "asuminen_ja_maankaytto:Vaestotietoruudukko_"
# The inhabitants-count field of the grid.
POPULATION_COLUMN = "asukkaita"

# HSY aggregates institutional residents and residents who cannot be
# linked to a building into one dummy cell placed in the Gulf of Finland
# at the map's lower-right corner. It is no place anyone lives, so the
# step drops it, identified by its documented signature: the no-data
# sentinel in the floor-area-per-resident field, in a cell standing
# alone out at sea.
POPULATION_NODATA_COLUMN = "asvaljyys"
POPULATION_NODATA_SENTINEL = 999999999
# The sentinel alone is not the signature — 44 real cells of the 2025
# grid carry it too. Isolation separates them: the most remote real cell
# lies 1.5 km from its nearest neighbour, the dummy cell 14 km.
POPULATION_DUMMY_ISOLATION_METERS = 5000

# The grid is ~6000 cells and a few MB: one bounded request fetches it
# whole (no offset paging, whose order a server need not keep stable),
# and anything past these ceilings is not the capital-region grid.
MAX_WFS_FEATURES = 30_000
MAX_WFS_RESPONSE_BYTES = 60 << 20

# The metropolitan grid has ~6000 populated cells; far fewer means a
# truncated or wrong layer, not a smaller city.
MIN_POPULATION_CELLS = 3000

POPULATION_ASSET = "hsy_population_grid_250m.gpkg"
POPULATION_LICENSE = "CC BY 4.0"
POPULATION_ATTRIBUTION = "Helsinki Region Environmental Services HSY population grid"

# --- Points of interest (OSM) -----------------------------------------------

# Destination categories extracted from the capital-region extract, one
# GeoPackage each. `tags` is the pyrosm filter; `sport` (when given) is
# an extra requirement on the feature's own `sport` tag, which the
# filter cannot express — pyrosm keeps a feature matching *any* filter
# key, so a two-key filter would widen the category rather than narrow
# it. `minimum` is the count below which the category is treated as an
# upstream tagging change rather than a thin month; the counts in the
# comments are the 2026-08 extract's.
POI_CATEGORIES = {
    "library": {"tags": {"amenity": ["library"]}, "minimum": 50},  # 103
    "kindergarten": {"tags": {"amenity": ["kindergarten"]}, "minimum": 400},  # 843
    "university": {"tags": {"amenity": ["university"]}, "minimum": 12},  # 27
    "supermarket": {"tags": {"shop": ["supermarket"]}, "minimum": 100},  # 218
    "shopping_centre": {"tags": {"shop": ["mall"]}, "minimum": 40},  # 87
    "sports_centre": {"tags": {"leisure": ["sports_centre"]}, "minimum": 150},  # 342
    # The swimming halls are sports centres whose sport is swimming;
    # leisure=swimming_pool is the water basin, not the facility, and
    # names the region's paddling pools rather than its halls.
    "swimming_hall": {
        "tags": {"leisure": ["sports_centre"]},
        "sport": "swimming",
        "minimum": 15,
    },  # 33
}

#: The layer inside every POI GeoPackage, and the file name pattern.
POI_LAYER = "pois"
POI_ASSET_TEMPLATE = "helsinki_pois_{category}.gpkg"

#: The columns every POI layer carries, in order.
POI_COLUMNS = ("osm_id", "osm_type", "name", "category", "tags")

# The POIs are extracted from the OSM extract this same run produced,
# so both cover one and the same area; features are served as points
# (ways and relations reduced to their centroid), in OSM's own CRS.
POI_CRS = "EPSG:4326"
POI_LICENSE = OSM_LICENSE
POI_ATTRIBUTION = OSM_ATTRIBUTION


def poi_asset(category) -> str:
    return POI_ASSET_TEMPLATE.format(category=category)


POI_ASSETS = {category: poi_asset(category) for category in POI_CATEGORIES}
