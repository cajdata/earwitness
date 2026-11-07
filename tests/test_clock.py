"""Start times read from file names and container tags."""

from datetime import UTC, datetime, timedelta

from earwitness.audit.clock import name_time, start_time

MST = timedelta(hours=-7)


def _fixed(offset):
    return lambda _local: offset


def test_name_patterns():
    samsung = name_time("20261104_143750.mp4")
    assert samsung is not None
    assert not samsung.utc
    assert samsung.time == datetime(2026, 11, 4, 14, 37, 50)
    assert name_time("VID_20261104_143750.mp4").time == datetime(2026, 11, 4, 14, 37, 50)
    pixel = name_time("PXL_20261104_213750123.mp4")
    assert pixel is not None
    assert pixel.utc
    assert pixel.time == datetime(2026, 11, 4, 21, 37, 50)
    assert name_time("Screen_Recording_20261104-143750.mp4").time.minute == 37
    assert name_time("2026-11-04 14.37.50.m4a").time == datetime(2026, 11, 4, 14, 37, 50)
    assert name_time("IMG_1234.MOV") is None
    assert name_time("recording.wav") is None
    assert name_time("20261340_250000.mp4") is None  # not a date


def test_samsung_name_offset_and_stop_time_agree():
    tags = {
        "creation_time": "2026-11-04T21:59:05.000000Z",
        "com.android.version": "17",
        "com.samsung.android.utc_offset": "-0700",
    }
    st = start_time("20261104_143750.mp4", tags, 1273.6, local_offset=None)
    assert st is not None
    assert st.exact
    assert not st.assumed_zone
    assert st.utc == datetime(2026, 11, 4, 21, 37, 50, tzinfo=UTC)
    assert st.utc_offset == MST
    assert st.tolerance_s == 2.0
    assert "agrees" in st.note
    assert st.to_dict()["local"] == "2026-11-04T14:37:50"


def test_android_stop_time_fixes_the_zone_of_a_local_name():
    # no offset tag: the name (local) and creation_time minus the length (UTC) agree to
    # 1.4 s at exactly -7 h, which fixes the zone without asking this computer
    tags = {"creation_time": "2026-11-04T21:59:05Z", "com.android.version": "17"}
    st = start_time("VID_20261104_143750.mp4", tags, 1273.6, local_offset=None)
    assert st is not None
    assert st.utc_offset == MST
    assert not st.assumed_zone
    assert st.utc == datetime(2026, 11, 4, 21, 37, 50, tzinfo=UTC)


def test_a_bare_name_takes_this_computers_zone_and_says_so():
    st = start_time("20260704_101500.mp4", {}, 600.0, local_offset=_fixed(timedelta(hours=-6)))
    assert st is not None
    assert st.assumed_zone
    assert st.utc == datetime(2026, 7, 4, 16, 15, 0, tzinfo=UTC)
    assert "check it" in st.note
    assert start_time("20260704_101500.mp4", {}, 600.0, local_offset=None) is None


def test_iphone_creation_date_is_the_start_with_its_offset():
    tags = {
        "com.apple.quicktime.creationdate": "2026-11-04T14:37:50-0700",
        "creation_time": "2026-11-04T21:37:50.000000Z",
    }
    st = start_time("IMG_1234.MOV", tags, 90.0, local_offset=None)
    assert st is not None
    assert st.exact
    assert st.utc == datetime(2026, 11, 4, 21, 37, 50, tzinfo=UTC)
    assert st.utc_offset == MST


def test_a_renamed_android_file_still_has_its_stop_time():
    tags = {"creation_time": "2026-11-04T21:59:05Z", "com.android.version": "17"}
    st = start_time("clip.mp4", tags, 1273.6, local_offset=None)
    assert st is not None
    assert st.exact
    assert abs((st.utc - datetime(2026, 11, 4, 21, 37, 51, tzinfo=UTC)).total_seconds()) < 1.0


def test_an_unknown_creation_time_brackets_the_clip():
    st = start_time("clip.mp4", {"creation_time": "2026-11-04T21:59:05Z"}, 600.0)
    assert st is not None
    assert not st.exact
    assert st.tolerance_s == 305.0
    assert st.utc == datetime(2026, 11, 4, 21, 54, 5, tzinfo=UTC)


def test_nothing_to_go_on():
    assert start_time("clip.wav", {}, 60.0) is None
    assert start_time("clip.mp4", {"creation_time": "1904-01-01T00:00:00Z"}, 60.0) is None
