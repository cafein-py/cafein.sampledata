"""The smoke build: prove the produced assets consumable by cafein.

Runs after the steps, before a release is cut. Verifies every step
manifest still describes the bytes on disk, then drives the assets
through cafein itself: the transit network builds from the feed and the
extract (DEM included), the street network routes, and the population
grid reads and routes as polygon origins, and the bundled emission
factors byte-equal a fresh export with exactly the defaults' coverage
over the feed.
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


def sample_stops(stops, count=25):
    """An even spread across the stop list. Stop ids sort stations
    first in HSL feeds (an unserved-terminal block leads the id order),
    so probing the head of the list would sample no served stop."""
    step = max(1, len(stops) // count)
    return stops[::step][:count]


FEED_ROUTES_LIMIT = 8 << 20  # routes.txt is small; cap the read


def feed_route_types(gtfs_path) -> set:
    """The route_type codes the feed's routes.txt declares."""
    import csv
    import io
    import zipfile

    with zipfile.ZipFile(gtfs_path) as archive:
        try:
            entry = archive.getinfo("routes.txt")
        except KeyError:
            raise PipelineError("the feed has no routes.txt") from None
        if entry.file_size > FEED_ROUTES_LIMIT:
            raise PipelineError("routes.txt is implausibly large")
        with archive.open(entry) as member:
            raw = member.read(FEED_ROUTES_LIMIT)
    types = set()
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        value = (row.get("route_type") or "").strip()
        if value:
            types.add(int(value))
    return types


def check_factor_coverage(route_types, covered, base_of=None) -> None:
    """The bundled defaults' covered route types — read from the table
    itself, not hard-coded — must all appear in the feed, and the only
    uncovered mode must be the Suomenlinna ferry (4). A missing covered
    mode or any other uncovered mode fails the release rather than
    silently shifting the contract.

    `base_of` maps an extended route_type code to its base GTFS mode
    (or None) the same way cafein's factor resolver does: HSL publishes
    e.g. 109 (commuter rail) and 700-series (bus), which count as their
    covered base modes here because that is how the factors match them.
    """
    route_types = set(route_types)
    if not covered:
        raise PipelineError("the loaded factor table covers no route types")
    covered = set(covered)

    def mode_of(route_type):
        if route_type in covered:
            return route_type
        base = base_of(route_type) if base_of is not None else None
        return base if base in covered else None

    missing = covered - {mode_of(t) for t in route_types}
    if missing:
        raise PipelineError(
            f"the feed carries no route type(s) {sorted(missing)} — a "
            f"covered mode disappeared from the HSL feed"
        )
    uncovered = set()
    for route_type in route_types:
        if mode_of(route_type) is None:
            base = base_of(route_type) if base_of is not None else None
            uncovered.add(route_type if base is None else base)
    if uncovered != {4}:
        raise PipelineError(
            f"the feed's uncovered route types are {sorted(uncovered)}, "
            f"expected exactly [4] (the ferry) — the factor coverage "
            f"contract changed"
        )


def check_bundled_factors(gtfs_path, snapshot_dir) -> pathlib.Path:
    """The bundled CSVs byte-equal a fresh export from the installed
    cafein, and their coverage over the feed — read through the
    production loader — is exactly the defaults'. Returns a private
    snapshot copy of the loader-ready file, so later annotation
    consumes exactly the validated bytes."""
    from cafein import emissions

    from pipeline import factors

    # One read per file: the buffer that passed the comparison is the
    # buffer the snapshot gets, so nothing can swap the bytes between
    # validation and consumption.
    validated = {}
    for path, render in (
        (factors.BUNDLED_PATH, factors.render_csv),
        (factors.BUNDLED_FULL_PATH, factors.render_full_csv),
    ):
        data = path.read_bytes()
        if data != render().encode("utf-8"):
            raise PipelineError(
                f"{path.name} does not byte-equal a fresh export from the "
                f"installed cafein — regenerate with pipeline/factors.py"
            )
        validated[path.name] = data
    snapshot = pathlib.Path(snapshot_dir) / factors.BUNDLED_PATH.name
    snapshot.write_bytes(validated[factors.BUNDLED_PATH.name])
    loaded = emissions.load_factors(str(snapshot))
    covered = set(loaded["route_type"].dropna().astype(int))
    # The resolver's own extended->base mapping; a cafein rename fails
    # loudly here rather than silently changing the coverage contract.
    check_factor_coverage(
        feed_route_types(gtfs_path), covered, emissions._base_route_type
    )
    return snapshot


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

    # The bundled factors: byte-identity with the installed cafein and
    # the exact expected coverage over this very feed, snapshotted for
    # the annotation below.
    factors_snapshot = check_bundled_factors(gtfs, snapshot_dir)

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
    # The bundled factors applied for real: search across origins until
    # some journey rides a *non-ferry* transit leg (an origin's whole
    # neighbourhood may be walkable or ferry-served, so one origin's
    # reachability set proves nothing) and require grams on those legs,
    # annotated from the validated snapshot.
    ferry_routes = {
        route_id
        for route_id, agency_id, route_type in network.routes
        if route_type == 4
    }
    sampled = sample_stops(stops)
    saw_service = False
    ridden = None
    for origin in sampled:
        times = network.travel_times_from_stop(origin, date, "08:30:00")
        candidates = [
            stop
            for stop, seconds in times.items()
            if stop != origin and 0 < seconds < 86_400 and math.isfinite(seconds)
        ]
        saw_service = saw_service or bool(candidates)
        for destination in candidates[:20]:
            journeys = network.route_between_stops(
                origin, destination, date, "08:30:00"
            )
            annotated = network.annotate_emissions(
                journeys, factors=str(factors_snapshot)
            )
            legs = [
                leg
                for journey in annotated
                for leg in journey["legs"]
                if leg["type"] == "transit" and leg.get("route_id") not in ferry_routes
            ]
            if legs:
                ridden = legs
                break
        if ridden is not None:
            break
    if not saw_service:
        raise PipelineError(
            f"none of {len(sampled)} sampled stops reaches another "
            f"stop on {date} — the feed has no usable service that day"
        )
    if ridden is None:
        raise PipelineError(
            f"no sampled journey rides a non-ferry transit leg on {date}"
        )
    if any(leg.get("emissions") is None for leg in ridden):
        raise PipelineError(
            "a non-ferry transit leg resolved no emissions through the "
            "bundled factors"
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
