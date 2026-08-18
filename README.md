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
helsinki.air_quality      # FMI-ENFUSER air quality, one pinned hour
helsinki.green_view       # Green View Index (street-level greenery)
helsinki.noise            # Helsinki noise survey 2022, zone polygons
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

## Exposure layers

`air_quality` is a multi-band GeoTIFF of ONE model hour of
FMI-ENFUSER (the hour and model origin are stamped in the metadata) —
not a climatology — on its native ~13 m EPSG:4326 grid. Band names
and units also travel in the file's band descriptions:

| band | FMI variable                  | unit    |
|-----:|-------------------------------|---------|
|    1 | `AQIndex`                     | 1–5, unitless |
|    2 | `NO2Concentration`            | µg/m³   |
|    3 | `O3Concentration`             | µg/m³   |
|    4 | `PM10Concentration`           | µg/m³   |
|    5 | `PM25Concentration`           | µg/m³   |
|    6 | `LungDepositedSurfaceArea`    | µm²/cm³ |
|    7 | `BlackCarbonConcentration`    | µg/m³   |
|    8 | `ParticleNumberConcentration` | 1/cm³   |

`green_view` is the published Green View Index dataset for the
capital region (Data in Brief, doi:10.1016/j.dib.2020.105601),
columns verbatim as published, EPSG:3067:

| layer  | column       | meaning                                        |
|--------|--------------|------------------------------------------------|
| points | `panoID`     | Google Street View panorama identifier         |
| points | `panoDate`   | imagery capture month, `YYYY-MM` (2009–2014+ as inspected) |
| points | `longitude`, `lattitude` | the published coordinates (the spelling is the dataset's own) |
| points | `Gvi_Mean`   | Green View Index, percent (0–100)              |
| roads  | `GSV_GVI`    | segment GVI from the panorama points           |
| roads  | `LU_GVI`     | land-use-derived GVI                           |
| roads  | `Comb_GVI`   | the combined per-segment exposure value (0–100)|
| roads  | `GVI_source` | which source fed `Comb_GVI` (`gsv`/land use)   |
| roads  | `TEKSTI`     | street name (the network's own attribute)      |
| roads  | `TOIMINNALL` | functional road class code                     |
| roads  | `TYYPPI`     | link type code                                 |
| roads  | `LIIKENNEVI` | traffic-direction code                         |
| roads  | `luokka`     | segment class code                             |
| roads  | `Pyoravayla` | cycleway flag                                  |
| roads  | `BufAarea`   | segment buffer area, m²                        |
| roads  | `LUArea`     | vegetated land-use area within the buffer, m²  |

(92 126 points, 56 074 segments; the road attribute codes are the
MetropAccess street network's own, published as-is)

`noise` holds Helsinki's 2022 noise survey zones as polygons in
EPSG:3067, Helsinki-city extent; the 2022 round's calculation method
is not comparable with 2017's:

| column    | meaning                                               |
|-----------|-------------------------------------------------------|
| `source`  | `road`, `rail`, `metro`, or `tram`                    |
| `metric`  | `Lden` (day–evening–night) or `Ln` (night)            |
| `db_low`  | the zone class's lower bound, dB                      |
| `db_high` | upper bound, dB — NULL on each source × metric's open-ended top class (≥ `db_low`) |

## Data licenses

The package code is MIT. The datasets carry their own open licenses —
OpenStreetMap (ODbL 1.0, the extract and the POI layers derived from
it), HSL GTFS (CC BY 4.0), National Land Survey of Finland elevation
model (CC BY 4.0), HSY population grid (CC BY 4.0), FMI-ENFUSER air
quality (CC BY 4.0), the Green View Index dataset (CC BY 4.0, Toikka
et al. 2020), and the Helsinki noise survey (CC BY 4.0) — with
attribution recorded per asset in `<region>.metadata`.
