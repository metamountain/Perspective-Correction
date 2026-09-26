"""Remote control for the review window: the real App, off-screen.

User-directed 2026-09-26: verify on what the program itself produces, never on
a hand-rebuilt pipeline. This opens the actual ``App`` (the user's remembered
settings: detector, fill, mask), loads each photograph into the actual review
panel, presses the same controls a person would -- horizontal auto, the facade
strip, Auto crop, Save -- and records what the window reports. The saved
``*_corr.jpg`` and ``correction_log/`` are written by the panel's own ``_save``.

    python tools/drive_review.py                       # tests/assets/Horizontal
    python tools/drive_review.py some/folder --out debug_out --roi
    python tools/drive_review.py tests/assets/Horizontal/altbau.jpeg --strip 0.0 0.45

Output: ``<out>/<stem>_corr.jpg`` per photograph and ``<out>/summary.csv``.
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

EXTS = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff")


def _pump(app, n=10):
    for _ in range(n):
        app.update()
        time.sleep(0.02)


def _files(target):
    if os.path.isfile(target):
        return [target]
    out = []
    for p in sorted(glob.glob(os.path.join(target, "*"))):
        low = p.lower()
        if low.endswith(EXTS) and "_corr." not in low:
            out.append(p)
    return out


def _roi_strips():
    path = os.path.join(ROOT, "tests", "assets", "roi.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    strips = {}
    for key, val in data.items():
        if key.startswith("_") or not isinstance(val, dict) or "roi" not in val:
            continue
        strips[os.path.splitext(key)[0]] = tuple(float(v) for v in val["roi"])
    return strips


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("target", nargs="?",
                    default=os.path.join(ROOT, "tests", "assets", "Horizontal"))
    ap.add_argument("--out", default=os.path.join(ROOT, "debug_out"))
    ap.add_argument("--no-ha", action="store_true", help="leave horizontal auto off")
    ap.add_argument("--no-autocrop", action="store_true", help="do not press Auto crop")
    ap.add_argument("--roi", action="store_true",
                    help="use the facade strips recorded in tests/assets/roi.json")
    ap.add_argument("--strip", nargs=2, type=float, metavar=("X0", "X1"),
                    help="one facade strip (fractions of width) for every photograph")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # umlauts in file names
    except Exception:
        pass

    files = _files(args.target)
    if not files:
        print(f"no photographs in {args.target}")
        return 1
    os.makedirs(args.out, exist_ok=True)
    roi = _roi_strips() if args.roi else {}

    # correction_log/ is written relative to the working directory, as in the app
    os.chdir(ROOT)
    from pc import gui as GUI
    from pc.gui import App
    from pc import warp as W

    # A modal dialog on an off-screen window would wait forever for a click
    # nobody can make. Record what the window WOULD have said instead.
    dialogs = []
    GUI.messagebox.showerror = lambda title, msg, **_k: dialogs.append(f"{title}: {msg}")
    GUI.messagebox.showwarning = lambda title, msg, **_k: dialogs.append(f"{title}: {msg}")
    GUI.messagebox.askyesno = lambda *_a, **_k: False

    app = App(start_maximized=False)
    app.geometry("1920x1080-4000+0")
    _pump(app, 20)
    r = app.review
    rows = []
    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0]
        dst = os.path.join(args.out, f"{stem}_corr.jpg")
        if os.path.isfile(dst):
            os.remove(dst)          # a stale file must not read as this run's save
        t0 = time.time()
        row = {"file": os.path.basename(path)}
        try:
            r.load(path, app._settings(), dst, overwrite=False)
            _pump(app, 15)
            s = r.session
            if s is None:
                raise RuntimeError("the panel did not open a session")
            row["diverge_prompt"] = bool(s.directions_diverge()) if s.strip is None else ""
            if not args.no_ha and not s.settings.correct_horizontal:
                r.v_correct_horizontal.set(True)
                r._on_horizontal_toggle()
                _pump(app, 5)
            strip = tuple(args.strip) if args.strip else roi.get(stem)
            if strip:
                r.v_strip.set(True)
                r.v_strip_x0.set(round(strip[0] * 100.0, 2))
                r.v_strip_x1.set(round(strip[1] * 100.0, 2))
                r._apply_strip()
                _pump(app, 5)
            if not args.no_autocrop:
                r.v_autocrop.set(True)
                r._auto_crop()
                _pump(app, 5)
            roll, pitch, f, _ = s.current_angles()
            yaw = s.current_yaw()
            ow, oh = s.output_size()
            crop = s.crop_rect
            row.update(
                src=f"{s.w}x{s.h}", roll=round(math.degrees(roll), 2),
                pitch=round(math.degrees(pitch), 2), yaw=round(math.degrees(yaw), 2),
                x_scale=round(W.yaw_x_scale(yaw), 3),
                confidence=round(s.model.confidence, 3) if s.model else "",
                skip=s.would_skip() or "",
                strip="" if s.strip is None else f"{s.strip[0]:.3f}-{s.strip[1]:.3f}",
                crop="" if crop is None else " ".join(f"{v:.3f}" for v in crop),
                canvas=f"{ow}x{oh}")
            del dialogs[:]
            r._save()
            _pump(app, 5)
            if dialogs:
                row["dialog"] = " | ".join(dialogs)
            row["saved"] = os.path.basename(dst) if os.path.isfile(dst) else "NOT WRITTEN"
            try:
                import cv2
                import numpy as np
                img = cv2.imdecode(np.fromfile(dst, dtype=np.uint8), cv2.IMREAD_COLOR)
                row["out"] = f"{img.shape[1]}x{img.shape[0]}"
            except Exception:
                row["out"] = ""
            try:
                with open(os.path.join("correction_log", f"{stem}.json"), encoding="utf-8") as fh:
                    rec = json.load(fh)
                after = (rec.get("after") or {}).get("lines") or {}
                row["after_v_lean"] = after.get("vertical_lean_median_deg")
                row["after_h_slope"] = after.get("horizontal_slope_median_deg")
            except (OSError, ValueError):
                pass
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["seconds"] = round(time.time() - t0, 1)
        rows.append(row)
        print("  ".join(f"{k}={v}" for k, v in row.items()), flush=True)

    try:
        app.destroy()
    except Exception:
        pass
    keys = []
    for row in rows:
        keys += [k for k in row if k not in keys]
    with open(os.path.join(args.out, "summary.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    print(f"# {len(rows)} photograph(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
