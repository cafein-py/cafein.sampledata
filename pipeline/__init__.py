"""The data pipeline behind cafein.sampledata's release assets.

Repo-only — never shipped in the wheel. Each step module produces one
or more data files in a working directory and returns their manifest
records; the release workflow runs the steps, assembles
``manifest.json``, and attaches everything to a data release.
"""

import contextlib
import os
import pathlib


class PipelineError(RuntimeError):
    """A pipeline step could not produce a releasable asset."""


@contextlib.contextmanager
def workdir_lock(work_dir):
    """One build per working directory: the public asset names and the
    step manifest are only coherent when a single run writes them, so a
    second concurrent build is refused rather than interleaved."""
    lock = pathlib.Path(work_dir) / ".pipeline-lock"
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise PipelineError(
            f"{work_dir} is already in use by another pipeline run "
            f"(remove {lock} if it is stale)"
        ) from None
    os.close(handle)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)
