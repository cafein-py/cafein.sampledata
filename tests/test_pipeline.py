"""The pipeline's OSM/GTFS steps: manifest, downloads, and the gate."""

import functools
import hashlib
import http.server
import json
import pathlib
import sys
import threading
import types
import zipfile

import pytest

from pipeline import PipelineError, config, download, gtfs, manifest, osm

PAYLOAD = b"pipeline source bytes\n" * 64


@pytest.fixture()
def server(tmp_path):
    """A local HTTP server over a temp directory; yields (directory, url)."""
    root = tmp_path / "served"
    root.mkdir()
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(root)
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _gtfs_zip(path, feed_info_rows=None):
    """A minimal GTFS zip carrying only feed_info.txt (or nothing)."""
    with zipfile.ZipFile(path, "w") as archive:
        if feed_info_rows is not None:
            head = "feed_publisher_name,feed_version\n"
            body = "".join(f"{p},{v}\n" for p, v in feed_info_rows)
            archive.writestr("feed_info.txt", head + body)
        archive.writestr("agency.txt", "agency_id,agency_name\nHSL,HSL\n")
    return path


# --- manifest ---------------------------------------------------------------


def test_asset_record_hashes_and_measures(tmp_path):
    path = tmp_path / "asset.bin"
    path.write_bytes(PAYLOAD)
    record = manifest.asset_record(
        path, license="ODbL 1.0", attribution="© OSM", source_stamp="stamp"
    )
    assert record["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
    assert record["size"] == len(PAYLOAD)
    assert record["file"] == "asset.bin"
    assert record["license"] == "ODbL 1.0"


def test_manifest_round_trips_sorted(tmp_path):
    path = tmp_path / "manifest.json"
    written = manifest.write_manifest(
        path, {"b.bin": {"sha256": "y"}, "a.bin": {"sha256": "x"}}
    )
    assert list(written["assets"]) == ["a.bin", "b.bin"]
    assert manifest.read_manifest(path) == written


def test_manifest_refuses_unknown_schema(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema": 99, "assets": {}}))
    with pytest.raises(ValueError, match="schema"):
        manifest.read_manifest(path)


# --- downloads --------------------------------------------------------------


def test_stream_download_records_what_it_fetched(server, tmp_path):
    root, url = server
    (root / "source.bin").write_bytes(PAYLOAD)
    destination = tmp_path / "out" / "source.bin"
    record = download.stream_download(f"{url}/source.bin", destination)
    assert destination.read_bytes() == PAYLOAD
    assert record["sha256"] == hashlib.sha256(PAYLOAD).hexdigest()
    assert record["size"] == len(PAYLOAD)
    assert record["fetched_at"]
    assert record["last_modified"]  # SimpleHTTPRequestHandler sends it


def test_stream_download_failure_leaves_no_temporaries(tmp_path, monkeypatch):
    import urllib.error
    import urllib.request

    def refuse(url, timeout=None):
        raise urllib.error.URLError("deterministically refused")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    destination = tmp_path / "out" / "gone.bin"
    with pytest.raises(PipelineError, match="could not download"):
        download.stream_download("http://example.invalid/gone.bin", destination)
    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def test_a_truncated_declared_length_download_is_rejected(tmp_path):
    class Truncating(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler naming
            self.send_response(200)
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD[:16])  # far short of the header

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Truncating)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    destination = tmp_path / "out" / "cut.bin"
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(PipelineError, match="truncated"):
            download.stream_download(f"{url}/cut.bin", destination)
    finally:
        httpd.shutdown()
    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


# --- the GTFS gate ----------------------------------------------------------


def test_partition_blocks_errors_and_incomplete_files():
    report = {
        "notices": [
            {"code": "missing_required_file", "severity": "ERROR", "context": {}},
            {"code": "fast_travel", "severity": "WARNING", "context": {}},
            {"code": "note", "severity": "INFO", "context": {}},
        ],
        "incomplete": ["stop_times.txt"],
    }
    blocking, warnings = gtfs.partition(report)
    assert [n["code"] for n in blocking] == [
        "missing_required_file",
        "incomplete_file",
    ]
    assert [n["code"] for n in warnings] == ["fast_travel"]


def test_partition_passes_a_clean_report():
    blocking, warnings = gtfs.partition({"notices": [], "incomplete": []})
    assert blocking == [] and warnings == []


@pytest.fixture()
def fake_transitio(monkeypatch):
    """Injects a fake `transitio` module; the test sets `.report`."""
    module = types.ModuleType("transitio")
    module.report = {"notices": [], "incomplete": []}
    module.validate_feed = lambda path, reference_date=None: module.report
    monkeypatch.setitem(sys.modules, "transitio", module)
    return module


def test_the_reference_date_normalizes_to_the_gtfs_form():
    assert gtfs.normalize_reference_date("2026-08-11") == "20260811"
    assert gtfs.normalize_reference_date("20260811") == "20260811"
    assert gtfs.normalize_reference_date(None) is None


@pytest.mark.parametrize("bad", ["2026-13-01", "11-08-2026", "notadate", "260811"])
def test_a_malformed_reference_date_is_refused(bad):
    with pytest.raises(PipelineError, match="invalid reference date"):
        gtfs.normalize_reference_date(bad)


def test_validate_writes_the_report_and_passes_clean(tmp_path, fake_transitio):
    fake_transitio.report = {
        "notices": [{"code": "fast_travel", "severity": "WARNING", "context": {}}],
        "incomplete": [],
    }
    report_path = tmp_path / "report.json"
    warnings = gtfs.validate(tmp_path / "feed.zip", report_path)
    assert [n["code"] for n in warnings] == ["fast_travel"]
    assert json.loads(report_path.read_text()) == fake_transitio.report


def test_validate_raises_on_blocking_notices(tmp_path, fake_transitio):
    fake_transitio.report = {
        "notices": [
            {"code": "missing_required_file", "severity": "ERROR", "context": {}}
        ],
        "incomplete": [],
    }
    report_path = tmp_path / "report.json"
    with pytest.raises(PipelineError, match="missing_required_file"):
        gtfs.validate(tmp_path / "feed.zip", report_path)
    # The full report is still written for inspection.
    assert report_path.exists()


def test_validate_without_transitio_names_the_dependency(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "transitio", None)
    with pytest.raises(PipelineError, match="transitio"):
        gtfs.validate(tmp_path / "feed.zip", tmp_path / "report.json")


# --- feed_info --------------------------------------------------------------


def test_feed_info_reads_the_first_row(tmp_path):
    feed = _gtfs_zip(tmp_path / "feed.zip", [("HSL", "2026-08-09")])
    info = gtfs.feed_info(feed)
    assert info["feed_publisher_name"] == "HSL"
    assert info["feed_version"] == "2026-08-09"


def test_feed_info_tolerates_a_missing_file(tmp_path):
    feed = _gtfs_zip(tmp_path / "feed.zip", feed_info_rows=None)
    assert gtfs.feed_info(feed) == {}


# --- step orchestration -----------------------------------------------------


def test_gtfs_build_composes_the_stamp(server, tmp_path, fake_transitio):
    root, url = server
    _gtfs_zip(root / "hsl.zip", [("HSL", "2026-08-09")])
    records = gtfs.build(tmp_path, url=f"{url}/hsl.zip")
    record = records[config.GTFS_ASSET]
    assert record["file"] == config.GTFS_ASSET
    assert "feed_version=2026-08-09" in record["source_stamp"]
    assert record["license"] == config.GTFS_LICENSE
    assert (tmp_path / "gtfs-validation-report.json").exists()
    assert (tmp_path / config.GTFS_ASSET).exists()  # published under its name


def test_gtfs_build_fails_before_recording_a_bad_feed(server, tmp_path, fake_transitio):
    root, url = server
    _gtfs_zip(root / "hsl.zip", [("HSL", "v1")])
    fake_transitio.report = {
        "notices": [{"code": "broken", "severity": "ERROR", "context": {}}],
        "incomplete": [],
    }
    with pytest.raises(PipelineError, match="broken"):
        gtfs.build(tmp_path, url=f"{url}/hsl.zip")


def test_gtfs_build_refuses_a_feed_swapped_during_validation(
    server, tmp_path, fake_transitio
):
    # The fake validator swaps the feed file mid-validation: the stream
    # hash and the manifest re-hash disagree, so nothing is pinned.
    root, url = server
    _gtfs_zip(root / "hsl.zip", [("HSL", "v1")])
    swapped = _gtfs_zip(tmp_path / "swapped.zip", [("HSL", "v2")])

    def swapping_validate(path, reference_date=None):
        pathlib.Path(path).write_bytes(swapped.read_bytes())
        return {"notices": [], "incomplete": []}

    fake_transitio.validate_feed = swapping_validate
    with pytest.raises(PipelineError, match="changed between download"):
        gtfs.build(tmp_path, url=f"{url}/hsl.zip")


def test_osm_build_refuses_a_source_swapped_during_the_clip(
    server, tmp_path, monkeypatch
):
    root, url = server
    (root / "finland-latest.osm.pbf").write_bytes(PAYLOAD)

    def swapping_clip(source, out, bbox=config.CAPITAL_REGION_BBOX, engine="in_memory"):
        out.write_bytes(b"clipped")
        source.write_bytes(b"replaced mid-clip")
        return out

    monkeypatch.setattr(osm, "clip_capital_region", swapping_clip)
    with pytest.raises(PipelineError, match="changed between download"):
        osm.build(tmp_path, url=f"{url}/finland-latest.osm.pbf")
    # Nothing was published under the asset's public name.
    assert not (tmp_path / config.OSM_ASSET).exists()


def test_osm_build_composes_the_stamp(server, tmp_path, monkeypatch):
    root, url = server
    (root / "finland-latest.osm.pbf").write_bytes(PAYLOAD)

    def fake_clip(source, out, bbox=config.CAPITAL_REGION_BBOX, engine="in_memory"):
        out.write_bytes(b"clipped")
        return out

    monkeypatch.setattr(osm, "clip_capital_region", fake_clip)
    records = osm.build(tmp_path, url=f"{url}/finland-latest.osm.pbf")
    record = records[config.OSM_ASSET]
    assert record["file"] == config.OSM_ASSET
    assert record["source_stamp"].startswith("Geofabrik finland-latest ")
    assert record["sha256"] == hashlib.sha256(b"clipped").hexdigest()
    assert (tmp_path / config.OSM_ASSET).read_bytes() == b"clipped"


def test_clip_without_pyrosm_names_the_dependency(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pyrosm", None)
    with pytest.raises(PipelineError, match="pyrosm"):
        osm.clip_capital_region(tmp_path / "in.pbf", tmp_path / "out.pbf")


def test_the_bbox_is_sane():
    west, south, east, north = config.CAPITAL_REGION_BBOX
    assert -180 <= west < east <= 180
    assert -90 <= south < north <= 90
    # Helsinki is inside it.
    assert west < 24.94 < east and south < 60.17 < north


def test_clip_rejects_a_malformed_bbox(tmp_path):
    with pytest.raises(PipelineError, match="bounding box"):
        osm.clip_capital_region(
            tmp_path / "in.pbf", tmp_path / "out.pbf", bbox=(25.0, 60.0, 24.0, 61.0)
        )


def test_feed_info_refuses_an_implausibly_large_member(tmp_path):
    with zipfile.ZipFile(tmp_path / "feed.zip", "w") as archive:
        archive.writestr("feed_info.txt", "a" * (gtfs.FEED_INFO_LIMIT + 1))
    with pytest.raises(PipelineError, match="implausible"):
        gtfs.feed_info(tmp_path / "feed.zip")


@pytest.mark.parametrize(
    "report",
    [
        {},
        {"notices": None, "incomplete": []},
        {"notices": [], "incomplete": None},
        {"notices": [{"severity": "FATAL"}], "incomplete": []},
        {"notices": [None], "incomplete": []},
    ],
)
def test_partition_refuses_a_report_off_contract(report):
    with pytest.raises(PipelineError, match="contract"):
        gtfs.partition(report)


def test_a_failed_gtfs_build_leaves_no_staging_files(server, tmp_path, fake_transitio):
    root, url = server
    _gtfs_zip(root / "hsl.zip", [("HSL", "v1")])
    fake_transitio.report = {
        "notices": [{"code": "broken", "severity": "ERROR", "context": {}}],
        "incomplete": [],
    }
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(PipelineError, match="preserved at"):
        gtfs.build(work, url=f"{url}/hsl.zip")
    # Only the failure-named diagnostic report survives a failed run.
    names = [p.name for p in work.iterdir()]
    assert names == ["gtfs-validation-report.failed.json"]


def test_a_successful_osm_build_removes_the_country_source(
    server, tmp_path, monkeypatch
):
    root, url = server
    (root / "finland-latest.osm.pbf").write_bytes(PAYLOAD)

    def fake_clip(source, out, bbox=config.CAPITAL_REGION_BBOX, engine="in_memory"):
        out.write_bytes(b"clipped")
        return out

    monkeypatch.setattr(osm, "clip_capital_region", fake_clip)
    work = tmp_path / "work"
    work.mkdir()
    osm.build(work, url=f"{url}/finland-latest.osm.pbf")
    names = sorted(p.name for p in work.iterdir())
    assert names == [config.OSM_ASSET, "manifest-osm.json"]


def test_the_work_directory_is_created_on_demand(tmp_path):
    from pipeline import workdir_lock

    work = tmp_path / "not" / "yet" / "created"
    with workdir_lock(work):
        assert work.is_dir()
        assert (work / ".pipeline-lock").exists()
    assert not (work / ".pipeline-lock").exists()


def test_a_second_build_in_the_same_work_dir_is_refused(server, tmp_path):
    _, url = server
    (tmp_path / ".pipeline-lock").touch()  # another run holds the directory
    with pytest.raises(PipelineError, match="already in use"):
        gtfs.build(tmp_path, url=f"{url}/hsl.zip")
    with pytest.raises(PipelineError, match="already in use"):
        osm.build(tmp_path, url=f"{url}/finland-latest.osm.pbf")


def test_finland_url_selects_the_dated_snapshot():
    assert "finland-260801.osm.pbf" in osm.finland_url("260801")
    assert osm.finland_url(None) == config.GEOFABRIK_FINLAND_URL
    with pytest.raises(PipelineError, match="YYMMDD"):
        osm.finland_url("2026-08-01")


def test_a_failed_rerun_preserves_the_previous_generation(
    server, tmp_path, fake_transitio
):
    # First run succeeds; the rerun fails at validation. The previous
    # asset/manifest pair must survive intact and coherent.
    root, url = server
    _gtfs_zip(root / "hsl.zip", [("HSL", "v1")])
    work = tmp_path / "work"
    work.mkdir()
    gtfs.build(work, url=f"{url}/hsl.zip")
    good_asset = (work / config.GTFS_ASSET).read_bytes()
    good_manifest = (work / "manifest-gtfs.json").read_text()
    fake_transitio.report = {
        "notices": [{"code": "broken", "severity": "ERROR", "context": {}}],
        "incomplete": [],
    }
    with pytest.raises(PipelineError):
        gtfs.build(work, url=f"{url}/hsl.zip")
    assert (work / config.GTFS_ASSET).read_bytes() == good_asset
    assert (work / "manifest-gtfs.json").read_text() == good_manifest
    # The good generation's report is untouched; the rejected feed's
    # diagnostics live under the failure name.
    assert (work / "gtfs-validation-report.json").exists()
    assert (work / "gtfs-validation-report.failed.json").exists()


def test_oversized_sources_are_rejected(server, tmp_path):
    root, url = server
    (root / "big.bin").write_bytes(PAYLOAD)
    destination = tmp_path / "out" / "big.bin"
    # Declared over the cap: refused before the body is read.
    with pytest.raises(PipelineError, match="source cap"):
        download.stream_download(f"{url}/big.bin", destination, max_bytes=16)
    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def test_a_chunked_stream_past_the_cap_is_aborted(tmp_path):
    import http.server as hs
    import threading

    class Chunked(hs.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 - stdlib handler naming
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            body = PAYLOAD  # no Content-Length: the stream cap must act
            self.wfile.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")

        def log_message(self, *args):
            pass

    httpd = hs.ThreadingHTTPServer(("127.0.0.1", 0), Chunked)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    destination = tmp_path / "out" / "chunked.bin"
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(PipelineError, match="source cap"):
            download.stream_download(f"{url}/chunked.bin", destination, max_bytes=16)
    finally:
        httpd.shutdown()
    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def test_a_dated_snapshot_is_stamped_as_itself(server, tmp_path, monkeypatch):
    root, url = server
    (root / "finland-latest.osm.pbf").write_bytes(PAYLOAD)

    def fake_clip(source, out, bbox=config.CAPITAL_REGION_BBOX, engine="in_memory"):
        out.write_bytes(b"clipped")
        return out

    monkeypatch.setattr(osm, "clip_capital_region", fake_clip)
    records = osm.build(
        tmp_path, url=f"{url}/finland-latest.osm.pbf", snapshot_date="260801"
    )
    stamp = records[config.OSM_ASSET]["source_stamp"]
    assert "finland-260801" in stamp
    assert "finland-latest" not in stamp
