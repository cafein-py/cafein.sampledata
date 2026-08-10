"""The download client: cache, checksums, atomicity, failure modes."""

import functools
import hashlib
import http.server
import threading

import pytest

from cafein.sampledata import Asset, ChecksumMismatch, DownloadFailed, cache_root, fetch

PAYLOAD = b"cafein sample bytes\n" * 64


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    """An isolated cache directory for every test."""
    directory = tmp_path / "cache"
    monkeypatch.setenv("CAFEIN_SAMPLEDATA_DIR", str(directory))
    return directory


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


def _asset(url, payload=PAYLOAD, **overrides):
    fields = {
        "name": "sample.bin",
        "url": f"{url}/sample.bin",
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "release": "helsinki-2026.08",
    }
    fields.update(overrides)
    return Asset(**fields)


def _target(cache, asset, region="helsinki"):
    """Where the client caches `asset`: region/release/sha256/name."""
    return cache / region / asset.release / asset.sha256 / asset.name


def _stamp(target):
    """The verification stamp beside a cached file."""
    return target.parent / ".stamps" / target.name


def test_cache_root_prefers_the_environment_override(cache):
    assert cache_root() == cache


def test_fetch_downloads_verifies_and_caches(server, cache):
    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    asset = _asset(url)
    path = fetch(asset, "helsinki")
    assert path == _target(cache, asset)
    assert path.read_bytes() == PAYLOAD
    # The verification stamp marks the copy as already checked.
    assert _stamp(path).read_text() == hashlib.sha256(PAYLOAD).hexdigest()


def test_a_cached_file_is_served_without_the_network(server, cache):
    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    asset = _asset(url)
    first = fetch(asset, "helsinki")
    (root / "sample.bin").unlink()  # the server can no longer provide it
    assert fetch(asset, "helsinki") == first


def test_release_tags_keep_separate_copies(server, cache):
    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    old = fetch(_asset(url), "helsinki")
    new = fetch(_asset(url, release="helsinki-2026.11"), "helsinki")
    assert old != new
    assert old.exists() and new.exists()


def test_a_corrupt_download_is_discarded_and_raises(server, cache):
    root, url = server
    (root / "sample.bin").write_bytes(b"not the pinned bytes")
    asset = _asset(url)
    with pytest.raises(ChecksumMismatch, match="expected"):
        fetch(asset, "helsinki")
    target = _target(cache, asset)
    assert not target.exists()
    assert [p for p in target.parent.iterdir() if ".part." in p.name] == []


def test_an_unreachable_asset_names_the_manual_path(cache):
    asset = _asset("http://127.0.0.1:9", name="gone.bin")
    with pytest.raises(DownloadFailed, match="place it at"):
        fetch(asset, "helsinki")


def test_forced_verification_catches_silent_corruption(server, cache, monkeypatch):
    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    asset = _asset(url)
    path = fetch(asset, "helsinki")
    path.write_bytes(b"tampered")
    # Stamp present: the fast path trusts the file...
    assert fetch(asset, "helsinki") == path
    # ...but forced verification re-hashes, discards, and raises.
    monkeypatch.setenv("CAFEIN_SAMPLEDATA_VERIFY", "1")
    with pytest.raises(ChecksumMismatch, match="discarded"):
        fetch(asset, "helsinki")
    assert not path.exists()
    # The next fetch downloads a clean copy again.
    monkeypatch.delenv("CAFEIN_SAMPLEDATA_VERIFY")
    assert fetch(asset, "helsinki").read_bytes() == PAYLOAD


def test_an_unstamped_cached_file_is_rehashed_and_stamped(server, cache):
    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    asset = _asset(url)
    path = fetch(asset, "helsinki")
    _stamp(path).unlink()  # e.g. killed mid-stamp
    assert fetch(asset, "helsinki") == path
    assert _stamp(path).exists()


@pytest.mark.parametrize(
    "field, value",
    [
        ("region", ".."),
        ("region", "a/b"),
        ("name", "../evil.bin"),
        ("name", "/etc/evil.bin"),
        ("name", "C:\\evil.bin"),
        ("name", ""),
        ("name", ".stamps"),
        ("release", "../../elsewhere"),
    ],
)
def test_unsafe_path_components_are_rejected(server, cache, field, value):
    from cafein.sampledata import SampleDataError

    _, url = server
    region = value if field == "region" else "helsinki"
    overrides = {field: value} if field in ("name", "release") else {}
    with pytest.raises(SampleDataError, match="unsafe"):
        fetch(_asset(url, **overrides), region)
    # Nothing was written anywhere — the cache directory was never created.
    assert not cache.exists()


def test_concurrent_fetches_agree_and_leave_no_temporaries(server, cache):
    import concurrent.futures

    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    asset = _asset(url)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _: fetch(asset, "helsinki"), range(8)))
    assert len(set(paths)) == 1
    assert paths[0].read_bytes() == PAYLOAD
    leftovers = [p for p in paths[0].parent.iterdir() if ".part." in p.name]
    assert leftovers == []


def test_an_orphaned_stamp_never_exempts_a_manually_placed_file(cache):
    # A failed download leaves no stamp behind; a file the user then
    # places manually is hashed like any other, so a wrong file raises.
    asset = _asset("http://127.0.0.1:9")
    target = _target(cache, asset)
    stamp = _stamp(target)
    stamp.parent.mkdir(parents=True)
    stamp.write_text(asset.sha256)  # orphan: a stamp with no file
    with pytest.raises(DownloadFailed):
        fetch(asset, "helsinki")
    assert not stamp.exists()
    target.write_bytes(b"manually placed wrong bytes")
    with pytest.raises(ChecksumMismatch):
        fetch(asset, "helsinki")


def test_a_planted_stamp_symlink_out_of_root_is_refused(server, cache):
    # A stamp symlink pointing outside the cache trips the containment
    # check before any read, write, or unlink touches its destination.
    from cafein.sampledata import SampleDataError

    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    asset = _asset(url)
    victim = cache.parent / "victim.txt"
    victim.write_text("untouched")
    target = _target(cache, asset)
    stamp = _stamp(target)
    stamp.parent.mkdir(parents=True)
    stamp.symlink_to(victim)
    with pytest.raises(SampleDataError, match="escapes the cache root"):
        fetch(asset, "helsinki")
    assert victim.read_text() == "untouched"


def test_a_planted_stamp_symlink_inside_the_cache_is_replaced(server, cache):
    # Pointing at another cache file instead: the fetch proceeds, and
    # the stamp is republished as a regular file — never written
    # through the link.
    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    asset = _asset(url)
    victim = cache / "victim.txt"
    victim.parent.mkdir(parents=True)
    victim.write_text("untouched")
    target = _target(cache, asset)
    stamp = _stamp(target)
    stamp.parent.mkdir(parents=True)
    stamp.symlink_to(victim)
    path = fetch(asset, "helsinki")
    assert path.read_bytes() == PAYLOAD
    assert victim.read_text() == "untouched"
    assert not stamp.is_symlink()


def test_a_release_tag_is_required(server, cache):
    from cafein.sampledata import SampleDataError

    _, url = server
    with pytest.raises(SampleDataError, match="release"):
        fetch(_asset(url, release=""), "helsinki")
    assert not cache.exists()


@pytest.mark.parametrize(
    "sha256",
    ["", "abc123", "../" * 21 + "x", "A" * 64, "g" * 64],
)
def test_a_malformed_pin_is_rejected(server, cache, sha256):
    from cafein.sampledata import SampleDataError

    _, url = server
    with pytest.raises(SampleDataError, match="sha256"):
        fetch(_asset(url, sha256=sha256), "helsinki")
    assert not cache.exists()


@pytest.mark.parametrize("size", [0, -1, "512", 3.5])
def test_a_missing_or_malformed_size_is_rejected(server, cache, size):
    from cafein.sampledata import SampleDataError

    _, url = server
    with pytest.raises(SampleDataError, match="size"):
        fetch(_asset(url, size=size), "helsinki")
    assert not cache.exists()


def test_a_stream_beyond_the_pinned_size_is_aborted(server, cache):
    # The server has more bytes than the pin admits: the download stops
    # at the cap instead of filling the disk, and nothing is cached.
    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD + b"and then a whole lot more")
    asset = _asset(url)  # pins len(PAYLOAD)
    with pytest.raises(ChecksumMismatch, match="exceeded the pinned size"):
        fetch(asset, "helsinki")
    target = _target(cache, asset)
    assert not target.exists()
    assert [p for p in target.parent.iterdir() if ".part." in p.name] == []


def test_identical_content_assets_never_collide_with_a_stamp(server, cache):
    # `foo` and `foo.sha256` with identical bytes share a digest
    # directory; the dot-directory stamp namespace keeps either asset's
    # stamp from overwriting the other asset's data.
    import dataclasses

    root, url = server
    (root / "foo").write_bytes(PAYLOAD)
    (root / "foo.sha256").write_bytes(PAYLOAD)
    plain = dataclasses.replace(_asset(url), name="foo", url=f"{url}/foo")
    shadowing = dataclasses.replace(plain, name="foo.sha256", url=f"{url}/foo.sha256")
    first = fetch(plain, "helsinki")
    second = fetch(shadowing, "helsinki")
    assert first.parent == second.parent  # same digest directory
    assert first.read_bytes() == PAYLOAD
    assert second.read_bytes() == PAYLOAD
    # Re-fetching both still verifies: neither stamp clobbered any data.
    assert fetch(plain, "helsinki") == first
    assert fetch(shadowing, "helsinki") == second


def test_a_changed_pin_never_serves_the_stale_stamped_file(server, cache):
    # Same name and release, new pin: the digest in the cache path keeps
    # the two copies disjoint, so the new pin fetches fresh bytes and the
    # old copy stays untouched.
    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    old = fetch(_asset(url), "helsinki")
    assert old.read_bytes() == PAYLOAD
    replacement = b"the repinned bytes\n" * 64
    (root / "sample.bin").write_bytes(replacement)
    new = fetch(_asset(url, payload=replacement), "helsinki")
    assert new != old
    assert new.read_bytes() == replacement
    assert old.read_bytes() == PAYLOAD


def test_concurrent_different_pins_never_cross_contaminate(server, cache):
    # Two pins for the same name and release, fetched concurrently: the
    # digest-disjoint paths mean each caller gets exactly its own bytes.
    import concurrent.futures
    import dataclasses

    root, url = server
    other_payload = b"a second pinned file\n" * 64
    (root / "sample.bin").write_bytes(PAYLOAD)
    (root / "other.bin").write_bytes(other_payload)
    first = _asset(url)
    second = dataclasses.replace(
        _asset(url, payload=other_payload), url=f"{url}/other.bin"
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(fetch, [first, second][index % 2], "helsinki")
            for index in range(8)
        ]
        paths = [future.result() for future in futures]
    assert _target(cache, first).read_bytes() == PAYLOAD
    assert _target(cache, second).read_bytes() == other_payload
    assert set(paths) == {_target(cache, first), _target(cache, second)}


def test_a_stalling_server_fails_instead_of_hanging(cache, monkeypatch):
    import http.server
    import threading
    import time

    class Stalling(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler naming
            time.sleep(5)  # far beyond the configured timeout

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stalling)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("CAFEIN_SAMPLEDATA_TIMEOUT", "0.3")
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        with pytest.raises(DownloadFailed, match="could not download"):
            fetch(_asset(url), "helsinki")
    finally:
        httpd.shutdown()


def test_a_truncated_response_fails_cleanly_without_temporaries(cache):
    import http.server
    import threading

    class Truncating(http.server.BaseHTTPRequestHandler):
        # A chunked body that dies mid-chunk raises IncompleteRead in the
        # client — the HTTPException path, not a plain short read.
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802 - stdlib handler naming
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(b"400\r\n")  # promise 0x400 bytes...
            self.wfile.write(PAYLOAD[:16])  # ...deliver 16, then hang up
            self.wfile.flush()
            self.connection.close()

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Truncating)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}"
        asset = _asset(url)
        with pytest.raises(DownloadFailed, match="could not download"):
            fetch(asset, "helsinki")
    finally:
        httpd.shutdown()
    target = _target(cache, asset)
    assert not target.exists()
    assert [p for p in target.parent.iterdir() if ".part." in p.name] == []


def test_a_symlinked_stamp_directory_is_refused_before_any_mutation(server, cache):
    # `.stamps` itself pointing outside the cache: the containment check
    # refuses the fetch before the orphan-stamp cleanup could delete a
    # file at the symlink's destination.
    from cafein.sampledata import SampleDataError

    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    asset = _asset(url)
    target = _target(cache, asset)
    outside = cache.parent / "outside"
    outside.mkdir()
    (outside / asset.name).write_text("precious")
    target.parent.mkdir(parents=True)
    (target.parent / ".stamps").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SampleDataError, match="escapes the cache root"):
        fetch(asset, "helsinki")
    assert (outside / asset.name).read_text() == "precious"


def test_an_invalid_timeout_fails_early_and_leaves_nothing(cache, monkeypatch):
    from cafein.sampledata import SampleDataError

    monkeypatch.setenv("CAFEIN_SAMPLEDATA_TIMEOUT", "banana")
    asset = _asset("http://127.0.0.1:9")
    with pytest.raises(SampleDataError, match="CAFEIN_SAMPLEDATA_TIMEOUT"):
        fetch(asset, "helsinki")
    target = _target(cache, asset)
    assert not target.parent.exists()  # nothing was created at all


def test_a_relative_cache_override_is_absolutized(server, tmp_path, monkeypatch):
    # A relative CAFEIN_SAMPLEDATA_DIR binds to the working directory at
    # call time; the returned paths are absolute, so a later chdir
    # cannot redirect them.
    import os as _os

    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAFEIN_SAMPLEDATA_DIR", "relative-cache")
    assert cache_root() == (tmp_path / "relative-cache").resolve()
    path = fetch(_asset(url), "helsinki")
    assert path.is_absolute()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert path.read_bytes() == PAYLOAD
    assert _os.path.exists(path)


def test_a_corrupt_cache_heals_under_concurrent_fetches(server, cache):
    # Starting from a corrupt cached copy, concurrent fetches may race a
    # re-download against the discard, but the guard unlinks only the
    # exact file it hashed: the cache converges on valid bytes.
    import concurrent.futures

    root, url = server
    (root / "sample.bin").write_bytes(PAYLOAD)
    asset = _asset(url)
    target = _target(cache, asset)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"corrupt bytes from a bad disk")

    def attempt(_):
        try:
            return fetch(asset, "helsinki").read_bytes()
        except ChecksumMismatch:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(attempt, range(4)))
    assert all(outcome in (None, PAYLOAD) for outcome in outcomes)
    assert fetch(asset, "helsinki").read_bytes() == PAYLOAD


def test_metadata_surfaces_the_provenance(server):
    _, url = server
    asset = _asset(
        url,
        license="ODbL 1.0",
        attribution="© OpenStreetMap contributors",
        source_stamp="Geofabrik finland-latest 2026-08-07",
    )
    metadata = asset.metadata()
    assert metadata["license"] == "ODbL 1.0"
    assert metadata["release"] == "helsinki-2026.08"
    assert metadata["sha256"] == asset.sha256
