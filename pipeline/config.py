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

# Sources are hundreds of megabytes; allow slow mirrors before giving up.
DOWNLOAD_TIMEOUT = 300.0

# The largest source is the Finland extract (~600 MB); anything past
# this cap is a misbehaving endpoint, aborted before it fills the disk.
MAX_DOWNLOAD_BYTES = 4 << 30
