"""All tunables in one place.

Defaults are chosen for hand-held architectural photography: buildings, wide-ish
lenses, a mixture of images that need correcting and images that do not.  The
guiding rule is the one from the brief -- when in doubt, leave the photo alone:
a correction the estimator is unsure about is skipped rather than applied, and
the batch defaults stay conservative so an unattended run never invents or
throws away frame it was not asked to touch.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class Settings:
    # ---- detection ----
    # lsd | fld | mlsd | hybrid | union | deeplsd | deep-hybrid
    # | deep-union.  The deep-* pair is the mlsd hybrid/union with DeepLSD as
    # the guide instead of M-LSD; see lines.detect_segments.
    detector: str = "lsd"
    mlsd_model: str = ""                # path, or a name in models/
    mlsd_score_thr: float = 0.10
    mlsd_dist_thr: float = 20.0
    deeplsd_model: str = ""             # path, or a name in models/
    deeplsd_device: str = ""            # "" = cuda when available
    deeplsd_grad_nfa: bool = True       # off for night/fog/blur, per the paper
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
    mask_mode: str = "off"          # off | file | birefnet | gdino
    birefnet_model: str = ""        # path to BiRefNet weights
    birefnet_threshold: float = 0.5  # matte is near-binary; not a tuning knob
    birefnet_device: str = ""       # "" = cuda when available
    birefnet_res: int = 0           # 0 = the size implied by the weight name
    birefnet_shrink_frac: float = 0.008  # of the diagonal; ~15 px at 1600
    mask_file: str = ""             # a PNG, or a folder of <stem>.png
    mask_invert: bool = False       # set when white means "keep"
    gdino_prompt: str = "building"  # text prompt for the --mask gdino detector
    gdino_model: str = ""           # GDINO model dir; "" = models/GroundingDINO
    sam_model: str = ""             # SAM2 checkpoint; "" = models/sam2/sam2.1_hiera_base_plus.pt
    sam_device: str = ""            # "" = cuda when available

    # ---- line partitioning (ArchitectureScheme) ----
    # Off by default: the classifier only ever *removes* evidence, and this is a
    # batch tool that runs unattended -- so it stays opt-in until measured
    # against the assets.  When on, detected lines are partitioned into the
    # building's Manhattan planes before the VP search; a non-established frame
    # filters nothing (a safe no-op), so the risk is bounded to frames where a
    # second horizontal plane is confidently present.
    use_scheme: bool = False

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
    max_pitch_deg: float = 30.0        # raised from 20 (2026-09-13): admit steep corner shots
    refuse_beyond_limit: bool = True   # a capped correction is refused,
                                      # not trimmed to fit
    max_roll_deg: float = 12.0
    min_correction_deg: float = 0.15    # below this: nothing worth doing
    correct_roll: bool = True
    correct_pitch: bool = True
    # Horizontal (yaw) de-convergence -- the third camera angle, and a
    # SPECIAL CASE that is off by default.  It is not a levelling operation:
    # yaw squares the camera onto one horizontal direction, i.e. it makes one
    # facade fronto-parallel.  That is only meaningful when the photograph has
    # exactly one facade plane, and architectural photographs routinely have
    # two.  Measured over the seventeen assets with it switched on, fourteen
    # asked for more than 25 deg of yaw at a confidence the gate admits
    # (burgebrach +57.7 at 0.53, ulica -67.5 at 0.46, quaker +27.7 at 0.75);
    # only the one genuinely frontal frame came back near zero.  Those are not
    # errors the estimator made -- converging horizontals ARE correct
    # perspective on an oblique facade -- so confidence cannot catch them and
    # is not built to.  The cap is therefore back at 8 deg, which is what
    # `warp.limit` needs to keep a weak or wrong horizontal VP from shearing
    # the frame sideways.  See "Horizontal (yaw) de-convergence" in CLAUDE.md
    # for the open design question (which plane, and who chooses it).
    correct_horizontal: bool = False
    horizontal_strength: float = 1.0
    # Raised 8 -> 30 on 2026-09-13 (user: "horizontal correction is often way too
    # weak").  The 8 above was reasoned for an *unattended* batch, where a wrong
    # yaw shears a frame nobody looks at.  Manual review is the product now, so
    # that trade is gone: P9 measured the real single-VP yaw on this pool at
    # 16-70 deg, which the old cap clamped to a fraction of what the geometry
    # asked for.  30 matches the manual slider's range, so automatic and by-hand
    # now reach the same place.  The risk the old comment describes is unchanged
    # and real -- a weak horizontal VP still shears -- it is simply visible to
    # the person reviewing the photograph before anything is written.
    max_horizontal_deg: float = 60.0
    min_horizontal_support: float = 0.15

    # ---- gating ----
    min_confidence: float = 0.40
    preserve_near_edge: bool = True     # apply `keep_pixels` when a yaw is corrected
    # How many pixels a yaw correction is allowed to keep.  **1 = maximum
    # pixels, 0 = minimum pixels** (user, 2026-09-20).
    #
    # `H = K R K^-1` at a large yaw inflates the receding edge of a facade five-
    # to sevenfold, and the plan scales the whole output back so the facade
    # keeps *about* its source pixel count.  On average that is right; at the
    # NEAR edge it is a loss, because the average is dragged up by the edge that
    # was inflated -- and the near edge is where the real detail is.  This knob
    # says how far to scale back up against that.
    #
    #   1.0  keep every pixel: nothing in the frame is ever sampled below 1:1,
    #        so no photographed detail is discarded.  Largest file.
    #   0.0  keep the fewest: no scaling at all, the near edge is downsampled.
    #        Smallest file.
    #
    # Measured over 8 corner views, 2026-09-20:
    #
    #   keep_pixels   min sampling   canvas vs source   linear detail lost
    #      1.00           1.08            6.44x                0.0%
    #      0.75           1.03            5.75x                4.8%
    #      0.50           0.98            5.16x                9.2%   <- default
    #      0.25           0.94            4.65x               13.4%
    #      0.00           0.90            4.22x               17.3%
    #
    # 0.5 is the user's call and the honest one.  The arithmetic alone argues
    # for more (the whole range moves the canvas by only a third while detail
    # loss runs 0 to 17.3%), but rendered side by side at equal magnification
    # the two ends are indistinguishable on this pool -- every photograph in it
    # is downscaled to ~1800 px and has no fine detail left to lose.  Neither
    # end is defensible over the middle on the evidence that exists.
    keep_pixels: float = 0.5
    max_area_ratio: float = 9.0         # crop canvas to this × source area (trim fill zones)

    # ---- distortion correction (Stage 0) ----
    # "off" = no undistortion.  "lensfun" = look up the EXIF make/model/lens in
    # the Lensfun database and apply a per-pixel remap before the perspective
    # warp.  Only fires when EXIF identifies the camera; EXIF-stripped images
    # fall through unchanged (Stage 1 AnyCalib is the no-EXIF route, not yet).
    undistort: str = "off"              # off | lensfun

    # ---- output ----
    crop: str = "auto"                  # auto | aspect | inside | none
    # One gate for "how much frame may a crop cost before we pad instead."
    # Batch default is tight (5 %): an unattended run must not silently throw
    # away a third of every picture.  The review window raises this to 30 % at
    # session start because the user sees the result on screen and "Reset crop"
    # undoes it; the number here is what `--max-crop-loss` maps to.
    crop_max_loss: float = 0.05
    # Margin around the line-motif bounding box when reframing (fraction of
    # motif size).  0.30 = 30% context on each side.  The output is always
    # centred on the motif; this controls how much surrounding scene survives.
    reframe_margin: float = 0.30
    pad: str = "edge"                   # edge | black | white | #rrggbb | r,g,b
    # What to do with the band the rotation opens up, once padding has put
    # something there.  "none" keeps the pad; the rest put pixels there that
    # the camera never saw.  The default is "telea" -- the only one of the
    # three that needs no model, no download and no network, and is the same
    # every run.  It is still invention: see the fill section of CLAUDE.md for
    # why that is defensible here and why "none" is one flag away.
    fill: str = "telea"                 # none | telea | lama | comfyui
    fill_max_edge: int = 2048           # generate at this size, paste back full res
    fill_max_share: float = 0.40        # refuse to invent more of the frame than this
    fill_device: str = ""               # torch device for lama; "" = its default
    comfy_url: str = "http://127.0.0.1:8188"
    comfy_workflow: str = ""            # "" = inpaint.DEFAULT_WORKFLOW, and the
                                        # indicator says nobody chose it
    comfy_prompt: str = ""              # fills the node titled BPC_PROMPT
    comfy_seed: int = 0                 # 0 = leave the workflow's own seeds alone
    comfy_timeout: float = 300.0
    # Chosen by hand from what the server offers, and applied over whatever the
    # workflow names.  Empty means "leave the workflow alone and let
    # `inpaint.resolve_models` guess", which is right until two files on the
    # same machine are equally plausible -- and with 46 text encoders installed
    # they usually are.
    comfy_unet: str = ""
    comfy_clip: str = ""
    comfy_vae: str = ""
    keep_size: bool = False
    interpolation: str = "lanczos"      # lanczos | cubic | linear
    jpeg_quality: int = 95
    keep_exif: bool = True

    def replace(self, **kw) -> "Settings":
        d = asdict(self)
        d.update(kw)
        return Settings(**d)
