# cafein.sampledata

Sample datasets for [cafein](https://github.com/cafein-py/cafein):
up-to-date, openly licensed data for the Helsinki Metropolitan Area,
downloaded on first access and cached locally.

```python
import cafein.sampledata.helsinki as helsinki

helsinki.osm_pbf          # OpenStreetMap extract, capital region
helsinki.gtfs             # HSL GTFS feed
helsinki.dem              # NLS 10 m elevation model
helsinki.population_grid  # HSY 250 m population grid
helsinki.pois.library     # OSM destinations, one category per file
helsinki.emission_factors # cafein's default GHG factors as a CSV
```

Destination categories under `pois` — `library`, `kindergarten`,
`university`, `supermarket`, `shopping_centre`, `sports_centre`,
`swimming_hall` — each a GeoPackage of points (layer `pois`,
EPSG:4326) extracted from the same OpenStreetMap release as
`osm_pbf`.

Each data attribute — including each category under `pois` — is a
`pathlib.Path`; the file downloads and verifies on first use. A package release pins exact data snapshots — upgrade the
package to get newer data.

Data files are cached under the platform user-cache directory; set
`CAFEIN_SAMPLEDATA_DIR` to relocate the cache.

## Data licenses

The package code is MIT. The datasets carry their own open licenses —
OpenStreetMap (ODbL 1.0, the extract and the POI layers derived from
it), HSL GTFS (CC BY 4.0), National Land Survey of Finland elevation
model (CC BY 4.0), HSY population grid (CC BY 4.0) — with attribution
recorded per asset in `<region>.metadata`.
