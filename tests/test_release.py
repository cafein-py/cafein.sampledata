"""The release assembly: verification, artifacts, tag discipline."""

import pytest

from pipeline import PipelineError, config, manifest, release, smoke


def _produced_work_dir(tmp_path):
    """A work dir as the four steps leave it, with tiny stand-in assets."""
    payloads = {
        "manifest-osm.json": (config.OSM_ASSET, b"osm bytes"),
        "manifest-gtfs.json": (config.GTFS_ASSET, b"gtfs bytes"),
        "manifest-dem.json": (config.DEM_ASSET, b"dem bytes"),
        "manifest-population.json": (config.POPULATION_ASSET, b"grid bytes"),
    }
    for manifest_name, (asset, payload) in payloads.items():
        (tmp_path / asset).write_bytes(payload)
        record = manifest.asset_record(
            tmp_path / asset,
            license="CC BY 4.0",
            attribution=f"source of {asset}",
            source_stamp=f"stamp of {asset}",
        )
        manifest.write_manifest(tmp_path / manifest_name, {asset: record})
    return tmp_path


def test_assemble_writes_the_release_artifacts(tmp_path):
    work = _produced_work_dir(tmp_path)
    records = release.assemble(work, "helsinki-2026.08")
    assert set(records) == set(smoke.STEP_MANIFESTS.values())
    combined = manifest.read_manifest(work / "manifest.json")
    assert set(combined["assets"]) == set(records)
    licenses = (work / "LICENSES.txt").read_text()
    assert "CC BY 4.0" in licenses
    assert "creativecommons.org/licenses/by/4.0" in licenses
    assert "OpenStreetMap contributors" in licenses
    assert f"stamp of {config.OSM_ASSET}" in licenses
    notes = (work / "RELEASE_NOTES.md").read_text()
    assert notes.startswith("# helsinki-2026.08")
    assert config.GTFS_ASSET in notes


def test_assemble_reverifies_the_assets(tmp_path):
    work = _produced_work_dir(tmp_path)
    (work / config.DEM_ASSET).write_bytes(b"tampered after the steps")
    with pytest.raises(PipelineError, match="does not match its manifest"):
        release.assemble(work, "helsinki-2026.08")
    assert not (work / "manifest.json").exists()


@pytest.mark.parametrize(
    "tag", ["2026.08", "helsinki-26.8", "helsinki-2026.8", "oslo-2026.08", "../x"]
)
def test_assemble_refuses_a_malformed_tag(tmp_path, tag):
    work = _produced_work_dir(tmp_path)
    with pytest.raises(PipelineError, match="malformed data release tag"):
        release.assemble(work, tag)
