"""Land mask from a tiny GeoJSON fixture."""

import json

import numpy as np

from earwitness.data.landmask import LandMask

SQUARE = [[[-98.0, 34.0], [-96.0, 34.0], [-96.0, 36.0], [-98.0, 36.0], [-98.0, 34.0]]]
ISLANDS = [
    [[[-90.0, 30.0], [-89.0, 30.0], [-89.0, 31.0], [-90.0, 31.0], [-90.0, 30.0]]],
    [[[-80.0, 25.0], [-79.0, 25.0], [-79.0, 26.0], [-80.0, 26.0], [-80.0, 25.0]]],
]


def test_contains_with_polygons_and_multipolygons(tmp_path):
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": SQUARE},
            },
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "MultiPolygon", "coordinates": ISLANDS},
            },
        ],
    }
    path = tmp_path / "land.geojson"
    path.write_text(json.dumps(data))
    mask = LandMask.from_geojson(path)
    assert len(mask) == 3
    lat = np.array([35.0, 30.5, 25.5, 35.0, 0.0])
    lon = np.array([-97.0, -89.5, -79.5, -95.0, 0.0])
    np.testing.assert_array_equal(mask.contains(lat, lon), [True, True, True, False, False])
    assert mask.contains(35.0, -97.0).tolist() == [True]
