"""The OSM step: Geofabrik Finland, clipped to the capital region.

Reads the country extract with pyrosm's ``bounding_box`` filter and
writes the cached (clipped) dataset back out with ``write_pbf`` under
zero edits — a faithful, re-readable extract of everything inside the
box.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil

from pipeline import PipelineError, config, download, manifest, workdir_lock


def clip_capital_region(
    source, out, bbox=config.CAPITAL_REGION_BBOX, engine="in_memory"
):
    """Clip `source` to `bbox` and write a valid PBF at `out`."""
    west, south, east, north = bbox
    if not (west < east and south < north):
        raise PipelineError(f"malformed bounding box {bbox!r}")
    try:
        import geopandas
        from pyrosm import OSM
    except ImportError as error:
        raise PipelineError(
            "the OSM step needs pyrosm >= 0.13 and geopandas "
            "(conda-forge; see the pipeline environment)"
        ) from error
    reader = OSM(os.fspath(source), bounding_box=list(bbox), engine=engine)
    # Zero edits: an empty frame updates nothing, so the whole cached
    # (bbox-filtered) dataset is written through unchanged.
    no_edits = geopandas.GeoDataFrame(columns=["id", "osm_type", "geometry"])
    reader.write_pbf(no_edits, os.fspath(out))
    return pathlib.Path(out)


def finland_url(snapshot_date=None) -> str:
    """The source URL: the immutable dated snapshot when a YYMMDD date
    is given (what release runs do), else the mutable `latest`."""
    if snapshot_date:
        if len(snapshot_date) != 6 or not snapshot_date.isdigit():
            raise PipelineError(f"snapshot date {snapshot_date!r} is not a YYMMDD date")
        return config.GEOFABRIK_FINLAND_DATED_URL.format(date=snapshot_date)
    return config.GEOFABRIK_FINLAND_URL


def build(work_dir, *, url=None, snapshot_date=None, engine="in_memory") -> dict:
    """Run the step; returns ``{asset name: manifest record}``."""
    url = url or finland_url(snapshot_date)
    work_dir = pathlib.Path(work_dir)
    with workdir_lock(work_dir):
        # Source and output both live inside a private 0700 run
        # directory; the finished extract alone is renamed onto its
        # public name.
        run_dir = download.run_directory(work_dir)
        source = download.staging_path(run_dir, "finland-latest.osm.pbf")
        out = download.staging_path(run_dir, config.OSM_ASSET)
        try:
            fetched = download.stream_download(url, source)
            clip_capital_region(source, out, engine=engine)
            # The clip must have read exactly the bytes the download
            # hashed — re-hashing the source after the clip brackets the
            # read, so a source swapped mid-clip cannot pass provenance
            # to the extract.
            digest, _ = manifest.file_digest(source)
            if digest != fetched["sha256"]:
                raise PipelineError(
                    f"{source} changed between download and clip "
                    f"({fetched['sha256']} -> {digest}); the clipped bytes "
                    f"have no trustworthy provenance"
                )
            identifier = (
                f"finland-{snapshot_date}" if snapshot_date else "finland-latest"
            )
            if fetched["last_modified"]:
                stamp = f"Geofabrik {identifier} {fetched['last_modified']}"
            else:
                # No source timestamp to cite: label the fallback as the
                # fetch time rather than passing it off as one.
                stamp = f"Geofabrik {identifier} (fetched {fetched['fetched_at']})"
            record = manifest.asset_record(
                out,
                name=config.OSM_ASSET,
                license=config.OSM_LICENSE,
                attribution=config.OSM_ATTRIBUTION,
                source_stamp=stamp,
            )
            records = {config.OSM_ASSET: record}
            # A transaction over a possibly reused directory: invalidate
            # the previous manifest, install the asset, then write the
            # manifest describing it. Every crash window leaves either
            # the intact previous generation or an absent manifest —
            # never a plausible-but-incoherent pair. A failure before
            # this point leaves the previous generation untouched.
            manifest_path = work_dir / "manifest-osm.json"
            manifest_path.unlink(missing_ok=True)
            out.replace(work_dir / config.OSM_ASSET)
            manifest.write_manifest(manifest_path, records)
        finally:
            # The country extract is ~600 MB and never an output: the
            # run directory goes on success and failure alike, taking
            # the source and any unpublished clip with it.
            shutil.rmtree(run_dir, ignore_errors=True)
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_dir", help="directory for downloads and outputs")
    parser.add_argument("--engine", default="in_memory")
    parser.add_argument(
        "--snapshot-date", help="YYMMDD Geofabrik snapshot (release runs)"
    )
    arguments = parser.parse_args()
    print(
        build(
            arguments.work_dir,
            snapshot_date=arguments.snapshot_date,
            engine=arguments.engine,
        )
    )
