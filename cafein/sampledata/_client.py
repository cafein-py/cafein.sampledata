"""The download client: pinned, checksummed assets cached locally.

Deliberately stdlib-only. An asset downloads to a private exclusive
tempfile, is hashed while streaming, and is atomically renamed into
place only when its sha256 matches the registry pin — a cache never
holds a file that was not verified. A verified file is trusted on
later accesses through a stamp file beside it (whose recorded digest
must still equal the pin); set ``CAFEIN_SAMPLEDATA_VERIFY=1`` to
re-hash instead.

The threat model is the user's own single-user cache directory: every
write is exclusive-create + atomic rename and resolved paths must stay
under the cache root, but no defence is attempted against a hostile
process racing filesystem operations inside the cache — an actor with
write access there already owns the account's files.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import pathlib
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass


class SampleDataError(RuntimeError):
    """Base class for sample-data failures."""


class DownloadFailed(SampleDataError):
    """The asset could not be fetched and no verified copy is cached."""


class ChecksumMismatch(SampleDataError):
    """The bytes on disk or from the network do not match the pin."""


@dataclass(frozen=True)
class Asset:
    """One pinned data file: where it lives, what it must hash to and
    measure, and the provenance surfaced through ``metadata``."""

    name: str
    url: str
    sha256: str
    #: Exact byte size from the release manifest; the stream is capped
    #: at it, so a misbehaving server cannot fill the disk.
    size: int
    license: str = ""
    attribution: str = ""
    source_stamp: str = ""
    #: The data-release tag the file belongs to, e.g. ``helsinki-2026.08``.
    #: Part of the cache path, so two releases never collide.
    release: str = ""

    def metadata(self) -> dict[str, object]:
        return {
            "name": self.name,
            "url": self.url,
            "sha256": self.sha256,
            "size": self.size,
            "license": self.license,
            "attribution": self.attribution,
            "source_stamp": self.source_stamp,
            "release": self.release,
        }


def cache_root() -> pathlib.Path:
    """The cache directory: ``CAFEIN_SAMPLEDATA_DIR`` if set (and
    non-empty), else the platform user-cache convention.

    Always one absolute, resolved path — a relative override or an
    empty platform variable never leaves the cache at the mercy of a
    later working-directory change.
    """
    override = os.environ.get("CAFEIN_SAMPLEDATA_DIR")
    if override:
        return pathlib.Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        base = pathlib.Path.home() / "Library" / "Caches"
    elif os.name == "nt":
        base = pathlib.Path(
            os.environ.get("LOCALAPPDATA") or pathlib.Path.home() / "AppData" / "Local"
        )
    else:
        base = pathlib.Path(
            os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache"
        )
    return (base / "cafein-sampledata").expanduser().resolve()


def fetch(asset: Asset, region: str) -> pathlib.Path:
    """The local path of `asset`, downloading and verifying on first use.

    The path is ``<cache>/<region>/<release>/<sha256>/<name>`` — every
    component validated as a plain filename so no registry or caller
    value can step outside the cache, and the full digest in the path
    keeps *different pins physically disjoint*: two processes holding
    different pins for the same name can never interleave one's bytes
    with the other's stamp. A cached file whose stamp records the
    pinned digest returns immediately; otherwise it is re-hashed first.
    A checksum mismatch discards the file and raises — a retry
    re-downloads, and a mismatch that survives a retry means the hosted
    asset changed under the pin, which a package upgrade resolves.
    """
    _component(region, "region")
    _component(asset.name, "asset name")
    # The release tag is part of the cache path, so a changed pin lands
    # at a fresh path; a releaseless asset would let a later pin of the
    # same name silently serve the earlier bytes.
    _component(asset.release, "release")
    _hex_digest(asset.sha256)
    if not isinstance(asset.size, int) or asset.size <= 0:
        raise SampleDataError(
            f"unsafe size {asset.size!r}: expected the exact positive "
            f"byte count from the release manifest"
        )
    root = cache_root()
    target = root / region / asset.release / asset.sha256 / asset.name
    # Stamps live in a dot-directory assets can never collide with:
    # `_component` rejects dot-led names, so no legal asset name maps
    # onto another asset's stamp path.
    stamp = target.parent / ".stamps" / target.name
    # Belt and braces over the component checks — also rejects either
    # path reached through a symlinked directory pointing elsewhere
    # (e.g. a planted `.stamps` symlink), before anything is touched.
    for path in (target, stamp):
        if not path.resolve().is_relative_to(root):
            raise SampleDataError(f"cache path {path} escapes the cache root {root}")
    if target.exists():
        # The stamp must record the *pinned* digest: anything else — a
        # stale pin, a symlink, unreadable bytes — is no verification.
        if _read_stamp(stamp) == asset.sha256 and not os.environ.get(
            "CAFEIN_SAMPLEDATA_VERIFY"
        ):
            return target
        try:
            with open(target, "rb") as handle:
                digest = _stream_sha256(handle)
                hashed = os.fstat(handle.fileno())
        except FileNotFoundError:
            # A parallel fetch discarded the file between the existence
            # check and the open — an ordinary cache miss; fall through
            # to the download below.
            pass
        else:
            if digest == asset.sha256:
                _write_stamp(stamp, digest)
                return target
            # Discard only the exact file that hashed wrong: if a
            # parallel fetch replaced it with a verified copy meanwhile,
            # that copy stays. (A replacement inside the remaining
            # stat/unlink window is not defended — see the module's
            # threat-model note.)
            try:
                if os.path.samestat(os.stat(target), hashed):
                    target.unlink()
                    stamp.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            raise ChecksumMismatch(
                f"cached {asset.name} hashed {digest}, expected "
                f"{asset.sha256}; the file was discarded — re-run to "
                f"download it again"
            )
    # No target: any stamp is an orphan (e.g. a failed earlier run), and
    # left in place it would exempt a manually placed file from hashing.
    stamp.unlink(missing_ok=True)
    return _download(asset, target, stamp)


def _component(value: str, field_name: str) -> None:
    """Reject anything that is not a plain, visible path component —
    dot-led names are reserved for the client's own metadata."""
    if (
        not value
        or value.startswith(".")
        or any(character in value for character in "/\\:")
    ):
        raise SampleDataError(
            f"unsafe {field_name} {value!r}: must be a plain filename "
            f"without path separators or a leading dot"
        )


def _hex_digest(value: str) -> None:
    """The pin joins the cache path, so it must really be a sha256 hex."""
    if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise SampleDataError(
            f"unsafe sha256 {value!r}: expected 64 lowercase hex characters"
        )


def _read_stamp(stamp: pathlib.Path) -> str | None:
    """The digest a stamp records, or None when there is no usable stamp
    — absent, a symlink (never followed), or unreadable — in which case
    the caller falls back to hashing the data file itself."""
    try:
        if stamp.is_symlink() or not stamp.is_file():
            return None
        return stamp.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None


def _download(asset: Asset, target: pathlib.Path, stamp: pathlib.Path) -> pathlib.Path:
    # A finite socket timeout, so a peer that accepts and then stalls
    # fails as DownloadFailed instead of hanging fetch() forever.
    # Parsed before anything touches the filesystem: a bad value must
    # not leak a descriptor or tempfile.
    raw_timeout = os.environ.get("CAFEIN_SAMPLEDATA_TIMEOUT", "60")
    try:
        timeout = float(raw_timeout)
        if not timeout > 0:
            raise ValueError
    except ValueError:
        raise SampleDataError(
            f"CAFEIN_SAMPLEDATA_TIMEOUT={raw_timeout!r} is not a positive "
            f"number of seconds"
        ) from None
    target.parent.mkdir(parents=True, exist_ok=True)
    # A private exclusive tempfile per caller: concurrent fetches of the
    # same asset never touch each other's bytes, and creation cannot
    # follow a planted symlink. Whoever verifies last wins the rename —
    # identical bytes either way, since every rename is behind the hash.
    handle, name = tempfile.mkstemp(dir=target.parent, prefix=target.name + ".part.")
    part = pathlib.Path(name)
    hasher = hashlib.sha256()
    published = False
    received = 0
    try:
        try:
            with os.fdopen(handle, "wb") as sink:
                with urllib.request.urlopen(asset.url, timeout=timeout) as response:
                    while chunk := response.read(1 << 20):
                        received += len(chunk)
                        # The pin includes the exact byte count, so a
                        # stream running past it is aborted rather than
                        # allowed to fill the disk before hashing.
                        if received > asset.size:
                            raise ChecksumMismatch(
                                f"downloading {asset.name}: the stream "
                                f"exceeded the pinned size of {asset.size} "
                                f"bytes; the hosted asset does not match "
                                f"the pin"
                            )
                        hasher.update(chunk)
                        sink.write(chunk)
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            # URLError covers refused/unreachable; HTTPException covers a
            # stream dying mid-body (e.g. IncompleteRead on truncation).
            raise DownloadFailed(
                f"could not download {asset.name} from {asset.url}: {error}. "
                f"If you have the file, place it at {target} and re-run."
            ) from error
        digest = hasher.hexdigest()
        if digest != asset.sha256:
            raise ChecksumMismatch(
                f"downloaded {asset.name} hashed {digest}, expected "
                f"{asset.sha256}; re-run to retry — if the mismatch persists, "
                f"the hosted asset changed and a cafein.sampledata upgrade "
                f"carries the new pin"
            )
        part.replace(target)
        published = True
    finally:
        # Whatever went wrong — translated or not — the tempfile never
        # outlives the call it belongs to.
        if not published:
            part.unlink(missing_ok=True)
    _write_stamp(stamp, digest)
    return target


def _write_stamp(stamp: pathlib.Path, digest: str) -> None:
    """Publish the stamp through the same exclusive-tempfile + rename
    pattern as the data file, so a pre-planted stamp symlink is replaced
    rather than written through."""
    stamp.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=stamp.parent, prefix=stamp.name + ".")
    temporary = pathlib.Path(name)
    published = False
    try:
        try:
            sink = os.fdopen(handle, "w")
        except Exception:
            os.close(handle)
            raise
        with sink:
            sink.write(digest)
        temporary.replace(stamp)
        published = True
    finally:
        if not published:
            temporary.unlink(missing_ok=True)


def _stream_sha256(source) -> str:
    hasher = hashlib.sha256()
    while chunk := source.read(1 << 20):
        hasher.update(chunk)
    return hasher.hexdigest()
