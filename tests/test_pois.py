"""The POI step: tag handling, point conversion, layers, and the pins."""

import hashlib
import json
import pathlib

import pytest

from pipeline import PipelineError, config, manifest, pois, registry, release, smoke

# --- tags -------------------------------------------------------------------


def test_tag_values_reads_what_pyrosm_hands_over():
    assert pois.tag_values('{"sport": "swimming"}') == {"sport": "swimming"}
    assert pois.tag_values({"sport": "swimming"}) == {"sport": "swimming"}
    # Absent, empty, unparseable, or not an object: no tags, no crash.
    assert pois.tag_values(None) == {}
    assert pois.tag_values("") == {}
    assert pois.tag_values("{not json") == {}
    assert pois.tag_values("[1, 2]") == {}


def test_has_sport_splits_the_semicolon_list():
    assert pois.has_sport({"sport": "swimming"}, "swimming")
    assert pois.has_sport({"sport": "water_polo;swimming"}, "swimming")
    assert pois.has_sport({"sport": "swimming;fitness"}, "swimming")
    # A different sport that merely contains the word is not a match —
    # this is why the value is split rather than searched.
    assert not pois.has_sport({"sport": "swimming_pool"}, "swimming")
    assert not pois.has_sport({"sport": "tennis"}, "swimming")
    assert not pois.has_sport({}, "swimming")
    assert not pois.has_sport({"sport": None}, "swimming")


# --- extraction -------------------------------------------------------------


def _frame(rows):
    """A pyrosm-shaped POI frame: promoted columns plus `tags` JSON."""
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Point, Polygon

    geometries, records = [], []
    for row in rows:
        east, north = row["at"]
        if row.get("areal"):
            geometries.append(
                Polygon(
                    [
                        (east, north),
                        (east + 0.002, north),
                        (east + 0.002, north + 0.001),
                        (east, north + 0.001),
                    ]
                )
            )
        else:
            geometries.append(Point(east, north))
        records.append(
            {
                "id": row["id"],
                "osm_type": row.get("osm_type", "way" if row.get("areal") else "node"),
                "name": row.get("name"),
                "tags": json.dumps(row.get("tags", {})),
            }
        )
    return geopandas.GeoDataFrame(records, geometry=geometries, crs="EPSG:4326")


class _Reader:
    """A stand-in for pyrosm's OSM reader."""

    def __init__(self, frame):
        self.frame = frame
        self.filters = []

    def get_pois(self, custom_filter=None):
        self.filters.append(custom_filter)
        return self.frame


def test_extract_reduces_areal_features_to_their_centroid():
    pytest.importorskip("geopandas")
    frame = _frame(
        [
            {"id": 1, "at": (24.94, 60.17), "name": "point library"},
            {"id": 2, "at": (24.95, 60.18), "name": "building library", "areal": True},
        ]
    )
    extracted = pois.extract_category(
        _Reader(frame), "library", {"tags": {"amenity": ["library"]}, "minimum": 1}
    )
    assert list(extracted.columns) == list(config.POI_COLUMNS) + ["geometry"]
    assert set(extracted.geometry.geom_type) == {"Point"}
    assert extracted.crs.to_epsg() == 4326
    assert extracted["category"].tolist() == ["library", "library"]
    assert extracted["osm_type"].tolist() == ["node", "way"]
    # The node keeps its exact position; the way lands inside its own
    # footprint.
    assert extracted.geometry.iloc[0].x == pytest.approx(24.94)
    assert extracted.geometry.iloc[1].x == pytest.approx(24.951, abs=1e-3)
    assert extracted.geometry.iloc[1].y == pytest.approx(60.1805, abs=1e-3)


def test_extract_keeps_only_the_requested_sport():
    pytest.importorskip("geopandas")
    frame = _frame(
        [
            {
                "id": 1,
                "at": (24.94, 60.17),
                "name": "hall",
                "tags": {"sport": "swimming"},
            },
            {
                "id": 2,
                "at": (24.95, 60.17),
                "name": "rink",
                "tags": {"sport": "ice_hockey"},
            },
            {
                "id": 3,
                "at": (24.96, 60.17),
                "name": "multi",
                "tags": {"sport": "fitness;swimming"},
            },
        ]
    )
    extracted = pois.extract_category(
        _Reader(frame),
        "swimming_hall",
        {"tags": {"leisure": ["sports_centre"]}, "sport": "swimming", "minimum": 1},
    )
    assert extracted["name"].tolist() == ["hall", "multi"]
    # The surviving tags ride along as JSON, sorted for a stable diff.
    assert json.loads(extracted["tags"].iloc[0]) == {"sport": "swimming"}


def test_extract_survives_a_category_without_a_name_column():
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Point

    # pyrosm's columns follow the tags a filter matches; `name` can be
    # missing entirely.
    frame = geopandas.GeoDataFrame(
        {"id": [1], "osm_type": ["node"], "tags": ["{}"]},
        geometry=[Point(24.94, 60.17)],
        crs="EPSG:4326",
    )
    extracted = pois.extract_category(
        _Reader(frame), "library", {"tags": {"amenity": ["library"]}, "minimum": 1}
    )
    assert extracted["name"].isna().all()


def test_extract_refuses_an_empty_category():
    geopandas = pytest.importorskip("geopandas")
    empty = geopandas.GeoDataFrame({"id": []}, geometry=[], crs="EPSG:4326")
    with pytest.raises(PipelineError, match="no 'library' features"):
        pois.extract_category(
            _Reader(empty), "library", {"tags": {"amenity": ["library"]}, "minimum": 1}
        )


def test_extract_refuses_null_geometries():
    geopandas = pytest.importorskip("geopandas")
    from shapely.geometry import Point

    frame = geopandas.GeoDataFrame(
        {"id": [1, 2], "osm_type": ["node", "node"], "tags": ["{}", "{}"]},
        geometry=[Point(24.94, 60.17), None],
        crs="EPSG:4326",
    )
    with pytest.raises(PipelineError, match="null or empty"):
        pois.extract_category(
            _Reader(frame), "library", {"tags": {"amenity": ["library"]}, "minimum": 1}
        )


# --- writing ----------------------------------------------------------------


def _extracted(rows):
    return pois.extract_category(
        _Reader(_frame(rows)),
        "library",
        {"tags": {"amenity": ["library"]}, "minimum": 1},
    )


def test_write_category_round_trips_to_geopackage(tmp_path):
    geopandas = pytest.importorskip("geopandas")
    frame = _extracted([{"id": 1, "at": (24.94, 60.17), "name": "Kirjasto"}])
    out = tmp_path / "pois.gpkg"
    pois.write_category(frame, out, "library", {"minimum": 1})
    written = geopandas.read_file(out, layer=config.POI_LAYER)
    assert written["name"].tolist() == ["Kirjasto"]
    assert written.crs.to_epsg() == 4326
    assert set(written.geometry.geom_type) == {"Point"}


def test_write_category_refuses_a_thin_layer(tmp_path):
    pytest.importorskip("geopandas")
    frame = _extracted([{"id": 1, "at": (24.94, 60.17), "name": "Kirjasto"}])
    with pytest.raises(PipelineError, match="expected at least"):
        pois.write_category(frame, tmp_path / "pois.gpkg", "library", {"minimum": 50})


def test_write_category_refuses_a_repeated_feature(tmp_path):
    pytest.importorskip("geopandas")
    import pandas

    frame = _extracted(
        [
            {"id": 1, "at": (24.94, 60.17), "name": "a"},
            {"id": 1, "at": (24.95, 60.17), "name": "a again"},
        ]
    )
    assert isinstance(frame, pandas.DataFrame)
    with pytest.raises(PipelineError, match="duplicated OSM ids"):
        pois.write_category(frame, tmp_path / "pois.gpkg", "library", {"minimum": 1})


def test_write_category_accepts_one_id_per_element_type(tmp_path):
    # OSM ids repeat across element types; identity is the pair.
    pytest.importorskip("geopandas")
    frame = _extracted(
        [
            {"id": 7, "at": (24.94, 60.17), "name": "node seven"},
            {"id": 7, "at": (24.95, 60.17), "name": "way seven", "areal": True},
        ]
    )
    written = pois.write_category(
        frame, tmp_path / "pois.gpkg", "library", {"minimum": 1}
    )
    assert written.exists()


# --- the step ---------------------------------------------------------------


@pytest.fixture()
def two_categories(monkeypatch):
    """A small category table, so a fake extract can satisfy it."""
    categories = {
        "library": {"tags": {"amenity": ["library"]}, "minimum": 1},
        "swimming_hall": {
            "tags": {"leisure": ["sports_centre"]},
            "sport": "swimming",
            "minimum": 1,
        },
    }
    monkeypatch.setattr(config, "POI_CATEGORIES", categories)
    monkeypatch.setattr(
        config,
        "POI_ASSETS",
        {category: config.poi_asset(category) for category in categories},
    )
    return categories


EXTRACT_BYTES = b"the extract"
_EXTRACT_DIGEST = hashlib.sha256(EXTRACT_BYTES).hexdigest()


def _produced_extract(tmp_path, payload=EXTRACT_BYTES):
    """A work dir as the OSM step leaves it: the extract and its manifest."""
    work = tmp_path / "work"
    work.mkdir()
    (work / config.OSM_ASSET).write_bytes(payload)
    record = manifest.asset_record(
        work / config.OSM_ASSET,
        license=config.OSM_LICENSE,
        attribution=config.OSM_ATTRIBUTION,
        source_stamp="Geofabrik finland-260809",
    )
    manifest.write_manifest(work / "manifest-osm.json", {config.OSM_ASSET: record})
    return work


def test_build_publishes_every_category(tmp_path, monkeypatch, two_categories):
    pytest.importorskip("geopandas")
    import sys
    import types

    frame = _frame(
        [
            {"id": 1, "at": (24.94, 60.17), "name": "a", "tags": {"sport": "swimming"}},
            {"id": 2, "at": (24.95, 60.17), "name": "b", "areal": True},
        ]
    )
    module = types.ModuleType("pyrosm")
    module.OSM = lambda path: _Reader(frame)
    monkeypatch.setitem(sys.modules, "pyrosm", module)

    work = _produced_extract(tmp_path)
    records = pois.build(work)

    assert set(records) == set(config.POI_ASSETS.values())
    published = sorted(path.name for path in work.iterdir())
    # The extract the step read stays; nothing else is left behind.
    assert published == sorted(
        list(config.POI_ASSETS.values())
        + ["manifest-pois.json", "manifest-osm.json", config.OSM_ASSET]
    )
    for record in records.values():
        assert record["license"] == "ODbL 1.0"
        assert "OpenStreetMap" in record["attribution"]
        # The stamp names the very bytes the points were read from.
        assert config.OSM_ASSET in record["source_stamp"]
        assert f"sha256={_EXTRACT_DIGEST}" in record["source_stamp"]
    # The step verifies against its own manifest.
    assert smoke.verify_manifests(work, names={"manifest-pois.json": tuple(records)})


def test_build_without_an_extract_says_so(tmp_path, two_categories):
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(PipelineError, match="run the OSM step before"):
        pois.build(work)


def test_build_without_the_osm_manifest_says_so(tmp_path, two_categories):
    work = tmp_path / "work"
    work.mkdir()
    (work / config.OSM_ASSET).write_bytes(EXTRACT_BYTES)
    with pytest.raises(PipelineError, match="run the OSM step before"):
        pois.build(work)


def test_build_refuses_an_extract_that_lost_its_provenance(
    tmp_path, monkeypatch, two_categories
):
    work = _produced_extract(tmp_path)
    # The published extract and the manifest that measured it disagree.
    (work / config.OSM_ASSET).write_bytes(b"some other extract")
    with pytest.raises(PipelineError, match="does not match manifest-osm.json"):
        pois.build(work)


def test_build_refuses_an_extract_that_is_a_symlink(tmp_path, two_categories):
    """A symlinked extract is refused rather than copied as a link —
    the private copy must be bytes, not a pointer at bytes somebody
    else can still change."""
    work = _produced_extract(tmp_path)
    elsewhere = tmp_path / "elsewhere.osm.pbf"
    elsewhere.write_bytes(EXTRACT_BYTES)
    extract = work / config.OSM_ASSET
    extract.unlink()
    extract.symlink_to(elsewhere)
    with pytest.raises(PipelineError, match="cannot read the OSM extract"):
        pois.build(work)


def _fake_pyrosm(monkeypatch, reader_class, frame):
    """Install a stub pyrosm; returns the paths its reader was given."""
    import sys
    import types

    opened = []

    def _open(path):
        opened.append(path)
        return reader_class(frame, path)

    module = types.ModuleType("pyrosm")
    module.OSM = _open
    monkeypatch.setitem(sys.modules, "pyrosm", module)
    return opened


class _PathReader(_Reader):
    """A reader that remembers which file it was pointed at."""

    def __init__(self, frame, path):
        super().__init__(frame)
        self.path = pathlib.Path(path)


def test_build_reads_a_private_copy_not_the_work_tree_extract(
    tmp_path, monkeypatch, two_categories
):
    """Swap the work-tree extract and put it back — the ABA a digest
    taken before and after the reads cannot see. The POIs come from a
    verified private copy, so the swap reaches nothing."""
    pytest.importorskip("geopandas")
    work = _produced_extract(tmp_path)
    extract = work / config.OSM_ASSET

    class _AbaReader(_PathReader):
        def get_pois(self, custom_filter=None):
            extract.write_bytes(b"a different extract")
            try:
                return super().get_pois(custom_filter=custom_filter)
            finally:
                extract.write_bytes(EXTRACT_BYTES)  # ...and restored

    frame = _frame(
        [{"id": 1, "at": (24.94, 60.17), "name": "a", "tags": {"sport": "swimming"}}]
    )
    opened = _fake_pyrosm(monkeypatch, _AbaReader, frame)
    records = pois.build(work)

    assert opened and all(pathlib.Path(path) != extract for path in opened)
    for record in records.values():
        assert f"sha256={_EXTRACT_DIGEST}" in record["source_stamp"]


def test_build_refuses_a_copy_changed_under_the_reader(
    tmp_path, monkeypatch, two_categories
):
    pytest.importorskip("geopandas")
    work = _produced_extract(tmp_path)

    class _SelfSwappingReader(_PathReader):
        def get_pois(self, custom_filter=None):
            self.path.write_bytes(b"a different extract")
            return super().get_pois(custom_filter=custom_filter)

    frame = _frame(
        [{"id": 1, "at": (24.94, 60.17), "name": "a", "tags": {"sport": "swimming"}}]
    )
    _fake_pyrosm(monkeypatch, _SelfSwappingReader, frame)
    with pytest.raises(PipelineError, match="no trustworthy provenance"):
        pois.build(work)
    # Nothing was published from bytes whose provenance cannot be told.
    assert not (work / "manifest-pois.json").exists()


def test_the_smoke_refuses_layers_from_another_extract(tmp_path, two_categories):
    """The layers and the street network must be the same OSM bytes."""
    pytest.importorskip("geopandas")
    rows = [{"id": 1, "at": (24.9384, 60.1699), "name": "Keskusta"}]
    assets, records = {}, {config.OSM_ASSET: {"sha256": "a" * 64}}
    for category, asset in config.POI_ASSETS.items():
        frame = pois.extract_category(
            _Reader(_frame(rows)), category, {"tags": {}, "minimum": 1}
        )
        path = tmp_path / asset
        pois.write_category(frame, path, category, {"minimum": 1})
        assets[asset] = path
        records[asset] = {"source_stamp": f"extracted from x sha256={'b' * 64}"}

    class _Streets:
        def travel_time(self, origin, destination, *, mode):
            return 600.0

    with pytest.raises(PipelineError, match="not extracted from this release"):
        smoke.check_pois(assets, records, _Streets())


# --- coherence --------------------------------------------------------------


def test_every_category_is_pinned_and_attributed():
    from cafein.sampledata.helsinki import pois as client

    assert set(client.CATEGORIES) == set(config.POI_CATEGORIES)
    for category, asset in config.POI_ASSETS.items():
        assert registry.ATTRIBUTES[f"poi_{category}"] == asset
        # CC BY / ODbL: every published layer states how it was derived.
        assert asset in release.MODIFICATIONS
    # The smoke consumes exactly the assets the step publishes.
    assert set(config.POI_ASSETS.values()) <= smoke.expected_assets()
