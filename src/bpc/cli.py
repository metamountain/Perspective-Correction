"""Command line front end.

    rectify.py "D:\\Fotos"                    write next to the originals as *_corr.jpg
    rectify.py "D:\\Fotos" -o "D:\\Out"        write to another folder
    rectify.py "D:\\Fotos" --overwrite        replace the originals (asks first)
    rectify.py "D:\\Fotos" --dry-run -v       decide but write nothing
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time

from .config import Settings
from .pipeline import ERROR, OK, SKIPPED, process


def collect(inputs, recursive):
    from .imageio import READABLE
    files = []
    for item in inputs:
        if os.path.isdir(item):
            if recursive:
                for root, _, names in os.walk(item):
                    files += [os.path.join(root, n) for n in sorted(names)
                              if os.path.splitext(n)[1].lower() in READABLE]
            else:
                files += [os.path.join(item, n) for n in sorted(os.listdir(item))
                          if os.path.splitext(n)[1].lower() in READABLE
                          and os.path.isfile(os.path.join(item, n))]
        elif os.path.isfile(item):
            files.append(item)
    seen, out = set(), []
    for f in files:
        k = os.path.abspath(f)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def destination(src, args, roots):
    if args.overwrite:
        return src
    stem, ext = os.path.splitext(os.path.basename(src))
    name = f"{stem}{args.suffix}{ext}"
    if not args.output:
        return os.path.join(os.path.dirname(src), name)
    rel = ""
    for root in roots:
        try:
            r = os.path.relpath(os.path.dirname(src), root)
        except ValueError:
            continue
        if not r.startswith(".."):
            rel = "" if r == "." else r
            break
    return os.path.join(args.output, rel, name)


def build_parser():
    p = argparse.ArgumentParser(
        prog="rectify", formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Straighten converging verticals in architectural photos, in batch. "
                    "Images the detector is not confident about are left alone.")
    p.add_argument("inputs", nargs="*", help="image files and/or folders")
    p.add_argument("-o", "--output", help="output folder (default: beside the input)")
    p.add_argument("--suffix", default="_corr", help="suffix for output files")
    p.add_argument("--overwrite", action="store_true", help="replace the originals")
    p.add_argument("--yes", "-y", action="store_true", help="do not ask before overwriting")
    p.add_argument("-r", "--recursive", action="store_true", help="descend into subfolders")
    p.add_argument("--skip-existing", action="store_true", help="do not redo existing outputs")

    g = p.add_argument_group("correction")
    g.add_argument("--strength", type=float, default=1.0,
                   help="scale both corrections (0 = none, 1 = full)")
    g.add_argument("--pitch-strength", type=float, help="scale the converging-verticals fix")
    g.add_argument("--roll-strength", type=float, help="scale the levelling")
    g.add_argument("--max-pitch", type=float, default=Settings.max_pitch_deg,
                   help="cap on the converging-verticals fix, degrees")
    g.add_argument("--max-roll", type=float, default=Settings.max_roll_deg,
                   help="cap on levelling, degrees")
    g.add_argument("--clamp-beyond-limit", action="store_true",
                   help="apply the capped correction when the estimate runs past "
                        "--max-pitch/--max-roll, instead of refusing the photo. "
                        "The default is to refuse: an estimate that extreme is "
                        "usually about something that is not a facade")
    g.add_argument("--min-correction", type=float, default=Settings.min_correction_deg,
                   help="below this the photo counts as already upright, degrees")
    g.add_argument("--no-roll", action="store_true", help="never level, only fix verticals")
    g.add_argument("--no-pitch", action="store_true", help="only level, never fix verticals")

    g = p.add_argument_group("decision")
    g.add_argument("--min-confidence", type=float, default=Settings.min_confidence,
                   help="below this the image is left unchanged")
    g.add_argument("--max-area", type=float, default=Settings.max_area_ratio,
                   help="refuse warps that inflate the frame more than this")

    g = p.add_argument_group("camera")
    g.add_argument("--focal-35mm", type=float, default=0.0,
                   help="override the focal length, 35mm equivalent")
    g.add_argument("--default-focal-35mm", type=float, default=Settings.default_focal_35mm,
                   help="assumed focal length when EXIF has none")
    g.add_argument("--no-exif-focal", action="store_true", help="ignore the EXIF focal length")
    g.add_argument("--no-refine", action="store_true", help="skip the joint (roll,pitch,f) fit")
    g.add_argument("--focal-estimate", choices=["off", "vp", "horizon", "both"],
                   default=Settings.focal_estimate,
                   help="estimate the focal length from the geometry as well as the prior")
    g.add_argument("--uncertain-damping", type=float, default=Settings.uncertain_pitch_damping,
                   help="scale the pitch fix when the focal length is only a guess")

    g = p.add_argument_group("detection")
    g.add_argument("--detector",
                   choices=["auto", "lsd", "fld", "hough", "mlsd", "hybrid", "union",
                            "deeplsd", "deep-hybrid", "deep-union"],
                   default="auto",
                   help="line detector; mlsd/hybrid/union need a TFLite runtime, "
                        "deeplsd/deep-* need torch and a DeepLSD checkout "
                        "(see docs/detectors.md for the measurements)")
    g.add_argument("--mlsd-model", default="",
                   help="M-LSD tflite model path, or a filename inside models/")
    g.add_argument("--deeplsd-model", default="",
                   help="DeepLSD weights (.tar), or a filename inside models/. "
                        "Not bundled: curl -L -o models/deeplsd_md.tar "
                        "https://cvg-data.inf.ethz.ch/DeepLSD/deeplsd_md.tar")
    g.add_argument("--deeplsd-device", default="",
                   help="torch device for DeepLSD; empty picks cuda when available")
    g.add_argument("--no-grad-nfa", action="store_true",
                   help="DeepLSD: skip the image-gradient NFA filter. The paper "
                        "recommends it off for night, fog and blur")
    g.add_argument("--detector-info", action="store_true",
                   help="what this Python can run as a line detector, and exit")
    g.add_argument("--mask", choices=["off", "file", "birefnet"],
                   default=Settings.mask_mode,
                   help="'file' a painted PNG or a folder of them, 'birefnet' segment "
                        "the building out (see --birefnet-model)")
    g.add_argument("--birefnet-model", default="",
                   help="BiRefNet weights, or 'auto' to search the usual ComfyUI "
                        "folders. Example: "
                        r'"D:\ComfyUI_windows_portable\ComfyUI\models\RMBG\BiRefNet\BiRefNet-HR.safetensors"')
    g.add_argument("--birefnet-threshold", type=float, default=Settings.birefnet_threshold,
                   help="matte cut-off. The matte is near-binary, so this is not a "
                        "tuning knob: 0.1 to 0.9 moves the masked share by half a percent")
    g.add_argument("--birefnet-res", type=int, default=Settings.birefnet_res,
                   help="inference size; 0 takes the size implied by the weight name "
                        "(2048 for HR and 2K, else 1024)")
    g.add_argument("--birefnet-shrink", type=float, default=Settings.birefnet_shrink_frac,
                   metavar="FRAC",
                   help="pull the masked region off the building silhouette by this "
                        "fraction of the frame diagonal, so its own corner and roof "
                        "edges survive (0.008 is ~15 px at 1600; 0 disables)")
    g.add_argument("--birefnet-device", default="",
                   help="cuda, cpu; empty picks cuda when present")
    g.add_argument("--remember", action="store_true",
                   help="store --birefnet-model, --mask-file, -o and --focal-35mm as "
                        "defaults for future runs")
    g.add_argument("--forget", action="store_true",
                   help="delete the remembered defaults and exit")
    g.add_argument("--mask-export", metavar="DIR",
                   help="write one mask PNG per photo into DIR, then exit. Two uses: "
                        "run it from the interpreter that has torch (ComfyUI's "
                        "python_embeded) and consume the folder anywhere with "
                        "--mask file --mask-file DIR, including the GUI; or cache the "
                        "masks so a repeated run does not recompute them")
    g.add_argument("--diagnostics", action="store_true",
                   help="print the environment (interpreter, versions, backends, "
                        "settings) before the run, so a log is self-describing")
    g.add_argument("--mask-info", action="store_true",
                   help="report whether BiRefNet can run here, describe "
                        "--birefnet-model, and exit")
    g.add_argument("--mask-file", default="",
                   help="a PNG mask, or a folder holding one <stem>.png per image")
    g.add_argument("--mask-invert", action="store_true",
                   help="the mask marks what to KEEP, which is what a segmenter "
                        "naturally outputs; --mask-export already inverts")
    g.add_argument("--detect-max-edge", type=int, default=Settings.detect_max_edge)
    g.add_argument("--min-line-length", type=float, default=Settings.min_line_length_frac,
                   help="minimum line length as a fraction of the short edge")
    g.add_argument("--inlier-threshold", type=float, default=Settings.inlier_threshold_deg,
                   help="RANSAC inlier band, degrees")
    g.add_argument("--angular-softness", type=float, default=Settings.angular_softness,
                   help="how sharply leaning lines are down-weighted; lower is stricter, "
                        "which helps on half-timbered facades")
    g.add_argument("--seed", type=int, default=Settings.seed)
    g.add_argument("--merge-lines", action="store_true",
                   help="join collinear fragments before fitting (measurably worse; "
                        "kept for images whose edges are heavily broken up)")

    g = p.add_argument_group("output")
    g.add_argument("--crop", choices=["auto", "aspect", "inside", "none"],
                   default=Settings.crop,
                   help="'auto' crops while the loss stays under --max-crop-loss and "
                        "keeps the whole frame otherwise; 'aspect'/'inside' always crop; "
                        "'none' never does")
    g.add_argument("--max-crop-loss", type=float, default=Settings.max_crop_loss,
                   help="with --crop auto, the share of the frame a crop may cost "
                        "before the whole frame is kept and padded instead")
    g.add_argument("--pad", default=Settings.pad, metavar="EDGE|COLOUR",
                   help="what fills the corners a rotation opens up when the frame "
                        "is kept: 'edge' extends the border colour, or give a colour "
                        "as a name (black, white, grey), #rrggbb, or r,g,b")
    g.add_argument("--fill", choices=["none", "lama", "comfyui"], default=Settings.fill,
                   help="generate the padded band instead of leaving it padded. "
                        "'lama' needs simple-lama-inpainting, 'comfyui' a running "
                        "ComfyUI. Off by default: these pixels were never "
                        "photographed, and only the padded band is ever touched. "
                        "Each -j worker loads its own copy of the model")
    g.add_argument("--fill-max-edge", type=int, default=Settings.fill_max_edge,
                   help="generate at this long edge and paste back at full "
                        "resolution; 0 generates at full size")
    g.add_argument("--fill-max-share", type=float, default=Settings.fill_max_share,
                   help="refuse to invent more than this fraction of the frame")
    g.add_argument("--fill-device", default="",
                   help="torch device for the local fill; empty picks its default")
    g.add_argument("--comfy-url", default=Settings.comfy_url)
    g.add_argument("--comfy-workflow", default="", metavar="FILE.json",
                   help="ComfyUI API-format workflow; empty uses the bundled "
                        "workflows/flux-klein-outpaint.json. Nodes titled "
                        "BPC_IMAGE, BPC_MASK and (optionally) BPC_PROMPT are "
                        "where the photograph, the hole and the prompt go")
    g.add_argument("--comfy-prompt", default="",
                   help="text for the node titled BPC_PROMPT")
    g.add_argument("--comfy-seed", type=int, default=0,
                   help="force every sampler seed in the workflow; 0 leaves them")
    g.add_argument("--fill-info", action="store_true",
                   help="whether the chosen fill backend is usable, and exit")
    g.add_argument("--keep-size", action="store_true",
                   help="rescale the crop back to the original pixel dimensions")
    g.add_argument("--jpeg-quality", type=int, default=Settings.jpeg_quality)
    g.add_argument("--no-exif", action="store_true", help="do not carry EXIF/ICC over")
    g.add_argument("--interpolation", choices=["lanczos", "cubic", "linear"],
                   default=Settings.interpolation)

    g = p.add_argument_group("reporting")
    g.add_argument("-n", "--dry-run", action="store_true", help="decide but write nothing")
    g.add_argument("--debug-dir", help="write line/horizon overlays and before-after pairs here")
    g.add_argument("--log-file", help="append the log to this file as well")
    g.add_argument("--json-report", help="write a machine readable report here")
    g.add_argument("-j", "--workers", type=int, default=0,
                   help="parallel workers (0 = one per core, 1 = sequential)")
    g.add_argument("-v", "--verbose", action="store_true")
    g.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("--gui", action="store_true", help="open the graphical batch window")
    return p


def settings_from(args) -> Settings:
    s = Settings()
    s.detector = args.detector
    s.mlsd_model = args.mlsd_model
    s.deeplsd_model = args.deeplsd_model
    s.deeplsd_device = args.deeplsd_device
    s.deeplsd_grad_nfa = not args.no_grad_nfa
    s.mask_mode = args.mask
    s.mask_file = args.mask_file
    s.mask_invert = args.mask_invert
    s.birefnet_model = args.birefnet_model
    s.birefnet_threshold = args.birefnet_threshold
    s.birefnet_res = args.birefnet_res
    s.birefnet_shrink_frac = args.birefnet_shrink
    s.birefnet_device = args.birefnet_device
    s.detect_max_edge = args.detect_max_edge
    s.min_line_length_frac = args.min_line_length
    s.inlier_threshold_deg = args.inlier_threshold
    s.angular_softness = args.angular_softness
    s.seed = args.seed
    s.merge_lines = args.merge_lines
    s.focal_35mm = args.focal_35mm
    s.default_focal_35mm = args.default_focal_35mm
    s.use_exif_focal = not args.no_exif_focal
    s.refine = not args.no_refine
    s.focal_estimate = args.focal_estimate
    s.uncertain_pitch_damping = args.uncertain_damping
    s.pitch_strength = args.strength if args.pitch_strength is None else args.pitch_strength
    s.roll_strength = args.strength if args.roll_strength is None else args.roll_strength
    s.max_pitch_deg = args.max_pitch
    s.max_roll_deg = args.max_roll
    s.refuse_beyond_limit = not args.clamp_beyond_limit
    s.min_correction_deg = args.min_correction
    s.correct_roll = not args.no_roll
    s.correct_pitch = not args.no_pitch
    s.min_confidence = args.min_confidence
    s.max_area_ratio = args.max_area
    s.crop = args.crop
    s.max_crop_loss = args.max_crop_loss
    s.pad = args.pad
    s.fill = args.fill
    s.fill_max_edge = args.fill_max_edge
    s.fill_max_share = args.fill_max_share
    s.fill_device = args.fill_device
    s.comfy_url = args.comfy_url
    s.comfy_workflow = args.comfy_workflow
    s.comfy_prompt = args.comfy_prompt
    s.comfy_seed = args.comfy_seed
    s.keep_size = args.keep_size
    s.jpeg_quality = args.jpeg_quality
    s.keep_exif = not args.no_exif
    s.interpolation = args.interpolation
    return s


class _Log:
    def __init__(self, path, quiet):
        self.fh = open(path, "a", encoding="utf-8") if path else None
        self.quiet = quiet

    def __call__(self, msg):
        if not self.quiet:
            print(msg, flush=True)
        if self.fh:
            self.fh.write(msg + "\n")
            self.fh.flush()

    def close(self):
        if self.fh:
            self.fh.close()


def _job(item):
    src, dst, settings, debug_dir, dry = item
    return process(src, dst, settings, debug_dir=debug_dir, dry_run=dry)


def diagnostics_text(args, settings) -> str:
    """Everything needed to interpret a log without asking follow-up questions.

    A log that says "SKIPPED, low confidence" is nearly useless on its own: the
    answer depends on the interpreter, the library versions, which optional
    backends were present and what the settings actually were after defaults and
    remembered values were applied.
    """
    import platform
    import sys as _sys

    from . import __version__
    from . import birefnet as BN

    out = ["# --- environment " + "-" * 48]
    out.append(f"# bpc {__version__} on {platform.platform()}")
    out.append(f"# python {_sys.version.split()[0]}  {_sys.executable}")
    for mod in ("numpy", "cv2", "PIL", "piexif"):
        try:
            m = __import__(mod)
            out.append(f"#   {mod:8s} {getattr(m, '__version__', '?')}")
        except Exception as exc:
            out.append(f"#   {mod:8s} MISSING ({type(exc).__name__})")
    out.append("# optional backends: " + ", ".join(
        f"{k}={'yes' if v else 'no'}" for k, v in BN.backends().items()))
    if settings.mask_mode == "birefnet" and settings.birefnet_model:
        out.append(f"# birefnet: {BN.describe(settings.birefnet_model)}")
    interesting = ("detector", "deeplsd_model", "mask_mode", "mask_file", "birefnet_model",
                   "birefnet_threshold", "focal_35mm", "default_focal_35mm",
                   "focal_estimate", "min_confidence", "max_pitch_deg",
                   "max_roll_deg", "pitch_strength", "roll_strength", "crop",
                   "max_crop_loss", "pad",
                   "detect_max_edge", "inlier_threshold_deg", "angular_softness",
                   "uncertain_pitch_damping", "seed")
    out.append("# settings: " + ", ".join(
        f"{k}={getattr(settings, k)!r}" for k in interesting))
    out.append("# " + "-" * 62)
    return "\n".join(out)


def mask_info(args) -> int:
    """Answer "is my setup right?" without running a batch first.

    Worth a flag of its own: the failure modes are a missing torch, a torch
    installed into a *different* interpreter, and weights sitting apart from the
    architecture that defines them -- and none of them is obvious from a run
    that simply errors on every file.
    """
    import sys as _sys

    from . import birefnet as BN
    print(f"interpreter: {_sys.executable}")
    for name, ok in BN.backends().items():
        print(f"  {'yes' if ok else 'no ':>3}  {name}")
    if args.birefnet_model:
        print(f"\nmodel: {BN.describe(args.birefnet_model)}")
        try:
            BN._load(args.birefnet_model, args.birefnet_device)
            print("  loads: yes")
        except Exception as exc:
            print(f"  loads: NO\n{exc}")
    else:
        print("\n(pass --birefnet-model to check specific weights)")
    return 0


def detector_info(args) -> int:
    """The detector half of --mask-info, and for the same reason.

    Three of the detectors are optional dependencies with three different
    failure modes -- a missing TFLite runtime, a missing checkout, missing
    weights -- and a run that simply falls back to LSD says none of that out
    loud.  It is a batch tool: the silent fallback is the dangerous one.
    """
    import sys as _sys

    import cv2

    from . import deeplsd as DL
    from . import mlsd as ML
    print(f"interpreter: {_sys.executable}")
    print(f"  lsd/fld/hough  opencv {cv2.__version__}")
    print(f"  {'yes' if ML.available(args.mlsd_model) else 'no '}  mlsd, hybrid, union")
    print(f"  {'yes' if DL.available(args.deeplsd_model) else 'no '}  "
          f"deeplsd, deep-hybrid, deep-union")
    print(f"       {DL.describe(args.deeplsd_model, args.deeplsd_device)}")
    return 0


def apply_prefs(args, parser):
    """Fill unset path arguments from the remembered ones.

    Only fills what the command line left at its default, so an explicit flag
    always wins and a run is still fully described by what was typed plus what
    ``--mask-info`` reports.
    """
    from . import prefs
    stored = prefs.load()
    used = []
    for key in ("birefnet_model", "mask_file", "output"):
        if not getattr(args, key, None) and stored.get(key):
            setattr(args, key, stored[key])
            used.append(key)
    if not args.focal_35mm and stored.get("focal_35mm"):
        args.focal_35mm = float(stored["focal_35mm"])
        used.append("focal_35mm")
    return used


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from . import prefs
    if args.forget:
        print("forgot remembered defaults" if prefs.forget() else "nothing was remembered")
        return 0
    if args.birefnet_model == "auto":
        from . import birefnet as BN
        found = BN.find_weights()
        if not found:
            print("--birefnet-model auto found no weights; pass a path")
            return 2
        args.birefnet_model = found
        if not args.quiet:
            print(f"# using {BN.describe(found)}")
    used = apply_prefs(args, parser)
    if used and not args.quiet:
        print(f"# using remembered {', '.join(used)} from {prefs.path()}")
    if args.remember:
        ok = prefs.save(birefnet_model=args.birefnet_model, mask_file=args.mask_file,
                        output=args.output,
                        focal_35mm=args.focal_35mm if args.focal_35mm else None)
        print(f"# remembered in {prefs.path()}" if ok else "# nothing to remember")
    if args.fill_info:
        from . import inpaint as FILL
        st = Settings().replace(fill=args.fill, comfy_url=args.comfy_url,
                                comfy_workflow=args.comfy_workflow)
        print(FILL.describe(args.fill, st))
        if args.fill == "none":
            print("(pass --fill lama or --fill comfyui to check a backend)")
        return 0
    if args.detector_info:
        return detector_info(args)
    if args.mask_info:
        return mask_info(args)
    if args.mask_export:
        from . import birefnet as BN
        if not args.birefnet_model:
            args.birefnet_model = BN.find_weights()
        if not args.birefnet_model:
            print("--mask-export needs --birefnet-model <weights>")
            return 2
        files = collect(args.inputs, args.recursive)
        if not files:
            print("no readable images found")
            return 1
        log = _Log(args.log_file, args.quiet)
        _, failed = BN.export_masks(files, args.birefnet_model, args.mask_export,
                                    settings_from(args), log=log)
        log.close()
        return 0 if failed == 0 else 3
    if args.mask == "birefnet" and not args.birefnet_model:
        from . import birefnet as BN
        found = BN.find_weights()
        if found:
            print("--mask birefnet needs --birefnet-model. This machine has:\n"
                  "  " + found + "\n\nRun it with:\n"
                  "    --mask birefnet --birefnet-model auto --remember")
        else:
            print("--mask birefnet needs --birefnet-model <weights>, or 'auto'.\n\n"
                  + BN.what_you_need())
        return 2
    if args.mask == "file" and not args.mask_file:
        print("--mask file needs --mask-file <png or folder>")
        return 2
    if args.gui or not args.inputs:
        if args.gui or sys.stdin is None or not sys.stdin.isatty():
            try:
                from .gui import run as run_gui
            except Exception as exc:
                print(f"GUI unavailable ({exc}); pass image files or folders instead.")
                return 2
            return run_gui(args.inputs)
        parser.print_help()
        return 2

    files = collect(args.inputs, args.recursive)
    if not files:
        print("no readable images found")
        return 1

    if args.overwrite and not args.yes and not args.dry_run:
        if sys.stdin is not None and sys.stdin.isatty():
            ans = input(f"--overwrite will replace {len(files)} original file(s). Continue? [y/N] ")
            if ans.strip().lower() not in ("y", "yes", "j", "ja"):
                print("aborted")
                return 1
        else:
            print("--overwrite needs --yes when running non-interactively")
            return 1

    settings = settings_from(args)
    roots = [os.path.abspath(i) for i in args.inputs if os.path.isdir(i)]
    log = _Log(args.log_file, args.quiet)
    if args.diagnostics:
        for line in diagnostics_text(args, settings).splitlines():
            log(line)
    log(f"# batch-perspective-correction  {len(files)} file(s)  "
        f"{time.strftime('%Y-%m-%d %H:%M:%S')}")

    jobs = []
    skipped_existing = 0
    for src in files:
        dst = destination(src, args, roots)
        if args.skip_existing and not args.overwrite and os.path.exists(dst):
            skipped_existing += 1
            continue
        jobs.append((src, dst, settings, args.debug_dir, args.dry_run))

    workers = args.workers or min(8, (os.cpu_count() or 1))
    if not args.workers and settings.mask_mode == "birefnet" and workers > 2:
        # Each worker is a separate process with its own birefnet._CACHE, so
        # eight of them load the 444 MB checkpoint eight times and then queue
        # for one GPU.  On a ten-image folder that is most of the runtime; on a
        # large batch it amortises, but never usefully past a couple of workers.
        # An explicit -j is left alone -- the user may know their machine better.
        # --mask-export once and --mask file avoids the question entirely.
        workers = 2
        log(f"# --mask birefnet: capped at {workers} workers "
            f"(each loads the checkpoint; pass -j to override, or --mask-export once)")
    results = []
    t0 = time.time()
    if workers <= 1 or len(jobs) <= 1:
        for item in jobs:
            r = _job(item)
            results.append(r)
            log(r.line())
    else:
        with cf.ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(_job, jobs):
                results.append(r)
                log(r.line())

    counts = {OK: 0, SKIPPED: 0, ERROR: 0}
    for r in results:
        counts[r.status] += 1
    log(f"# done in {time.time() - t0:.1f}s: {counts[OK]} OK, {counts[SKIPPED]} SKIPPED, "
        f"{counts[ERROR]} ERROR" + (f", {skipped_existing} already present" if skipped_existing else ""))

    if args.verbose:
        for r in results:
            if r.diagnostics:
                log(f"  {os.path.basename(r.src)}: {json.dumps(r.diagnostics, default=str)}")

    if args.json_report:
        with open(args.json_report, "w", encoding="utf-8") as fh:
            json.dump({"environment": diagnostics_text(args, settings),
                       "results": [dict(r.as_dict(), diagnostics=r.diagnostics)
                                   for r in results]},
                      fh, indent=2, default=str)
        log(f"# report written to {args.json_report}")

    log.close()
    return 0 if counts[ERROR] == 0 else 3
