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
                   choices=["auto", "lsd", "fld", "hough", "mlsd", "hybrid", "union"],
                   default="auto",
                   help="line detector; mlsd/hybrid/union need a TFLite runtime "
                        "(see docs/detectors.md for the measurements)")
    g.add_argument("--mlsd-model", default="",
                   help="M-LSD tflite model path, or a filename inside models/")
    g.add_argument("--mask", choices=["off", "auto", "file", "sam"],
                   default=Settings.mask_mode,
                   help="'auto' vegetation/sky heuristic, 'file' a painted PNG or folder, "
                        "'sam' a Segment Anything checkpoint (see --sam-model)")
    g.add_argument("--sam-model", default="",
                   help="SAM checkpoint, e.g. "
                        r'"D:\ComfyUI_windows_portable\ComfyUI\models\sams\sam_vit_b_01ec64.pth"')
    g.add_argument("--sam-text", default="",
                   help="SAM 3 only: segment these instead, e.g. "
                        "'tree, foliage, sky, car'. Empty uses the line-density route, "
                        "which works with SAM 1 and 2 as well")
    g.add_argument("--sam-max-edge", type=int, default=Settings.sam_max_edge)
    g.add_argument("--sam-device", default="", help="cuda, cpu; empty picks cuda when present")
    g.add_argument("--sam-export", metavar="DIR",
                   help="run SAM over the inputs and write one mask PNG per photo "
                        "into DIR, then exit. Use it from the interpreter that has "
                        "torch (ComfyUI's python_embeded), and consume the folder "
                        "afterwards with --mask file --mask-file DIR from anywhere, "
                        "including the GUI")
    g.add_argument("--sam-info", action="store_true",
                   help="report which SAM backends are installed, describe "
                        "--sam-model, and exit")
    g.add_argument("--sam-min-density", type=float, default=Settings.sam_min_density,
                   help="keep regions whose straight-line density is at least this "
                        "fraction of the densest region in the frame")
    g.add_argument("--mask-file", default="",
                   help="a PNG mask, or a folder holding one <stem>.png per image")
    g.add_argument("--mask-invert", action="store_true",
                   help="the mask marks what to KEEP (what a segmenter such as SAM outputs)")
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
    g.add_argument("--crop", choices=["aspect", "inside", "none"], default=Settings.crop)
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
    s.mask_mode = args.mask
    s.mask_file = args.mask_file
    s.mask_invert = args.mask_invert
    s.sam_model = args.sam_model
    s.sam_text = args.sam_text
    s.sam_max_edge = args.sam_max_edge
    s.sam_device = args.sam_device
    s.sam_min_density = args.sam_min_density
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
    s.min_correction_deg = args.min_correction
    s.correct_roll = not args.no_roll
    s.correct_pitch = not args.no_pitch
    s.min_confidence = args.min_confidence
    s.max_area_ratio = args.max_area
    s.crop = args.crop
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


def sam_info(args) -> int:
    """Answer "is my setup right?" without running a batch first.

    Worth a flag of its own: the failure modes are an uninstalled backend, a
    backend installed into a different interpreter, and a checkpoint that needs
    a different package -- and none of them is obvious from a run that simply
    errors on every file.
    """
    import sys as _sys

    from . import sam as SAM
    print(f"interpreter: {_sys.executable}")
    for name, ok in SAM.backends().items():
        print(f"  {'yes' if ok else 'no ':>3}  {name}")
    if args.sam_model:
        print(f"\nmodel: {SAM.describe(args.sam_model)}")
        try:
            SAM._load(args.sam_model, args.sam_device)
            print("  loads: yes")
        except Exception as exc:
            print(f"  loads: NO\n{exc}")
    else:
        print("\n(pass --sam-model to check a specific checkpoint)")
    return 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.sam_info:
        return sam_info(args)
    if args.sam_export:
        if not args.sam_model:
            print("--sam-export needs --sam-model <checkpoint>")
            return 2
        files = collect(args.inputs, args.recursive)
        if not files:
            print("no readable images found")
            return 1
        from . import sam as SAM
        log = _Log(args.log_file, args.quiet)
        _, failed = SAM.export_masks(files, args.sam_model, args.sam_export,
                                     settings_from(args), log=log)
        log.close()
        return 0 if failed == 0 else 3
    if args.mask == "sam" and not args.sam_model:
        print("--mask sam needs --sam-model <checkpoint>")
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
        build_parser().print_help()
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
            json.dump([dict(r.as_dict(), diagnostics=r.diagnostics) for r in results],
                      fh, indent=2, default=str)
        log(f"# report written to {args.json_report}")

    log.close()
    return 0 if counts[ERROR] == 0 else 3
