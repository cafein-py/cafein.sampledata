"""The DEM and population steps and the smoke's manifest verification."""

import hashlib
import http.server
import json
import threading

import pytest

from pipeline import PipelineError, config, dem, manifest, population, smoke

# --- DEM chunking and URLs --------------------------------------------------


def test_chunk_ranges_tile_the_extent_exactly():
    ranges = dem.chunk_ranges(bbox=(0, 0, 25000, 30000), chunk=12500)
    assert len(ranges) == 2 * 3
    east_edges = {r[0] for r in ranges} | {r[2] for r in ranges}
    north_edges = {r[1] for r in ranges} | {r[3] for r in ranges}
    assert min(east_edges) == 0 and max(east_edges) == 25000
    assert min(north_edges) == 0 and max(north_edges) == 30000
    # Full coverage: chunk areas sum to the extent's area.
    area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in ranges)
    assert area == 25000 * 30000


def test_chunk_ranges_clamp_the_ragged_edge():
    ranges = dem.chunk_ranges(bbox=(0, 0, 20000, 10000), chunk=12500)
    assert (12500, 0, 20000, 10000) in ranges


def test_chunk_ranges_reject_a_malformed_extent():
    with pytest.raises(PipelineError, match="malformed"):
        dem.chunk_ranges(bbox=(10, 0, 0, 10), chunk=100)


def test_coverage_url_carries_the_subsets_and_no_credential():
    url = dem.coverage_url((355000, 6650000, 360000, 6655000))
    assert "SUBSET=E(355000,360000)" in url
    assert "SUBSET=N(6650000,6655000)" in url
    assert "api-key" not in url  # the key travels as a header, never a URL
    assert url.startswith(config.NLS_WCS_URL)
    # The 10 m asset is the server's resampling of the 2 m coverage.
    assert "CoverageID=korkeusmalli_2m" in url
    assert "SCALEFACTOR=0.2" in url


def test_the_chunks_respect_the_server_area_cap():
    # The NLS WCS refuses elevation requests beyond 10 x 10 km.
    for e0, n0, e1, n1 in dem.chunk_ranges():
        assert e1 - e0 <= 10000 and n1 - n0 <= 10000


def test_the_api_key_becomes_a_basic_auth_header():
    import base64

    header = dem.auth_header("SECRET")
    assert header == {"Authorization": "Basic " + base64.b64encode(b"SECRET:").decode()}


def test_the_stored_3067_bbox_matches_a_fresh_transform():
    pyproj = pytest.importorskip("pyproj")
    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3067", always_xy=True)
    west, south, east, north = config.CAPITAL_REGION_BBOX
    xs, ys = [], []
    for lon, lat in [(west, south), (west, north), (east, south), (east, north)]:
        x, y = transformer.transform(lon, lat)
        xs.append(x)
        ys.append(y)
    east_min, north_min, east_max, north_max = config.CAPITAL_REGION_BBOX_3067
    # The stored envelope is the corner envelope rounded outward to 100 m.
    assert east_min <= min(xs) < east_min + 200
    assert north_min <= min(ys) < north_min + 200
    assert east_max - 200 < max(xs) <= east_max
    assert north_max - 200 < max(ys) <= north_max


def test_dem_build_requires_an_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("MML_API_KEY", raising=False)
    with pytest.raises(PipelineError, match="MML_API_KEY"):
        dem.build(tmp_path)


# --- DEM mosaic -------------------------------------------------------------


def _write_tile(path, *, east0, north1, size=50, value=100.0, epsg=3067):
    """A synthetic 10 m tile whose top-left corner is (east0, north1)."""
    rasterio = pytest.importorskip("rasterio")
    import numpy

    transform = rasterio.transform.from_origin(east0, north1, 10, 10)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=size,
        height=size,
        count=1,
        dtype="float32",
        crs=f"EPSG:{epsg}",
        transform=transform,
        nodata=-9999.0,
    ) as sink:
        sink.write(numpy.full((size, size), value, dtype="float32"), 1)
    return path


TILE_BBOX = (355000, 6650000, 356000, 6650500)  # what the two tiles cover


def test_mosaic_merges_tiles_into_a_cog(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    left = _write_tile(tmp_path / "a.tif", east0=355000, north1=6650500)
    right = _write_tile(tmp_path / "b.tif", east0=355500, north1=6650500, value=200.0)
    out = tmp_path / "dem.tif"
    dem.mosaic_to_cog(
        [
            (left, (355000, 6650000, 355500, 6650500)),
            (right, (355500, 6650000, 356000, 6650500)),
        ],
        out,
        bbox=TILE_BBOX,
    )
    with rasterio.open(out) as source:
        assert source.crs.to_epsg() == 3067
        assert source.res == (10.0, 10.0)
        assert source.width == 100 and source.height == 50
        assert source.bounds == (355000, 6650000, 356000, 6650500)
        data = source.read(1)
    assert data[0, 0] == 100.0 and data[0, -1] == 200.0


def test_mosaic_refuses_a_wrong_crs_tile(tmp_path):
    pytest.importorskip("rasterio")
    tile = _write_tile(tmp_path / "wgs.tif", east0=355000, north1=6650500, epsg=32635)
    with pytest.raises(PipelineError, match="not EPSG:3067"):
        dem.mosaic_to_cog(
            [(tile, (355000, 6650000, 355500, 6650500))],
            tmp_path / "dem.tif",
            bbox=TILE_BBOX,
        )


def test_mosaic_refuses_an_all_nodata_result(tmp_path):
    pytest.importorskip("rasterio")
    tile = _write_tile(
        tmp_path / "void.tif", east0=355000, north1=6650500, value=-9999.0
    )
    with pytest.raises(PipelineError, match="valid"):
        dem.mosaic_to_cog(
            [(tile, (355000, 6650000, 355500, 6650500))],
            tmp_path / "dem.tif",
            bbox=(355000, 6650000, 355500, 6650500),
        )


def test_mosaic_refuses_empty_input(tmp_path):
    with pytest.raises(PipelineError, match="no DEM chunks"):
        dem.mosaic_to_cog([], tmp_path / "dem.tif")


def test_mosaic_refuses_a_displaced_tile(tmp_path):
    pytest.importorskip("rasterio")
    # The tile sits a kilometre east of the chunk it was requested for.
    tile = _write_tile(tmp_path / "off.tif", east0=357000, north1=6650500)
    with pytest.raises(PipelineError, match="do not cover its requested"):
        dem.mosaic_to_cog(
            [(tile, (355000, 6650000, 355500, 6650500))],
            tmp_path / "dem.tif",
            bbox=(355000, 6650000, 355500, 6650500),
        )


def test_mosaic_refuses_incomplete_coverage(tmp_path):
    pytest.importorskip("rasterio")
    # One 500 m tile, correct for its own chunk, cannot fill a 1 km
    # extent whose other chunks never arrived.
    tile = _write_tile(tmp_path / "partial.tif", east0=355000, north1=6650500)
    with pytest.raises(PipelineError, match="does not cover|not the expected"):
        dem.mosaic_to_cog(
            [(tile, (355000, 6650000, 355500, 6650500))],
            tmp_path / "dem.tif",
            bbox=(355000, 6650000, 356000, 6651000),
        )


def test_dem_build_publishes_transactionally(tmp_path, monkeypatch):
    def fake_fetch(run_dir, api_key, bbox=config.CAPITAL_REGION_BBOX_3067):
        return []

    def fake_mosaic(chunks, out, bbox=config.CAPITAL_REGION_BBOX_3067):
        out.write_bytes(b"the mosaicked dem")
        return out

    monkeypatch.setattr(dem, "fetch_chunks", fake_fetch)
    monkeypatch.setattr(dem, "mosaic_to_cog", fake_mosaic)
    records = dem.build(tmp_path, api_key="SECRET")
    record = records[config.DEM_ASSET]
    assert record["sha256"] == hashlib.sha256(b"the mosaicked dem").hexdigest()
    # The NLS attribution carries the acquisition year.
    assert record["attribution"].rstrip()[-4:].isdigit()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [config.DEM_ASSET, "manifest-dem.json"]


def test_fetch_chunks_requires_a_stable_double_pass(tmp_path, monkeypatch):
    # Every chunk is fetched twice and must come back byte-identical; a
    # service that keeps changing across passes fails the step.
    import hashlib as _hashlib
    import pathlib as _pathlib

    calls = {"count": 0}

    def changing_download(url, destination, timeout=None, max_bytes=None, headers=None):
        calls["count"] += 1
        payload = f"generation-{calls['count']}".encode()
        _pathlib.Path(destination).write_bytes(payload)
        return {"sha256": _hashlib.sha256(payload).hexdigest(), "size": len(payload)}

    monkeypatch.setattr(dem.download, "stream_download", changing_download)
    with pytest.raises(PipelineError, match="different bytes across"):
        dem.fetch_chunks(tmp_path, "KEY", bbox=(0, 0, 10000, 10000))


def test_fetch_chunks_accepts_a_stable_service(tmp_path, monkeypatch):
    import hashlib as _hashlib
    import pathlib as _pathlib

    def stable_download(url, destination, timeout=None, max_bytes=None, headers=None):
        payload = b"one stable generation"
        _pathlib.Path(destination).write_bytes(payload)
        return {"sha256": _hashlib.sha256(payload).hexdigest(), "size": len(payload)}

    monkeypatch.setattr(dem.download, "stream_download", stable_download)
    chunks = dem.fetch_chunks(tmp_path, "KEY", bbox=(0, 0, 10000, 10000))
    assert len(chunks) == 1
    assert chunks[0][0].read_bytes() == b"one stable generation"


def test_mosaic_refuses_mask_only_nodata(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    import numpy

    transform = rasterio.transform.from_origin(355000, 6650500, 10, 10)
    path = tmp_path / "masked.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=50,
        height=50,
        count=1,
        dtype="float32",
        crs="EPSG:3067",
        transform=transform,
        nodata=None,
    ) as sink:
        sink.write(numpy.zeros((50, 50), dtype="float32"), 1)
        mask = numpy.full((50, 50), 255, dtype="uint8")
        mask[:10, :] = 0  # a masked gap with no numeric nodata
        sink.write_mask(mask)
    with pytest.raises(PipelineError, match="internal mask"):
        dem.mosaic_to_cog(
            [(path, (355000, 6650000, 355500, 6650500))],
            tmp_path / "dem.tif",
            bbox=(355000, 6650000, 355500, 6650500),
            probes=[],
        )


# --- population -------------------------------------------------------------

CAPABILITIES = """<?xml version="1.0"?>
<WFS_Capabilities>
  <FeatureType><Name>asuminen_ja_maankaytto:Vaestotietoruudukko_2022</Name></FeatureType>
  <FeatureType><Name>asuminen_ja_maankaytto:Vaestotietoruudukko_2024</Name></FeatureType>
  <FeatureType><Name>asuminen_ja_maankaytto:Rakennustietoruudukko_2024</Name></FeatureType>
</WFS_Capabilities>
"""


def test_latest_grid_layer_picks_the_newest_year():
    layer, year = population.latest_grid_layer(CAPABILITIES)
    assert layer == "asuminen_ja_maankaytto:Vaestotietoruudukko_2024"
    assert year == 2024


def test_missing_grid_layer_is_an_error():
    with pytest.raises(PipelineError, match="layer naming"):
        population.latest_grid_layer("<WFS_Capabilities/>")


def _grid_geojson(path, *, column="asukkaita", east=380000, north=6670000, value=42):
    cell = {
        "type": "Feature",
        "id": f"grid.{east}.{north}",
        "properties": {column: value, "index": 1},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [east, north],
                    [east + 250, north],
                    [east + 250, north + 250],
                    [east, north + 250],
                    [east, north],
                ]
            ],
        },
    }
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [cell]}))
    return path


def test_write_grid_round_trips_to_geopackage(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIN_POPULATION_CELLS", 1)
    geopandas = pytest.importorskip("geopandas")
    import pyogrio

    source = _grid_geojson(tmp_path / "grid.json")
    out = tmp_path / "grid.gpkg"
    population.write_grid(source, out, year=2024)
    grid = geopandas.read_file(out, layer="population_grid")
    assert grid.crs.to_epsg() == 3067
    assert grid[config.POPULATION_COLUMN].tolist() == [42]
    # The source year rides the layer metadata, per the plan.
    info = pyogrio.read_info(out, layer="population_grid")
    assert info["layer_metadata"] == {"source_year": "2024"}


def test_write_grid_refuses_a_missing_population_column(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIN_POPULATION_CELLS", 1)
    pytest.importorskip("geopandas")
    source = _grid_geojson(tmp_path / "grid.json", column="somethingelse")
    with pytest.raises(PipelineError, match="schema changed"):
        population.write_grid(source, tmp_path / "grid.gpkg")


def test_write_grid_clips_to_the_region_and_refuses_emptiness(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIN_POPULATION_CELLS", 1)
    geopandas = pytest.importorskip("geopandas")
    import json as _json

    # Inside + outside cells: the outside one is clipped away.
    inside = _json.loads(_grid_geojson(tmp_path / "a.json").read_text())
    outside = _json.loads(
        _grid_geojson(tmp_path / "b.json", east=500000, north=7300000).read_text()
    )
    both = tmp_path / "both.json"
    both.write_text(
        _json.dumps(
            {
                "type": "FeatureCollection",
                "features": inside["features"] + outside["features"],
            }
        )
    )
    out = tmp_path / "grid.gpkg"
    population.write_grid(both, out)
    assert len(geopandas.read_file(out, layer="population_grid")) == 1
    # All-outside input is a wrong layer, not a graze.
    source = _grid_geojson(tmp_path / "off.json", east=500000, north=7300000)
    with pytest.raises(PipelineError, match="grid cells fall inside"):
        population.write_grid(source, tmp_path / "off.gpkg")


class _WfsHandler(http.server.BaseHTTPRequestHandler):
    """A fake HSY WFS: capabilities, a hits count, and paged features."""

    grid_body = None
    hits = 1

    def do_GET(self):  # noqa: N802 - stdlib handler naming
        if "GetCapabilities" in self.path:
            body = CAPABILITIES.encode()
        elif "resultType=hits" in self.path:
            body = (
                f'<wfs:FeatureCollection numberMatched="{self.hits}" '
                f'numberReturned="0"/>'
            ).encode()
        elif "GetFeature" in self.path:
            body = self.grid_body
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_population_build_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIN_POPULATION_CELLS", 1)
    pytest.importorskip("geopandas")
    grid_json = _grid_geojson(tmp_path / "source.json")
    handler = type("Handler", (_WfsHandler,), {"grid_body": grid_json.read_bytes()})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(
        config, "HSY_WFS_URL", f"http://127.0.0.1:{httpd.server_address[1]}"
    )
    work = tmp_path / "work"
    work.mkdir()
    try:
        records = population.build(work)
    finally:
        httpd.shutdown()
    record = records[config.POPULATION_ASSET]
    assert "Vaestotietoruudukko_2024" in record["source_stamp"]
    assert "2024" in record["attribution"]
    names = sorted(p.name for p in work.iterdir())
    assert names == [config.POPULATION_ASSET, "manifest-population.json"]


def test_population_build_refuses_a_partial_grid(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIN_POPULATION_CELLS", 1)
    pytest.importorskip("geopandas")
    grid_json = _grid_geojson(tmp_path / "source.json")
    handler = type(
        "Handler",
        (_WfsHandler,),
        {"grid_body": grid_json.read_bytes(), "hits": 5},  # declares 5, sends 1
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(
        config, "HSY_WFS_URL", f"http://127.0.0.1:{httpd.server_address[1]}"
    )
    work = tmp_path / "work"
    work.mkdir()
    try:
        with pytest.raises(PipelineError, match="partial grid"):
            population.build(work)
    finally:
        httpd.shutdown()


def test_population_build_refuses_duplicate_features(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIN_POPULATION_CELLS", 1)
    pytest.importorskip("geopandas")
    import json as _json

    single = _json.loads(_grid_geojson(tmp_path / "one.json").read_text())
    doubled = tmp_path / "doubled.json"
    doubled.write_text(
        _json.dumps(
            {
                "type": "FeatureCollection",
                "features": single["features"] * 2,  # same id twice
            }
        )
    )
    handler = type(
        "Handler", (_WfsHandler,), {"grid_body": doubled.read_bytes(), "hits": 2}
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(
        config, "HSY_WFS_URL", f"http://127.0.0.1:{httpd.server_address[1]}"
    )
    work = tmp_path / "work"
    work.mkdir()
    try:
        with pytest.raises(PipelineError, match="twice"):
            population.build(work)
    finally:
        httpd.shutdown()


def test_mosaic_refuses_a_multiband_tile(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    import numpy

    transform = rasterio.transform.from_origin(355000, 6650500, 10, 10)
    path = tmp_path / "rgb.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=50,
        height=50,
        count=3,
        dtype="float32",
        crs="EPSG:3067",
        transform=transform,
    ) as sink:
        for band in (1, 2, 3):
            sink.write(numpy.zeros((50, 50), dtype="float32"), band)
    with pytest.raises(PipelineError, match="bands"):
        dem.mosaic_to_cog(
            [(path, (355000, 6650000, 355500, 6650500))],
            tmp_path / "dem.tif",
            bbox=(355000, 6650000, 355500, 6650500),
        )


def test_write_grid_refuses_null_geometries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIN_POPULATION_CELLS", 1)
    pytest.importorskip("geopandas")
    import json as _json

    cell = _json.loads(_grid_geojson(tmp_path / "a.json").read_text())
    nulled = dict(cell)
    nulled["features"] = cell["features"] + [
        {
            "type": "Feature",
            "id": "grid.null",
            "properties": {"asukkaita": 1, "index": 2},
            "geometry": None,
        }
    ]
    source = tmp_path / "null.json"
    source.write_text(_json.dumps(nulled))
    with pytest.raises(PipelineError, match="null or empty"):
        population.write_grid(source, tmp_path / "n.gpkg")


def test_write_grid_refuses_unusable_population_values(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIN_POPULATION_CELLS", 1)
    pytest.importorskip("geopandas")
    negative = _grid_geojson(tmp_path / "n.json", value=-5)
    with pytest.raises(PipelineError, match="negative"):
        population.write_grid(negative, tmp_path / "n.gpkg")
    nobody = _grid_geojson(tmp_path / "z.json", value=0)
    with pytest.raises(PipelineError, match="no positive population"):
        population.write_grid(nobody, tmp_path / "z.gpkg")


def test_write_grid_refuses_cells_that_are_not_250m(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIN_POPULATION_CELLS", 1)
    pytest.importorskip("geopandas")
    import json as _json

    cell = _json.loads(_grid_geojson(tmp_path / "a.json").read_text())
    # Stretch the cell to a kilometre.
    coordinates = cell["features"][0]["geometry"]["coordinates"][0]
    coordinates[1][0] += 750
    coordinates[2][0] += 750
    stretched = tmp_path / "stretched.json"
    stretched.write_text(_json.dumps(cell))
    with pytest.raises(PipelineError, match="not 250 m"):
        population.write_grid(stretched, tmp_path / "s.gpkg")


def test_mosaic_probes_catch_a_hole_over_land(tmp_path):
    pytest.importorskip("rasterio")
    # Two chunks: the probe's chunk is all nodata, the other is fine —
    # the global fraction passes, the probe does not.
    good = _write_tile(tmp_path / "good.tif", east0=355000, north1=6650500)
    hole = _write_tile(
        tmp_path / "hole.tif", east0=355500, north1=6650500, value=-9999.0
    )
    with pytest.raises(PipelineError, match="land probe"):
        dem.mosaic_to_cog(
            [
                (good, (355000, 6650000, 355500, 6650500)),
                (hole, (355500, 6650000, 356000, 6650500)),
            ],
            tmp_path / "dem.tif",
            bbox=TILE_BBOX,
            probes=[(355750, 6650250)],  # inside the nodata chunk
        )


def test_mosaic_fraction_floor_catches_widespread_nodata(tmp_path):
    pytest.importorskip("rasterio")
    # Both chunks nearly empty: no probe needed to refuse this.
    a = _write_tile(tmp_path / "a.tif", east0=355000, north1=6650500, value=-9999.0)
    b = _write_tile(tmp_path / "b.tif", east0=355500, north1=6650500, value=-9999.0)
    with pytest.raises(PipelineError, match="valid|land probe"):
        dem.mosaic_to_cog(
            [
                (a, (355000, 6650000, 355500, 6650500)),
                (b, (355500, 6650000, 356000, 6650500)),
            ],
            tmp_path / "dem.tif",
            bbox=TILE_BBOX,
            probes=[],
        )


# --- smoke: manifest verification -------------------------------------------


def _step_manifest(work_dir, manifest_name, asset_name, payload):
    (work_dir / asset_name).write_bytes(payload)
    record = manifest.asset_record(
        work_dir / asset_name,
        license="x",
        attribution="y",
        source_stamp="z",
    )
    manifest.write_manifest(work_dir / manifest_name, {asset_name: record})
    return record


def test_verify_manifests_accepts_matching_assets(tmp_path):
    _step_manifest(tmp_path, "manifest-a.json", "a.bin", b"aa")
    _step_manifest(tmp_path, "manifest-b.json", "b.bin", b"bb")
    merged = smoke.verify_manifests(
        tmp_path, names={"manifest-a.json": "a.bin", "manifest-b.json": "b.bin"}
    )
    assert set(merged) == {"a.bin", "b.bin"}


def test_verify_manifests_rejects_drifted_bytes(tmp_path):
    _step_manifest(tmp_path, "manifest-a.json", "a.bin", b"aa")
    (tmp_path / "a.bin").write_bytes(b"tampered")
    with pytest.raises(PipelineError, match="does not match its manifest"):
        smoke.verify_manifests(tmp_path, names={"manifest-a.json": "a.bin"})


def test_verify_manifests_rejects_a_missing_manifest(tmp_path):
    with pytest.raises(PipelineError, match="missing"):
        smoke.verify_manifests(tmp_path, names={"manifest-a.json": "a.bin"})


def test_verify_manifests_rejects_an_unexpected_asset_set(tmp_path):
    _step_manifest(tmp_path, "manifest-a.json", "other.bin", b"aa")
    with pytest.raises(PipelineError, match="expected exactly"):
        smoke.verify_manifests(tmp_path, names={"manifest-a.json": "a.bin"})


def test_verify_manifests_rejects_a_traversal_filename(tmp_path):
    import json as _json

    record = manifest.asset_record(
        _step_manifest(tmp_path, "unused.json", "a.bin", b"aa") and tmp_path / "a.bin",
        license="x",
        attribution="y",
        source_stamp="z",
        name="../a.bin",
    )
    (tmp_path / "manifest-a.json").write_text(
        _json.dumps({"schema": 1, "assets": {"a.bin": record}})
    )
    with pytest.raises(PipelineError, match="records filename"):
        smoke.verify_manifests(tmp_path, names={"manifest-a.json": "a.bin"})


def test_verify_manifests_rejects_a_symlinked_asset(tmp_path):
    _step_manifest(tmp_path, "manifest-a.json", "a.bin", b"aa")
    victim = tmp_path / "elsewhere.bin"
    (tmp_path / "a.bin").rename(victim)
    (tmp_path / "a.bin").symlink_to(victim)
    with pytest.raises(PipelineError, match="not a regular file"):
        smoke.verify_manifests(tmp_path, names={"manifest-a.json": "a.bin"})


def test_smoke_run_takes_the_workdir_lock(tmp_path):
    (tmp_path / ".pipeline-lock").touch()
    with pytest.raises(PipelineError, match="already in use"):
        smoke.run(tmp_path, "2026-08-11")


def test_smoke_without_cafein_names_the_dependency(tmp_path, monkeypatch):
    import sys

    _step_manifest(tmp_path, "manifest-osm.json", config.OSM_ASSET, b"osm")
    _step_manifest(tmp_path, "manifest-gtfs.json", config.GTFS_ASSET, b"gtfs")
    _step_manifest(tmp_path, "manifest-dem.json", config.DEM_ASSET, b"dem")
    _step_manifest(
        tmp_path, "manifest-population.json", config.POPULATION_ASSET, b"grid"
    )
    monkeypatch.setitem(sys.modules, "cafein", None)
    with pytest.raises(PipelineError, match="cafein"):
        smoke.run(tmp_path, "2026-08-11")


def test_write_grid_default_floor_rejects_a_tiny_grid(tmp_path):
    pytest.importorskip("geopandas")
    source = _grid_geojson(tmp_path / "tiny.json")
    with pytest.raises(PipelineError, match="truncated or wrong layer"):
        population.write_grid(source, tmp_path / "tiny.gpkg")


def test_smoke_snapshots_catch_a_swapped_asset(tmp_path):
    from pipeline import smoke as _smoke

    (tmp_path / "a.bin").write_bytes(b"aa")
    record = manifest.asset_record(
        tmp_path / "a.bin", license="x", attribution="y", source_stamp="z"
    )
    snap = tmp_path / "snap"
    snap.mkdir()
    # Unchanged: the snapshot verifies and is a private copy.
    paths = _smoke.snapshot_assets(tmp_path, {"a.bin": record}, snap)
    assert paths["a.bin"].read_bytes() == b"aa"
    assert paths["a.bin"].parent == snap
    # Swapped since the manifest was written: the copy re-hash fails.
    (tmp_path / "a.bin").write_bytes(b"swapped")
    with pytest.raises(PipelineError, match="changed while being snapshotted"):
        _smoke.snapshot_assets(tmp_path, {"a.bin": record}, snap)


def test_mosaic_refuses_an_inward_snapped_tile(tmp_path):
    pytest.importorskip("rasterio")
    # 490 m of coverage for a 500 m chunk: snapped inward, a gap.
    tile = _write_tile(tmp_path / "in.tif", east0=355010, north1=6650500, size=49)
    with pytest.raises(PipelineError, match="do not cover its requested"):
        dem.mosaic_to_cog(
            [(tile, (355000, 6650000, 355500, 6650500))],
            tmp_path / "dem.tif",
            bbox=(355000, 6650000, 355500, 6650500),
        )


def test_mosaic_refuses_an_all_nodata_land_chunk(tmp_path):
    pytest.importorskip("rasterio")
    good = _write_tile(tmp_path / "good.tif", east0=355000, north1=6650500)
    hole = _write_tile(
        tmp_path / "hole.tif", east0=355500, north1=6650500, value=-9999.0
    )
    with pytest.raises(PipelineError, match="mostly nodata"):
        dem.mosaic_to_cog(
            [
                (good, (355000, 6650000, 355500, 6650500)),
                (hole, (355500, 6650000, 356000, 6650500)),
            ],
            tmp_path / "dem.tif",
            bbox=TILE_BBOX,
            probes=[],
            land_north_of=6_650_000,  # everything in this test bbox is land
        )


def test_write_grid_refuses_a_holed_cell(tmp_path, monkeypatch):
    pytest.importorskip("geopandas")
    import json as _json

    monkeypatch.setattr(config, "MIN_POPULATION_CELLS", 1)
    cell = _json.loads(_grid_geojson(tmp_path / "a.json").read_text())
    geometry = cell["features"][0]["geometry"]
    east, north = 380000, 6670000
    geometry["coordinates"].append(
        [
            [east + 50, north + 50],
            [east + 200, north + 50],
            [east + 200, north + 200],
            [east + 50, north + 200],
            [east + 50, north + 50],
        ]
    )
    holed = tmp_path / "holed.json"
    holed.write_text(_json.dumps(cell))
    with pytest.raises(PipelineError, match="not full 250 m squares"):
        population.write_grid(holed, tmp_path / "holed.gpkg")


def test_land_chunk_check_honours_the_internal_mask(tmp_path):
    rasterio = pytest.importorskip("rasterio")
    import numpy

    transform = rasterio.transform.from_origin(355000, 6650500, 10, 10)
    path = tmp_path / "maskedland.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=50,
        height=50,
        count=1,
        dtype="float32",
        crs="EPSG:3067",
        transform=transform,
        nodata=-9999.0,
    ) as sink:
        sink.write(numpy.full((50, 50), 25.0, dtype="float32"), 1)
        sink.write_mask(numpy.zeros((50, 50), dtype="uint8"))  # all invalid
    with pytest.raises(PipelineError, match="mostly nodata"):
        dem.mosaic_to_cog(
            [(path, (355000, 6650000, 355500, 6650500))],
            tmp_path / "dem.tif",
            bbox=(355000, 6650000, 355500, 6650500),
            probes=[],
            land_north_of=6_650_000,
        )


def test_mosaic_refuses_a_subpixel_shifted_grid(tmp_path):
    pytest.importorskip("rasterio")
    # Covers the chunk (outward within slack) but on a half-pixel-
    # shifted grid: the crop cannot land on the requested bounds.
    tile = _write_tile(tmp_path / "half.tif", east0=354995, north1=6650505, size=51)
    with pytest.raises(PipelineError, match="sub-pixel-shifted"):
        dem.mosaic_to_cog(
            [(tile, (355000, 6650000, 355500, 6650500))],
            tmp_path / "dem.tif",
            bbox=(355000, 6650000, 355500, 6650500),
            probes=[],
        )


def test_factor_coverage_requires_exactly_the_ferry_gap():
    covered = {0, 1, 2, 3}
    smoke.check_factor_coverage({0, 1, 2, 3, 4}, covered)  # the expected shape
    with pytest.raises(PipelineError, match="carries no route type"):
        smoke.check_factor_coverage({0, 3, 4}, covered)  # tram, metro gone
    with pytest.raises(PipelineError, match="carries no route type"):
        smoke.check_factor_coverage({4}, covered)  # everything else gone
    with pytest.raises(PipelineError, match="expected exactly"):
        smoke.check_factor_coverage({0, 1, 2, 3}, covered)  # no ferry: drift
    with pytest.raises(PipelineError, match="expected exactly"):
        smoke.check_factor_coverage({0, 1, 2, 3, 4, 7}, covered)  # funicular
    with pytest.raises(PipelineError, match="covers no route types"):
        smoke.check_factor_coverage({0, 4}, set())  # an empty factor table


def test_factor_coverage_resolves_extended_route_types():
    # HSL publishes extended codes: 109 commuter rail, 700-series bus,
    # 1200-range ferry. They count as their base GTFS modes.
    covered = {0, 1, 2, 3}
    base_of = {109: 2, 700: 3, 701: 3, 702: 3, 704: 3, 1200: 4}.get
    smoke.check_factor_coverage({0, 1, 4, 109, 700, 701}, covered, base_of)
    # An extended ferry code is still exactly the ferry gap.
    smoke.check_factor_coverage({0, 1, 109, 702, 704, 1200}, covered, base_of)
    with pytest.raises(PipelineError, match="carries no route type"):
        # No rail in any form, classic or extended.
        smoke.check_factor_coverage({0, 1, 4, 700}, covered, base_of)
    with pytest.raises(PipelineError, match="expected exactly"):
        # A code with no base mapping is an uncovered mode, not noise.
        smoke.check_factor_coverage({0, 1, 4, 109, 700, 1500}, covered, base_of)


def test_stop_sampling_spreads_across_the_list():
    stops = [f"stop-{index:05d}" for index in range(8305)]
    sampled = smoke.sample_stops(stops)
    assert len(sampled) == 25
    # The head of an id-sorted HSL stop list is an unserved-terminal
    # block; a real spread must reach far past it.
    assert sampled[0] == "stop-00000"
    assert sampled[-1] >= "stop-07000"
    assert smoke.sample_stops(["a", "b"]) == ["a", "b"]


def test_feed_route_types_reads_the_feed(tmp_path):
    import zipfile

    path = tmp_path / "feed.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "routes.txt",
            "route_id,route_type\na,0\nb,3\nc,4\n",
        )
    assert smoke.feed_route_types(path) == {0, 3, 4}
