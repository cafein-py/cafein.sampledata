# Releasing

Two release kinds, both gated on a human click.

## 1. A data release (`helsinki-YYYY.MM[.N]`)

A published data release is immutable: package versions pin it by tag
and sha256, so correcting the data means cutting a *new* tag, never
moving or deleting an old one. Routine refreshes are `helsinki-YYYY.MM`;
a second release within the same month appends a serial —
`helsinki-2026.08.1`, `helsinki-2026.08.2` — so a fix never has to wait
for the calendar.

1. Run the **data-release** workflow (Actions → data-release → Run
   workflow) with the release tag (`helsinki-YYYY.MM[.N]`), a Geofabrik
   snapshot date (`YYMMDD`, an immutable dated source), a service
   date inside the fresh feed (`YYYY-MM-DD`, usually a near-future
   weekday), and the ENFUSER valid hour (an ISO instant like
   `2026-08-18T06:00:00Z`; recent hours only — the open WFS serves
   near-real-time and forecast, not an archive). Requires the
   `MML_API_KEY` repository secret. The noise step additionally
   requires the one-time resource pins in `pipeline/config.py`
   (`NOISE_RESOURCES`) — capture them once with
   `python -m pipeline.noise --discover <work_dir>` from a machine
   that reaches hri.fi, and refresh them only when HRI republishes
   the dataset.
2. The workflow fetches the sources, validates the feed with transitio,
   smoke-tests every asset through cafein, and attaches the assets plus
   `manifest.json`, the validation report, `LICENSES.txt`, and release
   notes to a **draft** release.
3. Review the draft — the notes table, the validation report, the
   source stamps — and **publish it**. Never delete a published data
   release that any package version pins.

## 2. A package release (`vYYYY.MM.PATCH` → PyPI)

1. On a branch, regenerate the registry from the published data release
   and bump the version, then PR and merge:

   ```bash
   TAG=helsinki-YYYY.MM[.N]   # the data release just published
   gh release download "$TAG" --pattern manifest.json --dir /tmp
   python -m pipeline.registry /tmp/manifest.json "$TAG"
   # pyproject.toml: version = "YYYY.MM.PATCH"
   ```

   The version's `YYYY.MM` follows the data tag's month; `PATCH` is the
   next free number for that month, whether the release carries new data
   or only package-code changes. A package release always pins exactly
   one data tag.

2. Tag the merged commit `vYYYY.MM.PATCH` (the version just set in
   `pyproject.toml`) and push the tag. The **release**
   workflow runs the tests, refuses an unpinned registry, builds, and
   pauses on the `pypi` environment — **approve the deployment** to
   publish through the PyPI trusted publisher. No tokens exist
   anywhere.

Users then get the new data with `pip install -U cafein.sampledata`.
