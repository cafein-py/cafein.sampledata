"""The noise step: Helsinki's meluselvitys 2022 zone polygons as one
GeoPackage, over the city's open WFS.

HRI's dataset publishes the WMS/WFS endpoints, not files, so the
eight source x metric layers are pinned by their exact 2022 type
names and fetched like the population grid: a hits request declares
the count, one bounded GetFeature delivers the zones in EPSG:3067,
and completeness and duplicate ids are verified before anything is
published. The published schema carries numeric ``db_lo``/``db_hi``
per zone; the asset normalizes to ``source``/``metric``/``db_low``/
``db_high`` (``db_high`` nullable — an open-ended top class), the
zone geometry unchanged.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import shutil

from pipeline import PipelineError, config, download, manifest, workdir_lock


def feature_url(layer, *, hits=False) -> str:
    url = (
        f"{config.NOISE_WFS_URL}?service=WFS&version=2.0.0&request=GetFeature"
        f"&typeNames={layer}&srsName=EPSG:3067"
    )
    if hits:
        return url + "&resultType=hits"
    return f"{url}&outputFormat=application/json&count={config.MAX_NOISE_FEATURES}"


def matched_count(hits_xml: str, layer) -> int:
    match = re.search(r'numberMatched="(\d+)"', hits_xml)
    if not match:
        raise PipelineError(
            f"the WFS hits response for {layer} carries no numberMatched — "
            "cannot verify the zones arrive complete (a renamed 2022 "
            "layer?)"
        )
    return int(match.group(1))


def fetch_zones(layer, run_dir, name) -> pathlib.Path:
    """One layer fetched complete and verified, the population-grid
    pattern: hits first, one bounded request, count and id checks."""
    hits = run_dir / f"{name}.hits.xml"
    download.stream_download(
        feature_url(layer, hits=True), hits, max_bytes=config.MAX_WFS_XML_BYTES
    )
    expected = matched_count(hits.read_text(encoding="utf-8", errors="replace"), layer)
    if expected == 0:
        raise PipelineError(f"{layer} declares zero zones — a wrong layer?")
    if expected > config.MAX_NOISE_FEATURES:
        raise PipelineError(
            f"{layer} declares {expected} zones — implausible; refusing"
        )
    page = run_dir / f"{name}.json"
    download.stream_download(
        feature_url(layer), page, max_bytes=config.MAX_NOISE_RESPONSE_BYTES
    )
    payload = json.loads(page.read_text(encoding="utf-8"))
    features = payload.get("features", [])
    identifiers = set()
    for feature in features:
        identifier = feature.get("id")
        if not identifier:
            raise PipelineError(f"{layer}: a zone carries no id")
        if identifier in identifiers:
            raise PipelineError(f"{layer}: zone {identifier!r} delivered twice")
        identifiers.add(identifier)
        # Only here, on the raw JSON, is a NaN/Infinity literal still
        # distinguishable from a genuine null — downstream both read
        # as missing. Refuse it before it can pose as an open class.
        properties = feature.get("properties") or {}
        for key in ("db_lo", "db_hi"):
            value = properties.get(key)
            if isinstance(value, float) and not math.isfinite(value):
                raise PipelineError(
                    f"{layer}: zone {identifier!r} carries a non-finite "
                    f"{key} literal — refusing"
                )
    declared = payload.get("numberMatched")
    if declared is not None and declared != expected:
        raise PipelineError(
            f"{layer} changed mid-fetch (hits {expected}, response "
            f"{declared}); re-run the step"
        )
    if len(features) != expected:
        raise PipelineError(
            f"{layer} delivered {len(features)} of {expected} zones; "
            "refusing a partial layer"
        )
    return page


def normalized_zones(fetched, out) -> pathlib.Path:
    """Every fetched source x metric layer into one GeoPackage with
    the class-bound schema, geometry unchanged."""
    try:
        import geopandas
        import pandas
    except ImportError as error:
        raise PipelineError(
            "the noise step needs geopandas (see pipeline/environment.yaml)"
        ) from error

    frames = []
    for (source, metric), path in sorted(fetched.items()):
        zones = geopandas.read_file(path)
        if zones.empty:
            raise PipelineError(f"{source} x {metric} carries no zones")
        if zones.geometry.isna().any() or zones.geometry.is_empty.any():
            raise PipelineError(f"{source} x {metric} carries null or empty geometries")
        if not zones.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
            raise PipelineError(
                f"{source} x {metric} carries non-polygon geometries "
                f"({sorted(zones.geometry.geom_type.unique())})"
            )
        for column in ("db_lo", "db_hi"):
            if column not in zones.columns:
                raise PipelineError(
                    f"{source} x {metric} has no {column!r} column "
                    f"(columns: {sorted(zones.columns)}) — the published "
                    "schema changed"
                )
        low = pandas.to_numeric(zones["db_lo"], errors="coerce")
        raw_high = zones["db_hi"]
        high = pandas.to_numeric(raw_high, errors="coerce")
        if low.isna().any():
            raise PipelineError(f"{source} x {metric}: non-numeric db_lo values")
        # Null db_high means the open-ended top class — but only a
        # genuinely null source value may say so. Any non-null value
        # that does not parse ("70+", "", whitespace) must refuse,
        # never silently publish as open-ended.
        garbled = high.isna() & raw_high.notna()
        if garbled.any():
            values = sorted(raw_high[garbled].astype(str).unique())[:5]
            raise PipelineError(
                f"{source} x {metric}: non-numeric db_hi values "
                f"{values!r} — refusing to publish them as open-ended "
                "classes"
            )
        # JSON parsers admit Infinity/NaN literals: only FINITE bounds
        # are publishable (a NaN db_lo is already caught above).
        import numpy

        if numpy.isinf(low).any() or numpy.isinf(high.fillna(0)).any():
            raise PipelineError(f"{source} x {metric}: non-finite dB bounds — refusing")
        # The requested features arrive in EPSG:3067; GeoServer's
        # GeoJSON does not label the CRS reliably, so declare what was
        # requested.
        frame = geopandas.GeoDataFrame(
            {
                "source": source,
                "metric": metric,
                "db_low": low.astype("float64"),
                "db_high": high.astype("Float64"),
            },
            geometry=zones.geometry,
            crs=None,
        ).set_crs("EPSG:3067", allow_override=True)
        frames.append(frame)
    merged = geopandas.GeoDataFrame(
        pandas.concat(frames, ignore_index=True), crs="EPSG:3067"
    )
    _validate_bounds(merged)
    merged.to_file(out, layer="noise_zones", driver="GPKG")
    return pathlib.Path(out)


def _validate_bounds(merged) -> None:
    both = merged.dropna(subset=["db_high"])
    if (both["db_low"] >= both["db_high"]).any():
        raise PipelineError("db_low >= db_high in a bounded class")
    for (source, metric), group in merged.groupby(["source", "metric"]):
        open_rows = group[group["db_high"].isna()]
        if open_rows.empty:
            continue
        top = group["db_low"].max()
        if (open_rows["db_low"] != top).any():
            raise PipelineError(
                f"{source} x {metric}: a null db_high outside the top "
                "class — the encoding drifted"
            )


def pinned_layers():
    """The layer table, validated complete: every source x metric
    combo exactly once (the config is a sequence so duplicates are
    detectable)."""
    layers = {}
    for source, metric, layer in config.NOISE_LAYERS:
        combo = (source, metric)
        if combo in layers:
            raise PipelineError(
                f"NOISE_LAYERS pins {combo} twice — missing or duplicate "
                "source x metric combinations"
            )
        layers[combo] = layer
    expected = {
        (source, metric)
        for source in config.NOISE_SOURCES
        for metric in config.NOISE_METRICS
    }
    if set(layers) != expected:
        raise PipelineError(
            f"NOISE_LAYERS covers {sorted(layers)}, expected exactly "
            f"{sorted(expected)}"
        )
    return layers


def build(work_dir) -> dict:
    """Run the step; returns ``{asset name: manifest record}``."""
    work_dir = pathlib.Path(work_dir)
    layers = pinned_layers()
    with workdir_lock(work_dir):
        run_dir = download.run_directory(work_dir)
        try:
            fetched = {}
            for combo, layer in sorted(layers.items()):
                fetched[combo] = fetch_zones(layer, run_dir, f"{combo[0]}_{combo[1]}")
            out = download.staging_path(run_dir, config.NOISE_ASSET)
            normalized_zones(fetched, out)
            names = ", ".join(layers[combo] for combo in sorted(layers))
            stamp = (
                f"kartta.hel.fi WFS layers {names} fetched {_utcnow()}, "
                "zones normalized to source/metric/db_low/db_high in "
                "EPSG:3067, geometry unchanged"
            )
            record = manifest.asset_record(
                out,
                name=config.NOISE_ASSET,
                license=config.NOISE_LICENSE,
                attribution=config.NOISE_ATTRIBUTION,
                source_stamp=stamp,
            )
            records = {config.NOISE_ASSET: record}
            manifest.publish_transaction(
                work_dir,
                "manifest-noise.json",
                records,
                {config.NOISE_ASSET: out},
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
