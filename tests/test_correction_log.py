"""Tests for correction_log: JSON sidecar + CSV append."""
import csv
import json
import os
import tempfile

from pc import correction_log as CLOG


class _FakeResult:
    def __init__(self, **kw):
        self.status = kw.get("status", "OK")
        self.src = kw.get("src", "D:\\photos\\IMG_0421.jpg")
        self.roll_deg = kw.get("roll_deg", 1.2)
        self.pitch_deg = kw.get("pitch_deg", -3.1)
        self.yaw_deg = kw.get("yaw_deg", 7.32)
        self.confidence = kw.get("confidence", 0.74)
        self.focal_35mm = kw.get("focal_35mm", 28.0)
        self.focal_source = kw.get("focal_source", "exif")
        self.clamped = kw.get("clamped", False)


def test_build_record_structure():
    r = _FakeResult()
    before = {"n_lines": 14, "yaw_deg": -7.32, "support": 0.62}
    after = {"n_lines": 12, "yaw_deg": -0.41, "support": 0.58}
    rec = CLOG.build_record(r, before, after, "1.01")
    assert rec["file"] == "IMG_0421.jpg"
    assert rec["version"] == "1.01"
    assert rec["status"] == "OK"
    assert rec["before"]["yaw_deg"] == -7.32
    assert rec["after"]["yaw_deg"] == -0.41
    assert rec["correction"]["yaw_deg"] == 7.32
    assert rec["correction"]["confidence"] == 0.74
    assert "timestamp" in rec


def test_write_record_creates_json():
    with tempfile.TemporaryDirectory() as tmp:
        r = _FakeResult()
        before = {"n_lines": 5, "yaw_deg": -2.0, "support": 0.5}
        after = {"n_lines": 4, "yaw_deg": 0.1, "support": 0.6}
        rec = CLOG.build_record(r, before, after, "1.01")
        CLOG.write_record(tmp, "IMG_0421", rec)
        path = os.path.join(tmp, "IMG_0421.json")
        assert os.path.exists(path)
        with open(path) as fh:
            data = json.load(fh)
        assert data["file"] == "IMG_0421.jpg"
        assert data["before"]["n_lines"] == 5


def test_write_record_overwrites():
    with tempfile.TemporaryDirectory() as tmp:
        r = _FakeResult(yaw_deg=5.0)
        rec1 = CLOG.build_record(r, {"n_lines": 3, "yaw_deg": -5.0, "support": 0.4},
                                {"n_lines": 3, "yaw_deg": 0.2, "support": 0.5}, "1.0")
        CLOG.write_record(tmp, "test", rec1)
        r2 = _FakeResult(yaw_deg=8.0)
        rec2 = CLOG.build_record(r2, {"n_lines": 6, "yaw_deg": -8.0, "support": 0.7},
                                {"n_lines": 5, "yaw_deg": -0.3, "support": 0.6}, "1.01")
        CLOG.write_record(tmp, "test", rec2)
        with open(os.path.join(tmp, "test.json")) as fh:
            data = json.load(fh)
        assert data["correction"]["yaw_deg"] == 8.0


def test_append_csv_creates_header():
    with tempfile.TemporaryDirectory() as tmp:
        r = _FakeResult()
        rec = CLOG.build_record(r, {"n_lines": 5, "yaw_deg": -2.0, "support": 0.5},
                               {"n_lines": 4, "yaw_deg": 0.1, "support": 0.6}, "1.01")
        row = CLOG.record_to_row(rec)
        CLOG.append_csv(tmp, row)
        path = os.path.join(tmp, "summary.csv")
        assert os.path.exists(path)
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        assert rows[0]["file"] == "IMG_0421.jpg"
        assert rows[0]["before_yaw_deg"] == "-2.0"


def test_append_csv_appends():
    with tempfile.TemporaryDirectory() as tmp:
        r1 = _FakeResult(yaw_deg=3.0)
        rec1 = CLOG.build_record(r1, {"n_lines": 5, "yaw_deg": -3.0, "support": 0.5},
                                {"n_lines": 4, "yaw_deg": 0.1, "support": 0.6}, "1.01")
        CLOG.append_csv(tmp, CLOG.record_to_row(rec1))

        r2 = _FakeResult(yaw_deg=7.0)
        rec2 = CLOG.build_record(r2, {"n_lines": 8, "yaw_deg": -7.0, "support": 0.6},
                                {"n_lines": 7, "yaw_deg": -0.5, "support": 0.7}, "1.01")
        CLOG.append_csv(tmp, CLOG.record_to_row(rec2))

        path = os.path.join(tmp, "summary.csv")
        with open(path, newline="") as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 2
        assert rows[0]["yaw_applied_deg"] == "3.0"
        assert rows[1]["yaw_applied_deg"] == "7.0"


def test_record_to_row_flattens():
    r = _FakeResult()
    rec = CLOG.build_record(r, {"n_lines": 5, "yaw_deg": -2.0, "support": 0.5},
                           {"n_lines": 4, "yaw_deg": 0.1, "support": 0.6}, "1.01")
    row = CLOG.record_to_row(rec)
    assert row["before_yaw_deg"] == -2.0
    assert row["after_yaw_deg"] == 0.1
    assert row["yaw_applied_deg"] == 7.32
    assert row["focal_35mm"] == 28.0
    assert row["status"] == "OK"


def test_lean_stats_signs_a_right_leaning_top_and_a_right_climbing_line_positive():
    import numpy as np
    # y points down: a vertical whose top (small y) sits 5 px right of its
    # foot, and a horizontal that ends 5 px higher than it starts.
    seg = np.array([[100.0, 200.0, 105.0, 100.0],      # vertical, top right
                    [100.0, 300.0, 200.0, 295.0]])     # horizontal, climbing
    s = CLOG._lean_stats(seg)
    assert s["n_vertical"] == 1 and s["n_horizontal"] == 1
    assert s["vertical_lean_median_deg"] > 2.5
    assert s["horizontal_slope_median_deg"] > 2.5
    # the same segments drawn end-to-start must give the same answer
    s2 = CLOG._lean_stats(seg[:, [2, 3, 0, 1]])
    assert s2 == s


def test_append_csv_sets_aside_a_file_with_an_older_header():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "summary.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write("file,timestamp\nold.jpg,2026-09-01\n")
        rec = CLOG.build_record(_FakeResult(), {"n_lines": 1}, {"n_lines": 1}, "1.2")
        CLOG.append_csv(tmp, CLOG.record_to_row(rec))
        with open(path, newline="", encoding="utf-8") as fh:
            assert next(csv.reader(fh)) == CLOG.CSV_HEADER
        kept = [n for n in os.listdir(tmp) if n.startswith("summary.") and n != "summary.csv"]
        assert len(kept) == 1


def test_lean_stats_weights_by_length_so_short_edges_cannot_outvote_long_ones():
    import numpy as np
    # the csm_klassik case: two long level cornices, two short 27 deg edges
    def seg(length, deg):
        a = np.radians(deg)
        return [0.0, 500.0, length * np.cos(a), 500.0 - length * np.sin(a)]
    s = CLOG._lean_stats(np.array([seg(1151, 0.0), seg(1330, 0.4),
                                   seg(164, 27.2), seg(110, 27.9)]))
    assert abs(s["horizontal_slope_median_deg"]) < 1.0
