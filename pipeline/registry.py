"""Regenerate the helsinki registry from a data release's manifest.

Reads ``manifest.json`` (as the steps emitted and the release carries)
and rewrites the generated block of
``cafein/sampledata/helsinki/_registry.py`` with the release tag and
per-asset pins — the mechanical step between approving a data release
and cutting the package release that carries it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re

from pipeline import PipelineError, config, manifest

REGISTRY_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "cafein"
    / "sampledata"
    / "helsinki"
    / "_registry.py"
)

BEGIN = "# --- REGISTRY (generated; do not edit by hand) ---"
END = "# --- END REGISTRY ---"

#: Which manifest asset feeds which registry attribute. The POI layers
#: are keyed ``poi_<category>``; the client's ``pois`` namespace reads
#: them under their bare category names.
ATTRIBUTES = {
    "osm_pbf": "helsinki_capital_region.osm.pbf",
    "gtfs": "hsl_gtfs.zip",
    "dem": "helsinki_dem_10m.tif",
    "population_grid": "hsy_population_grid_250m.gpkg",
}
ATTRIBUTES.update(
    {f"poi_{category}": asset for category, asset in config.POI_ASSETS.items()}
)


RELEASE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _literal(value, field=None, filename=None) -> str:
    """A manifest string as a safe Python literal — the registry is
    executable code, so every interpolated value is escaped and never
    bare-formatted. JSON string syntax is a subset of Python's and
    double-quoted, so the generated file is black-clean as written."""
    if not isinstance(value, str):
        raise PipelineError(f"{filename}: manifest field {field!r} is not a string")
    return json.dumps(value, ensure_ascii=False)


def render_block(release, assets) -> str:
    """The generated registry block for `release`, from manifest asset
    records keyed by filename."""
    if not isinstance(release, str) or not RELEASE_PATTERN.fullmatch(release):
        raise PipelineError(f"malformed release tag {release!r}")
    lines = [BEGIN, f"RELEASE = {_literal(release)}", "", "ASSETS = {"]
    for attribute, filename in ATTRIBUTES.items():
        if filename not in assets:
            raise PipelineError(
                f"the manifest carries no {filename!r} — refusing to "
                f"regenerate a partial registry"
            )
        record = assets[filename]
        if record.get("file") != filename:
            raise PipelineError(
                f"the manifest record for {filename!r} names "
                f"{record.get('file')!r} — an internally inconsistent "
                f"manifest; refusing to pin it"
            )
        sha256 = record.get("sha256")
        if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
            raise PipelineError(f"{filename}: malformed sha256 {sha256!r}")
        size = record.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise PipelineError(f"{filename}: malformed size {size!r}")
        # The generated line references DOWNLOAD_BASE at import time, so
        # moving the hosting later stays a one-constant change.
        url_line = '        url=f"{DOWNLOAD_BASE}/' + f'{release}/{filename}",'
        lines += [
            f'    "{attribute}": Asset(',
            f"        name={_literal(filename)},",
            url_line,
            f"        sha256={_literal(sha256)},",
            f"        size={size},",
            f"        license={_literal(record.get('license'), 'license', filename)},",
            f"        attribution="
            f"{_literal(record.get('attribution'), 'attribution', filename)},",
            f"        source_stamp="
            f"{_literal(record.get('source_stamp'), 'source_stamp', filename)},",
            "        release=RELEASE,",
            "    ),",
        ]
    lines += ["}", END]
    return "\n".join(lines)


def regenerate(manifest_path, release, registry_path=REGISTRY_PATH) -> str:
    """Rewrite the registry's generated block; returns the new text."""
    payload = manifest.read_manifest(manifest_path)
    registry_path = pathlib.Path(registry_path)
    text = registry_path.read_text(encoding="utf-8")
    # Markers match as exact whole lines only: values inside the block
    # are escaped single-line literals, so an attribution merely
    # *containing* the marker text can never confuse the splice.
    lines = text.split("\n")
    begins = [index for index, line in enumerate(lines) if line == BEGIN]
    ends = [index for index, line in enumerate(lines) if line == END]
    if len(begins) != 1 or len(ends) != 1 or ends[0] < begins[0]:
        raise PipelineError(
            f"{registry_path} does not carry exactly one generated " f"registry block"
        )
    block = render_block(release, payload["assets"])
    lines = lines[: begins[0]] + block.split("\n") + lines[ends[0] + 1 :]
    text = "\n".join(lines)
    registry_path.write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", help="path of the release's manifest.json")
    parser.add_argument("release", help="data release tag, e.g. helsinki-2026.08")
    arguments = parser.parse_args()
    regenerate(arguments.manifest, arguments.release)
    print(REGISTRY_PATH)
