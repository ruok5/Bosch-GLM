import json
import pathlib
import tempfile

import pytest

from glm.sites import (
    DEFAULT_RADIUS_M, Site, load_sites, nearest_site,
)


def write_sites(tmp_path: pathlib.Path, content) -> pathlib.Path:
    p = tmp_path / "sites.json"
    p.write_text(json.dumps(content))
    return p


def test_load_sites_returns_empty_when_file_missing(tmp_path):
    assert load_sites(tmp_path / "missing.json") == []


def test_load_sites_round_trip(tmp_path):
    p = write_sites(tmp_path, [
        {"name": "A", "lat": 37.0, "lon": -122.0},
        {"name": "B", "lat": 37.1, "lon": -122.1, "radius_m": 50, "address": "1 Main"},
    ])
    sites = load_sites(p)
    assert len(sites) == 2
    assert sites[0].name == "A"
    assert sites[0].radius_m == DEFAULT_RADIUS_M
    assert sites[1].radius_m == 50
    assert sites[1].address == "1 Main"


def test_load_sites_accepts_lng_alias(tmp_path):
    p = write_sites(tmp_path, [{"name": "A", "lat": 37.0, "lng": -122.0}])
    sites = load_sites(p)
    assert sites[0].longitude == -122.0


def test_load_sites_skips_malformed_entries(tmp_path):
    p = write_sites(tmp_path, [
        {"name": "Good", "lat": 37.0, "lon": -122.0},
        {"name": "MissingLatLon"},
        {"lat": 37.0, "lon": -122.0},  # no name
        "not a dict",
    ])
    sites = load_sites(p)
    assert len(sites) == 1
    assert sites[0].name == "Good"


def test_load_sites_handles_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json {")
    assert load_sites(p) == []


def test_load_sites_handles_non_list_root(tmp_path):
    p = write_sites(tmp_path, {"name": "A", "lat": 1, "lon": 2})  # dict, not list
    assert load_sites(p) == []


def test_nearest_site_returns_within_radius():
    sites = [
        Site("Far", 37.0, -122.0, radius_m=100),
        Site("Near", 37.5, -122.5, radius_m=200),
    ]
    # Within 50m of "Near" point
    location = (37.50001, -122.50001)
    match = nearest_site(location, sites)
    assert match is not None
    assert match[0].name == "Near"
    assert match[1] < 50


def test_nearest_site_returns_none_outside_all_radii():
    sites = [Site("S", 37.0, -122.0, radius_m=10)]
    location = (40.0, -100.0)  # very far
    assert nearest_site(location, sites) is None


def test_nearest_site_picks_closest_when_multiple_match():
    # Two overlapping sites, location is closer to "B"
    sites = [
        Site("A", 37.000, -122.000, radius_m=10_000),
        Site("B", 37.001, -122.001, radius_m=10_000),
    ]
    location = (37.0009, -122.0009)
    match = nearest_site(location, sites)
    assert match is not None
    assert match[0].name == "B"


def test_per_site_radius_is_respected():
    sites = [
        Site("Tight", 37.0, -122.0, radius_m=5),    # 5m radius
        Site("Loose", 37.0001, -122.0001, radius_m=500),  # 500m radius
    ]
    # Location is ~14m from both centers
    location = (37.0001, -122.0001)
    match = nearest_site(location, sites)
    assert match is not None
    assert match[0].name == "Loose"  # Tight's radius excludes it
