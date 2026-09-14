"""Line front end."""
import math

import numpy as np

from pc import lines as L
from pc.config import Settings


def _seg(x0, y0, x1, y1):
    return [float(x0), float(y0), float(x1), float(y1)]


def test_merge_joins_a_broken_edge():
    """LSD splits one facade corner into fragments at every balcony; unmerged,
    a length-weighted fit sees noise where there is one long, precise line."""
    seg = np.array([_seg(200, 100 + i * 60, 200, 145 + i * 60) for i in range(6)])
    merged = L.merge_collinear(seg, gap_tol=30.0)
    assert len(merged) == 1
    assert merged[0][3] - merged[0][1] > 340   # 100..445


def test_merge_keeps_genuinely_separate_edges_apart():
    seg = np.array([_seg(200, 100, 200, 400), _seg(600, 100, 600, 400)])
    assert len(L.merge_collinear(seg)) == 2


def test_border_segments_are_dropped():
    """A frame or sensor edge is long, straight and perfectly vertical, so it
    would win a length-weighted vote while saying nothing about the scene."""
    seg = np.array([_seg(1, 10, 1, 500), _seg(400, 100, 400, 500)])
    kept = L.drop_border_segments(seg, 800, 600, margin=3)
    assert len(kept) == 1 and kept[0][0] == 400


def test_orientation_split_and_angular_weighting():
    seg = np.array([_seg(100, 100, 100, 400),          # exactly vertical
                    _seg(100, 100, 160, 400),          # leaning ~11 deg
                    _seg(100, 100, 400, 110)])         # near horizontal
    ls = L.LineSet(seg)
    vert, horiz = L.split_by_orientation(ls, 32.0, 32.0)
    assert len(vert) == 2 and len(horiz) == 1
    # same length would give equal weight; the leaning one must weigh less
    per_px = vert.weight / vert.length
    assert per_px[0] > per_px[1]


def test_angular_prior_is_monotone_and_bounded():
    a = np.radians([0.0, 5.0, 15.0, 30.0])
    w = L.angular_prior(a, math.radians(32.0))
    assert w[0] == 1.0
    assert np.all(np.diff(w) < 0)
    assert np.all(w > 0)
