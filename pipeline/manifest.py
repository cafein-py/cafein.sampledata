"""The release manifest: every asset hashed, measured, and attributed.

``manifest.json`` is the bridge between a data release and the package
registry: the registry-regeneration script reads it to bake the pins
into ``cafein.sampledata``.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile

SCHEMA = 1


def file_digest(path) -> tuple[str, int]:
    """The (sha256, byte size) of a file, streamed."""
    hasher = hashlib.sha256()
    size = 0
    with open(path, "rb") as source:
        while chunk := source.read(1 << 20):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def asset_record(path, *, license, attribution, source_stamp, name=None) -> dict:
    """The manifest entry for one produced asset file; `name` overrides
    the recorded filename when `path` is a staging file."""
    digest, size = file_digest(path)
    return {
        "file": name or pathlib.Path(path).name,
        "sha256": digest,
        "size": size,
        "license": license,
        "attribution": attribution,
        "source_stamp": source_stamp,
    }


def atomic_write_text(path, text) -> None:
    """Publish `text` at `path` via an exclusive same-directory tempfile
    and rename: no write through a pre-planted symlink, no truncated
    file visible to a reader, fsynced before the rename."""
    path = pathlib.Path(path)
    handle, raw = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    temporary = pathlib.Path(raw)
    published = False
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as sink:
            sink.write(text)
            sink.flush()
            os.fsync(sink.fileno())
        temporary.replace(path)
        published = True
    finally:
        if not published:
            temporary.unlink(missing_ok=True)


def write_manifest(path, assets: dict) -> dict:
    """Write ``manifest.json``: the schema marker and the asset records,
    name-sorted so reruns diff cleanly."""
    payload = {"schema": SCHEMA, "assets": dict(sorted(assets.items()))}
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    return payload


def read_manifest(path) -> dict:
    payload = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise ValueError(
            f"manifest schema {payload.get('schema')!r} is not the "
            f"supported schema {SCHEMA}"
        )
    return payload
