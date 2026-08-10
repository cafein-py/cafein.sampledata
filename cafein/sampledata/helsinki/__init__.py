"""Up-to-date sample data for the Helsinki Metropolitan Area.

Module attributes resolve to local paths, downloading and verifying on
first access (see the package README for the cache):

- ``osm_pbf`` — OpenStreetMap extract of the capital region
- ``gtfs`` — the HSL GTFS feed, transitio-validated at release time
- ``dem`` — the NLS 10 m elevation model, EPSG:3067 COG
- ``population_grid`` — HSY's 250 m population grid, GeoPackage
- ``emission_factors`` — cafein's default transit GHG factors as a
  CSV bundled in the wheel (no download), in exactly the schema
  ``cafein.emissions.load_factors`` consumes
- ``emission_factors_full`` — the full three-table reference (transit,
  street, and per-powertrain rows, a ``table`` column apart), for
  authoring custom factor tables
- ``metadata`` — per-asset provenance: source, license, attribution,
  sha256, size, release identifier
"""

import pathlib

from cafein.sampledata import SampleDataError
from cafein.sampledata import fetch as _fetch
from cafein.sampledata.helsinki import _registry

REGION = "helsinki"

_DATA_DIR = pathlib.Path(__file__).parent / "data"
_FACTOR_FILES = {
    "emission_factors": _DATA_DIR / "emission_factors.csv",
    "emission_factors_full": _DATA_DIR / "emission_factors_full.csv",
}


def _package_release() -> str:
    """The bundled files' snapshot identifier: the package version."""
    import importlib.metadata

    try:
        return f"cafein.sampledata {importlib.metadata.version('cafein.sampledata')}"
    except importlib.metadata.PackageNotFoundError:
        return "cafein.sampledata (uninstalled checkout)"


def _factors_metadata(path) -> dict:
    """The bundled files carry the same audit fields as the downloads:
    sha256 and size computed over the shipped bytes."""
    import hashlib

    data = path.read_bytes()
    return {
        "name": path.name,
        "url": "",  # bundled in the wheel, nothing to download
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "license": "published research figures, shipped in cafein (MIT)",
        "attribution": (
            "Dey, Marín-Flores & Tenkanen (2026), doi:10.1016/j.scs.2026.107226"
        ),
        "source_stamp": "exported from cafein.emissions default factors",
        "release": _package_release(),
    }


__all__ = [
    "REGION",
    "dem",
    "emission_factors",
    "emission_factors_full",
    "fetch",
    "gtfs",
    "metadata",
    "osm_pbf",
    "population_grid",
]


def _resolve(name):
    asset = _registry.ASSETS[name]
    if not asset.sha256:
        raise SampleDataError(
            f"this build of cafein.sampledata pins no data release for "
            f"{name!r} yet — upgrade cafein.sampledata to a released "
            f"version"
        )
    return _fetch(asset, REGION)


def __getattr__(name):
    if name in _registry.ASSETS:
        return _resolve(name)
    if name in _FACTOR_FILES:
        return _FACTOR_FILES[name]
    if name == "metadata":
        table = {key: asset.metadata() for key, asset in _registry.ASSETS.items()}
        for key, path in _FACTOR_FILES.items():
            table[key] = _factors_metadata(path)
        return table
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))


def fetch():
    """Prefetch every downloadable asset; returns ``{name: Path}``.

    For CI warm-up and offline preparation — afterwards every attribute
    resolves without the network.
    """
    return {name: _resolve(name) for name in _registry.ASSETS}
