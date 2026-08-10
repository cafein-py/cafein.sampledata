# Releasing

Two release kinds, both gated on a human click.

## 1. A data release (`helsinki-YYYY.MM`)

1. Run the **data-release** workflow (Actions → data-release → Run
   workflow) with the release tag (`helsinki-YYYY.MM`), a Geofabrik
   snapshot date (`YYMMDD`, an immutable dated source), and a service
   date inside the fresh feed (`YYYY-MM-DD`, usually a near-future
   weekday). Requires the `MML_API_KEY` repository secret.
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
   gh release download helsinki-YYYY.MM --pattern manifest.json --dir /tmp
   python -m pipeline.registry /tmp/manifest.json helsinki-YYYY.MM
   # pyproject.toml: version = "YYYY.MM.0" (mirrors the data tag)
   ```

2. Tag the merged commit `vYYYY.MM.0` and push the tag. The **release**
   workflow runs the tests, refuses an unpinned registry, builds, and
   pauses on the `pypi` environment — **approve the deployment** to
   publish through the PyPI trusted publisher. No tokens exist
   anywhere.

Users then get the new data with `pip install -U cafein.sampledata`.
