"""The Paavo step: Statistics Finland's income variables by postal code
area, as a GeoPackage.

Paavo is published per year as a WFS layer
(``postialue:pno_tilasto_<year>``); the newest is discovered from
GetCapabilities, and the capital-region subset is fetched as GeoJSON in
EPSG:3067 — the extent as a server-side bbox filter, the columns pinned
by ``propertyName`` so a Paavo schema change fails the request instead
of shipping a different layer. Privacy-protected sentinel values are
replaced with nulls, the areas clipped to the shared extent, and the
result written to GeoPackage.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil

from pipeline import PipelineError, config, download, manifest, workdir_lock


def latest_statistics_layer(capabilities_xml: str):
    """The newest ``pno_tilasto_<year>`` layer in a WFS GetCapabilities
    document, as ``(layer name, year)``."""
    prefix = re.escape(config.PAAVO_LAYER_PREFIX)
    years = [int(year) for year in re.findall(rf"{prefix}(\d{{4}})", capabilities_xml)]
    if not years:
        raise PipelineError(
            f"no {config.PAAVO_LAYER_PREFIX}<year> layer in the WFS "
            f"capabilities — the Paavo layer naming changed?"
        )
    year = max(years)
    return f"{config.PAAVO_LAYER_PREFIX}{year}", year


def capabilities_url() -> str:
    return f"{config.PAAVO_WFS_URL}?service=WFS&version=2.0.0&request=GetCapabilities"


def bbox_parameter() -> str:
    """The capital-region extent as a WFS bbox filter, in the axis
    order the EPSG:3067 URN dictates."""
    east_min, north_min, east_max, north_max = config.CAPITAL_REGION_BBOX_3067
    return (
        f"{east_min},{north_min},{east_max},{north_max}," f"urn:ogc:def:crs:EPSG::3067"
    )


def feature_url(layer, *, hits=False) -> str:
    """One bounded GetFeature over the extent. The geometry property
    must be named alongside the columns — propertyName otherwise
    excludes it from the response."""
    url = (
        f"{config.PAAVO_WFS_URL}?service=WFS&version=2.0.0&request=GetFeature"
        f"&typeNames={layer}&srsName=EPSG:3067&bbox={bbox_parameter()}"
    )
    if hits:
        return url + "&resultType=hits"
    columns = ",".join(
        (config.PAAVO_GEOMETRY_PROPERTY,) + config.STATISTICS_INCOME_COLUMNS
    )
    return (
        f"{url}&outputFormat=application/json"
        f"&count={config.MAX_PAAVO_FEATURES}&propertyName={columns}"
    )


def matched_count(hits_xml: str) -> int:
    """The numberMatched a WFS hits response declares."""
    match = re.search(r'numberMatched="(\d+)"', hits_xml)
    if not match:
        raise PipelineError(
            "the WFS hits response carries no numberMatched — cannot "
            "verify the Paavo layer arrives complete"
        )
    return int(match.group(1))


def fetch_features(layer, run_dir) -> pathlib.Path:
    """Fetch the extent's areas in one bounded request, verified
    complete against the server's own numberMatched — a response cap or
    a mid-fetch layer change must never publish a partial layer."""
    import json

    hits = run_dir / "hits.xml"
    download.stream_download(
        feature_url(layer, hits=True), hits, max_bytes=config.MAX_WFS_XML_BYTES
    )
    expected = matched_count(hits.read_text(encoding="utf-8", errors="replace"))
    if expected > config.MAX_PAAVO_FEATURES:
        raise PipelineError(
            f"the WFS declares {expected} features — implausible for the "
            f"capital region's postal-code areas; refusing to fetch"
        )
    page_path = run_dir / "paavo.json"
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
                "a WFS feature carries no id — cannot verify the layer "
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
            f"{expected}; refusing to publish a partial layer"
        )
    return page_path


def null_protected(areas):
    """Replace Paavo's privacy sentinel with nulls, in place.

    Only the exact sentinel is nulled; any other negative in a
    disposable-income or count column is not a documented marker and
    fails the step rather than being guessed at.
    """
    import numpy
    import pandas

    for column in config.STATISTICS_INCOME_VALUES:
        values = areas[column]
        # Booleans count as numeric to pandas, and True==1 would sail
        # through every value check below as a plausible count.
        if not pandas.api.types.is_numeric_dtype(
            values
        ) or pandas.api.types.is_bool_dtype(values):
            raise PipelineError(
                f"{column!r} is not numeric — the Paavo schema changed?"
            )
        areas[column] = values.mask(values == config.PAAVO_PROTECTED_SENTINEL)
        remaining = areas[column].dropna()
        if not numpy.isfinite(remaining).all():
            raise PipelineError(
                f"{column!r} carries non-finite values — a number "
                f"overflowed on the way in; refusing to publish"
            )
        if (remaining < 0).any():
            raise PipelineError(
                f"{column!r} carries negative values besides the "
                f"documented {config.PAAVO_PROTECTED_SENTINEL} sentinel — "
                f"an undocumented marker; refusing to publish"
            )
    return areas


def write_layer(geojson_path, out, year=None):
    """Read the fetched areas, null the protected values, clip to the
    capital-region extent, and write the GeoPackage (the source year as
    layer metadata)."""
    try:
        import geopandas
    except ImportError as error:
        raise PipelineError(
            "the Paavo step needs geopandas (see pipeline/environment.yaml)"
        ) from error
    areas = geopandas.read_file(geojson_path)
    if areas.empty:
        raise PipelineError("the WFS returned no postal-code areas")
    missing = set(config.STATISTICS_INCOME_COLUMNS) - set(areas.columns)
    if missing:
        raise PipelineError(
            f"the layer has no column(s) {sorted(missing)} — the Paavo "
            f"schema changed?"
        )
    # The features were requested in EPSG:3067; GeoServer's GeoJSON does
    # not label the CRS reliably, so declare what was requested.
    areas = areas.set_crs("EPSG:3067", allow_override=True)
    if areas.geometry.isna().any() or areas.geometry.is_empty.any():
        raise PipelineError(
            "the layer carries null or empty geometries — refusing to "
            "publish a silently incomplete layer"
        )
    if not areas.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise PipelineError("the layer carries non-polygon geometries")
    if (~areas.geometry.is_valid).any():
        raise PipelineError("the layer carries invalid geometries")
    # A postal code is a five-digit *string*: served as a number it
    # would shed its leading zero (00100 -> 100) and break every join
    # against it, while still looking like a valid layer.
    codes = areas["postinumeroalue"]
    if (
        not codes.map(lambda code: isinstance(code, str)).all()
        or not codes.str.fullmatch(r"[0-9]{5}").all()
    ):
        raise PipelineError(
            "the layer carries postal codes that are not five-digit "
            "strings — the Paavo schema changed?"
        )
    if codes.duplicated().any():
        raise PipelineError(
            "the layer carries duplicated postal codes — it cannot "
            "identify its own areas"
        )
    names = areas["nimi"]
    if not names.map(lambda name: isinstance(name, str) and name.strip() != "").all():
        raise PipelineError(
            "the layer carries missing or empty area names — the Paavo "
            "schema changed?"
        )
    # The municipality code is a three-digit string with the same
    # leading-zero hazard as the postal code (049 Espoo, 091 Helsinki).
    kunta = areas["kunta"]
    if (
        not kunta.map(lambda code: isinstance(code, str)).all()
        or not kunta.str.fullmatch(r"[0-9]{3}").all()
    ):
        raise PipelineError(
            "the layer carries municipality codes that are not "
            "three-digit strings — the Paavo schema changed?"
        )
    areas = null_protected(areas)
    # The bbox filter admits every area *touching* the extent; keep the
    # ones whose centroid falls inside it (the explicit edge rule), so
    # every asset covers one and the same region.
    east_min, north_min, east_max, north_max = config.CAPITAL_REGION_BBOX_3067
    centroids = areas.geometry.centroid
    inside = (
        (centroids.x >= east_min)
        & (centroids.x <= east_max)
        & (centroids.y >= north_min)
        & (centroids.y <= north_max)
    )
    areas = areas.loc[inside]
    if len(areas) < config.MIN_STATISTICS_AREAS:
        raise PipelineError(
            f"only {len(areas)} postal-code areas centre inside the "
            f"capital-region extent (expected at least "
            f"{config.MIN_STATISTICS_AREAS}) — a truncated or wrong layer"
        )
    # Somebody must earn something somewhere: an all-null or all-zero
    # income layer is a wrong or empty variable set, not a poor region.
    medians = areas["hr_mtu"].dropna()
    if not (medians > 0).any():
        raise PipelineError("no positive median incomes in the layer")
    # And every advertised variable must actually carry values — a
    # column nulled everywhere is an upstream change, not privacy.
    for column in config.STATISTICS_INCOME_VALUES:
        if areas[column].dropna().empty:
            raise PipelineError(
                f"{column!r} carries no values at all in the retained "
                f"areas — the Paavo variable disappeared?"
            )
    areas = areas[list(config.STATISTICS_INCOME_COLUMNS) + ["geometry"]]
    metadata = {"source_year": str(year)} if year else None
    areas.to_file(out, layer="income", driver="GPKG", metadata=metadata)
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
            layer, year = latest_statistics_layer(
                capabilities.read_text(encoding="utf-8", errors="replace")
            )
            features = fetch_features(layer, run_dir)
            out = download.staging_path(run_dir, config.STATISTICS_INCOME_ASSET)
            write_layer(features, out, year=year)
            stamp = (
                f"Statistics Finland {layer} fetched {_utcnow()}, "
                "privacy-protected values nulled"
            )
            record = manifest.asset_record(
                out,
                name=config.STATISTICS_INCOME_ASSET,
                license=config.STATISTICS_LICENSE,
                attribution=f"{config.STATISTICS_ATTRIBUTION} {year}",
                source_stamp=stamp,
            )
            records = {config.STATISTICS_INCOME_ASSET: record}
            manifest.publish_transaction(
                work_dir,
                "manifest-paavo.json",
                records,
                {config.STATISTICS_INCOME_ASSET: out},
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
