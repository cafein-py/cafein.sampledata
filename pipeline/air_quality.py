"""The air-quality step: one hour of FMI-ENFUSER as a multi-band COG.

The stored query returns one GridSeriesObservation member per model
``origintime``, each referencing a NetCDF on FMI's download service.
The workflow pins the VALID hour; the freshest origin at or before it
is selected, the download is bound to both instants through the
reference URL, and the NetCDF's own coordinates are validated (and
sliced to the hour) before the COG is written on the native ~13 m
EPSG:4326 grid, values and units passed through unchanged.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import shutil
from datetime import datetime, timezone

from urllib.parse import parse_qsl, urlsplit

from pipeline import PipelineError, config, download, manifest, workdir_lock


def parse_instant(value) -> datetime:
    """An ISO instant as an aware UTC datetime, whole-hour required."""
    try:
        instant = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise PipelineError(f"invalid ENFUSER hour {value!r}: {error}") from error
    if instant.tzinfo is None:
        raise PipelineError(f"the ENFUSER hour {value!r} must carry a timezone")
    instant = instant.astimezone(timezone.utc)
    if instant.minute or instant.second or instant.microsecond:
        raise PipelineError(f"the ENFUSER hour {value!r} must be a whole hour")
    return instant


def query_url(valid_hour: datetime) -> str:
    stamp = valid_hour.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{config.FMI_WFS_URL}?service=WFS&version=2.0.0&request=getFeature"
        f"&storedquery_id={config.ENFUSER_STORED_QUERY}"
        f"&starttime={stamp}&endtime={stamp}"
    )


def member_references(response_xml: str):
    """Every (origintime, fileReference) pair in the WFS response.

    The reference URLs carry their query parameters XML-escaped;
    unescape the ampersands so the pairs are usable URLs.
    """
    pairs = []
    references = [
        match.replace("&amp;", "&")
        for match in re.findall(
            r"<gml:fileReference>([^<]+)</gml:fileReference>", response_xml
        )
    ]
    for reference in references:
        origin = re.search(r"[?&]origintime=([0-9TZ:\-]+)", reference)
        if not origin:
            raise PipelineError(
                "an ENFUSER fileReference carries no origintime parameter "
                f"({reference[:120]}...) — the FMI encoding changed"
            )
        pairs.append((parse_instant(origin.group(1)), reference))
    if not pairs:
        raise PipelineError(
            "the ENFUSER response carries no gml:fileReference — the FMI "
            "encoding changed, or the query failed"
        )
    return pairs


def select_reference(pairs, valid_hour: datetime):
    """The freshest origin at or before the valid hour, with its
    reference bound to both instants."""
    eligible = [(origin, ref) for origin, ref in pairs if origin <= valid_hour]
    if not eligible:
        origins = sorted(origin.isoformat() for origin, _ in pairs)
        raise PipelineError(
            f"no ENFUSER model origin at or before {valid_hour.isoformat()} "
            f"(origins offered: {origins}) — pick a later hour"
        )
    origin, reference = max(eligible, key=lambda pair: pair[0])
    validate_reference(reference, origin, valid_hour)
    return origin, reference


def validate_reference(reference, origin, valid_hour) -> None:
    """The WFS-controlled reference is untrusted input: require HTTPS
    on the configured FMI host, no userinfo or fragment, and exactly
    one origintime/starttime/endtime each, bound to the selected
    instants — never substring matching."""
    parts = urlsplit(reference)
    if parts.scheme != "https":
        raise PipelineError(f"fileReference is not HTTPS: {reference[:120]}")
    if parts.hostname != config.ENFUSER_DOWNLOAD_HOST or parts.port not in (None, 443):
        raise PipelineError(
            f"fileReference points at {parts.netloc!r}, expected "
            f"{config.ENFUSER_DOWNLOAD_HOST!r} — refusing a redirected "
            "download"
        )
    if parts.username is not None or parts.fragment:
        raise PipelineError("fileReference carries userinfo or a fragment")
    if parts.path != "/download":
        raise PipelineError(f"fileReference path {parts.path!r} != /download")
    query = parse_qsl(parts.query, keep_blank_values=True)
    stamp = valid_hour.strftime("%Y-%m-%dT%H:%M:%SZ")
    origin_stamp = origin.strftime("%Y-%m-%dT%H:%M:%SZ")
    for key, expected in (
        ("origintime", origin_stamp),
        ("starttime", stamp),
        ("endtime", stamp),
    ):
        values = [value for name, value in query if name == key]
        if values != [expected]:
            raise PipelineError(
                f"fileReference {key}={values!r}, expected exactly "
                f"[{expected!r}] — refusing an unbound download"
            )
    projections = [value for name, value in query if name == "projection"]
    if projections and projections != ["EPSG:4326"]:
        raise PipelineError(
            f"fileReference requests projection {projections!r}, expected "
            "EPSG:4326 — the native geographic grid"
        )


def write_cog(netcdf_path, out, valid_hour: datetime, origin: datetime):
    """Validate the NetCDF's coordinates, slice to the hour, and write
    the eight bands unchanged with their units as band descriptions."""
    try:
        import numpy
        import rioxarray  # noqa: F401  (registers the rio accessor)
        import xarray
        from rasterio.crs import CRS
    except ImportError as error:
        raise PipelineError(
            "the air-quality step needs rioxarray (see "
            "pipeline/environment.yaml)"
        ) from error

    dataset = xarray.open_dataset(netcdf_path)
    try:
        if "time" not in dataset.coords:
            raise PipelineError("the ENFUSER NetCDF carries no time coordinate")
        times = [
            datetime.fromtimestamp(
                stamp.astype("datetime64[s]").astype(int), tz=timezone.utc
            )
            for stamp in dataset["time"].values
        ]
        if valid_hour not in times:
            raise PipelineError(
                f"the ENFUSER NetCDF holds {[t.isoformat() for t in times]}, "
                f"not the requested {valid_hour.isoformat()} — refusing to "
                "publish the wrong hour"
            )
        _require_origin(dataset, origin)
        hour = dataset.sel(time=numpy.datetime64(valid_hour.replace(tzinfo=None)))
        bands = []
        for name, unit in config.AIR_QUALITY_BANDS:
            if name not in hour:
                raise PipelineError(
                    f"the ENFUSER NetCDF carries no {name!r} variable "
                    f"(variables: {sorted(hour.data_vars)})"
                )
            declared = str(hour[name].attrs.get("units", "")).strip()
            if declared and declared != unit:
                raise PipelineError(
                    f"{name} declares units {declared!r}, the pinned table "
                    f"says {unit!r} — refusing a silent relabel"
                )
            bands.append(hour[name].astype("float32"))
        stack = xarray.concat(bands, dim="band")
        stack = stack.assign_coords(
            band=list(range(1, len(config.AIR_QUALITY_BANDS) + 1))
        )
        for x_name, y_name in (("lon", "lat"), ("longitude", "latitude"), ("x", "y")):
            if x_name in stack.dims and y_name in stack.dims:
                break
        else:
            raise PipelineError(
                f"unrecognised spatial dimensions {sorted(stack.dims)} in "
                "the ENFUSER NetCDF — the FMI layout changed"
            )
        # The coordinates must BE geographic degrees over the metro
        # window before EPSG:4326 is assigned — projected metres would
        # otherwise publish a misregistered raster.
        xs = stack[x_name].values
        ys = stack[y_name].values
        if not (23.5 <= float(xs.min()) and float(xs.max()) <= 26.5):
            raise PipelineError(
                f"{x_name} spans {float(xs.min())}..{float(xs.max())} — "
                "not geographic longitudes over the Helsinki metropolitan "
                "area; refusing to label the grid EPSG:4326"
            )
        if not (59.5 <= float(ys.min()) and float(ys.max()) <= 61.0):
            raise PipelineError(
                f"{y_name} spans {float(ys.min())}..{float(ys.max())} — "
                "not geographic latitudes over the Helsinki metropolitan "
                "area; refusing to label the grid EPSG:4326"
            )
        declared_crs = None
        for candidate in ("crs", "spatial_ref", "grid_mapping"):
            if candidate in dataset.variables:
                declared_crs = dataset[candidate].attrs
                break
        if declared_crs and "grid_mapping_name" in declared_crs:
            if declared_crs["grid_mapping_name"] != "latitude_longitude":
                raise PipelineError(
                    f"the NetCDF declares grid mapping "
                    f"{declared_crs['grid_mapping_name']!r}, expected "
                    "latitude_longitude"
                )
        stack = stack.rio.set_spatial_dims(x_dim=x_name, y_dim=y_name)
        stack.rio.write_crs(CRS.from_epsg(4326), inplace=True)
        # rioxarray writes a list-valued long_name as per-band
        # descriptions; the COG driver refuses post-write edits.
        stack.attrs["long_name"] = [
            f"{name} [{unit}]" for name, unit in config.AIR_QUALITY_BANDS
        ]
        negatives = [
            name
            for (name, _), band in zip(config.AIR_QUALITY_BANDS, bands)
            if name != "AQIndex" and float(band.min()) < 0
        ]
        if negatives:
            raise PipelineError(f"negative concentrations in {negatives}")
        stack.rio.to_raster(
            out,
            driver="COG",
            compress="DEFLATE",
        )
    finally:
        dataset.close()
    return pathlib.Path(out)


def _require_origin(dataset, origin) -> None:
    """The downloaded bytes must declare the selected model origin —
    a forecast_reference_time coordinate or a recognised global
    attribute; a file without one, or with another origin, is refused."""
    from datetime import datetime, timezone

    found = None
    if "forecast_reference_time" in dataset.coords:
        import numpy

        value = numpy.asarray(dataset["forecast_reference_time"].values)
        if value.size != 1:
            raise PipelineError(
                f"forecast_reference_time carries {value.size} values, "
                "expected one"
            )
        found = datetime.fromtimestamp(
            value.reshape(())
            .astype("datetime64[s]")
            .astype(int),
            tz=timezone.utc,
        )
    if found is None:
        for name in ("origintime", "analysis_time", "forecast_reference_time"):
            raw = dataset.attrs.get(name)
            if raw:
                found = parse_instant(str(raw))
                break
    if found is None:
        raise PipelineError(
            "the ENFUSER NetCDF declares no model origin "
            "(no forecast_reference_time coordinate, none of the "
            f"recognised attributes; attrs: {sorted(dataset.attrs)}) — "
            "cannot bind the bytes to the selected origin"
        )
    if found != origin:
        raise PipelineError(
            f"the ENFUSER NetCDF declares origin {found.isoformat()}, the "
            f"selected member was {origin.isoformat()} — refusing the "
            "wrong model run"
        )


def build(work_dir, valid_hour) -> dict:
    """Run the step; returns ``{asset name: manifest record}``."""
    work_dir = pathlib.Path(work_dir)
    hour = parse_instant(valid_hour)
    with workdir_lock(work_dir):
        run_dir = download.run_directory(work_dir)
        try:
            response = run_dir / "enfuser.xml"
            download.stream_download(
                query_url(hour), response, max_bytes=config.MAX_WFS_XML_BYTES
            )
            pairs = member_references(
                response.read_text(encoding="utf-8", errors="replace")
            )
            origin, reference = select_reference(pairs, hour)
            netcdf_path = run_dir / "enfuser.nc"
            # The reference is WFS-controlled input: the validated host
            # must be the FINAL host, so redirects are refused.
            download.stream_download(
                reference,
                netcdf_path,
                max_bytes=config.MAX_ENFUSER_NETCDF_BYTES,
                refuse_redirects=True,
            )
            out = download.staging_path(run_dir, config.AIR_QUALITY_ASSET)
            write_cog(netcdf_path, out, hour, origin)
            import rasterio

            with rasterio.open(out) as raster:
                grid = (
                    f"{raster.width}x{raster.height} cells, "
                    f"{abs(raster.transform.a):.6f}x"
                    f"{abs(raster.transform.e):.6f} deg, "
                    f"EPSG:{raster.crs.to_epsg()}"
                )
            stamp = (
                f"FMI-ENFUSER valid hour {hour.isoformat()}, model origin "
                f"{origin.isoformat()}, fetched {_utcnow()} from "
                f"{reference}, native grid {grid}, values and units "
                "unchanged"
            )
            record = manifest.asset_record(
                out,
                name=config.AIR_QUALITY_ASSET,
                license=config.AIR_QUALITY_LICENSE,
                attribution=config.AIR_QUALITY_ATTRIBUTION,
                source_stamp=stamp,
            )
            records = {config.AIR_QUALITY_ASSET: record}
            manifest.publish_transaction(
                work_dir,
                "manifest-air-quality.json",
                records,
                {config.AIR_QUALITY_ASSET: out},
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
    return records


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_dir", help="directory for downloads and outputs")
    parser.add_argument("valid_hour", help="the pinned VALID hour, ISO instant")
    arguments = parser.parse_args()
    print(build(arguments.work_dir, arguments.valid_hour))
