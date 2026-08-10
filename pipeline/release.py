"""Assemble a verified data release from a completed pipeline run.

Re-verifies every asset against its step manifest and writes the
release artifacts beside them: the combined ``manifest.json`` (what the
registry regeneration reads), ``LICENSES.txt`` (the per-asset terms and
attributions), and ``RELEASE_NOTES.md``.
"""

from __future__ import annotations

import argparse
import pathlib
import re

from pipeline import PipelineError, manifest, smoke

TAG_PATTERN = re.compile(r"^helsinki-\d{4}\.\d{2}$")

#: Canonical license texts, referenced from the notice document.
LICENSE_URLS = {
    "ODbL 1.0": "https://opendatacommons.org/licenses/odbl/1-0/",
    "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
}


def assemble(work_dir, tag) -> dict:
    """Write the release artifacts; returns the merged records."""
    if not TAG_PATTERN.fullmatch(tag):
        raise PipelineError(
            f"malformed data release tag {tag!r}: expected helsinki-YYYY.MM"
        )
    work_dir = pathlib.Path(work_dir)
    records = smoke.verify_manifests(work_dir)
    manifest.write_manifest(work_dir / "manifest.json", records)
    manifest.atomic_write_text(work_dir / "LICENSES.txt", render_licenses(records))
    manifest.atomic_write_text(
        work_dir / "RELEASE_NOTES.md", render_notes(tag, records)
    )
    return records


def render_licenses(records) -> str:
    lines = [
        "NOTICE",
        "",
        "The data files in this release carry their sources' open",
        "licenses. Per file:",
        "",
    ]
    for name in sorted(records):
        record = records[name]
        lines += [
            f"{name}",
            f"  license:     {record['license']}",
            f"  attribution: {record['attribution']}",
            f"  source:      {record['source_stamp']}",
        ]
        url = LICENSE_URLS.get(record["license"])
        if url:
            lines.append(f"  license text: {url}")
        lines.append("")
    lines += [
        "The OpenStreetMap extract is (c) OpenStreetMap contributors,",
        "available under the Open Database License; any derived database",
        "must be shared under the same terms.",
    ]
    return "\n".join(lines)


def render_notes(tag, records) -> str:
    lines = [
        f"# {tag}",
        "",
        "Sample data for the Helsinki Metropolitan Area, produced by the",
        "cafein.sampledata pipeline and verified consumable by cafein",
        "(see gtfs-validation-report.json for the feed's QA report).",
        "",
        "| file | size | sha256 | source |",
        "|---|---|---|---|",
    ]
    for name in sorted(records):
        record = records[name]
        megabytes = record["size"] / 1e6
        lines.append(
            f"| {name} | {megabytes:.1f} MB | `{record['sha256'][:16]}…` "
            f"| {record['source_stamp']} |"
        )
    lines += [
        "",
        "Install `cafein.sampledata` to use these files — the package",
        "release pinning this tag downloads and verifies them on first",
        "access.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_dir", help="directory holding the produced assets")
    parser.add_argument("tag", help="data release tag, e.g. helsinki-2026.08")
    arguments = parser.parse_args()
    records = assemble(arguments.work_dir, arguments.tag)
    print("\n".join(sorted(records)))
