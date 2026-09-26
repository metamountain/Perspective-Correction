"""Horizontal correction verification log.

Writes per-image JSON sidecars and an appended CSV summary into a dedicated
folder (``correction_log/``) whenever ``--horizontal`` is active or the
review panel saves, plus ``<stem>.lines.json`` with the LSD and M-LSD segments of the
source and of the written file.  The data
serves two purposes: verifying that the residual yaw after warp is near zero,
and building a learning dataset of before/after measurements for algorithm
improvement.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime

CSV_HEADER = [
    "file", "timestamp", "version",
    "before_yaw_deg", "before_horiz_lines", "before_support",
    "after_yaw_deg", "after_horiz_lines", "after_support",
    "roll_deg", "pitch_deg", "yaw_applied_deg", "confidence",
    "focal_35mm", "focal_source", "status",
    # Fractions of the image width, not pixels. "Pixels" is three different
    # numbers here -- full-resolution in a Result, analysis-resolution in a
    # session, displayed in the window -- and a stored value that needs to be
    # told which of the three it is has not been stored.
    "strip_x0", "strip_x1",
    # Signed, length-weighted median lean from LSD (M-LSD reads verticals
    # -0.6 deg off; its numbers are nested under "mlsd" in the JSON record).
    "before_v_lean_deg", "before_h_slope_deg",
    "after_v_lean_deg", "after_h_slope_deg",
]



def remember_strip(folder: str, stem: str, strip) -> str:
    """Keep the facade strip beside the photograph it was drawn on.

    A strip is a statement about ONE picture -- where that building's corner
    falls -- so it belongs with that picture. The CLI's ``--strip`` cannot serve:
    it is one pair of numbers for a whole run, and thirty facades have thirty
    different corners.

    Merged into any existing record rather than replacing it, so a correction
    run's measurements survive a later hand-placed strip and the other way
    round.
    """
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{stem}.json")
    rec = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            rec = {}                    # unreadable: replace rather than refuse
    rec["file"] = stem
    # Already fractions: that is the one unit a strip is kept in, from the
    # session through the Result to this file.
    rec["strip"] = [float(strip[0]), float(strip[1])] if strip else None
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2)
    return path


LINE_WINDOW_DEG = 30.0
"""A segment counts as vertical/horizontal within this many degrees of the axis.

Wide on purpose: the BEFORE image still has its perspective in it, and a
facade's horizontals there can run 20 deg and more off level.
"""


def _lean_stats(seg):
    """Signed lean of near-vertical and near-horizontal segments, in degrees.

    Signed, not absolute: a systematic tilt (Ledger item 15, -2.2 deg on
    altbau.jpeg) shows as a median well away from zero, while scatter around a
    good correction averages out. Vertical lean is positive when the top leans
    right; horizontal slope is positive when the line climbs to the right
    (image y points down, hence the sign flip).
    """
    import numpy as np
    if len(seg) == 0:
        return {"n_vertical": 0, "vertical_lean_median_deg": None,
                "n_horizontal": 0, "horizontal_slope_median_deg": None}
    dx = seg[:, 2] - seg[:, 0]
    dy = seg[:, 3] - seg[:, 1]
    ang = np.degrees(np.arctan2(dy, dx))            # -180..180, y down
    ang = (ang + 90.0) % 180.0 - 90.0               # -90..90, direction-free
    horiz = np.abs(ang) <= LINE_WINDOW_DEG
    vert = np.abs(ang) >= 90.0 - LINE_WINDOW_DEG
    h_slope = -ang[horiz]
    v_lean = np.where(ang[vert] > 0, ang[vert] - 90.0, ang[vert] + 90.0)
    length = np.hypot(dx, dy)

    def med(a, w):
        # LENGTH-weighted median (2026-09-26, measured): on the cropped
        # csm_klassik output M-LSD returned four "horizontals" -- two cornices
        # of 1151 and 1330 px at 0.0 and 0.4 deg, two dormer edges of 110 and
        # 164 px at 27 deg -- and the plain median of four reported 13.8 deg
        # for a facade that is level. A long edge is more evidence than a
        # short one; weighted, the same four give 0.4 deg.
        if len(a) == 0:
            return None
        order = np.argsort(a)
        cw = np.cumsum(w[order])
        return round(float(a[order][np.searchsorted(cw, 0.5 * cw[-1])]), 3)

    return {"n_vertical": int(vert.sum()),
            "vertical_lean_median_deg": med(v_lean, length[vert]),
            "n_horizontal": int(horiz.sum()),
            "horizontal_slope_median_deg": med(h_slope, length[horiz])}


def line_record(bgr, settings, detector: str = "mlsd") -> dict:
    """Detected segments of one image, as data rather than as a picture.

    User-directed 2026-09-26 ("mlsd Vektoren mitspeichern"): the saved lines
    are what lets a correction be checked by arithmetic -- residual lean,
    per-facade slope, whether two eaves end at the same height -- instead of
    by squinting at a render. Runs at ``detect_max_edge``, the resolution the
    estimator itself sees. Coordinates are FRACTIONS of width/height (the same
    unit the strip is stored in), so a record needs no second number to be
    read. Raw detector output apart from the length floor and the border
    guard: no mask, no merge, no classification -- the point is an independent
    measurement, not a second copy of the estimator's opinion.
    """
    import cv2
    import numpy as np
    from . import imageio as IO
    from . import lines as L
    from . import geometry as G
    gray, _ = IO.analysis_gray(bgr, settings.detect_max_edge)
    h, w = gray.shape[:2]
    small = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)
    min_len = max(8.0, settings.min_line_length_frac * min(w, h))
    seg, name = L.detect_segments(gray, min_len, detector, small, settings)
    seg = np.asarray(seg, dtype=float).reshape(-1, 4)
    if len(seg):
        seg = seg[G.segment_lengths(seg) >= min_len]
    if len(seg):
        seg = L.drop_border_segments(seg, w, h, settings.border_margin_px)
    rec = {"detector": name, "width": w, "height": h}
    rec.update(_lean_stats(seg))
    norm = seg / np.array([w, h, w, h], dtype=float) if len(seg) else seg
    rec["segments"] = [[round(float(v), 5) for v in s] for s in norm]
    return rec


def line_records(bgr, settings) -> dict:
    """``{"lsd": ..., "mlsd": ...}`` -- both detectors' segments of one image.

    M-LSD was asked for by name (2026-09-26); LSD rides along because M-LSD
    turned out to read EXACT verticals as -0.62 deg (synthetic grid, same
    day; LSD reads 0.001) while its horizontals are fine. So the summary
    numbers come from LSD and M-LSD's segments are kept for what it is good
    at -- long structural edges -- with its bias on record.
    """
    return {"lsd": line_record(bgr, settings, "lsd"),
            "mlsd": line_record(bgr, settings, "mlsd")}


def line_summary(recs: dict) -> dict:
    """The record/CSV summary of `line_records`: LSD stats, M-LSD nested."""
    strip = lambda r: {k: v for k, v in r.items() if k != "segments"}
    out = strip(recs["lsd"])
    out["mlsd"] = strip(recs["mlsd"])
    return out


def write_lines(folder: str, stem: str, before: dict, after: dict) -> str:
    """Write the segment sidecar ``<stem>.lines.json`` beside the record."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{stem}.lines.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"file": stem, "before": before, "after": after}, fh)
    return path


def write_record(folder: str, stem: str, record: dict):
    """Write (or overwrite) the per-image JSON sidecar."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{stem}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2, default=str)


def append_csv(folder: str, row: dict):
    """Append one row to summary.csv, creating the header if new."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "summary.csv")
    if os.path.exists(path):
        # A header written by an older version would misalign every new
        # column; keep that file under a dated name and start a fresh one.
        with open(path, newline="", encoding="utf-8") as fh:
            first = next(csv.reader(fh), [])
        if first != CSV_HEADER:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            os.replace(path, os.path.join(folder, f"summary.{stamp}.csv"))
    is_new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_HEADER, extrasaction="ignore")
        if is_new:
            w.writeheader()
        w.writerow(row)


def build_record(result, before: dict, after: dict, version: str) -> dict:
    """Assemble the full record from a pipeline Result and measurements."""
    return {
        "file": os.path.basename(result.src),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "version": version,
        "status": result.status,
        "before": before,
        "after": after,
        "correction": {
            "roll_deg": round(result.roll_deg or 0.0, 3),
            "pitch_deg": round(result.pitch_deg or 0.0, 3),
            "yaw_deg": round(result.yaw_deg or 0.0, 3),
            "confidence": round(result.confidence or 0.0, 4),
            "focal_35mm": result.focal_35mm,
            "focal_source": result.focal_source,
            "clamped": bool(result.clamped),
        },
        # Already fractions on the Result -- that is the unit --strip takes
        # and the unit this file stores, so there is nothing to convert.
        "strip": ([float(v) for v in result.strip]
                  if getattr(result, "strip", None) else None),
    }


def record_to_row(record: dict) -> dict:
    """Flatten a record into the CSV row shape."""
    c = record.get("correction", {})
    b = record.get("before", {})
    a = record.get("after", {})
    return {
        "file": record["file"],
        "timestamp": record["timestamp"],
        "version": record["version"],
        "before_yaw_deg": b.get("yaw_deg"),
        "before_horiz_lines": b.get("n_lines"),
        "before_support": b.get("support"),
        "after_yaw_deg": a.get("yaw_deg"),
        "after_horiz_lines": a.get("n_lines"),
        "after_support": a.get("support"),
        "roll_deg": c.get("roll_deg"),
        "pitch_deg": c.get("pitch_deg"),
        "yaw_applied_deg": c.get("yaw_deg"),
        "confidence": c.get("confidence"),
        "focal_35mm": c.get("focal_35mm"),
        "focal_source": c.get("focal_source"),
        "status": record["status"],
        "strip_x0": (record.get("strip") or [None, None])[0],
        "strip_x1": (record.get("strip") or [None, None])[1],
        "before_v_lean_deg": (b.get("lines") or {}).get("vertical_lean_median_deg"),
        "before_h_slope_deg": (b.get("lines") or {}).get("horizontal_slope_median_deg"),
        "after_v_lean_deg": (a.get("lines") or {}).get("vertical_lean_median_deg"),
        "after_h_slope_deg": (a.get("lines") or {}).get("horizontal_slope_median_deg"),
    }
