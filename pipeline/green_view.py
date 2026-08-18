"""The green view step: the published Green View Index dataset for the
capital region as one GeoPackage.

Street-level visible greenery from Google Street View panoramas via
semantic segmentation (Data in Brief, S2352340920304959). The two
supplement archives are immutable published bytes fetched from the
publisher and verified against pinned sha256 values; the data itself
stays verbatim — normalization touches only the container (one
GeoPackage, layers ``points`` and ``roads``), the CRS
(EPSG:3067, authority-tagged), and nothing else.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import zipfile

from pipeline import PipelineError, config, download, manifest, workdir_lock


def verified_supplement(url, expected_sha256, run_dir, name) -> pathlib.Path:
    """Download one supplement archive and verify its pinned bytes."""
    archive = run_dir / name
    download.stream_download(
        url, archive, max_bytes=config.MAX_GREEN_VIEW_ZIP_BYTES
    )
    digest, _ = manifest.file_digest(archive)
    if digest != expected_sha256:
        raise PipelineError(
            f"supplement {url} hashes to {digest}, expected "
            f"{expected_sha256} — the published bytes changed; verify "
            "the article's supplement files before repinning"
        )
    return archive


def extracted_member(archive, member, run_dir) -> pathlib.Path:
    """Extract the archive's single expected GeoPackage member."""
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if names != [member]:
            raise PipelineError(
                f"{archive.name} contains {names!r}, expected exactly "
                f"[{member!r}] — the supplement packaging changed"
            )
        bundle.extract(member, run_dir)
    return run_dir / member


def normalized_layers(points_path, roads_path, out) -> pathlib.Path:
    """Both published layers into one GeoPackage, EPSG:3067, columns
    verbatim (the published ``lattitude`` spelling included)."""
    try:
        import geopandas
    except ImportError as error:
        raise PipelineError(
            "the green view step needs geopandas (see "
            "pipeline/environment.yaml)"
        ) from error

    points = geopandas.read_file(points_path)
    if len(points) != config.GREEN_VIEW_POINT_COUNT:
        raise PipelineError(
            f"the points layer carries {len(points)} features, the "
            f"published dataset has {config.GREEN_VIEW_POINT_COUNT} — "
            "a partial or substituted supplement"
        )
    if "Gvi_Mean" not in points.columns:
        raise PipelineError(
            f"the points layer has no Gvi_Mean column (columns: "
            f"{sorted(points.columns)})"
        )
    values = points["Gvi_Mean"].dropna()
    if ((values < 0) | (values > 100)).any():
        raise PipelineError("Gvi_Mean outside [0, 100] — wrong layer?")
    points = points.to_crs("EPSG:3067")

    roads = geopandas.read_file(roads_path)
    if len(roads) != config.GREEN_VIEW_ROAD_COUNT:
        raise PipelineError(
            f"the roads layer carries {len(roads)} features, the "
            f"published dataset has {config.GREEN_VIEW_ROAD_COUNT} — "
            "a partial or substituted supplement"
        )
    for column in ("GSV_GVI", "LU_GVI", "Comb_GVI", "GVI_source"):
        if column not in roads.columns:
            raise PipelineError(
                f"the roads layer has no {column!r} column (columns: "
                f"{sorted(roads.columns)})"
            )
    for column in ("GSV_GVI", "LU_GVI", "Comb_GVI"):
        values = roads[column].dropna()
        if ((values < 0) | (values > 100)).any():
            raise PipelineError(f"{column} outside [0, 100] — wrong layer?")
    # The published roads file carries TM35FIN as an authority-less
    # WKT; tag the EPSG code it is (same axes, same datum), never
    # reproject what is already metric.
    roads = roads.set_crs("EPSG:3067", allow_override=True)

    out = pathlib.Path(out)
    points.to_file(out, layer="points", driver="GPKG")
    roads.to_file(out, layer="roads", driver="GPKG")
    return out


def build(work_dir) -> dict:
    """Run the step; returns ``{asset name: manifest record}``."""
    work_dir = pathlib.Path(work_dir)
    with workdir_lock(work_dir):
        run_dir = download.run_directory(work_dir)
        try:
            (points_url, points_sha, points_member) = config.GREEN_VIEW_SUPPLEMENTS[0]
            (roads_url, roads_sha, roads_member) = config.GREEN_VIEW_SUPPLEMENTS[1]
            points_zip = verified_supplement(
                points_url, points_sha, run_dir, "mmc2.zip"
            )
            roads_zip = verified_supplement(roads_url, roads_sha, run_dir, "mmc3.zip")
            points_path = extracted_member(points_zip, points_member, run_dir)
            roads_path = extracted_member(roads_zip, roads_member, run_dir)
            out = download.staging_path(run_dir, config.GREEN_VIEW_ASSET)
            normalized_layers(points_path, roads_path, out)
            stamp = (
                f"publisher supplements {points_url} and {roads_url} "
                f"(sha256 {points_sha[:12]}…, {roads_sha[:12]}…) fetched "
                f"{_utcnow()}, layers normalized to EPSG:3067 with "
                "columns verbatim"
            )
            record = manifest.asset_record(
                out,
                name=config.GREEN_VIEW_ASSET,
                license=config.GREEN_VIEW_LICENSE,
                attribution=config.GREEN_VIEW_ATTRIBUTION,
                source_stamp=stamp,
            )
            records = {config.GREEN_VIEW_ASSET: record}
            manifest.publish_transaction(
                work_dir,
                "manifest-green-view.json",
                records,
                {config.GREEN_VIEW_ASSET: out},
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
