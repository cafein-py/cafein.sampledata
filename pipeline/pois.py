"""The POI step: destination categories from the capital-region extract.

Reads the OSM extract the OSM step just published — so the points and
the street network cover one and the same area — and writes one
GeoPackage per category (``pipeline.config.POI_CATEGORIES``). Every
feature is served as a point: OSM tags these places as nodes and as
building footprints alike, and a single geometry type is what a
destination set is for.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import stat

from pipeline import PipelineError, config, download, manifest, workdir_lock


def tag_values(raw) -> dict:
    """One feature's free-form tags as a dict; pyrosm hands them over as
    a JSON string, and anything unparseable carries no tags."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def has_sport(feature_tags, sport) -> bool:
    """Whether a feature plays `sport`. OSM writes multiple sports as a
    semicolon list, so a substring test would match ``swimming_pool``
    or ``water_polo;swimming`` alike — the list is split and compared."""
    values = feature_tags.get("sport")
    if not isinstance(values, str):
        return False
    return sport in {value.strip() for value in values.split(";")}


def as_points(geometry):
    """Every geometry as a point: nodes stay put, ways and relations
    become their centroid, taken in the projected CRS so it is a metric
    centre rather than a degree-space approximation."""
    points = geometry.copy()
    areal = geometry.geom_type != "Point"
    if areal.any():
        points.loc[areal] = (
            geometry.loc[areal]
            .to_crs(config.CAPITAL_REGION_CRS)
            .centroid.to_crs(config.POI_CRS)
        )
    return points


def extract_category(reader, category, definition):
    """The category's features as a point GeoDataFrame."""
    import geopandas
    import pandas

    pois = reader.get_pois(custom_filter=dict(definition["tags"]))
    if pois is None or pois.empty:
        raise PipelineError(
            f"the extract carries no {category!r} features at all "
            f"({definition['tags']}) — the OSM tagging changed?"
        )
    pois = pois.reset_index(drop=True)
    # pyrosm promotes the tags it knows to columns and leaves the rest
    # in `tags` as JSON; `sport` lives there.
    parsed = [tag_values(value) for value in _column(pois, "tags", pandas)]
    sport = definition.get("sport")
    if sport:
        keep = pandas.Series([has_sport(feature, sport) for feature in parsed])
        pois = pois.loc[keep.values].reset_index(drop=True)
        parsed = [feature for feature, wanted in zip(parsed, keep) if wanted]
    if pois.geometry.isna().any() or pois.geometry.is_empty.any():
        raise PipelineError(
            f"{category!r} carries null or empty geometries — refusing "
            f"to publish a silently incomplete layer"
        )
    frame = geopandas.GeoDataFrame(
        {
            "osm_id": _column(pois, "id", pandas),
            "osm_type": _column(pois, "osm_type", pandas).astype("string"),
            "name": _column(pois, "name", pandas).astype("string"),
            "category": pandas.Series([category] * len(pois), dtype="string"),
            "tags": pandas.Series(
                [
                    json.dumps(feature, ensure_ascii=False, sort_keys=True)
                    for feature in parsed
                ],
                dtype="string",
            ),
        },
        geometry=as_points(pois.geometry).values,
        crs=config.POI_CRS,
    )
    return frame[list(config.POI_COLUMNS) + ["geometry"]]


def _column(frame, name, pandas):
    """A column of `frame`, or an all-missing one of the same length —
    pyrosm's columns follow the tags a filter happens to match, so a
    category whose features never carry `name` simply has no column."""
    if name in frame.columns:
        return frame[name].reset_index(drop=True)
    return pandas.Series([None] * len(frame), dtype="object")


def write_category(frame, out, category, definition):
    """Check the layer is usable and write its GeoPackage."""
    minimum = definition["minimum"]
    if len(frame) < minimum:
        raise PipelineError(
            f"only {len(frame)} {category!r} features (expected at least "
            f"{minimum}) — a tagging change upstream, not a thin month"
        )
    if not (frame.geometry.geom_type == "Point").all():
        raise PipelineError(f"{category!r} carries non-point geometries")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise PipelineError(f"{category!r} lost geometries in the point conversion")
    # An OSM id is unique only within its element type, so identity is
    # the (type, id) pair.
    identity = frame[["osm_type", "osm_id"]]
    if identity.isna().any(axis=None) or identity.duplicated().any():
        raise PipelineError(
            f"{category!r} carries missing or duplicated OSM ids — the "
            f"layer cannot identify its own features"
        )
    frame.to_file(out, layer=config.POI_LAYER, driver="GPKG")
    return pathlib.Path(out)


def extract_digest(work_dir) -> str:
    """The sha256 the OSM step recorded for the extract it published."""
    path = pathlib.Path(work_dir) / "manifest-osm.json"
    if not path.exists():
        raise PipelineError(
            f"no {path.name} in {work_dir} — run the OSM step before the "
            f"POI step so both cover the same area"
        )
    assets = manifest.read_manifest(path)["assets"]
    if set(assets) != {config.OSM_ASSET}:
        raise PipelineError(
            f"{path.name} describes {sorted(assets)}, expected exactly "
            f"[{config.OSM_ASSET!r}]"
        )
    return assets[config.OSM_ASSET]["sha256"]


def snapshot_extract(source, run_dir):
    """A private copy of the extract to read, verified against the OSM
    step's manifest.

    The POIs are cut from this copy and never from the work-tree path:
    a file that is hashed, then read, then hashed again can be swapped
    and restored around the reads, and the layers would carry the
    published extract's digest while holding another snapshot's places.
    """
    source = pathlib.Path(source)
    if not source.exists():
        raise PipelineError(
            f"no OSM extract at {source} — run the OSM step before the "
            f"POI step so both cover the same area"
        )
    expected = extract_digest(source.parent)
    target = pathlib.Path(run_dir) / config.OSM_ASSET
    # O_NOFOLLOW refuses a symlink at open time rather than after a
    # separate check, and the copy then reads that one descriptor: the
    # bytes cannot be exchanged for another file's between the check
    # and the read.
    try:
        handle = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise PipelineError(
            f"cannot read the OSM extract at {source}: {error}"
        ) from error
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise PipelineError(f"{source} is not a regular file")
        with open(handle, "rb", closefd=False) as reading:
            with open(target, "xb") as writing:
                shutil.copyfileobj(reading, writing)
    finally:
        os.close(handle)
    digest, _ = manifest.file_digest(target)
    if digest != expected:
        raise PipelineError(
            f"{source} does not match manifest-osm.json ({expected} "
            f"recorded, {digest} on disk) — re-run the OSM step"
        )
    return target, digest


def build(work_dir) -> dict:
    """Run the step; returns ``{asset name: manifest record}``.

    The source is always the extract in the working directory — the one
    the OSM step published in this same run. There is deliberately no
    override: a POI layer cut from another snapshot than the extract the
    release ships would disagree with it about what exists and where,
    and nothing downstream could tell.
    """
    work_dir = pathlib.Path(work_dir)
    with workdir_lock(work_dir):
        try:
            from pyrosm import OSM
        except ImportError as error:
            raise PipelineError(
                "the POI step needs pyrosm >= 0.13 and geopandas "
                "(conda-forge; see the pipeline environment)"
            ) from error
        run_dir = download.run_directory(work_dir)
        try:
            source, digest = snapshot_extract(work_dir / config.OSM_ASSET, run_dir)
            reader = OSM(str(source))
            records, staged = {}, {}
            for category, definition in config.POI_CATEGORIES.items():
                asset = config.POI_ASSETS[category]
                out = download.staging_path(run_dir, asset)
                frame = extract_category(reader, category, definition)
                write_category(frame, out, category, definition)
                records[asset] = manifest.asset_record(
                    out,
                    name=asset,
                    license=config.POI_LICENSE,
                    attribution=config.POI_ATTRIBUTION,
                    source_stamp=(
                        f"extracted from {config.OSM_ASSET} "
                        f"sha256={digest} as {category}"
                    ),
                )
                staged[asset] = out
            # Re-hashing the copy after the reads brackets them, the way
            # the OSM step brackets its clip: bytes that changed under
            # the reader must not pass provenance to the layers.
            after, _ = manifest.file_digest(source)
            if after != digest:
                raise PipelineError(
                    f"the extract changed while the POIs were extracted "
                    f"({digest} -> {after}); the layers have no "
                    f"trustworthy provenance"
                )
            manifest.publish_transaction(
                work_dir, "manifest-pois.json", records, staged
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_dir", help="directory holding the OSM extract")
    arguments = parser.parse_args()
    print(build(arguments.work_dir))
