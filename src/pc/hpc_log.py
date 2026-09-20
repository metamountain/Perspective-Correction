"""Horizontal correction verification log.

Writes per-image JSON sidecars and an appended CSV summary into a dedicated
folder (default ``hpc_save/``) whenever ``--horizontal`` is active.  The data
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
]


def _strip_fractions(strip, width):
    """A facade strip as two fractions of the width, or ``None``.

    Full-resolution pixels in, fractions out -- the only form that still means
    the same thing after the analysis resolution changes.
    """
    if not strip or not width:
        return None
    return [float(strip[0]) / float(width), float(strip[1]) / float(width)]


def remember_strip(folder: str, stem: str, strip, width) -> str:
    """Keep the facade strip beside the photograph it was drawn on.

    A strip is a statement about ONE picture -- where that building's corner
    falls -- so it belongs with that picture. The CLI's ``--roi-x`` cannot serve:
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
    rec["strip"] = _strip_fractions(strip, width)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2)
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
        # Already fractions on the Result -- that is the unit --roi-x takes
        # and the unit this file stores, so there is nothing to convert.
        "strip": ([float(v) for v in result.roi_x]
                  if getattr(result, "roi_x", None) else None),
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
    }
