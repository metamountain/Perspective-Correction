"""All tunables in one place.

Defaults are chosen for hand-held architectural photography: buildings, wide-ish
lenses, a mixture of images that need correcting and images that do not.  The
guiding rule is the one from the brief -- when in doubt, leave the photo alone.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, fields


@dataclass
class Settings:
    # ---- detection ----
    detector: str = "auto"              # auto | lsd | fld | hough | mlsd | hybrid | union
    hybrid_dist_tol: float = 8.0
    mlsd_model: str = ""                # path, or a name in models/
    mlsd_score_thr: float = 0.10
    mlsd_dist_thr: float = 20.0
    detect_max_edge: int = 1600         # analysis resolution (long edge, px)
    min_line_length_frac: float = 0.035  # of the short edge
    vertical_window_deg: float = 32.0
    horizontal_window_deg: float = 32.0
    # Sharpness of the angular weighting inside the window.  This, not the
    # window width, is what keeps half-timbered bracing out of the fit; see
    # lines.angular_prior and docs/accuracy.md.
    angular_softness: float = 0.35
    border_margin_px: int = 3
    # Off by default: merging measurably costs accuracy.  Joining a broken
    # facade edge into one long line sounded right -- it is what a
    # length-weighted fit ought to want -- but it forces a single straight line
    # through fragments that are not exactly collinear and collapses many
    # independent measurements into one.  Measured over 40 scenes with a known
    # focal length: mean pitch error 0.10 deg off, 0.33 deg on; worst case
    # 0.61 deg off, 3.58 deg on.  See docs/accuracy.md.
    merge_lines: bool = False
    merge_horizontal: bool = False
    mask_mode: str = "off"          # off | auto | file
    mask_file: str = ""             # a PNG, or a folder of <stem>.png
    mask_invert: bool = False       # set when white means "keep"

    # ---- vanishing point search ----
    ransac_iters: int = 800
    inlier_threshold_deg: float = 1.6
    min_vertical_lines: int = 4
    n_hypotheses: int = 4               # keep several candidates, not just one
    min_vp_distance_frac: float = 1.0   # of max(W, H), from the principal point
    seed: int = 20260831

    # ---- camera ----
    focal_35mm: float = 0.0             # >0 overrides EXIF
    default_focal_35mm: float = 28.0    # same generic default darktable uses
    use_exif_focal: bool = True
    focal_estimate: str = "vp"          # off | vp | horizon | both
    uncertain_pitch_damping: float = 0.85
    refine: bool = True                 # joint (roll, pitch, f) refinement

    # ---- correction ----
    pitch_strength: float = 1.0
    roll_strength: float = 1.0
    max_pitch_deg: float = 20.0
    max_roll_deg: float = 12.0
    min_correction_deg: float = 0.15    # below this: nothing worth doing
    correct_roll: bool = True
    correct_pitch: bool = True

    # ---- gating ----
    min_confidence: float = 0.40
    max_area_ratio: float = 4.0         # refuse absurd warps

    # ---- output ----
    crop: str = "aspect"                # none | inside | aspect
    keep_size: bool = False
    interpolation: str = "lanczos"      # lanczos | cubic | linear
    jpeg_quality: int = 95
    keep_exif: bool = True

    def replace(self, **kw) -> "Settings":
        d = asdict(self)
        d.update(kw)
        return Settings(**d)

    @classmethod
    def field_names(cls):
        return [f.name for f in fields(cls)]
