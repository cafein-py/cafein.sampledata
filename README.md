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
helsinki.emission_factors # cafein's default GHG factors as a CSV
```

Each attribute is a `pathlib.Path`; the file downloads and verifies on
first use. A package release pins exact data snapshots — upgrade the
package to get newer data.

Data files are cached under the platform user-cache directory; set
`CAFEIN_SAMPLEDATA_DIR` to relocate the cache.

## Data licenses

The package code is MIT. The datasets carry their own open licenses —
OpenStreetMap (ODbL 1.0), HSL GTFS (CC BY 4.0), National Land Survey
of Finland elevation model (CC BY 4.0), HSY population grid
(CC BY 4.0) — with attribution recorded per asset in
`<region>.metadata`.
