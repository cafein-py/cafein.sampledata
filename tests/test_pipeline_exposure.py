"""Offline tests for the exposure steps: air quality, green view, noise."""

import datetime
import zipfile

import pytest

from pipeline import PipelineError, air_quality, config, green_view, noise

UTC = datetime.timezone.utc


# --- air quality -------------------------------------------------------------


def instant(hour):
    return datetime.datetime(2026, 8, 18, hour, tzinfo=UTC)


def reference(origin_hour, valid="2026-08-18T06:00:00Z"):
    return (
        "https://opendata.fmi.fi/download?"
        "producer=enfuser_helsinki_metropolitan&amp;param=AQIndex"
        f"&amp;origintime=2026-08-18T{origin_hour:02d}:00:00Z"
        f"&amp;starttime={valid}&amp;endtime={valid}&amp;format=netcdf"
    )


def response_xml(*origin_hours, valid="2026-08-18T06:00:00Z"):
    members = "".join(
        f"<gml:fileReference>{reference(hour, valid)}</gml:fileReference>"
        for hour in origin_hours
    )
    return f"<wfs:FeatureCollection>{members}</wfs:FeatureCollection>"


def test_member_references_parse_and_unescape():
    pairs = air_quality.member_references(response_xml(4, 6))
    assert [origin.hour for origin, _ in pairs] == [4, 6]
    assert all("&amp;" not in ref for _, ref in pairs)


def test_the_freshest_covering_origin_wins():
    pairs = air_quality.member_references(response_xml(2, 4, 6, 8))
    origin, ref = air_quality.select_reference(pairs, instant(6))
    assert origin == instant(6)
    assert "origintime=2026-08-18T06:00:00Z" in ref


def test_no_covering_origin_is_refused():
    pairs = air_quality.member_references(response_xml(8, 10))
    with pytest.raises(PipelineError, match="no ENFUSER model origin"):
        air_quality.select_reference(pairs, instant(6))


def test_an_unbound_reference_is_refused():
    pairs = air_quality.member_references(response_xml(4, valid="2026-08-18T09:00:00Z"))
    with pytest.raises(PipelineError, match="unbound download"):
        air_quality.select_reference(pairs, instant(6))


def test_a_reference_without_an_origin_is_refused():
    xml = (
        "<gml:fileReference>https://opendata.fmi.fi/download?"
        "producer=x</gml:fileReference>"
    )
    with pytest.raises(PipelineError, match="no origintime"):
        air_quality.member_references(xml)


def test_hours_must_be_whole_and_zoned():
    with pytest.raises(PipelineError, match="timezone"):
        air_quality.parse_instant("2026-08-18T06:00:00")
    with pytest.raises(PipelineError, match="whole hour"):
        air_quality.parse_instant("2026-08-18T06:30:00Z")


def synthetic_netcdf(path, hours=(6,), origin_hour=None):
    numpy = pytest.importorskip("numpy")
    xarray = pytest.importorskip("xarray")
    times = [numpy.datetime64(f"2026-08-18T{hour:02d}:00:00") for hour in hours]
    data = {}
    # Live FMI naming: the variable is the base name plus a numeric
    # parameter-id suffix, e.g. index_of_airquality_194.
    for index, (_, source_name, unit) in enumerate(config.AIR_QUALITY_BANDS):
        values = numpy.full((len(times), 4, 5), 3.5, dtype="float32")
        data[f"{source_name}_{100 + index}"] = xarray.DataArray(
            values,
            dims=("time", "lat", "lon"),
            attrs={"units": unit},
        )
    dataset = xarray.Dataset(
        data,
        coords={
            "time": times,
            "lat": numpy.linspace(60.14, 60.36, 4),
            "lon": numpy.linspace(24.6, 25.19, 5),
        },
    )
    if origin_hour is not None:
        dataset.attrs["origintime"] = f"2026-08-18T{origin_hour:02d}:00:00Z"
    dataset.to_netcdf(path)
    return path


def test_the_netcdf_slices_to_the_requested_hour(tmp_path):
    pytest.importorskip("rioxarray")
    rasterio = pytest.importorskip("rasterio")
    source = synthetic_netcdf(tmp_path / "multi.nc", hours=(4, 6, 8), origin_hour=4)
    out = tmp_path / "out.tif"
    air_quality.write_cog(source, out, instant(6), instant(4))
    with rasterio.open(out) as raster:
        assert raster.count == len(config.AIR_QUALITY_BANDS)
        expected = [f"{name} [{unit}]" for name, _, unit in config.AIR_QUALITY_BANDS]
        assert list(raster.descriptions) == expected
        assert raster.crs.to_epsg() == 4326


def test_a_netcdf_without_the_hour_is_refused(tmp_path):
    pytest.importorskip("rioxarray")
    source = synthetic_netcdf(tmp_path / "wrong.nc", hours=(8,), origin_hour=4)
    with pytest.raises(PipelineError, match="not the requested"):
        air_quality.write_cog(source, tmp_path / "out.tif", instant(6), instant(4))


def _band_variable(dataset, base_name):
    (match,) = [
        name for name in map(str, dataset.data_vars) if name.startswith(base_name)
    ]
    return match


def test_a_unit_mismatch_is_refused(tmp_path):
    pytest.importorskip("rioxarray")
    xarray = pytest.importorskip("xarray")
    source = synthetic_netcdf(tmp_path / "units.nc", hours=(6,), origin_hour=4)
    dataset = xarray.open_dataset(source)
    pm10 = _band_variable(dataset, "mass_concentration_of_pm10")
    dataset[pm10].attrs["units"] = "mg/m3"
    rewritten = tmp_path / "bad_units.nc"
    dataset.to_netcdf(rewritten)
    dataset.close()
    with pytest.raises(PipelineError, match="silent relabel"):
        air_quality.write_cog(rewritten, tmp_path / "out.tif", instant(6), instant(4))


def test_cf_unit_spellings_collapse():
    assert air_quality._normalized_unit("µg m-3") == "ug/m3"
    assert air_quality._normalized_unit("ug.m-3") == "ug/m3"
    assert air_quality._normalized_unit("um2 cm-3") == "um2/cm3"
    assert air_quality._normalized_unit("cm-3") == "1/cm3"
    assert air_quality._normalized_unit("1/cm3") == "1/cm3"
    assert air_quality._normalized_unit("mg/m3") != air_quality._normalized_unit(
        "ug/m3"
    )


def test_an_equivalent_cf_unit_spelling_passes(tmp_path):
    pytest.importorskip("rioxarray")
    xarray = pytest.importorskip("xarray")
    source = synthetic_netcdf(tmp_path / "cf.nc", hours=(6,), origin_hour=4)
    dataset = xarray.open_dataset(source)
    no2 = _band_variable(dataset, "mass_concentration_of_nitrogen_dioxide")
    dataset[no2].attrs["units"] = "µg m-3"
    rewritten = tmp_path / "cf_units.nc"
    dataset.to_netcdf(rewritten)
    dataset.close()
    air_quality.write_cog(rewritten, tmp_path / "out.tif", instant(6), instant(4))


def test_an_ambiguous_band_match_is_refused(tmp_path):
    pytest.importorskip("rioxarray")
    xarray = pytest.importorskip("xarray")
    source = synthetic_netcdf(tmp_path / "dup.nc", hours=(6,), origin_hour=4)
    dataset = xarray.open_dataset(source)
    aqi = _band_variable(dataset, "index_of_airquality")
    dataset["index_of_airquality_999"] = dataset[aqi].copy()
    rewritten = tmp_path / "dup_bands.nc"
    dataset.to_netcdf(rewritten)
    dataset.close()
    with pytest.raises(PipelineError, match="exactly one"):
        air_quality.write_cog(rewritten, tmp_path / "out.tif", instant(6), instant(4))


# --- green view --------------------------------------------------------------


def test_a_drifted_supplement_hash_is_refused(tmp_path, monkeypatch):
    archive = tmp_path / "mmc2.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("greenery_points.gpkg", b"not the published bytes")

    def fake_download(url, target, max_bytes=None):
        target.write_bytes(archive.read_bytes())

    monkeypatch.setattr(green_view.download, "stream_download", fake_download)
    with pytest.raises(PipelineError, match="published bytes changed"):
        green_view.verified_supplement(
            "https://example.invalid/mmc2.zip", "0" * 64, tmp_path, "mmc2.zip"
        )


def test_an_unexpected_archive_member_is_refused(tmp_path):
    archive = tmp_path / "mmc2.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("something_else.gpkg", b"x")
    with pytest.raises(PipelineError, match="packaging changed"):
        green_view.extracted_member(archive, "greenery_points.gpkg", tmp_path)


def synthetic_green_view(tmp_path):
    """Fixtures with EVERY documented published column, so the
    verbatim-survival assertions bite for real."""
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import LineString, Point

    n_points = config.GREEN_VIEW_POINT_COUNT
    points = geopandas.GeoDataFrame(
        {
            "panoID": [f"p{i}" for i in range(n_points)],
            "panoDate": ["2014-07"] * n_points,
            "longitude": [24.9] * n_points,
            "lattitude": [60.2] * n_points,
            "Gvi_Mean": [50.0] * n_points,
        },
        geometry=[Point(24.9, 60.2)] * n_points,
        crs="EPSG:4326",
    )
    n_roads = config.GREEN_VIEW_ROAD_COUNT
    roads = geopandas.GeoDataFrame(
        {
            "TEKSTI": ["Testikatu"] * n_roads,
            "TOIMINNALL": [4] * n_roads,
            "TYYPPI": [3] * n_roads,
            "LIIKENNEVI": [2] * n_roads,
            "luokka": [1] * n_roads,
            "Pyoravayla": [0] * n_roads,
            "GSV_GVI": [40.0] * n_roads,
            "BufAarea": [9000.0] * n_roads,
            "LUArea": [1500.0] * n_roads,
            "LU_GVI": [30.0] * n_roads,
            "Comb_GVI": [40.0] * n_roads,
            "GVI_source": ["gsv"] * n_roads,
        },
        geometry=[LineString([(385000, 6672000), (385100, 6672000)])] * n_roads,
        crs="EPSG:3067",
    )
    points_path = tmp_path / "points.gpkg"
    roads_path = tmp_path / "roads.gpkg"
    points.to_file(points_path, driver="GPKG")
    roads.to_file(roads_path, driver="GPKG")
    return points_path, roads_path


@pytest.mark.slow
def test_green_view_normalizes_verbatim(tmp_path):
    geopandas = pytest.importorskip("geopandas")
    points_path, roads_path = synthetic_green_view(tmp_path)
    source_points = geopandas.read_file(points_path)
    source_roads = geopandas.read_file(roads_path)
    out = tmp_path / "out.gpkg"
    green_view.normalized_layers(points_path, roads_path, out)
    points = geopandas.read_file(out, layer="points")
    roads = geopandas.read_file(out, layer="roads")
    assert str(points.crs).upper() == "EPSG:3067"
    assert str(roads.crs).upper() == "EPSG:3067"
    # Every published column survives verbatim — nothing dropped,
    # nothing renamed — and the feature counts hold.
    assert list(points.columns) == list(source_points.columns)
    assert list(roads.columns) == list(source_roads.columns)
    assert len(points) == len(source_points)
    assert len(roads) == len(source_roads)
    assert list(roads["Comb_GVI"]) == list(source_roads["Comb_GVI"])


def test_out_of_range_gvi_is_refused(tmp_path):
    geopandas = pytest.importorskip("geopandas")
    points_path, roads_path = synthetic_green_view(tmp_path)
    roads = geopandas.read_file(roads_path)
    roads.loc[0, "LU_GVI"] = 140.0
    bad = tmp_path / "bad_roads.gpkg"
    roads.to_file(bad, driver="GPKG")
    with pytest.raises(PipelineError, match="LU_GVI outside"):
        green_view.normalized_layers(points_path, bad, tmp_path / "out2.gpkg")


def test_a_partial_layer_is_refused(tmp_path):
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Point

    points = geopandas.GeoDataFrame(
        {"panoID": ["p1"], "Gvi_Mean": [50.0]},
        geometry=[Point(24.9, 60.2)],
        crs="EPSG:4326",
    )
    short = tmp_path / "short.gpkg"
    points.to_file(short, driver="GPKG")
    with pytest.raises(PipelineError, match="partial or substituted"):
        green_view.normalized_layers(short, short, tmp_path / "out.gpkg")


# --- noise -------------------------------------------------------------------


def test_pinned_layers_validate_the_full_grid(monkeypatch):
    layers = noise.pinned_layers()
    assert set(layers) == {
        (source, metric)
        for source in config.NOISE_SOURCES
        for metric in config.NOISE_METRICS
    }
    entry = config.NOISE_LAYERS[0]
    monkeypatch.setattr(config, "NOISE_LAYERS", (entry, entry))
    with pytest.raises(PipelineError, match="twice"):
        noise.pinned_layers()
    monkeypatch.setattr(config, "NOISE_LAYERS", (entry,))
    with pytest.raises(PipelineError, match="expected exactly"):
        noise.pinned_layers()


def test_hits_and_completeness_guard_the_fetch(tmp_path, monkeypatch):
    assert noise.matched_count('x numberMatched="16125" y', "layer") == 16125
    with pytest.raises(PipelineError, match="no numberMatched"):
        noise.matched_count("<no/>", "layer")

    responses = {
        "hits": 'numberMatched="2"',
        "page": {
            "numberMatched": 2,
            "features": [
                {"id": "a", "properties": {}},
                {"id": "a", "properties": {}},
            ],
        },
    }

    def fake_download(url, target, max_bytes=None):
        if "resultType=hits" in url:
            target.write_text(responses["hits"], encoding="utf-8")
        else:
            import json as json_module

            target.write_text(json_module.dumps(responses["page"]), encoding="utf-8")

    monkeypatch.setattr(noise.download, "stream_download", fake_download)
    with pytest.raises(PipelineError, match="delivered twice"):
        noise.fetch_zones("layer", tmp_path, "road_Lden")
    responses["page"]["features"] = [{"id": "a", "properties": {}}]
    with pytest.raises(PipelineError, match="delivered 1 of 2"):
        noise.fetch_zones("layer", tmp_path, "road_Lden2")
    responses["hits"] = 'numberMatched="1"'
    responses["page"]["numberMatched"] = 1
    responses["page"]["features"] = [
        {"id": "a", "properties": {"db_lo": 70, "db_hi": float("nan")}}
    ]
    with pytest.raises(PipelineError, match="non-finite db_hi"):
        noise.fetch_zones("layer", tmp_path, "road_Lden3")


def _zone_frame(tmp_path, name, rows, driver="GeoJSON"):
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Polygon

    square = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    frame = geopandas.GeoDataFrame(rows, geometry=[square] * len(rows["db_lo"]))
    suffix = "gpkg" if driver == "GPKG" else "json"
    path = tmp_path / f"{name}.{suffix}"
    frame.to_file(path, driver=driver)
    return path


def test_zones_normalize_with_the_numeric_schema(tmp_path):
    geopandas = pytest.importorskip("geopandas")
    fetched = {}
    for source in config.NOISE_SOURCES:
        for metric in config.NOISE_METRICS:
            fetched[(source, metric)] = _zone_frame(
                tmp_path,
                f"{source}_{metric}",
                {"db_lo": [45, 70], "db_hi": [50, None]},
            )
    out = tmp_path / "noise.gpkg"
    noise.normalized_zones(fetched, out)
    zones = geopandas.read_file(out)
    assert list(zones.columns) == [
        "source",
        "metric",
        "db_low",
        "db_high",
        "geometry",
    ]
    assert str(zones.crs).upper() == "EPSG:3067"
    assert len(zones) == 16
    top = zones[zones["db_high"].isna()]
    assert (top["db_low"] == 70).all()


def test_a_garbled_db_hi_is_refused_not_published_as_open(tmp_path):
    fetched = {
        (source, metric): _zone_frame(
            tmp_path, f"{source}_{metric}", {"db_lo": [45], "db_hi": [50]}
        )
        for source in config.NOISE_SOURCES
        for metric in config.NOISE_METRICS
    }
    fetched[("road", "Lden")] = _zone_frame(
        tmp_path, "garbled", {"db_lo": [70], "db_hi": ["70+"]}
    )
    with pytest.raises(PipelineError, match="non-numeric db_hi"):
        noise.normalized_zones(fetched, tmp_path / "out.gpkg")
    fetched[("road", "Lden")] = _zone_frame(
        tmp_path, "blank", {"db_lo": [70], "db_hi": [" "]}
    )
    with pytest.raises(PipelineError, match="non-numeric db_hi"):
        noise.normalized_zones(fetched, tmp_path / "out2.gpkg")


def test_non_finite_bounds_are_refused(tmp_path):
    fetched = {
        (source, metric): _zone_frame(
            tmp_path, f"{source}_{metric}", {"db_lo": [45], "db_hi": [50]}
        )
        for source in config.NOISE_SOURCES
        for metric in config.NOISE_METRICS
    }
    # GeoJSON cannot carry inf; GPKG stores real floats.
    fetched[("road", "Lden")] = _zone_frame(
        tmp_path, "infinite", {"db_lo": [70.0], "db_hi": [float("inf")]}, driver="GPKG"
    )
    with pytest.raises(PipelineError, match="non-finite"):
        noise.normalized_zones(fetched, tmp_path / "out.gpkg")


def test_a_missing_schema_column_is_refused(tmp_path):
    fetched = {
        (source, metric): _zone_frame(
            tmp_path, f"{source}_{metric}", {"db_lo": [45], "db_hi": [50]}
        )
        for source in config.NOISE_SOURCES
        for metric in config.NOISE_METRICS
    }
    broken = _zone_frame(tmp_path, "broken", {"db_lo": [45], "melu": [50]})
    fetched[("road", "Lden")] = broken
    with pytest.raises(PipelineError, match="db_hi"):
        noise.normalized_zones(fetched, tmp_path / "out.gpkg")


def test_a_misplaced_open_class_is_refused(tmp_path):
    fetched = {
        (source, metric): _zone_frame(
            tmp_path, f"{source}_{metric}", {"db_lo": [45], "db_hi": [50]}
        )
        for source in config.NOISE_SOURCES
        for metric in config.NOISE_METRICS
    }
    fetched[("road", "Lden")] = _zone_frame(
        tmp_path, "misplaced", {"db_lo": [55, 70], "db_hi": [None, 74]}
    )
    with pytest.raises(PipelineError, match="outside the top"):
        noise.normalized_zones(fetched, tmp_path / "out.gpkg")


def test_a_redirected_or_malformed_reference_is_refused():
    origin = instant(4)
    good = reference(4).replace("&amp;", "&")
    air_quality.validate_reference(good, origin, instant(6))
    evil_host = good.replace("opendata.fmi.fi", "evil.example.com")
    with pytest.raises(PipelineError, match="redirected"):
        air_quality.validate_reference(evil_host, origin, instant(6))
    with pytest.raises(PipelineError, match="not HTTPS"):
        air_quality.validate_reference(
            good.replace("https://", "http://"), origin, instant(6)
        )
    with pytest.raises(PipelineError, match="expected exactly"):
        air_quality.validate_reference(
            good + "&origintime=2026-08-18T05:00:00Z", origin, instant(6)
        )
    with pytest.raises(PipelineError, match="userinfo or a fragment"):
        air_quality.validate_reference(good + "#x", origin, instant(6))


def test_a_wrong_declared_origin_is_refused(tmp_path):
    pytest.importorskip("rioxarray")
    xarray = pytest.importorskip("xarray")
    source = synthetic_netcdf(tmp_path / "plain.nc", hours=(6,))
    dataset = xarray.open_dataset(source)
    dataset.attrs["origintime"] = "2026-08-18T02:00:00Z"
    wrong = tmp_path / "wrong_origin.nc"
    dataset.to_netcdf(wrong)
    dataset.close()
    with pytest.raises(PipelineError, match="wrong model run"):
        air_quality.write_cog(wrong, tmp_path / "out.tif", instant(6), instant(4))


def test_an_undeclared_origin_binds_by_reference(tmp_path):
    # The live service ships NetCDFs with no origin metadata at all;
    # they stay bound by the validated download reference.
    pytest.importorskip("rioxarray")
    source = synthetic_netcdf(tmp_path / "plain.nc", hours=(6,))
    _, binding = air_quality.write_cog(
        source, tmp_path / "out.tif", instant(6), instant(4)
    )
    assert binding == "bound by the validated download reference"


def test_a_declared_matching_origin_passes(tmp_path):
    pytest.importorskip("rioxarray")
    rasterio = pytest.importorskip("rasterio")
    xarray = pytest.importorskip("xarray")
    source = synthetic_netcdf(tmp_path / "ok.nc", hours=(6,))
    dataset = xarray.open_dataset(source)
    dataset.attrs["origintime"] = "2026-08-18T04:00:00Z"
    bound = tmp_path / "bound.nc"
    dataset.to_netcdf(bound)
    dataset.close()
    out = tmp_path / "out.tif"
    _, binding = air_quality.write_cog(bound, out, instant(6), instant(4))
    assert binding == "declared in the file"
    with rasterio.open(out) as raster:
        assert raster.count == len(config.AIR_QUALITY_BANDS)


def test_the_reconstructed_getfeature_fixture_parses_end_to_end():
    import pathlib

    fixture = (
        pathlib.Path(__file__).parent / "data" / "enfuser_getfeature_reconstructed.xml"
    )
    pairs = air_quality.member_references(fixture.read_text(encoding="utf-8"))
    assert [origin.hour for origin, _ in pairs] == [4, 6]
    origin, ref = air_quality.select_reference(pairs, instant(6))
    assert origin == instant(6)
    air_quality.validate_reference(ref, origin, instant(6))


def test_a_coordinate_declared_origin_binds(tmp_path):
    pytest.importorskip("rioxarray")
    numpy = pytest.importorskip("numpy")
    rasterio = pytest.importorskip("rasterio")
    xarray = pytest.importorskip("xarray")
    source = synthetic_netcdf(tmp_path / "coord.nc", hours=(6,))
    dataset = xarray.open_dataset(source)
    dataset = dataset.assign_coords(
        forecast_reference_time=numpy.datetime64("2026-08-18T04:00:00")
    )
    bound = tmp_path / "bound_coord.nc"
    dataset.to_netcdf(bound)
    dataset.close()
    out = tmp_path / "out.tif"
    _, binding = air_quality.write_cog(bound, out, instant(6), instant(4))
    assert binding == "declared in the file"
    with rasterio.open(out) as raster:
        assert raster.count == len(config.AIR_QUALITY_BANDS)
    with pytest.raises(PipelineError, match="wrong model run"):
        air_quality.write_cog(bound, tmp_path / "o2.tif", instant(6), instant(2))


def test_an_empty_declared_origin_is_refused(tmp_path):
    # Present-but-empty metadata is malformed, not "no declaration".
    pytest.importorskip("rioxarray")
    xarray = pytest.importorskip("xarray")
    source = synthetic_netcdf(tmp_path / "empty.nc", hours=(6,))
    dataset = xarray.open_dataset(source)
    dataset.attrs["origintime"] = ""
    hollow = tmp_path / "empty_origin.nc"
    dataset.to_netcdf(hollow)
    dataset.close()
    with pytest.raises(PipelineError, match="invalid ENFUSER hour"):
        air_quality.write_cog(hollow, tmp_path / "out.tif", instant(6), instant(4))


def test_conflicting_declared_origins_are_refused(tmp_path):
    # A matching coordinate must not shadow a conflicting attribute:
    # every declaration has to agree with the selected member.
    pytest.importorskip("rioxarray")
    numpy = pytest.importorskip("numpy")
    xarray = pytest.importorskip("xarray")
    source = synthetic_netcdf(tmp_path / "conflict.nc", hours=(6,))
    dataset = xarray.open_dataset(source)
    dataset = dataset.assign_coords(
        forecast_reference_time=numpy.datetime64("2026-08-18T04:00:00")
    )
    dataset.attrs["origintime"] = "2026-08-18T02:00:00Z"
    conflicted = tmp_path / "conflict_bound.nc"
    dataset.to_netcdf(conflicted)
    dataset.close()
    with pytest.raises(PipelineError, match="wrong model run"):
        air_quality.write_cog(conflicted, tmp_path / "out.tif", instant(6), instant(4))
