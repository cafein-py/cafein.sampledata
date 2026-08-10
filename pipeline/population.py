"""The population step: HSY's 250 m grid, latest year, as a GeoPackage.

The grid is published per year as a WFS layer
(``Vaestotietoruudukko_<year>``); the newest is discovered from
GetCapabilities, fetched as GeoJSON in EPSG:3067, sanity-checked
against the capital-region extent, and written to GeoPackage.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil

from pipeline import PipelineError, config, download, manifest, workdir_lock


def latest_grid_layer(capabilities_xml: str):
    """The newest ``Vaestotietoruudukko_<year>`` layer in a WFS
    GetCapabilities document, as ``(layer name, year)``."""
    prefix = re.escape(config.HSY_GRID_LAYER_PREFIX)
    years = [int(year) for year in re.findall(rf"{prefix}(\d{{4}})", capabilities_xml)]
    if not years:
        raise PipelineError(
            f"no {config.HSY_GRID_LAYER_PREFIX}<year> layer in the WFS "
            f"capabilities — the HSY layer naming changed?"
        )
    year = max(years)
    return f"{config.HSY_GRID_LAYER_PREFIX}{year}", year


def capabilities_url() -> str:
    return f"{config.HSY_WFS_URL}?service=WFS&version=2.0.0&request=GetCapabilities"


def feature_url(layer, *, count=None, hits=False) -> str:
    url = (
        f"{config.HSY_WFS_URL}?service=WFS&version=2.0.0&request=GetFeature"
        f"&typeNames={layer}&srsName=EPSG:3067"
    )
    if hits:
        return url + "&resultType=hits"
    return (
        f"{url}&outputFormat=application/json"
        f"&count={count or config.MAX_WFS_FEATURES}"
    )


def matched_count(hits_xml: str) -> int:
    """The numberMatched a WFS hits response declares."""
    match = re.search(r'numberMatched="(\d+)"', hits_xml)
    if not match:
        raise PipelineError(
            "the WFS hits response carries no numberMatched — cannot "
            "verify the grid arrives complete"
        )
    return int(match.group(1))


def fetch_features(layer, run_dir) -> pathlib.Path:
    """Fetch the whole layer in one bounded request, verified complete.

    The layer is a few thousand cells, so no offset paging (whose order
    a server need not keep stable across requests): one GetFeature with
    an explicit count ceiling, cross-checked against the server's own
    numberMatched — a server-side response cap or a mid-fetch layer
    change must never publish a partial or mixed grid as complete.
    """
    import json

    hits = run_dir / "hits.xml"
    download.stream_download(
        feature_url(layer, hits=True), hits, max_bytes=config.MAX_WFS_XML_BYTES
    )
    expected = matched_count(hits.read_text(encoding="utf-8", errors="replace"))
    if expected > config.MAX_WFS_FEATURES:
        raise PipelineError(
            f"the WFS declares {expected} features — implausible for the "
            f"250 m capital-region grid; refusing to fetch"
        )
    page_path = run_dir / "grid.json"
    download.stream_download(
        feature_url(layer),
        page_path,
        max_bytes=config.MAX_WFS_RESPONSE_BYTES,
    )
    page = json.loads(page_path.read_text(encoding="utf-8"))
    features = page.get("features", [])
    identifiers = set()
    for feature in features:
        identifier = feature.get("id")
        if not identifier:
            raise PipelineError(
                "a WFS feature carries no id — cannot verify the grid "
                "arrives complete and unduplicated"
            )
        if identifier in identifiers:
            raise PipelineError(
                f"the WFS delivered feature {identifier!r} twice — "
                f"refusing to publish"
            )
        identifiers.add(identifier)
    declared = page.get("numberMatched")
    if declared is not None and declared != expected:
        raise PipelineError(
            f"the layer changed mid-fetch (hits said {expected}, the "
            f"response says {declared}); re-run the step"
        )
    if len(features) != expected:
        raise PipelineError(
            f"the WFS delivered {len(features)} features of a declared "
            f"{expected}; refusing to publish a partial grid"
        )
    return page_path


def write_grid(geojson_path, out, year=None):
    """Read the fetched features, clip to the capital-region extent,
    and write the GeoPackage (the source year as layer metadata)."""
    try:
        import geopandas
    except ImportError as error:
        raise PipelineError(
            "the population step needs geopandas (see pipeline/environment.yaml)"
        ) from error
    grid = geopandas.read_file(geojson_path)
    if grid.empty:
        raise PipelineError("the WFS returned no population-grid features")
    if config.POPULATION_COLUMN not in grid.columns:
        raise PipelineError(
            f"the grid has no {config.POPULATION_COLUMN!r} column "
            f"(columns: {sorted(grid.columns)}) — the HSY schema changed?"
        )
    # The features were requested in EPSG:3067; GeoServer's GeoJSON does
    # not label the CRS reliably, so declare what was requested.
    grid = grid.set_crs("EPSG:3067", allow_override=True)
    east_min, north_min, east_max, north_max = config.CAPITAL_REGION_BBOX_3067
    # Unusable geometries must fail loudly before the clip: a centroid
    # mask would silently drop them, publishing an unknowingly
    # incomplete grid.
    if grid.geometry.isna().any() or grid.geometry.is_empty.any():
        raise PipelineError(
            "the grid carries null or empty geometries — refusing to "
            "publish a silently incomplete grid"
        )
    # The published layer can extend past the shared extent; keep the
    # cells whose centroid falls inside it (the explicit edge rule), so
    # every asset covers one and the same region.
    centroids = grid.geometry.centroid
    inside = (
        (centroids.x >= east_min)
        & (centroids.x <= east_max)
        & (centroids.y >= north_min)
        & (centroids.y <= north_max)
    )
    grid = grid.loc[inside]
    if len(grid) < config.MIN_POPULATION_CELLS:
        raise PipelineError(
            f"only {len(grid)} grid cells fall inside the capital-region "
            f"extent (expected at least {config.MIN_POPULATION_CELLS}) — "
            f"a truncated or wrong layer"
        )
    # The values must be usable, not merely present: numeric, never
    # negative, and somebody actually lives in the region. The cells
    # must be the 250 m grid, not another layer's polygons.
    import pandas

    values = grid[config.POPULATION_COLUMN]
    if not pandas.api.types.is_numeric_dtype(values):
        raise PipelineError(
            f"{config.POPULATION_COLUMN!r} is not numeric — the HSY schema " f"changed?"
        )
    if (values.dropna() < 0).any():
        raise PipelineError("negative population counts in the grid")
    if not (values.dropna() > 0).any():
        raise PipelineError("no positive population counts in the grid")
    if not grid.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise PipelineError("the grid carries non-polygon geometries")
    if (~grid.geometry.is_valid).any():
        raise PipelineError("the grid carries invalid geometries")
    bounds = grid.bounds
    widths = bounds.maxx - bounds.minx
    heights = bounds.maxy - bounds.miny
    if ((widths - 250).abs() > 10).any() or ((heights - 250).abs() > 10).any():
        raise PipelineError("grid cells are not 250 m squares — wrong layer?")
    # The envelope alone admits triangles and holed shapes; the area of
    # a full 250 m cell does not.
    if ((grid.geometry.area - 62_500).abs() > 3_000).any():
        raise PipelineError("grid cells are not full 250 m squares — wrong layer?")
    metadata = {"source_year": str(year)} if year else None
    grid.to_file(out, layer="population_grid", driver="GPKG", metadata=metadata)
    return pathlib.Path(out)


def build(work_dir) -> dict:
    """Run the step; returns ``{asset name: manifest record}``."""
    work_dir = pathlib.Path(work_dir)
    with workdir_lock(work_dir):
        run_dir = download.run_directory(work_dir)
        try:
            capabilities = run_dir / "capabilities.xml"
            download.stream_download(
                capabilities_url(), capabilities, max_bytes=config.MAX_WFS_XML_BYTES
            )
            layer, year = latest_grid_layer(
                capabilities.read_text(encoding="utf-8", errors="replace")
            )
            features = fetch_features(layer, run_dir)
            out = download.staging_path(run_dir, config.POPULATION_ASSET)
            write_grid(features, out, year=year)
            stamp = f"HSY {layer} fetched {_utcnow()}"
            record = manifest.asset_record(
                out,
                name=config.POPULATION_ASSET,
                license=config.POPULATION_LICENSE,
                attribution=f"{config.POPULATION_ATTRIBUTION} {year}",
                source_stamp=stamp,
            )
            records = {config.POPULATION_ASSET: record}
            manifest.publish_transaction(
                work_dir,
                "manifest-population.json",
                records,
                {config.POPULATION_ASSET: out},
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
    return records


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_dir", help="directory for downloads and outputs")
    arguments = parser.parse_args()
    print(build(arguments.work_dir))
