"""Bounding boxes and candidate grids."""

import numpy as np
import pytest

from earwitness.core import geodesy
from earwitness.core.grid import (
    METERS_PER_DEGREE,
    BBox,
    geometry_bbox,
    geometry_from_geojson,
    grid_points,
    local_grid,
    points_in_geometry,
)


def test_bbox_parse_contains_expand():
    box = BBox.parse("-98,34,-96,36")
    assert BBox.parse("-98 34 -96 36") == box
    assert str(box) == "-98,34,-96,36"
    assert box.contains(35.0, -97.0)
    assert not box.contains(37.0, -97.0)
    np.testing.assert_array_equal(box.contains([35.0, 33.0], [-97.0, -97.0]), [True, False])
    bigger = box.expand(10000.0)
    assert bigger.west < box.west
    assert bigger.east > box.east
    assert bigger.south < box.south
    assert bigger.north > box.north
    assert box.center == (35.0, -97.0)
    assert 35000.0 < box.area_km2() < 45000.0
    with pytest.raises(ValueError, match="longitude"):
        BBox(10.0, 0.0, 5.0, 1.0)
    with pytest.raises(ValueError, match="west,south,east,north"):
        BBox.parse("1,2,3")


def test_bbox_around_is_the_requested_size():
    box = BBox.around(35.0, -97.0, 50000.0)
    assert abs((box.north - box.south) * METERS_PER_DEGREE - 100000.0) < 1.0
    width = (box.east - box.west) * METERS_PER_DEGREE * np.cos(np.deg2rad(35.0))
    assert abs(width - 100000.0) < 1.0


def test_grid_points_have_metric_spacing():
    box = BBox.around(35.0, -97.0, 50000.0)
    lat, lon = grid_points(box, 5000.0)
    assert 350 < lat.size < 450
    rows = np.unique(lat)
    row_gap = geodesy.horizontal_distance(rows[1], -97.0, float(rows[0]), -97.0)
    assert abs(float(row_gap) - 5000.0) < 2.0
    row = lat == rows[len(rows) // 2]
    along = geodesy.horizontal_distance(lat[row], lon[row], float(lat[row][0]), float(lon[row][0]))
    steps = np.diff(np.sort(along))
    assert np.all(np.abs(steps - 5000.0) < 1.0)
    assert np.all(box.contains(lat, lon))


def test_grid_points_empty_and_invalid():
    tiny = BBox(-97.0, 35.0, -96.999, 35.001)
    lat, lon = grid_points(tiny, 5000.0)
    assert lat.size == 0
    assert lon.size == 0
    with pytest.raises(ValueError, match="positive"):
        grid_points(tiny, 0.0)


def test_local_grid_is_centered_and_square():
    lat, lon = local_grid(35.0, -97.0, 1000.0, 200.0)
    assert lat.size == 121
    d = geodesy.horizontal_distance(lat, lon, 35.0, -97.0)
    assert d.min() < 1e-3
    assert abs(d.max() - np.sqrt(2.0) * 1000.0) < 0.5


def test_geojson_polygon_helpers():
    square = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[-98.0, 34.0], [-96.0, 34.0], [-96.0, 36.0], [-98.0, 36.0], [-98.0, 34.0]]
            ],
        },
    }
    geom = geometry_from_geojson(square)
    assert geometry_bbox(geom) == BBox(-98.0, 34.0, -96.0, 36.0)
    inside = points_in_geometry([35.0, 37.0], [-97.0, -97.0], geom)
    np.testing.assert_array_equal(inside, [True, False])
    collection = {"type": "FeatureCollection", "features": [square, square]}
    assert geometry_bbox(geometry_from_geojson(collection)) == BBox(-98.0, 34.0, -96.0, 36.0)
