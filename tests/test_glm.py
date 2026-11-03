"""GLM loader pieces that run without the network: schedule, keys, listing XML, file parsing."""

from datetime import UTC, date, datetime

import numpy as np
import pyarrow.parquet as pq
import pytest
import xarray as xr

from earwitness.core.grid import BBox
from earwitness.data import glm

LISTING_PAGE_1 = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>noaa-goes19</Name><Prefix>GLM-L2-LCFA/2025/136/23/</Prefix>
  <KeyCount>2</KeyCount><MaxKeys>2</MaxKeys><IsTruncated>true</IsTruncated>
  <NextContinuationToken>abc123</NextContinuationToken>
  <Contents><Key>GLM-L2-LCFA/2025/136/23/OR_GLM-L2-LCFA_G19_s1.nc</Key><Size>608039</Size></Contents>
  <Contents><Key>GLM-L2-LCFA/2025/136/23/OR_GLM-L2-LCFA_G19_s2.nc</Key><Size>628519</Size></Contents>
  <Contents><Key>GLM-L2-LCFA/2025/136/23/notes.txt</Key><Size>10</Size></Contents>
</ListBucketResult>
"""
LISTING_PAGE_2 = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <KeyCount>1</KeyCount><IsTruncated>false</IsTruncated>
  <Contents><Key>GLM-L2-LCFA/2025/136/23/OR_GLM-L2-LCFA_G19_s3.nc</Key><Size>1</Size></Contents>
</ListBucketResult>
"""


def test_satellite_schedule():
    assert glm.satellite_for(date(2024, 7, 1)) == "G16"
    assert glm.satellite_for(date(2025, 4, 6)) == "G16"
    assert glm.satellite_for(date(2025, 4, 7)) == "G19"
    assert glm.satellite_for(date(2025, 5, 16)) == "G19"
    assert glm.satellite_for(date(2022, 6, 1), "west") == "G17"
    assert glm.satellite_for(date(2024, 6, 1), "west") == "G18"
    with pytest.raises(ValueError, match="no operational"):
        glm.satellite_for(date(2016, 1, 1))
    with pytest.raises(ValueError, match="position"):
        glm.satellite_for(date(2024, 1, 1), "north")


def test_resolve_satellite():
    central = BBox(-100.0, 30.0, -90.0, 40.0)
    pacific = BBox(-125.0, 35.0, -115.0, 45.0)
    assert glm.resolve_satellite("auto", date(2025, 5, 16), central) == "G19"
    assert glm.resolve_satellite("auto", date(2025, 5, 16), pacific) == "G18"
    assert glm.resolve_satellite("auto", date(2025, 5, 16), None) == "G19"
    assert glm.resolve_satellite("g16", date(2025, 5, 16), central) == "G16"
    assert glm.resolve_satellite("west", date(2022, 1, 1), central) == "G17"
    with pytest.raises(ValueError, match="unknown satellite"):
        glm.resolve_satellite("hubble", date(2025, 5, 16), central)


def test_prefix_and_urls():
    assert glm.s3_prefix(date(2025, 5, 16), 23) == "GLM-L2-LCFA/2025/136/23/"
    url = glm.listing_url("G19", "GLM-L2-LCFA/2025/136/23/", token="t/1")
    assert url.startswith("https://noaa-goes19.s3.amazonaws.com/?")
    assert "prefix=GLM-L2-LCFA%2F2025%2F136%2F23%2F" in url
    assert "continuation-token=t%2F1" in url
    assert glm.object_url("G16", "a/b.nc") == "https://noaa-goes16.s3.amazonaws.com/a/b.nc"


def test_parse_listing_pages():
    items, token = glm.parse_listing(LISTING_PAGE_1)
    assert [k for k, _ in items] == [
        "GLM-L2-LCFA/2025/136/23/OR_GLM-L2-LCFA_G19_s1.nc",
        "GLM-L2-LCFA/2025/136/23/OR_GLM-L2-LCFA_G19_s2.nc",
    ]
    assert items[1][1] == 628519
    assert token == "abc123"
    items2, token2 = glm.parse_listing(LISTING_PAGE_2)
    assert len(items2) == 1
    assert token2 is None


def _write_fake_glm(path, lats, lons, offsets, quality):
    n = len(lats)
    ds = xr.Dataset(
        {
            "flash_lat": ("number_of_flashes", np.asarray(lats, dtype=np.float32)),
            "flash_lon": ("number_of_flashes", np.asarray(lons, dtype=np.float32)),
            "flash_time_offset_of_first_event": (
                "number_of_flashes",
                np.asarray(offsets, dtype=np.float32),
                {"units": "seconds since 2025-05-16 23:00:00.000"},
            ),
            "flash_energy": ("number_of_flashes", np.full(n, 1e-14, dtype=np.float32)),
            "flash_area": ("number_of_flashes", np.full(n, 2.5e8, dtype=np.float32)),
            "flash_quality_flag": ("number_of_flashes", np.asarray(quality, dtype=np.float32)),
            "flash_id": ("number_of_flashes", np.arange(n, dtype=np.uint16)),
        },
        attrs={"platform_ID": "G19"},
    )
    ds.to_netcdf(path, engine="h5netcdf")


def test_read_fake_glm_file_and_cache_roundtrip(tmp_path):
    nc = tmp_path / "fake.nc"
    _write_fake_glm(nc, [35.0, 36.0, 10.0], [-97.0, -96.0, -60.0], [1.5, 0.25, 10.0], [0, 0, 3])
    batch = glm.read_glm_file(nc)
    assert batch.satellite == "G19"
    base = datetime(2025, 5, 16, 23, 0, tzinfo=UTC).timestamp()
    np.testing.assert_allclose(
        batch.time_ns / 1e9, [base + 1.5, base + 0.25, base + 10.0], atol=1e-6
    )
    assert batch.quality.tolist() == [0, 0, 3]

    table = glm.flashes_table([batch], "G19")
    assert table.column_names[:3] == ["time", "lat", "lon"]
    assert table.column("lat").to_pylist() == pytest.approx([36.0, 35.0, 10.0])  # sorted by time

    root = tmp_path / "cache"
    dest = glm.hour_cache_path(root, "G19", date(2025, 5, 16), 23)
    assert dest == root / "glm" / "G19" / "2025" / "136" / "23.parquet"
    dest.parent.mkdir(parents=True)
    pq.write_table(table, dest)

    start = datetime(2025, 5, 16, 23, 0, tzinfo=UTC)
    end = datetime(2025, 5, 17, 0, 0, tzinfo=UTC)
    box = BBox(-100.0, 30.0, -90.0, 40.0)
    events = glm.load_flashes("G19", start, end, box, root, fetch=False)
    assert len(events) == 2
    np.testing.assert_allclose(events.time_unix, [base + 0.25, base + 1.5])
    everything = glm.load_flashes("G19", start, end, None, root, good_only=False, fetch=False)
    assert len(everything) == 3
    assert everything.source == "GLM G19"
    later = glm.load_flashes("G19", start.replace(minute=5), end, None, root, fetch=False)
    assert len(later) == 0
