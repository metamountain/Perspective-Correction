"""One image, end to end."""
from __future__ import annotations

import math
import os
import time

import numpy as np

from . import imageio as io
from . import lines as L
from . import model as M
from . import preview as PV
from . import warp as W

OK, SKIPPED, ERROR = "OK", "SKIPPED", "ERROR"


class Result:
    __slots__ = ("status", "reason", "src", "dst", "roll_deg", "pitch_deg",
                 "confidence", "focal_35mm", "focal_source", "coverage",
                 "n_lines", "n_inliers", "seconds", "detector", "clamped",
                 "out_size", "diagnostics")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def line(self) -> str:
        name = os.path.basename(self.src)
        if self.status == ERROR:
            return f"ERROR   {name}  {self.reason}"
        if self.status == SKIPPED:
            return f"SKIPPED {name}  {self.reason}"
        mask = ""
        d = self.diagnostics or {}
        if d.get("mask_refused"):
            mask = f" mask=REFUSED(would lose {d.get('evidence_lost', 0.0) * 100:.0f}% lines)"
        elif d.get("mask_note"):
            mask = (f" mask={d.get('mask_share', 0.0) * 100:.0f}%"
                    f"(-{d.get('evidence_lost', 0.0) * 100:.0f}% lines)")
        return (f"OK      {name}  roll={self.roll_deg:+.2f}deg pitch={self.pitch_deg:+.2f}deg "
                f"conf={self.confidence:.2f} f={self.focal_35mm:.0f}mm({self.focal_source}) "
                f"keep={self.coverage * 100:.0f}%{mask} {self.out_size[0]}x{self.out_size[1]} "
                f"{self.seconds:.2f}s" + ("  [clamped]" if self.clamped else ""))

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__ if k != "diagnostics"}


def _match_scale(bgr, gray):
    """Colour copy at the analysis resolution, for the mask producers."""
    import cv2
    if bgr.shape[:2] == gray.shape[:2]:
        return bgr
    return cv2.resize(bgr, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_AREA)


def analyse(bgr, settings, exif_focal_px=None, image_path=""):
    """Detection + model for an already loaded image.  Returns
    ``(model, vert, horiz, scale, detector)`` with the focal length in
    full-resolution pixels."""
    gray, scale = io.analysis_gray(bgr, settings.detect_max_edge)
    small = _match_scale(bgr, gray)
    _, vert, horiz, detector, info = L.prepare(gray, settings, small, image_path)
    gh, gw = gray.shape[:2]
    exif_small = exif_focal_px * scale if exif_focal_px else None
    m = M.estimate(vert, horiz, gw, gh, settings, exif_small)
    if m.f:
        m.f = m.f / scale                       # back to full resolution pixels
    m.detect_info = info
    return m, vert, horiz, scale, detector


def process(src_path, dst_path, settings, debug_dir=None, dry_run=False):
    t0 = time.time()
    base = dict(src=src_path, dst=dst_path, roll_deg=0.0, pitch_deg=0.0,
                confidence=0.0, focal_35mm=0.0, focal_source="none", coverage=1.0,
                n_lines=0, n_inliers=0, detector="-", clamped=False,
                out_size=(0, 0), diagnostics={})
    try:
        src = io.load(src_path)
    except Exception as exc:
        return Result(status=ERROR, reason=f"cannot read ({exc})",
                      seconds=time.time() - t0, **base)

    bgr = src.bgr
    h, w = bgr.shape[:2]
    base["out_size"] = (w, h)
    try:
        exif_f = io.focal_px_from_exif(src, w, h) if settings.use_exif_focal else None
        m, vert, horiz, scale, detector = analyse(bgr, settings, exif_f, src_path)
        info = m.detect_info
    except Exception as exc:
        return Result(status=ERROR, reason=f"analysis failed ({exc})",
                      seconds=time.time() - t0, **base)

    # fold what the mask did into the diagnostics the log and report already
    # carry, rather than opening a second channel for it.  Until this existed a
    # mask covering 0.0% of the frame was indistinguishable from a working one.
    if info.get("mask_note"):
        for key in ("mask_note", "mask_share", "evidence_lost", "mask_refused"):
            # copied even when zero: "0% of the frame" is the finding, not the
            # absence of one
            m.diagnostics[key] = info.get(key, 0.0)
    base.update(n_lines=len(vert) + len(horiz), detector=detector,
                confidence=m.confidence, diagnostics=m.diagnostics)
    if m.f:
        base.update(focal_35mm=M.focal_35mm_from_px(m.f, w, h), focal_source=m.f_source)
    if m.vert_inliers is not None:
        base["n_inliers"] = int(np.sum(m.vert_inliers))

    def finish_skip(reason):
        if debug_dir:
            _write_debug(debug_dir, src_path, bgr, vert, horiz, m, scale,
                         f"SKIPPED  {reason}", info=info)
        if dst_path and not dry_run and os.path.abspath(dst_path) != os.path.abspath(src_path):
            io.copy_through(src_path, dst_path)
        return Result(status=SKIPPED, reason=reason, seconds=time.time() - t0, **base)

    if m.confidence < settings.min_confidence:
        why = m.diagnostics.get("reason", "low confidence")
        weakest = m.diagnostics.get("weakest_term")
        detail = f"; weakest: {weakest}" if weakest else ""
        return finish_skip(f"{why} (conf={m.confidence:.2f} < "
                           f"{settings.min_confidence:.2f}{detail})")

    guessed = m.f_source in ("default", "prior", "none", "refined")
    roll, pitch, clamped = W.limit(m.roll, m.pitch, settings, guessed)
    base["clamped"] = clamped
    if clamped and settings.refuse_beyond_limit:
        # A correction that runs past the configured limit is not a correction
        # to be trimmed to fit -- it is a sign the estimate is about something
        # other than a facade, and applying the largest allowed warp to it is
        # the worst available answer.  Found on a photograph of a railway
        # station ceiling: a coffered ceiling has a clean bundle of parallel
        # lines and a perfectly good vanishing point, so every confidence factor
        # scored well (0.57) while the model quietly assumed the ceiling grid
        # was the world vertical.  The result was pitch pinned to the -20 deg
        # clamp and 41 % of the frame thrown away.
        #
        # Clamping is the wrong instinct here in exactly the way this project
        # keeps documenting: it turns "I do not believe this" into "I will do as
        # much of it as I am allowed to".
        # report the values that actually breached -- after strength and the
        # uncertain-focal damping -- not the raw estimate, or the numbers will
        # not explain the decision they caused
        want_r, want_p = W.limit(m.roll, m.pitch, settings.replace(
            max_roll_deg=1e6, max_pitch_deg=1e6), guessed)[:2]
        return finish_skip(
            f"correction beyond the limit (roll {math.degrees(want_r):+.1f}deg, "
            f"pitch {math.degrees(want_p):+.1f}deg; caps are "
            f"{settings.max_roll_deg:.0f}/{settings.max_pitch_deg:.0f}deg)")
    total_deg = math.degrees(math.hypot(roll, pitch))
    base.update(roll_deg=math.degrees(roll), pitch_deg=math.degrees(pitch))
    if total_deg < settings.min_correction_deg:
        return finish_skip(f"already upright ({total_deg:.2f}deg < "
                           f"{settings.min_correction_deg:.2f}deg)")

    H = W.build(w, h, m.f, roll, pitch)
    planned = W.plan(w, h, H, settings)
    if planned is None:
        return finish_skip("crop would be degenerate")
    H_total, ow, oh, coverage, area_ratio = planned
    if area_ratio > settings.max_area_ratio:
        return finish_skip(f"warp too extreme (area x{area_ratio:.1f})")
    base.update(coverage=coverage, out_size=(ow, oh))

    if dry_run:
        if debug_dir:
            _write_debug(debug_dir, src_path, bgr, vert, horiz, m, scale,
                         _label(base, total_deg), info=info)
        return Result(status=OK, reason="dry run", seconds=time.time() - t0, **base)

    try:
        out = W.apply(bgr, H_total, ow, oh, settings)
        os.makedirs(os.path.dirname(os.path.abspath(dst_path)) or ".", exist_ok=True)
        io.save(dst_path, out, src, settings)
    except Exception as exc:
        return Result(status=ERROR, reason=f"write failed ({exc})",
                      seconds=time.time() - t0, **base)

    if debug_dir:
        _write_debug(debug_dir, src_path, bgr, vert, horiz, m, scale,
                     _label(base, total_deg), after=out, info=info)
    return Result(status=OK, reason="", seconds=time.time() - t0, **base)


def _label(base, total_deg):
    return (f"OK  roll={base['roll_deg']:+.2f} pitch={base['pitch_deg']:+.2f} "
            f"(total {total_deg:.2f}deg)\nconf={base['confidence']:.2f}  "
            f"f={base['focal_35mm']:.0f}mm ({base['focal_source']})  "
            f"lines={base['n_inliers']}/{base['n_lines']}")


def _write_debug(debug_dir, src_path, bgr, vert, horiz, m, scale, text, after=None,
                 info=None):
    import cv2
    os.makedirs(debug_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src_path))[0]
    ov = PV.overlay(bgr, vert, horiz, m, scale, text, info=info)
    s = min(1.0, 1400.0 / max(ov.shape[:2]))
    if s < 1.0:
        ov = cv2.resize(ov, (int(ov.shape[1] * s), int(ov.shape[0] * s)),
                        interpolation=cv2.INTER_AREA)
    cv2.imwrite(os.path.join(debug_dir, f"{stem}_lines.jpg"), ov,
                [cv2.IMWRITE_JPEG_QUALITY, 88])
    if after is not None:
        cv2.imwrite(os.path.join(debug_dir, f"{stem}_compare.jpg"),
                    PV.side_by_side(bgr, after), [cv2.IMWRITE_JPEG_QUALITY, 88])
