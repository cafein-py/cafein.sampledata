"""Unpinned source downloads for the pipeline.

Unlike the package client, the pipeline downloads *sources of truth* —
there is no pin to verify against; the hashes it measures become the
next release's pins. Streams to an exclusive tempfile and renames into
place, recording what was fetched and when.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import pathlib
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone

from pipeline import PipelineError
from pipeline import config


def stream_download(
    url,
    destination,
    timeout=config.DOWNLOAD_TIMEOUT,
    max_bytes=config.MAX_DOWNLOAD_BYTES,
) -> dict:
    """Download `url` to `destination`, returning what was fetched:
    sha256, byte size, fetch time, and the server's Last-Modified.

    `max_bytes` bounds the body — declared or streamed — so a
    misbehaving endpoint cannot fill the release runner's disk."""
    destination = pathlib.Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        dir=destination.parent, prefix=destination.name + ".part."
    )
    part = pathlib.Path(name)
    hasher = hashlib.sha256()
    size = 0
    published = False
    try:
        try:
            with os.fdopen(handle, "wb") as sink:
                with urllib.request.urlopen(url, timeout=timeout) as response:
                    last_modified = response.headers.get("Last-Modified")
                    declared = _declared_length(response)
                    if declared is not None and declared > max_bytes:
                        raise PipelineError(
                            f"{url} declares {declared} bytes, over the "
                            f"{max_bytes}-byte source cap"
                        )
                    while chunk := response.read(1 << 20):
                        size += len(chunk)
                        if size > max_bytes:
                            raise PipelineError(
                                f"{url} streamed past the {max_bytes}-byte "
                                f"source cap"
                            )
                        hasher.update(chunk)
                        sink.write(chunk)
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            raise PipelineError(f"could not download {url}: {error}") from error
        # Sized reads return EOF silently when a server closes early; a
        # declared length the stream did not reach means truncation, and
        # a truncated source must never become a pinned release asset.
        if declared is not None and size != declared:
            raise PipelineError(
                f"downloading {url}: received {size} bytes of a declared "
                f"{declared} — the source was truncated"
            )
        part.replace(destination)
        published = True
    finally:
        if not published:
            part.unlink(missing_ok=True)
    return {
        "url": url,
        "sha256": hasher.hexdigest(),
        "size": size,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_modified": last_modified,
    }


def run_directory(work_dir) -> pathlib.Path:
    """A private (0700) per-run staging directory inside `work_dir`.

    Every source and output candidate lives here until publication, so
    other *users* cannot touch them; a hostile same-user process racing
    directory entries is outside the pipeline's threat model (release
    runs use a fresh CI workspace).
    """
    return pathlib.Path(tempfile.mkdtemp(dir=work_dir, prefix=".run."))


def staging_path(run_dir, name) -> pathlib.Path:
    """A uniquely named file in the run directory for one asset:
    downloads, validation, and hashing all happen at this path, and only
    a finished asset is renamed onto its public name."""
    handle, raw = tempfile.mkstemp(dir=run_dir, prefix=name + ".staging.")
    os.close(handle)
    return pathlib.Path(raw)


def _declared_length(response) -> int | None:
    """The response's Content-Length as an int, or None when absent or
    malformed (e.g. a chunked response, whose truncation raises on its
    own)."""
    header = response.headers.get("Content-Length")
    try:
        return int(header) if header is not None else None
    except ValueError:
        return None
