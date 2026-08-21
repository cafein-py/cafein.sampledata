"""The Paavo step: layer discovery, fetch, sentinel nulling, the pins."""

import http.server
import json
import threading

import pytest

from pipeline import PipelineError, config, paavo, registry, release, smoke

CAPABILITIES = """<?xml version="1.0"?>
<WFS_Capabilities>
  <FeatureType><Name>postialue:pno_2024</Name></FeatureType>
  <FeatureType><Name>postialue:pno_tilasto_2024</Name></FeatureType>
  <FeatureType><Name>postialue:pno_tilasto_2026</Name></FeatureType>
  <FeatureType><Name>postialue:pno_meri_2026</Name></FeatureType>
</WFS_Capabilities>
"""


def test_latest_statistics_layer_picks_the_newest_year():
    layer, year = paavo.latest_statistics_layer(CAPABILITIES)
    assert layer == "postialue:pno_tilasto_2026"
    assert year == 2026


def test_missing_statistics_layer_is_an_error():
    with pytest.raises(PipelineError, match="layer naming"):
        paavo.latest_statistics_layer("<WFS_Capabilities/>")


def test_feature_url_pins_extent_columns_and_geometry():
    url = paavo.feature_url("postialue:pno_tilasto_2026")
    # The bbox filter in the URN axis order, so the fetch is bounded to
    # the shared extent server-side.
    east_min, north_min, east_max, north_max = config.CAPITAL_REGION_BBOX_3067
    assert f"bbox={east_min},{north_min},{east_max},{north_max}," in url
    assert "urn:ogc:def:crs:EPSG::3067" in url
    assert "srsName=EPSG:3067" in url
    # Every column requested by name — and the geometry property too,
    # which propertyName would otherwise exclude from the response.
    assert f"propertyName={config.PAAVO_GEOMETRY_PROPERTY}," in url
    for column in config.STATISTICS_INCOME_COLUMNS:
        assert column in url
    assert f"count={config.MAX_PAAVO_FEATURES}" in url
    hits = paavo.feature_url("postialue:pno_tilasto_2026", hits=True)
    assert "resultType=hits" in hits and "propertyName" not in hits


# --- fixtures ----------------------------------------------------------------

EAST0, NORTH0 = 380000, 6670000


def _area(code, east, north, size=2000, overrides=None):
    values = {
        "postinumeroalue": code,
        "nimi": f"Alue {code}",
        "kunta": "091",
        "hr_tuy": 500,
        "hr_ktu": 30000,
        "hr_mtu": 27000,
        "hr_pi_tul": 100,
        "hr_ke_tul": 250,
        "hr_hy_tul": 150,
        "tr_kuty": 300,
        "tr_ktu": 45000,
        "tr_mtu": 40000,
        "tr_pi_tul": 80,
        "tr_ke_tul": 130,
        "tr_hy_tul": 90,
        "te_taly": 310,
    }
    values.update(overrides or {})
    return {
        "type": "Feature",
        "id": f"pno_tilasto_2026.{code}",
        "properties": values,
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [east, north],
                    [east + size, north],
                    [east + size, north + size],
                    [east, north + size],
                    [east, north],
                ]
            ],
        },
    }


def _layer_geojson(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path


@pytest.fixture()
def small_floor(monkeypatch):
    monkeypatch.setattr(config, "MIN_STATISTICS_AREAS", 1)


# --- write_layer -------------------------------------------------------------


def test_write_layer_round_trips_to_geopackage(tmp_path, small_floor):
    geopandas = pytest.importorskip("geopandas")
    import pyogrio

    source = _layer_geojson(tmp_path / "paavo.json", [_area("00100", EAST0, NORTH0)])
    out = tmp_path / "income.gpkg"
    paavo.write_layer(source, out, year=2026)
    areas = geopandas.read_file(out, layer="income")
    assert areas.crs.to_epsg() == 3067
    assert list(areas.columns) == list(config.STATISTICS_INCOME_COLUMNS) + ["geometry"]
    assert areas["hr_mtu"].tolist() == [27000]
    assert areas["postinumeroalue"].tolist() == ["00100"]
    info = pyogrio.read_info(out, layer="income")
    assert info["layer_metadata"] == {"source_year": "2026"}


def test_write_layer_nulls_the_privacy_sentinel_only(tmp_path, small_floor):
    geopandas = pytest.importorskip("geopandas")
    protected = _area(
        "01740",
        EAST0 + 3000,
        NORTH0,
        overrides={
            "tr_ktu": -1,
            "tr_mtu": -1,
            "tr_pi_tul": -1,
            "tr_ke_tul": -1,
            "tr_hy_tul": -1,
        },
    )
    source = _layer_geojson(
        tmp_path / "paavo.json", [_area("00100", EAST0, NORTH0), protected]
    )
    out = tmp_path / "income.gpkg"
    paavo.write_layer(source, out, year=2026)
    areas = geopandas.read_file(out, layer="income").set_index("postinumeroalue")
    assert (
        areas.loc["01740", "tr_mtu"] is None
        or areas.loc["01740"][
            ["tr_ktu", "tr_mtu", "tr_pi_tul", "tr_ke_tul", "tr_hy_tul"]
        ]
        .isna()
        .all()
    )
    # The published values next to the sentinel survive untouched.
    assert areas.loc["01740", "hr_mtu"] == 27000
    assert areas.loc["00100", "tr_mtu"] == 40000


def test_write_layer_refuses_an_undocumented_negative(tmp_path, small_floor):
    pytest.importorskip("geopandas")
    source = _layer_geojson(
        tmp_path / "paavo.json",
        [_area("00100", EAST0, NORTH0, overrides={"hr_ktu": -5})],
    )
    with pytest.raises(PipelineError, match="undocumented marker"):
        paavo.write_layer(source, tmp_path / "income.gpkg")


def test_write_layer_refuses_a_missing_column(tmp_path, small_floor):
    pytest.importorskip("geopandas")
    area = _area("00100", EAST0, NORTH0)
    del area["properties"]["hr_mtu"]
    source = _layer_geojson(tmp_path / "paavo.json", [area])
    with pytest.raises(PipelineError, match="schema changed"):
        paavo.write_layer(source, tmp_path / "income.gpkg")


def test_write_layer_clips_to_the_region(tmp_path, small_floor):
    geopandas = pytest.importorskip("geopandas")
    # An area whose centroid falls outside the extent is bbox-adjacent
    # noise, clipped away.
    outside = _area("03100", 340000, 6710000)
    source = _layer_geojson(
        tmp_path / "paavo.json", [_area("00100", EAST0, NORTH0), outside]
    )
    out = tmp_path / "income.gpkg"
    paavo.write_layer(source, out)
    areas = geopandas.read_file(out, layer="income")
    assert areas["postinumeroalue"].tolist() == ["00100"]


def test_write_layer_default_floor_rejects_a_tiny_layer(tmp_path):
    pytest.importorskip("geopandas")
    source = _layer_geojson(tmp_path / "paavo.json", [_area("00100", EAST0, NORTH0)])
    with pytest.raises(PipelineError, match="truncated or wrong layer"):
        paavo.write_layer(source, tmp_path / "income.gpkg")


def test_write_layer_refuses_duplicate_postal_codes(tmp_path, small_floor):
    pytest.importorskip("geopandas")
    source = _layer_geojson(
        tmp_path / "paavo.json",
        [_area("00100", EAST0, NORTH0), _area("00100", EAST0 + 3000, NORTH0)],
    )
    with pytest.raises(PipelineError, match="duplicated postal codes"):
        paavo.write_layer(source, tmp_path / "income.gpkg")


def test_write_layer_refuses_numeric_postal_codes(tmp_path, small_floor):
    # A numeric code sheds its leading zero (00100 -> 100) and breaks
    # every downstream join; refuse rather than publish it.
    pytest.importorskip("geopandas")
    source = _layer_geojson(
        tmp_path / "paavo.json",
        [_area(100, EAST0, NORTH0)],
    )
    with pytest.raises(PipelineError, match="five-digit"):
        paavo.write_layer(source, tmp_path / "income.gpkg")


def test_write_layer_refuses_malformed_postal_codes(tmp_path, small_floor):
    pytest.importorskip("geopandas")
    source = _layer_geojson(
        tmp_path / "paavo.json",
        [_area("0010", EAST0, NORTH0)],
    )
    with pytest.raises(PipelineError, match="five-digit"):
        paavo.write_layer(source, tmp_path / "income.gpkg")


def test_write_layer_refuses_a_missing_or_numeric_identity(tmp_path, small_floor):
    pytest.importorskip("geopandas")
    # An empty area name, or a municipality code served as a number
    # (049 -> 49): both are schema drift, refused.
    nameless = _layer_geojson(
        tmp_path / "a.json", [_area("00100", EAST0, NORTH0, overrides={"nimi": ""})]
    )
    with pytest.raises(PipelineError, match="empty area names"):
        paavo.write_layer(nameless, tmp_path / "a.gpkg")
    numeric = _layer_geojson(
        tmp_path / "b.json", [_area("00100", EAST0, NORTH0, overrides={"kunta": 49})]
    )
    with pytest.raises(PipelineError, match="three-digit"):
        paavo.write_layer(numeric, tmp_path / "b.gpkg")


def test_write_layer_refuses_a_boolean_variable(tmp_path, small_floor):
    # Booleans count as numeric to pandas; True==1 would read as a
    # plausible count.
    pytest.importorskip("geopandas")
    source = _layer_geojson(
        tmp_path / "paavo.json",
        [
            _area("00100", EAST0, NORTH0, overrides={"hr_pi_tul": True}),
            _area("00120", EAST0 + 3000, NORTH0, overrides={"hr_pi_tul": False}),
        ],
    )
    with pytest.raises(PipelineError, match="not numeric"):
        paavo.write_layer(source, tmp_path / "income.gpkg")


def test_the_smoke_refuses_a_nulled_identity(tmp_path, monkeypatch, small_floor):
    """A null postal code must fail the smoke even though pandas'
    string matcher answers NA for it."""
    geopandas = pytest.importorskip("geopandas")
    source = _layer_geojson(
        tmp_path / "paavo.json",
        [_area("00100", EAST0, NORTH0), _area("00120", EAST0 + 3000, NORTH0)],
    )
    out = tmp_path / "income.gpkg"
    paavo.write_layer(source, out, year=2026)
    # Corrupt the written layer the way an upstream drift would.
    areas = geopandas.read_file(out, layer="income")
    areas.loc[0, "postinumeroalue"] = None
    areas.to_file(out, layer="income", driver="GPKG")

    class _Streets:
        def travel_time(self, origin, destination, *, mode):
            return 600.0

    monkeypatch.setattr(config, "MIN_STATISTICS_AREAS", 1)
    with pytest.raises(PipelineError, match="identity columns"):
        smoke.check_statistics({config.STATISTICS_INCOME_ASSET: out}, _Streets())


def test_write_layer_refuses_a_non_finite_value(tmp_path, small_floor):
    # JSON has no infinity, but 1e999 parses to one — and an inf in a
    # mean poisons it as silently as the sentinel would.
    pytest.importorskip("geopandas")
    source = _layer_geojson(
        tmp_path / "paavo.json",
        [_area("00100", EAST0, NORTH0, overrides={"hr_ktu": 1e999})],
    )
    with pytest.raises(PipelineError, match="non-finite"):
        paavo.write_layer(source, tmp_path / "income.gpkg")


def test_write_layer_refuses_an_all_null_variable(tmp_path, small_floor):
    # A column nulled in every retained area is a vanished variable,
    # not privacy protection.
    pytest.importorskip("geopandas")
    source = _layer_geojson(
        tmp_path / "paavo.json",
        [
            _area("00100", EAST0, NORTH0, overrides={"tr_hy_tul": -1}),
            _area("00120", EAST0 + 3000, NORTH0, overrides={"tr_hy_tul": -1}),
        ],
    )
    with pytest.raises(PipelineError, match="no values at all"):
        paavo.write_layer(source, tmp_path / "income.gpkg")


def test_write_layer_refuses_incomeless_data(tmp_path, small_floor):
    pytest.importorskip("geopandas")
    source = _layer_geojson(
        tmp_path / "paavo.json",
        [_area("00100", EAST0, NORTH0, overrides={"hr_mtu": 0})],
    )
    with pytest.raises(PipelineError, match="no positive median incomes"):
        paavo.write_layer(source, tmp_path / "income.gpkg")


# --- the step against a fake WFS ---------------------------------------------


class _WfsHandler(http.server.BaseHTTPRequestHandler):
    """A fake Paavo WFS: capabilities, a hits count, and the features."""

    body = None
    hits = 1

    def do_GET(self):  # noqa: N802 - stdlib handler naming
        if "GetCapabilities" in self.path:
            payload = CAPABILITIES.encode()
        elif "resultType=hits" in self.path:
            payload = (
                f'<wfs:FeatureCollection numberMatched="{self.hits}" '
                f'numberReturned="0"/>'
            ).encode()
        elif "GetFeature" in self.path:
            payload = self.body
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def _serve(monkeypatch, body, hits):
    handler = type("Handler", (_WfsHandler,), {"body": body, "hits": hits})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(
        config, "PAAVO_WFS_URL", f"http://127.0.0.1:{httpd.server_address[1]}"
    )
    return httpd


def test_paavo_build_end_to_end(tmp_path, monkeypatch, small_floor):
    pytest.importorskip("geopandas")
    body = _layer_geojson(
        tmp_path / "source.json", [_area("00100", EAST0, NORTH0)]
    ).read_bytes()
    httpd = _serve(monkeypatch, body, hits=1)
    work = tmp_path / "work"
    work.mkdir()
    try:
        records = paavo.build(work)
    finally:
        httpd.shutdown()
    record = records[config.STATISTICS_INCOME_ASSET]
    assert "pno_tilasto_2026" in record["source_stamp"]
    assert "privacy-protected values nulled" in record["source_stamp"]
    assert "2026" in record["attribution"]
    assert record["license"] == "CC BY 4.0"
    names = sorted(path.name for path in work.iterdir())
    assert names == sorted([config.STATISTICS_INCOME_ASSET, "manifest-paavo.json"])
    assert smoke.verify_manifests(
        work, names={"manifest-paavo.json": (config.STATISTICS_INCOME_ASSET,)}
    )


def test_paavo_build_refuses_a_partial_layer(tmp_path, monkeypatch, small_floor):
    pytest.importorskip("geopandas")
    body = _layer_geojson(
        tmp_path / "source.json", [_area("00100", EAST0, NORTH0)]
    ).read_bytes()
    httpd = _serve(monkeypatch, body, hits=5)  # declares 5, sends 1
    work = tmp_path / "work"
    work.mkdir()
    try:
        with pytest.raises(PipelineError, match="partial layer"):
            paavo.build(work)
    finally:
        httpd.shutdown()


def test_paavo_build_refuses_duplicate_features(tmp_path, monkeypatch, small_floor):
    pytest.importorskip("geopandas")
    single = _area("00100", EAST0, NORTH0)
    body = _layer_geojson(tmp_path / "doubled.json", [single, single]).read_bytes()
    httpd = _serve(monkeypatch, body, hits=2)
    work = tmp_path / "work"
    work.mkdir()
    try:
        with pytest.raises(PipelineError, match="twice"):
            paavo.build(work)
    finally:
        httpd.shutdown()


# --- coherence ---------------------------------------------------------------


def test_the_income_layer_is_pinned_and_attributed():
    from cafein.sampledata.helsinki import statistics as client

    assert set(client.LAYERS) == set(config.STATISTICS_ASSETS)
    for layer, asset in config.STATISTICS_ASSETS.items():
        assert registry.ATTRIBUTES[f"statistics_{layer}"] == asset
        # CC BY: the published layer states how it was derived.
        assert asset in release.MODIFICATIONS
        assert "null" in release.MODIFICATIONS[asset]
    assert set(config.STATISTICS_ASSETS.values()) <= smoke.expected_assets()


def test_the_statistics_namespace_resolves_like_pois(monkeypatch, tmp_path):
    import cafein.sampledata.helsinki as helsinki
    from cafein.sampledata.helsinki import _registry

    monkeypatch.setenv("CAFEIN_SAMPLEDATA_DIR", str(tmp_path / "cache"))
    assert "statistics" in dir(helsinki)
    assert "income" in dir(helsinki.statistics)
    if "statistics_income" in _registry.ASSETS:
        pytest.skip("this build already pins the income layer")
    # No data release carries the layer yet: the shipped registry has
    # no entry, and the guidance is the upgrade one, not a KeyError.
    from cafein.sampledata import SampleDataError

    with pytest.raises(SampleDataError, match="pins no data release"):
        helsinki.statistics.income


def test_an_unknown_statistics_layer_raises_attribute_error():
    import cafein.sampledata.helsinki as helsinki

    with pytest.raises(AttributeError, match="no attribute"):
        helsinki.statistics.wealth
