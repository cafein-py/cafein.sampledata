"""The helsinki subpackage: API surface, registry, factors, regeneration."""

import csv
import dataclasses
import functools
import hashlib
import http.server
import importlib
import threading

import pytest

import cafein.sampledata.helsinki as helsinki
from cafein.sampledata import Asset, SampleDataError
from cafein.sampledata.helsinki import _registry
from pipeline import PipelineError, manifest, registry

PAYLOAD = b"helsinki asset bytes\n" * 64


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    directory = tmp_path / "cache"
    monkeypatch.setenv("CAFEIN_SAMPLEDATA_DIR", str(directory))
    return directory


@pytest.fixture()
def served_asset(tmp_path, monkeypatch, cache):
    """A pinned `osm_pbf` registry entry backed by a local server."""
    root = tmp_path / "served"
    root.mkdir()
    (root / "helsinki_capital_region.osm.pbf").write_bytes(PAYLOAD)
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(root)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    pinned = dataclasses.replace(
        _registry.ASSETS["osm_pbf"],
        url=f"{url}/helsinki_capital_region.osm.pbf",
        sha256=hashlib.sha256(PAYLOAD).hexdigest(),
        size=len(PAYLOAD),
        release="helsinki-2026.08",
    )
    monkeypatch.setitem(_registry.ASSETS, "osm_pbf", pinned)
    yield pinned
    httpd.shutdown()


def test_a_pinned_asset_downloads_on_attribute_access(served_asset, cache):
    path = helsinki.osm_pbf
    assert path.read_bytes() == PAYLOAD
    assert path.name == "helsinki_capital_region.osm.pbf"
    assert str(path).startswith(str(cache))


def test_an_unpinned_asset_raises_with_guidance(cache, monkeypatch):
    # Synthesized, so the test outlives the first registry regeneration.
    unpinned = dataclasses.replace(
        _registry.ASSETS["gtfs"], url="", sha256="", size=0, release=""
    )
    monkeypatch.setitem(_registry.ASSETS, "gtfs", unpinned)
    with pytest.raises(SampleDataError, match="pins no data release"):
        helsinki.gtfs


def test_unknown_attributes_raise_attribute_error():
    with pytest.raises(AttributeError, match="no attribute"):
        helsinki.does_not_exist


def test_dir_lists_the_public_surface():
    names = dir(helsinki)
    for expected in (
        "osm_pbf",
        "gtfs",
        "dem",
        "population_grid",
        "pois",
        "emission_factors",
        "metadata",
        "fetch",
    ):
        assert expected in names


# --- points of interest -----------------------------------------------------


def test_pois_resolve_by_category(tmp_path, monkeypatch, cache):
    """`helsinki.pois.library` downloads its own pinned layer."""
    payload = b"a geopackage of libraries\n" * 8
    root = tmp_path / "served"
    root.mkdir()
    (root / "helsinki_pois_library.gpkg").write_bytes(payload)
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(root)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    pinned = Asset(
        name="helsinki_pois_library.gpkg",
        url=f"{url}/helsinki_pois_library.gpkg",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        release="helsinki-2026.08",
    )
    monkeypatch.setitem(_registry.ASSETS, "poi_library", pinned)
    try:
        path = helsinki.pois.library
    finally:
        httpd.shutdown()
    assert path.read_bytes() == payload
    assert path.name == "helsinki_pois_library.gpkg"
    # Every category is reachable and listed, pinned or not.
    assert "library" in dir(helsinki.pois)
    assert "supermarket" in helsinki.pois.CATEGORIES


def test_an_unpinned_poi_category_raises_with_guidance(cache):
    # No data release carries the POI layers yet, so the shipped
    # registry has no entry at all — the guidance must still be the
    # "upgrade the package" one, not a KeyError.
    if "poi_supermarket" in _registry.ASSETS:
        pytest.skip("this build already pins the POI layers")
    with pytest.raises(SampleDataError, match="pins no data release"):
        helsinki.pois.supermarket


def test_an_unknown_poi_category_raises_attribute_error():
    with pytest.raises(AttributeError, match="no attribute"):
        helsinki.pois.swimming_pool


def test_metadata_surfaces_every_asset():
    table = helsinki.metadata
    # Whatever the shipped registry pins, plus the bundled files — the
    # pins grow with each data release (the POI layers land in the next
    # one), and metadata must follow without a test edit.
    assert set(table) == set(_registry.ASSETS) | {
        "emission_factors",
        "emission_factors_full",
    }
    for key in ("osm_pbf", "gtfs", "dem", "population_grid"):
        assert key in table
    assert table["osm_pbf"]["license"] == "ODbL 1.0"
    assert "Dey" in table["emission_factors"]["attribution"]
    # The bundled files carry the same audit fields as the downloads.
    import hashlib as _hashlib

    for key in ("emission_factors", "emission_factors_full"):
        entry = table[key]
        path = getattr(helsinki, key)
        assert entry["sha256"] == _hashlib.sha256(path.read_bytes()).hexdigest()
        assert entry["size"] == path.stat().st_size
        assert entry["release"].startswith("cafein.sampledata")


def test_fetch_prefetches_the_pinned_assets(served_asset, cache, monkeypatch):
    # Only one asset is pinned in this fixture; narrow the registry so
    # fetch() succeeds end to end.
    monkeypatch.setattr(_registry, "ASSETS", {"osm_pbf": _registry.ASSETS["osm_pbf"]})
    paths = helsinki.fetch()
    assert set(paths) == {"osm_pbf"}
    assert paths["osm_pbf"].read_bytes() == PAYLOAD


# --- the bundled emission factors -------------------------------------------


def test_the_loader_ready_factors_ship_and_parse():
    path = helsinki.emission_factors
    with open(path, newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    # Exactly cafein's transit defaults: route types 0-3, ferry absent.
    assert {row["route_type"] for row in rows} == {"0", "1", "2", "3"}
    assert list(rows[0]) == [
        "trip_id",
        "route_id",
        "agency_id",
        "route_type",
        "vehicle",
        "fuel",
        "infrastructure",
        "operations",
    ]
    bus = next(row for row in rows if row["route_type"] == "3")
    assert float(bus["fuel"]) == 72.0


def test_the_full_factors_reference_ships_and_parses():
    path = helsinki.emission_factors_full
    with open(path, newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) >= 20
    assert {row["table"] for row in rows} == {"transit", "street", "vehicle_class"}
    bus = next(
        row
        for row in rows
        if row["table"] == "vehicle_class" and row["vehicle_class"] == "bus-ICE"
    )
    assert float(bus["fuel"]) == 72.0
    assert all("Dey" in row["source"] for row in rows)


def test_the_bundled_factors_byte_equal_a_fresh_export():
    pytest.importorskip("cafein.emissions")
    from pipeline import factors

    assert helsinki.emission_factors.read_bytes() == factors.render_csv().encode(
        "utf-8"
    )
    assert (
        helsinki.emission_factors_full.read_bytes()
        == factors.render_full_csv().encode("utf-8")
    )


def test_the_loader_ready_file_loads_directly():
    pytest.importorskip("cafein.emissions")
    from cafein import emissions

    # The documented consumer call, no preprocessing: the file IS the
    # load_factors schema.
    loaded = emissions.load_factors(helsinki.emission_factors)
    assert len(loaded) == 4


# --- registry regeneration --------------------------------------------------


def _release_manifest(path):
    assets = {}
    for filename in registry.ATTRIBUTES.values():
        assets[filename] = {
            "file": filename,
            "sha256": hashlib.sha256(filename.encode()).hexdigest(),
            "size": 1000 + len(filename),
            "license": "CC BY 4.0",
            "attribution": f"source of {filename}",
            "source_stamp": f"stamp of {filename}",
        }
    manifest.write_manifest(path, assets)
    return assets


def test_regenerate_rewrites_the_pins(tmp_path):
    assets = _release_manifest(tmp_path / "manifest.json")
    registry_copy = tmp_path / "_registry.py"
    registry_copy.write_text(registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    registry.regenerate(tmp_path / "manifest.json", "helsinki-2026.08", registry_copy)
    # The rewritten module parses and carries the pins.
    namespace = {}
    code = registry_copy.read_text(encoding="utf-8")
    code = code.replace("from cafein.sampledata import Asset", "")
    namespace["Asset"] = Asset
    exec(compile(code, str(registry_copy), "exec"), namespace)
    assert namespace["RELEASE"] == "helsinki-2026.08"
    rebuilt = namespace["ASSETS"]
    assert set(rebuilt) == set(registry.ATTRIBUTES)
    entry = rebuilt["osm_pbf"]
    assert entry.sha256 == assets["helsinki_capital_region.osm.pbf"]["sha256"]
    assert entry.size == assets["helsinki_capital_region.osm.pbf"]["size"]
    assert entry.release == "helsinki-2026.08"
    assert entry.url == (
        f"{namespace['DOWNLOAD_BASE']}/helsinki-2026.08/"
        f"helsinki_capital_region.osm.pbf"
    )
    # Regeneration is idempotent: a second run produces identical text.
    before = registry_copy.read_text(encoding="utf-8")
    registry.regenerate(tmp_path / "manifest.json", "helsinki-2026.08", registry_copy)
    assert registry_copy.read_text(encoding="utf-8") == before


def test_generated_literals_are_double_quoted():
    # The registry is committed source: the generator must emit
    # black-clean (double-quoted) literals, repr's single quotes fail
    # the lint gate.
    assert registry._literal("plain") == '"plain"'
    assert registry._literal("© OSM") == '"© OSM"'
    assert registry._literal('with "quotes"') == '"with \\"quotes\\""'
    with pytest.raises(PipelineError, match="not a string"):
        registry._literal(7, "license", "file")


def test_regenerate_refuses_a_partial_manifest(tmp_path):
    manifest.write_manifest(
        tmp_path / "manifest.json",
        {
            "hsl_gtfs.zip": {
                "file": "hsl_gtfs.zip",
                "sha256": "x",
                "size": 1,
                "license": "l",
                "attribution": "a",
                "source_stamp": "s",
            }
        },
    )
    registry_copy = tmp_path / "_registry.py"
    registry_copy.write_text(registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    with pytest.raises(PipelineError, match="partial registry"):
        registry.regenerate(
            tmp_path / "manifest.json", "helsinki-2026.08", registry_copy
        )


def test_regenerate_refuses_an_inconsistent_manifest_record(tmp_path):
    assets = _release_manifest(tmp_path / "manifest.json")
    # Cross-wire one record's file field.
    payload = manifest.read_manifest(tmp_path / "manifest.json")
    payload["assets"]["hsl_gtfs.zip"]["file"] = "helsinki_capital_region.osm.pbf"
    manifest.write_manifest(tmp_path / "manifest.json", payload["assets"])
    registry_copy = tmp_path / "_registry.py"
    registry_copy.write_text(registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    with pytest.raises(PipelineError, match="internally inconsistent"):
        registry.regenerate(
            tmp_path / "manifest.json", "helsinki-2026.08", registry_copy
        )
    del assets


def test_regenerate_refuses_a_malformed_release_tag(tmp_path):
    _release_manifest(tmp_path / "manifest.json")
    registry_copy = tmp_path / "_registry.py"
    registry_copy.write_text(registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    with pytest.raises(PipelineError, match="malformed release"):
        registry.regenerate(tmp_path / "manifest.json", "../evil", registry_copy)


def test_regenerate_survives_marker_text_inside_values(tmp_path):
    # An attribution merely containing the END marker text must not
    # corrupt the next regeneration.
    assets = _release_manifest(tmp_path / "manifest.json")
    payload = manifest.read_manifest(tmp_path / "manifest.json")
    poisoned = "credits # --- END REGISTRY --- and more"
    payload["assets"]["hsl_gtfs.zip"]["attribution"] = poisoned
    manifest.write_manifest(tmp_path / "manifest.json", payload["assets"])
    registry_copy = tmp_path / "_registry.py"
    registry_copy.write_text(registry.REGISTRY_PATH.read_text(encoding="utf-8"))
    registry.regenerate(tmp_path / "manifest.json", "helsinki-2026.08", registry_copy)
    # A second regeneration still finds exactly one block and succeeds.
    registry.regenerate(tmp_path / "manifest.json", "helsinki-2026.09", registry_copy)
    text = registry_copy.read_text(encoding="utf-8")
    assert text.count("helsinki-2026.09") > 0
    compile(text, str(registry_copy), "exec")  # still a valid module
    del assets


def test_the_shipped_registry_module_reimports():
    importlib.reload(_registry)
    # The shipped pins may lag the generator by one data release — an
    # asset added to ATTRIBUTES only reaches the registry when a release
    # produces it — but nothing may be pinned that the generator does
    # not know how to rewrite.
    assert set(_registry.ASSETS) <= set(registry.ATTRIBUTES)
