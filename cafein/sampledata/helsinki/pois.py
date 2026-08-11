"""Points of interest for the Helsinki Metropolitan Area, by category.

One GeoPackage per category, each downloading and verifying on first
access like every other asset::

    import cafein.sampledata.helsinki as helsinki

    helsinki.pois.library       # -> pathlib.Path
    helsinki.pois.supermarket

Every layer is named ``pois`` and carries points in EPSG:4326 —
``osm_id``, ``osm_type``, ``name``, ``category``, and the feature's
remaining OSM ``tags`` as JSON. Places OSM maps as building footprints
are served as their centroid, so a category is one geometry type.

The categories are extracted from the same OpenStreetMap release as
``helsinki.osm_pbf`` and carry its ODbL terms.
"""

#: Every category, and the OSM tagging each one selects.
CATEGORIES = {
    "library": "amenity=library",
    "kindergarten": "amenity=kindergarten",
    "university": "amenity=university",
    "supermarket": "shop=supermarket",
    "shopping_centre": "shop=mall",
    "sports_centre": "leisure=sports_centre",
    # A subset of sports_centre: the halls, not their water basins.
    "swimming_hall": "leisure=sports_centre + sport=swimming",
}

__all__ = ["CATEGORIES", "fetch", *sorted(CATEGORIES)]


def _registry_key(category) -> str:
    return f"poi_{category}"


def __getattr__(name):
    if name in CATEGORIES:
        from cafein.sampledata.helsinki import _resolve

        return _resolve(_registry_key(name))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(__all__))


def fetch():
    """Prefetch every POI category; returns ``{category: Path}``."""
    from cafein.sampledata.helsinki import _resolve

    return {
        category: _resolve(_registry_key(category)) for category in sorted(CATEGORIES)
    }
