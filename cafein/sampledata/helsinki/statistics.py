"""Socioeconomic statistics for the Helsinki Metropolitan Area.

One GeoPackage per layer, each downloading and verifying on first
access like every other asset::

    import cafein.sampledata.helsinki as helsinki

    helsinki.statistics.income  # -> pathlib.Path

``income`` carries Statistics Finland's Paavo disposable-income
variables by postal-code area, as polygons in EPSG:3067 (layer name
``income``): postal code, area name, and municipality code, then the
inhabitant variables — ``hr_tuy`` (aged 18 or over), ``hr_ktu``
(average income), ``hr_mtu`` (median income), ``hr_pi_tul`` /
``hr_ke_tul`` / ``hr_hy_tul`` (lowest / middle / highest income
category counts) — and their household counterparts ``tr_kuty``,
``tr_ktu``, ``tr_mtu``, ``tr_pi_tul`` / ``tr_ke_tul`` / ``tr_hy_tul``,
plus ``te_taly`` (households, total). Values Paavo protects for
privacy (published as -1) are nulls here.

The data is © Statistics Finland (Paavo), CC BY 4.0.
"""

#: Every statistics layer, and what it carries.
LAYERS = {
    "income": "Statistics Finland Paavo — disposable income by postal-code area",
}

__all__ = ["LAYERS", "fetch", *sorted(LAYERS)]


def _registry_key(layer) -> str:
    return f"statistics_{layer}"


def __getattr__(name):
    if name in LAYERS:
        from cafein.sampledata.helsinki import _resolve

        return _resolve(_registry_key(name))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))


def fetch():
    """Prefetch every statistics layer; returns ``{layer: Path}``."""
    from cafein.sampledata.helsinki import _resolve

    return {layer: _resolve(_registry_key(layer)) for layer in sorted(LAYERS)}
