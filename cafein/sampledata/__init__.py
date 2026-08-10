"""Sample datasets for cafein, downloaded on first access.

The public surface is the region modules (``cafein.sampledata.helsinki``);
this package carries the shared download machinery they resolve through.
"""

from cafein.sampledata._client import (
    Asset,
    ChecksumMismatch,
    DownloadFailed,
    SampleDataError,
    cache_root,
    fetch,
)

__all__ = [
    "Asset",
    "ChecksumMismatch",
    "DownloadFailed",
    "SampleDataError",
    "cache_root",
    "fetch",
]
