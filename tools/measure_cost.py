"""What each stage costs, against image size. The basis for any estimate.

Estimating from a guess is worse than not estimating: a progress bar that lies
teaches people to ignore progress bars.
"""
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, r"D:\Coding\Perspective-Correction\src")
SRC = r"D:\Coding\Perspective-Correction\tests\assets\Horizontal\Platte_1.jpg"
TMP = os.environ["TEMP"]

from pc import imageio as IO
from pc import inpaint as FILL
from pc import lines as L
from pc import model as M
from pc import warp as W
from pc.config import Settings
from pc.pipeline import _match_scale

base = cv2.imread(SRC)


def at(mpx):
    """The test image scaled to about *mpx* megapixels."""
    h, w = base.shape[:2]
    s = (mpx * 1e6 / (w * h)) ** 0.5
    return cv2.resize(base, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)


def t(fn, n=1):
    fn()                                     # warm: model load is not the cost
    t0 = time.time()
    for _ in range(n):
        fn()
    return (time.time() - t0) / n


st = Settings()
st.correct_horizontal = True
print(f"{'MPx':>6s} {'detect':>9s} {'warp':>9s} {'telea':>9s} {'lama':>9s}")
for mpx in (1.0, 4.0, 12.0, 24.0):
    img = at(mpx)
    h, w = img.shape[:2]
    real = w * h / 1e6

    def detect():
        gray, sc = IO.analysis_gray(img, st.detect_max_edge)
        L.prepare(gray, st, _match_scale(img, gray), "x")

    gray, sc = IO.analysis_gray(img, st.detect_max_edge)
    ls, vert, horiz, det, info = L.prepare(gray, st, _match_scale(img, gray), "x")
    m = M.estimate(vert, horiz, gray.shape[1], gray.shape[0], st, None)
    f = (m.f or 1000.0) / max(sc, 1e-9)
    H = W.build(w, h, f, m.roll, m.pitch, m.yaw)
    planned = W.plan(w, h, H, st, yaw=m.yaw)
    Ht, ow, oh = planned[0], planned[1], planned[2]

    def warp():
        cv2.warpPerspective(img, Ht, (ow, oh), flags=cv2.INTER_LINEAR)

    out = cv2.warpPerspective(img, Ht, (ow, oh), flags=cv2.INTER_LINEAR)
    hole = W.filled_region(Ht, w, h, ow, oh)

    def fill(mode):
        s2 = st.replace(fill=mode)
        return lambda: FILL.fill(out.copy(), hole, s2)

    row = [t(detect), t(warp)]
    for mode in ("telea", "lama"):
        try:
            row.append(t(fill(mode)))
        except Exception as e:
            row.append(float("nan"))
    print(f"{real:6.1f} " + " ".join(f"{v:8.2f}s" for v in row)
          + f"   (Warp-Leinwand {ow}x{oh} = {ow*oh/1e6:.1f} MPx)")
