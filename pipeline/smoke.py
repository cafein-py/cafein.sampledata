"""The smoke build: prove the produced assets consumable by cafein.

Runs after the steps, before a release is cut. Verifies every step
manifest still describes the bytes on disk, then drives the assets
through cafein itself: the transit network builds from the feed and the
extract (DEM included), the street network routes, and the population
grid reads and routes as polygon origins. The emission-factors check
joins when the factors asset lands (PR 4).
"""

from __future__ import annotations

import argparse
import math
import pathlib
import shutil
import tempfile

from pipeline import PipelineError, config, manifest, workdir_lock

#: Which asset each step manifest must describe — nothing more, nothing
#: less; the smoke consumes exactly these files by name.
STEP_MANIFESTS = {
    "manifest-osm.json": config.OSM_ASSET,
    "manifest-gtfs.json": config.GTFS_ASSET,
    "manifest-dem.json": config.DEM_ASSET,
    "manifest-population.json": config.POPULATION_ASSET,
}


def verify_manifests(work_dir, names=None) -> dict:
    """Re-hash every asset against its step manifest; returns the merged
    records. A missing file, a digest drift, or a manifest describing
    anything but its own expected asset fails — the release must ship
    exactly what the steps produced and measured."""
    work_dir = pathlib.Path(work_dir)
    merged = {}
    for name, expected in (names or STEP_MANIFESTS).items():
        path = work_dir / name
        if not path.exists():
            raise PipelineError(f"step manifest {name} is missing from {work_dir}")
        payload = manifest.read_manifest(path)
        if set(payload["assets"]) != {expected}:
            raise PipelineError(
                f"{name} describes {sorted(payload['assets'])}, expected "
                f"exactly [{expected!r}]"
            )
        record = payload["assets"][expected]
        # The recorded filename is untrusted input: it must be the plain
        # expected basename, and the file a regular non-symlink inside
        # the work directory.
        if record["file"] != expected:
            raise PipelineError(
                f"{name} records filename {record['file']!r}, expected " f"{expected!r}"
            )
        target = work_dir / expected
        if target.is_symlink() or not target.is_file():
            raise PipelineError(f"{target} is not a regular file")
        digest, size = manifest.file_digest(target)
        if digest != record["sha256"] or size != record["size"]:
            raise PipelineError(
                f"{expected} does not match its manifest "
                f"({record['sha256']}/{record['size']} recorded, "
                f"{digest}/{size} on disk)"
            )
        merged[expected] = record
    return merged


def snapshot_assets(work_dir, records, snapshot_dir) -> dict:
    """Copy every verified asset into a private snapshot directory and
    re-hash the copies against the manifest records; the smoke consumes
    only these copies, so nothing that happens to the originals after
    verification can change what is being tested."""
    paths = {}
    for name, record in records.items():
        source = work_dir / name
        if source.is_symlink() or not source.is_file():
            raise PipelineError(f"{source} is not a regular file")
        target = pathlib.Path(snapshot_dir) / name
        shutil.copyfile(source, target)
        digest, size = manifest.file_digest(target)
        if digest != record["sha256"] or size != record["size"]:
            raise PipelineError(
                f"{name} changed while being snapshotted "
                f"({record['sha256']} recorded, {digest} copied)"
            )
        paths[name] = target
    return paths


def run(work_dir, date) -> dict:
    """The full smoke: manifest verification, cafein builds, one query
    per surface — under the work-directory lock, and consuming private
    verified snapshot copies so the tested bytes cannot change beneath
    the long builds. Returns the merged records on success."""
    work_dir = pathlib.Path(work_dir)
    with workdir_lock(work_dir):
        snapshot_dir = pathlib.Path(tempfile.mkdtemp(dir=work_dir, prefix=".smoke."))
        try:
            return _run_locked(work_dir, snapshot_dir, date)
        finally:
            shutil.rmtree(snapshot_dir, ignore_errors=True)


def _run_locked(work_dir, snapshot_dir, date) -> dict:
    records = verify_manifests(work_dir)
    assets = snapshot_assets(work_dir, records, snapshot_dir)
    try:
        import geopandas
        from cafein import StreetNetwork, TransportNetwork, TravelTimeMatrix
    except ImportError as error:
        raise PipelineError(
            "the smoke build needs cafein[dem] and geopandas "
            "(see pipeline/environment.yaml)"
        ) from error
    osm = assets[config.OSM_ASSET]
    gtfs = assets[config.GTFS_ASSET]
    dem = assets[config.DEM_ASSET]
    grid_path = assets[config.POPULATION_ASSET]

    # Transit: full build — footpaths, multimodal graph, elevations.
    network = TransportNetwork.from_gtfs(
        [str(gtfs)],
        osm_pbf=str(osm),
        street_modes=("walk", "bicycle", "e_scooter"),
        dem=str(dem),
    )
    stops = [stop for stop, lat, lon in network.stops if lat is not None]
    if len(stops) < 2:
        raise PipelineError("the built network has fewer than two located stops")
    # An arbitrary first stop may simply have no service on the date;
    # the feed is proven routable when any of a handful of origins
    # reaches a *different* stop in finite time.
    for origin in stops[:25]:
        times = network.travel_times_from_stop(origin, date, "08:30:00")
        if any(
            stop != origin and 0 < seconds < 86_400 and math.isfinite(seconds)
            for stop, seconds in times.items()
        ):
            break
    else:
        raise PipelineError(
            f"none of {min(25, len(stops))} sampled stops reaches another "
            f"stop on {date} — the feed has no usable service that day"
        )

    # Streets: standalone build with elevations, one slope-aware ride.
    streets = StreetNetwork.from_osm(str(osm), dem=str(dem))
    ride = streets.travel_time((60.1699, 24.9384), (60.2055, 24.6559), mode="bicycle")
    if ride is None or not math.isfinite(ride) or ride <= 0:
        raise PipelineError("the street network cannot ride across the region")

    # Population grid: read, then route a few cells as polygon origins.
    grid = geopandas.read_file(grid_path, layer="population_grid")
    if grid.crs is None or grid.crs.to_epsg() != 3067:
        raise PipelineError(f"the population grid is in {grid.crs}, not EPSG:3067")
    if config.POPULATION_COLUMN not in grid.columns:
        raise PipelineError(
            f"the population grid lost its {config.POPULATION_COLUMN!r} column"
        )
    if not (grid[config.POPULATION_COLUMN].dropna() > 0).any():
        raise PipelineError("the population grid counts nobody anywhere")
    # The most central cells are on the street graph by construction —
    # arbitrary WFS-ordered ones might not be — and the proof demands a
    # journey between two *distinct* cells, not a diagonal zero.
    centre_east, centre_north = 385700, 6671500  # central Helsinki, EPSG:3067
    centroids = grid.geometry.centroid
    distance = (centroids.x - centre_east) ** 2 + (centroids.y - centre_north) ** 2
    cells = grid.loc[distance.nsmallest(5).index].copy()
    cells["id"] = [f"cell-{index}" for index in range(len(cells))]
    matrix = TravelTimeMatrix(streets, cells, transport_mode="walk")
    import numpy

    off_diagonal = matrix[matrix.from_id != matrix.to_id]
    durations = off_diagonal.travel_time_s.astype("float64")
    if off_diagonal.empty or not ((durations > 0) & numpy.isfinite(durations)).any():
        raise PipelineError(
            "no finite positive-duration walking journey between "
            "distinct central grid cells"
        )
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_dir", help="directory holding the produced assets")
    parser.add_argument("date", help="service date for the transit query, YYYY-MM-DD")
    arguments = parser.parse_args()
    print(run(arguments.work_dir, arguments.date))
