"""The GTFS step: fetch the HSL feed and gate it through transitio.

The feed ships exactly as published — transitio validates, it never
edits. ERROR-severity notices (and files transitio could not read in
full) block the release; warnings ride along as a report artifact.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import pathlib
import re
import shutil
import zipfile

from pipeline import PipelineError, config, download, manifest, workdir_lock

FEED_INFO_LIMIT = 1 << 20


def feed_info(path) -> dict:
    """The first row of ``feed_info.txt``, or ``{}`` when absent.

    Reads at most ``FEED_INFO_LIMIT`` uncompressed bytes — the file is a
    handful of header fields, and a remotely supplied archive must not
    be able to balloon the release runner's memory through it.
    """
    with zipfile.ZipFile(path) as archive:
        try:
            entry = archive.getinfo("feed_info.txt")
        except KeyError:
            return {}
        if entry.file_size > FEED_INFO_LIMIT:
            raise PipelineError(
                f"feed_info.txt declares {entry.file_size} uncompressed "
                f"bytes — implausible for a header file; refusing to read"
            )
        with archive.open(entry) as member:
            raw = member.read(FEED_INFO_LIMIT)
    rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    for row in rows:
        return {key.strip(): (value or "").strip() for key, value in row.items()}
    return {}


def partition(report) -> tuple[list, list]:
    """Split a transitio validation report into (blocking, warnings).

    Blocking: every ERROR notice, plus a synthesized notice per file the
    validator could not read in full — a truncated read means the rest
    of the report cannot vouch for the feed.
    """
    notices = report.get("notices")
    incomplete = report.get("incomplete")
    if not isinstance(notices, list) or not isinstance(incomplete, list):
        raise PipelineError(
            "the validation report lacks its notices/incomplete lists — "
            "the transitio report contract changed; refusing to gate on it"
        )
    blocking, warnings = [], []
    for notice in notices:
        severity = notice.get("severity") if isinstance(notice, dict) else None
        if severity == "ERROR":
            blocking.append(notice)
        elif severity == "WARNING":
            warnings.append(notice)
        elif severity != "INFO":
            raise PipelineError(
                f"validation notice with unknown severity {severity!r} — "
                f"the transitio report contract changed; refusing to gate"
            )
    for name in incomplete:
        blocking.append(
            {
                "code": "incomplete_file",
                "severity": "ERROR",
                "context": {"filename": name},
            }
        )
    return blocking, warnings


def normalize_reference_date(value):
    """``YYYY-MM-DD`` or ``YYYYMMDD`` -> transitio's ``YYYYMMDD``."""
    if value is None:
        return None
    text = str(value)
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
            parsed = datetime.date.fromisoformat(text)
        elif re.fullmatch(r"[0-9]{8}", text):
            parsed = datetime.datetime.strptime(text, "%Y%m%d").date()
        else:
            raise ValueError(text)
    except ValueError as error:
        raise PipelineError(f"invalid reference date: {text!r}") from error
    return parsed.strftime("%Y%m%d")


def validate(feed_path, report_path, reference_date=None) -> list:
    """Gate the feed; writes the full report, raises on blocking notices,
    returns the warnings."""
    try:
        from transitio import validate_feed
    except ImportError as error:
        raise PipelineError(
            "the GTFS step needs transitio (see the pipeline environment)"
        ) from error
    report = validate_feed(feed_path, reference_date=reference_date)
    manifest.atomic_write_text(
        report_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    blocking, warnings = partition(report)
    if blocking:
        codes = sorted({notice.get("code", "?") for notice in blocking})
        raise PipelineError(
            f"the feed has {len(blocking)} blocking validation notice(s) "
            f"({', '.join(codes)}); full report at {report_path}"
        )
    return warnings


def build(work_dir, *, url=config.HSL_GTFS_URL, reference_date=None) -> dict:
    """Run the step; returns ``{asset name: manifest record}``."""
    reference_date = normalize_reference_date(reference_date)
    work_dir = pathlib.Path(work_dir)
    with workdir_lock(work_dir):
        # Everything happens inside a private 0700 run directory —
        # download, validation, hashing — and only the finished asset is
        # renamed onto its public name, so no other process can swap the
        # bytes between what transitio saw and what the manifest pins.
        run_dir = download.run_directory(work_dir)
        staging = download.staging_path(run_dir, config.GTFS_ASSET)
        report_staging = run_dir / "gtfs-validation-report.json"
        try:
            fetched = download.stream_download(
                url, staging, max_bytes=config.MAX_GTFS_BYTES
            )
            try:
                validate(staging, report_staging, reference_date)
            except PipelineError as error:
                # The rejected feed's diagnostics survive the run under a
                # name that can never shadow a good generation's report.
                if report_staging.exists():
                    failed = work_dir / "gtfs-validation-report.failed.json"
                    report_staging.replace(failed)
                    raise PipelineError(f"{error} (preserved at {failed})") from error
                raise
            info = feed_info(staging)
            version = info.get("feed_version", "")
            stamp = (
                f"HSL GTFS{f' feed_version={version}' if version else ''} "
                f"fetched {fetched['fetched_at']}"
            )
            record = manifest.asset_record(
                staging,
                name=config.GTFS_ASSET,
                license=config.GTFS_LICENSE,
                attribution=config.GTFS_ATTRIBUTION,
                source_stamp=stamp,
            )
            # Belt and braces: the manifest hash must equal the stream hash.
            if record["sha256"] != fetched["sha256"]:
                raise PipelineError(
                    f"{staging} changed between download and manifest "
                    f"({fetched['sha256']} -> {record['sha256']}); the "
                    f"validated bytes were not the pinned bytes"
                )
            records = {config.GTFS_ASSET: record}
            manifest.publish_transaction(
                work_dir,
                "manifest-gtfs.json",
                records,
                {
                    config.GTFS_ASSET: staging,
                    "gtfs-validation-report.json": report_staging,
                },
            )
        finally:
            # A feed is 150-250 MB; a failed run must not leave one
            # behind — the run directory and any staging in it go.
            shutil.rmtree(run_dir, ignore_errors=True)
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_dir", help="directory for downloads and outputs")
    parser.add_argument(
        "--reference-date", help="YYYY-MM-DD (or YYYYMMDD) for service checks"
    )
    arguments = parser.parse_args()
    print(build(arguments.work_dir, reference_date=arguments.reference_date))
