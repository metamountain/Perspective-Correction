"""What the estimator *wants*, with every cap and gate switched off.

A cap can only be re-decided against the corrections it actually refuses, and
that number is not visible while the cap is in force -- the refusal happens
before anything is recorded.  So this runs the pool with `max_*_deg` and
`min_confidence` opened right up and reports the distribution, which is the
only evidence that can move a default.

    python tools/cap_sweep.py            # pitch, the whole assets pool
    python tools/cap_sweep.py roll

It answers one question honestly, including when the answer is "this cap never
binds, so there is nothing here to decide from" -- which is what it said about
`max_pitch_deg` on 2026-09-15.
"""

from __future__ import annotations

import glob
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from pc.config import Settings                      # noqa: E402
from pc.imageio import READABLE                     # noqa: E402
import pc.imageio as io                             # noqa: E402
from pc.pipeline import analyse                     # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "tests", "assets")
CUTS = (10, 20, 30, 45, 60)


def _uncapped() -> Settings:
    """Every magnitude gate opened, so nothing is refused before it is seen."""
    s = Settings()
    s.max_pitch_deg = s.max_roll_deg = s.max_horizontal_deg = 1e6
    s.min_confidence = 0.0
    return s


def sweep(field: str = "pitch"):
    files = [f for f in sorted(glob.glob(os.path.join(ASSETS, "*")))
             if os.path.splitext(f)[1].lower() in READABLE]
    s = _uncapped()
    rows, unusable = [], 0
    for f in files:
        try:
            model = analyse(io.load(f).bgr, s, image_path=f)[0]
            deg = getattr(model, field + "_deg", None)
            if deg is None:
                deg = math.degrees(getattr(model, field))
        except Exception:                            # a file the pool cannot use
            unusable += 1
            continue
        rows.append((abs(deg), getattr(model, "confidence", -1.0),
                     os.path.basename(f)))
    rows.sort(reverse=True)

    print(f"{field}: measured {len(rows)} assets, {unusable} unusable")
    if not rows:
        return rows
    for cut in CUTS:
        n = sum(1 for d, _, _ in rows if d > cut)
        print(f"  |{field}| > {cut:3d} deg : {n:3d}  ({n / len(rows):.0%})")
    print(f"  largest {rows[0][0]:.2f} deg  (conf {rows[0][1]:.2f})  {rows[0][2][:48]}")
    print("  top ten: " + ", ".join(f"{d:.1f}" for d, _, _ in rows[:10]))
    return rows


if __name__ == "__main__":
    sweep(sys.argv[1] if len(sys.argv) > 1 else "pitch")
