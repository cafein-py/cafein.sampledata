"""The noise step: Helsinki's meluselvitys 2022 zone polygons as one
GeoPackage.

Immutability is per RESOURCE: every source x metric input pins its
exact CKAN resource id, download URL, and sha256 in
``config.NOISE_RESOURCES`` — captured once from the live dataset with
``python -m pipeline.noise --discover`` — and the build verifies each
download against its pin. The CKAN API serves only as a drift alarm
when a pinned id stops resolving. Zones normalize to columns
``source``/``metric``/``db_low``/``db_high`` (``db_high`` nullable —
the open-ended top class), polygons in EPSG:3067.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil

from pipeline import PipelineError, config, download, manifest, workdir_lock


def package_url() -> str:
    return f"{config.HRI_CKAN_URL}/package_show?id={config.NOISE_CKAN_PACKAGE}"


def discover(work_dir) -> None:
    """Print the live dataset's resources — id, url, sha256 after a
    verification download — for pasting into ``NOISE_RESOURCES``."""
    work_dir = pathlib.Path(work_dir)
    run_dir = download.run_directory(work_dir)
    try:
        listing = run_dir / "package.json"
        download.stream_download(
            package_url(), listing, max_bytes=config.MAX_WFS_XML_BYTES
        )
        payload = json.loads(listing.read_text(encoding="utf-8"))
        resources = payload.get("result", {}).get("resources", [])
        if not resources:
            raise PipelineError(
                f"CKAN package {config.NOISE_CKAN_PACKAGE!r} lists no "
                "resources — wrong package id?"
            )
        for resource in resources:
            target = run_dir / "resource.bin"
            download.stream_download(
                resource["url"], target, max_bytes=config.MAX_NOISE_RESOURCE_BYTES
            )
            digest, size = manifest.file_digest(target)
            print(
                f"{resource['id']}  {resource.get('name', '?')!r}  "
                f"{resource.get('format', '?')}  {size} bytes\n"
                f"  url: {resource['url']}\n  sha256: {digest}"
            )
            target.unlink()
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def pinned_resources():
    """The pin entries as a validated mapping: every source x metric
    combo exactly once — a repeated entry is refused, never silently
    overwritten (the config is a sequence for exactly this reason)."""
    if not config.NOISE_RESOURCES:
        raise PipelineError(
            "NOISE_RESOURCES is uncaptured — run "
            "`python -m pipeline.noise --discover <work_dir>` once (in an "
            "environment that reaches hri.fi) and paste the pins into "
            "pipeline/config.py"
        )
    pins = {}
    for source, metric, resource_id, url, sha256 in config.NOISE_RESOURCES:
        combo = (source, metric)
        if combo in pins:
            raise PipelineError(
                f"NOISE_RESOURCES pins {combo} twice — missing or "
                "duplicate source x metric combinations"
            )
        pins[combo] = (resource_id, url, sha256)
    expected = {
        (source, metric)
        for source in config.NOISE_SOURCES
        for metric in config.NOISE_METRICS
    }
    if set(pins) != expected:
        raise PipelineError(
            f"NOISE_RESOURCES covers {sorted(pins)}, expected exactly "
            f"{sorted(expected)} — missing or duplicate source x metric "
            "combinations"
        )
    return pins


def alarm_on_drift(run_dir) -> None:
    """A pinned resource id that no longer resolves fails the run with
    the package's current listing — never a silent substitution."""
    listing = run_dir / "package.json"
    download.stream_download(
        package_url(), listing, max_bytes=config.MAX_WFS_XML_BYTES
    )
    payload = json.loads(listing.read_text(encoding="utf-8"))
    live = {
        resource["id"]: resource.get("name", "?")
        for resource in payload.get("result", {}).get("resources", [])
    }
    missing = [
        ((source, metric), pinned_id)
        for source, metric, pinned_id, _, _ in config.NOISE_RESOURCES
        if pinned_id not in live
    ]
    if missing:
        raise PipelineError(
            f"pinned noise resources {missing} no longer exist in CKAN "
            f"package {config.NOISE_CKAN_PACKAGE!r}; the dataset now "
            f"lists {live} — re-verify and repin before publishing"
        )


def normalized_zones(fetched, out) -> pathlib.Path:
    """Every fetched source x metric layer into one GeoPackage with
    the class-bound schema."""
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
            raise PipelineError(
                f"{source} x {metric} carries null or empty geometries"
            )
        if not zones.geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
            raise PipelineError(
                f"{source} x {metric} carries non-polygon geometries "
                f"({sorted(zones.geometry.geom_type.unique())}) — a "
                "mislabeled resource?"
            )
        low, high = _class_bounds(zones, source, metric)
        frame = geopandas.GeoDataFrame(
            {
                "source": source,
                "metric": metric,
                "db_low": low,
                "db_high": high,
            },
            geometry=zones.geometry,
            crs=zones.crs,
        ).to_crs("EPSG:3067")
        frames.append(frame)
    merged = geopandas.GeoDataFrame(
        pandas.concat(frames, ignore_index=True), crs="EPSG:3067"
    )
    _validate_bounds(merged)
    merged.to_file(out, layer="noise_zones", driver="GPKG")
    return pathlib.Path(out)


def _class_bounds(zones, source, metric):
    """The dB class bounds from the published attribute — the exact
    column and encoding are pinned here once the resources are; until
    then this names what it found."""
    candidates = [
        column
        for column in zones.columns
        if column.lower() in ("db_lo", "db_low", "melutaso", "luokka", "db")
    ]
    if not candidates:
        raise PipelineError(
            f"{source} x {metric}: no recognised class column (columns: "
            f"{sorted(zones.columns)}) — pin the schema after --discover"
        )
    import pandas

    raw = zones[candidates[0]].astype(str).str.strip()
    # The published classes read like "55-59" or ">= 70" / "yli 70".
    bounds = raw.str.extract(r"^(?P<low>\d+)\s*-\s*(?P<high>\d+)$")
    open_top = raw.str.extract(r"^(?:>=?|yli)\s*(?P<low>\d+)$")
    low = pandas.to_numeric(bounds["low"]).fillna(pandas.to_numeric(open_top["low"]))
    high = pandas.to_numeric(bounds["high"])
    unparsed = raw[low.isna()]
    if len(unparsed):
        raise PipelineError(
            f"{source} x {metric}: unparsed class values "
            f"{sorted(unparsed.unique())[:5]!r} — pin the encoding after "
            "--discover"
        )
    return low.astype("float64"), high.astype("float64")


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


def build(work_dir) -> dict:
    """Run the step; returns ``{asset name: manifest record}``."""
    work_dir = pathlib.Path(work_dir)
    pins = pinned_resources()
    with workdir_lock(work_dir):
        run_dir = download.run_directory(work_dir)
        try:
            alarm_on_drift(run_dir)
            fetched = {}
            for combo, (resource_id, url, sha256) in sorted(pins.items()):
                target = run_dir / f"{combo[0]}_{combo[1]}"
                download.stream_download(
                    url, target, max_bytes=config.MAX_NOISE_RESOURCE_BYTES
                )
                digest, _ = manifest.file_digest(target)
                if digest != sha256:
                    raise PipelineError(
                        f"noise resource {resource_id} ({combo}) hashes to "
                        f"{digest}, pinned {sha256} — the published bytes "
                        "changed; re-verify and repin"
                    )
                fetched[combo] = target
            out = download.staging_path(run_dir, config.NOISE_ASSET)
            normalized_zones(fetched, out)
            identifiers = ", ".join(
                pins[combo][0] for combo in sorted(pins)
            )
            stamp = (
                f"HRI {config.NOISE_CKAN_PACKAGE} resources {identifiers} "
                f"fetched {_utcnow()}, sha256-pinned, zones normalized to "
                "EPSG:3067"
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
    parser.add_argument(
        "--discover",
        action="store_true",
        help="print the live CKAN resources for pinning, then exit",
    )
    arguments = parser.parse_args()
    if arguments.discover:
        discover(arguments.work_dir)
    else:
        print(build(arguments.work_dir))
