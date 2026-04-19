"""Site registry — JSON list of named locations with optional radius.

File format (any of these is valid):

    [
      {"name": "Smith House", "lat": 37.5, "lon": -122.5},
      {"name": "Jones Garage", "lat": 37.6, "lon": -122.4, "radius_m": 50,
       "address": "200 Oak Ave"}
    ]

Default location: ``~/Library/Application Support/bosch-glm/sites.json``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

from .location import haversine_m

logger = logging.getLogger(__name__)

DEFAULT_RADIUS_M = 100.0


@dataclass
class Site:
    name: str
    latitude: float
    longitude: float
    address: str | None = None
    radius_m: float = DEFAULT_RADIUS_M

    @classmethod
    def from_dict(cls, d: dict) -> "Site":
        if "name" not in d:
            raise ValueError(f"site entry missing 'name': {d}")
        # Accept both 'lon' and 'lng' for ergonomics
        lon = d.get("lon", d.get("lng"))
        if "lat" not in d or lon is None:
            raise ValueError(f"site '{d.get('name')}' missing lat/lon")
        return cls(
            name=str(d["name"]),
            latitude=float(d["lat"]),
            longitude=float(lon),
            address=d.get("address"),
            radius_m=float(d.get("radius_m", DEFAULT_RADIUS_M)),
        )


def default_sites_path() -> Path:
    base = user_data_path("bosch-glm", appauthor=False, ensure_exists=True)
    return base / "sites.json"


def load_sites(path: Path | None = None) -> list[Site]:
    """Load sites from the given JSON file. Returns empty list if missing
    or malformed (logs the issue)."""
    p = path or default_sites_path()
    if not p.exists():
        logger.debug("no sites file at %s", p)
        return []
    try:
        with p.open() as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("failed to load sites from %s: %s", p, e)
        return []
    if not isinstance(data, list):
        logger.warning("sites file at %s is not a JSON list", p)
        return []
    out = []
    for entry in data:
        try:
            out.append(Site.from_dict(entry))
        except (ValueError, KeyError, TypeError) as e:
            logger.warning("skipping invalid site entry %r: %s", entry, e)
    return out


def nearest_site(location: tuple[float, float],
                 sites: list[Site]) -> tuple[Site, float] | None:
    """Find the site whose center is closest to ``location`` AND within its
    own ``radius_m``. Returns (site, distance_meters) or None if no match.

    The radius is per-site so a small lot can use 30m while a sprawling
    industrial address can use 500m."""
    best: tuple[Site, float] | None = None
    for s in sites:
        d = haversine_m(location, (s.latitude, s.longitude))
        if d > s.radius_m:
            continue
        if best is None or d < best[1]:
            best = (s, d)
    return best
