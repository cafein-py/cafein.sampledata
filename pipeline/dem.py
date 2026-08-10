"""The DEM step: the NLS 10 m elevation model over the capital region.

Fetched from the National Land Survey's open WCS in bbox chunks (each
request far below server caps), mosaicked, and written as a
cloud-optimised GeoTIFF in the native EPSG:3067.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil

from pipeline import PipelineError, config, download, manifest, workdir_lock


def chunk_ranges(bbox=config.CAPITAL_REGION_BBOX_3067, chunk=config.DEM_CHUNK_METERS):
    """The chunk bboxes tiling `bbox`, as (e0, n0, e1, n1) metres."""
    east_min, north_min, east_max, north_max = bbox
    if not (east_min < east_max and north_min < north_max and chunk > 0):
        raise PipelineError(f"malformed DEM extent {bbox!r} / chunk {chunk!r}")
    ranges = []
    east = east_min
    while east < east_max:
        north = north_min
        while north < north_max:
            ranges.append(
                (
                    east,
                    north,
                    min(east + chunk, east_max),
                    min(north + chunk, north_max),
                )
            )
            north += chunk
        east += chunk
    return ranges


def coverage_url(chunk_bbox) -> str:
    """The WCS GetCoverage request for one chunk. Credential-free: the
    api key travels as an Authorization header, never in a URL that
    error messages and CI logs would echo."""
    east_min, north_min, east_max, north_max = chunk_bbox
    return (
        f"{config.NLS_WCS_URL}?service=WCS&version=2.0.1&request=GetCoverage"
        f"&CoverageID={config.NLS_DEM_COVERAGE}&format=image/tiff"
        f"&SUBSET=E({east_min},{east_max})&SUBSET=N({north_min},{north_max})"
    )


def auth_header(api_key) -> dict:
    """The api key as HTTP basic auth's username — one of the two
    mechanisms NLS documents for the open APIs (the other being an
    ``api-key`` query parameter, avoided here because URLs end up in
    logs and error messages). See
    maanmittauslaitos.fi/en/rajapinnat/api-avaimen-ohje: "add the API
    key to the request as a username" (empty password)."""
    import base64

    token = base64.b64encode(f"{api_key}:".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def fetch_chunks(run_dir, api_key, bbox=config.CAPITAL_REGION_BBOX_3067):
    """Download every chunk GeoTIFF into the run directory; returns
    ``(path, requested chunk bbox)`` pairs so the mosaic can hold each
    tile to the exact coverage it was asked for.

    The WCS exposes no edition to pin, so the whole set is fetched
    **twice** and must come back byte-identical: an NLS model update
    mid-session cannot produce a mixed-generation mosaic, only a
    retried or failed run. The set is a few tens of megabytes, so the
    second pass is cheap insurance.
    """
    headers = auth_header(api_key)
    ranges = chunk_ranges(bbox)
    previous = None
    for attempt in range(3):
        digests = []
        chunks = []
        for index, chunk in enumerate(ranges):
            path = pathlib.Path(run_dir) / f"dem-chunk-{index:03d}.tif"
            fetched = download.stream_download(
                coverage_url(chunk),
                path,
                headers=headers,
                max_bytes=config.MAX_DEM_CHUNK_BYTES,
            )
            digests.append(fetched["sha256"])
            chunks.append((path, chunk))
        if previous is not None and digests == previous:
            return chunks
        previous = digests
    raise PipelineError(
        "the NLS elevation service kept returning different bytes across "
        "passes — an upstream update in progress; re-run the step later"
    )


def mosaic_to_cog(
    chunks,
    out,
    bbox=config.CAPITAL_REGION_BBOX_3067,
    probes=None,
    land_north_of=config.DEM_LAND_NORTH_OF,
):
    """Mosaic the chunk rasters and write a COG at `out`, validating
    that the result really is the 10 m EPSG:3067 model over `bbox`."""
    if not chunks:
        raise PipelineError("no DEM chunks to mosaic")
    try:
        import numpy
        import rasterio
        import rasterio.merge
    except ImportError as error:
        raise PipelineError(
            "the DEM step needs rasterio (see pipeline/environment.yaml)"
        ) from error
    import rasterio.windows

    east_min, north_min, east_max, north_max = bbox
    sources = [
        (rasterio.open(os.fspath(path)), requested) for path, requested in chunks
    ]
    try:
        for source, requested in sources:
            if source.crs is None or source.crs.to_epsg() != 3067:
                raise PipelineError(
                    f"DEM chunk {source.name} is in {source.crs}, not EPSG:3067"
                )
            resolution = (abs(source.transform.a), abs(source.transform.e))
            if max(abs(resolution[0] - 10.0), abs(resolution[1] - 10.0)) > 0.01:
                raise PipelineError(
                    f"DEM chunk {source.name} resolution {resolution} is not "
                    f"the 10 m model"
                )
            if source.count != 1:
                raise PipelineError(
                    f"DEM chunk {source.name} carries {source.count} bands, "
                    f"expected the single elevation band"
                )
            if not numpy.issubdtype(numpy.dtype(source.dtypes[0]), numpy.number):
                raise PipelineError(
                    f"DEM chunk {source.name} dtype {source.dtypes[0]} is "
                    f"not numeric"
                )
            b = source.bounds
            # Each tile must *cover* the chunk it was requested for:
            # outward pixel snapping is tolerated (up to 1.5 px), but
            # never inward — an inward-snapped tile beside its
            # neighbour leaves a strip the merge fills with nodata.
            e0, n0, e1, n1 = requested
            slack, epsilon = 15, 0.01
            if (
                b.left > e0 + epsilon
                or b.bottom > n0 + epsilon
                or b.right < e1 - epsilon
                or b.top < n1 - epsilon
                or b.left < e0 - slack
                or b.bottom < n0 - slack
                or b.right > e1 + slack
                or b.top > n1 + slack
            ):
                raise PipelineError(
                    f"DEM chunk {source.name} bounds {tuple(b)} do not cover "
                    f"its requested extent {requested}"
                )
            # Chunks fully north of the coastal strip are land; one
            # arriving all-nodata is a hole, not sea.
            if n0 >= land_north_of:
                band = source.read(1)
                valid = numpy.isfinite(band)
                if source.nodata is not None:
                    valid &= band != source.nodata
                # The internal validity mask counts regardless of any
                # numeric nodata: masked-invalid pixels become nodata in
                # the merge even when their raw values look finite.
                valid &= source.read_masks(1) != 0
                if float(valid.mean()) < config.DEM_MIN_CHUNK_VALID_FRACTION:
                    raise PipelineError(
                        f"DEM chunk {source.name} over land "
                        f"({requested}) is mostly nodata — a hole in the "
                        f"model delivery"
                    )

        # NaN is a legal nodata that never equals itself: compare via a
        # normalized key so identical NaN-nodata chunks agree.
        def nodata_key(value):
            if isinstance(value, float) and value != value:
                return "nan"
            return value

        nodatas = {nodata_key(source.nodata) for source, _ in sources}
        if len(nodatas) > 1:
            raise PipelineError(
                f"DEM chunks disagree on nodata ({sorted(map(str, nodatas))})"
            )
        nodata = sources[0][0].nodata
        if nodata is None:
            # Without a numeric nodata, merge fills any gap with zero —
            # a plausible sea-level elevation here. Only fully valid
            # masks make that safe.
            for source, _ in sources:
                if not bool(source.read_masks(1).all()):
                    raise PipelineError(
                        f"DEM chunk {source.name} uses an internal mask "
                        f"with no numeric nodata — gaps would surface as "
                        f"elevation 0; refusing"
                    )
        mosaic, transform = rasterio.merge.merge([s for s, _ in sources], indexes=[1])
    finally:
        for source, _ in sources:
            source.close()
    # Crop to the exact pixel-aligned extent; a mosaic that cannot fill
    # it means the WCS did not deliver the requested coverage.
    window = (
        rasterio.windows.from_bounds(
            east_min, north_min, east_max, north_max, transform=transform
        )
        .round_offsets()
        .round_lengths()
    )
    rows, columns = mosaic.shape[1], mosaic.shape[2]
    if (
        window.row_off < 0
        or window.col_off < 0
        or window.row_off + window.height > rows
        or window.col_off + window.width > columns
    ):
        raise PipelineError(f"the mosaic does not cover the requested extent {bbox}")
    data = mosaic[
        0,
        window.row_off : window.row_off + window.height,
        window.col_off : window.col_off + window.width,
    ]
    cropped_bounds = rasterio.windows.bounds(window, transform)
    transform = rasterio.windows.transform(window, transform)
    # Fractional crop offsets round silently; the cropped bounds must
    # still equal the requested bbox, else a sub-pixel-shifted source
    # has displaced the whole output grid.
    if (
        max(
            abs(cropped_bounds[0] - east_min),
            abs(cropped_bounds[1] - north_min),
            abs(cropped_bounds[2] - east_max),
            abs(cropped_bounds[3] - north_max),
        )
        > 0.01
    ):
        raise PipelineError(
            f"the cropped mosaic bounds {cropped_bounds} miss the requested "
            f"extent {bbox} — a sub-pixel-shifted source grid"
        )
    expected = (round((north_max - north_min) / 10), round((east_max - east_min) / 10))
    if data.shape != expected:
        raise PipelineError(
            f"cropped mosaic shape {data.shape} is not the expected {expected}"
        )
    finite = numpy.isfinite(data)
    if nodata is not None:
        finite &= data != nodata
    fraction = float(finite.mean())
    if fraction < config.DEM_MIN_VALID_FRACTION:
        raise PipelineError(
            f"only {fraction:.0%} of the DEM mosaic carries valid "
            f"elevations — a missing land chunk, not just sea"
        )
    # Sea chunks are legitimately all nodata, so the fraction alone
    # cannot catch a single missing land chunk; the municipal-centre
    # probes can.
    if probes is None:
        probes = [
            probe
            for probe in config.DEM_LAND_PROBES
            if east_min <= probe[0] <= east_max and north_min <= probe[1] <= north_max
        ]
    for east, north in probes:
        row, column = rasterio.transform.rowcol(transform, east, north)
        if not (0 <= row < data.shape[0] and 0 <= column < data.shape[1]):
            raise PipelineError(f"land probe ({east}, {north}) is off the mosaic")
        value = data[row, column]
        if not numpy.isfinite(value) or (nodata is not None and value == nodata):
            raise PipelineError(
                f"no valid elevation at the land probe ({east}, {north}) — "
                f"a hole over built-up land"
            )
    with rasterio.open(
        os.fspath(out),
        "w",
        driver="COG",
        crs="EPSG:3067",
        transform=transform,
        width=data.shape[1],
        height=data.shape[0],
        count=1,
        dtype=data.dtype,
        nodata=nodata,
        compress="DEFLATE",
    ) as sink:
        sink.write(data, 1)
    return pathlib.Path(out)


def build(work_dir, *, api_key=None) -> dict:
    """Run the step; returns ``{asset name: manifest record}``.

    The api key comes from ``MML_API_KEY`` (or the keyword for tests) —
    never from a CLI flag, which would leak through shell history and
    process listings."""
    api_key = api_key or os.environ.get("MML_API_KEY")
    if not api_key:
        raise PipelineError("the DEM step needs an NLS api key: set MML_API_KEY")
    work_dir = pathlib.Path(work_dir)
    with workdir_lock(work_dir):
        run_dir = download.run_directory(work_dir)
        try:
            # The chunks come from a live service in one short session;
            # the stamp records the window so a rare mid-run model
            # update is at least auditable.
            fetch_started = _utcnow()
            chunks = fetch_chunks(run_dir, api_key)
            out = download.staging_path(run_dir, config.DEM_ASSET)
            mosaic_to_cog(chunks, out)
            fetched_at = _utcnow()
            stamp = (
                f"NLS {config.NLS_DEM_COVERAGE} over the capital region, "
                f"fetched {fetch_started}..{fetched_at}"
            )
            record = manifest.asset_record(
                out,
                name=config.DEM_ASSET,
                license=config.DEM_LICENSE,
                # NLS attribution names the year the data was obtained.
                attribution=f"{config.DEM_ATTRIBUTION}, {fetched_at[:4]}",
                source_stamp=stamp,
            )
            records = {config.DEM_ASSET: record}
            manifest.publish_transaction(
                work_dir, "manifest-dem.json", records, {config.DEM_ASSET: out}
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
