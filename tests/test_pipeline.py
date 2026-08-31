"""End to end: files in, files out, decisions logged."""
import math
import os
import shutil
import tempfile

import cv2
import numpy as np

import synth
from bpc.config import Settings
from bpc.pipeline import ERROR, OK, SKIPPED, process


def _tmp():
    d = tempfile.mkdtemp(prefix="bpc-test-")
    return d


def _write(scene, path, quality=95):
    cv2.imwrite(path, scene.img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return path


def test_a_tilted_photo_is_corrected_and_written():
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=9, roll_deg=-3, seed=12), os.path.join(d, "a.jpg"))
        dst = os.path.join(d, "a_corr.jpg")
        r = process(src, dst, Settings())
        assert r.status == OK, r.line()
        assert os.path.exists(dst)
        out = cv2.imread(dst)
        assert out is not None and out.shape[0] > 100
        assert abs(r.roll_deg) > 1.0
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_an_upright_photo_is_left_alone():
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=0, roll_deg=0, seed=13), os.path.join(d, "b.jpg"))
        r = process(src, os.path.join(d, "b_corr.jpg"), Settings())
        assert r.status == SKIPPED
        assert "upright" in r.reason or "confidence" in r.reason
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_an_image_without_structure_is_skipped_not_mangled():
    d = _tmp()
    try:
        src = os.path.join(d, "c.jpg")
        cv2.imwrite(src, synth.flat_image())
        dst = os.path.join(d, "c_corr.jpg")
        r = process(src, dst, Settings())
        assert r.status == SKIPPED
        # a skipped image still produces an output, byte identical to the input
        assert os.path.exists(dst)
        assert open(src, "rb").read() == open(dst, "rb").read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_unreadable_input_is_an_error_not_a_crash():
    d = _tmp()
    try:
        bad = os.path.join(d, "broken.jpg")
        with open(bad, "wb") as fh:
            fh.write(b"not an image at all")
        r = process(bad, os.path.join(d, "out.jpg"), Settings())
        assert r.status == ERROR and "cannot read" in r.reason
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_exif_survives_and_orientation_is_reset():
    import piexif
    from PIL import Image
    d = _tmp()
    try:
        src = os.path.join(d, "e.jpg")
        sc = synth.Scene(pitch_deg=8, roll_deg=2, seed=14)
        Image.fromarray(cv2.cvtColor(sc.img, cv2.COLOR_BGR2RGB)).save(src, quality=95)
        exif = {"0th": {piexif.ImageIFD.Make: b"TestCam",
                        piexif.ImageIFD.Orientation: 1},
                "Exif": {piexif.ExifIFD.FocalLengthIn35mmFilm: 28,
                         piexif.ExifIFD.DateTimeOriginal: b"2026:01:02 03:04:05"},
                "GPS": {}, "1st": {}, "thumbnail": None}
        piexif.insert(piexif.dump(exif), src)
        dst = os.path.join(d, "e_corr.jpg")
        r = process(src, dst, Settings())
        assert r.status == OK, r.line()
        assert r.focal_source == "exif", r.focal_source
        got = piexif.load(dst)
        assert got["0th"][piexif.ImageIFD.Make] == b"TestCam"
        assert got["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:01:02 03:04:05"
        assert got["0th"][piexif.ImageIFD.Orientation] == 1
        out = Image.open(dst)
        assert got["Exif"][piexif.ExifIFD.PixelXDimension] == out.width
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_portrait_orientation_tag_is_applied_before_detection():
    """A portrait shot stored landscape plus an orientation tag: detecting on
    the stored pixels would look for verticals along the wrong axis."""
    import piexif
    from PIL import Image
    d = _tmp()
    try:
        sc = synth.Scene(w=800, h=1200, pitch_deg=9, roll_deg=0, seed=15)
        stored = cv2.rotate(sc.img, cv2.ROTATE_90_COUNTERCLOCKWISE)   # as the file holds it
        src = os.path.join(d, "p.jpg")
        Image.fromarray(cv2.cvtColor(stored, cv2.COLOR_BGR2RGB)).save(src, quality=95)
        piexif.insert(piexif.dump({"0th": {piexif.ImageIFD.Orientation: 6},
                                   "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}), src)
        r = process(src, os.path.join(d, "p_corr.jpg"), Settings())
        assert r.status == OK, r.line()
        assert abs(r.pitch_deg) > 3.0, r.line()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_dry_run_writes_nothing():
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=9, seed=16), os.path.join(d, "f.jpg"))
        dst = os.path.join(d, "f_corr.jpg")
        r = process(src, dst, Settings(), dry_run=True)
        assert r.status in (OK, SKIPPED)
        assert not os.path.exists(dst)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_same_input_gives_a_byte_identical_output_twice():
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=7, roll_deg=2, seed=17), os.path.join(d, "g.jpg"))
        a, b = os.path.join(d, "g1.jpg"), os.path.join(d, "g2.jpg")
        assert process(src, a, Settings()).status == OK
        assert process(src, b, Settings()).status == OK
        assert open(a, "rb").read() == open(b, "rb").read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_debug_overlay_is_written_when_asked():
    d = _tmp()
    try:
        src = _write(synth.Scene(pitch_deg=9, roll_deg=-2, seed=18), os.path.join(d, "h.jpg"))
        dbg = os.path.join(d, "debug")
        process(src, os.path.join(d, "h_corr.jpg"), Settings(), debug_dir=dbg)
        assert os.path.exists(os.path.join(dbg, "h_lines.jpg"))
        assert os.path.exists(os.path.join(dbg, "h_compare.jpg"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_skipped_image_can_be_written_into_a_new_output_folder():
    """The corrected path creates the output folder; the skipped path used not
    to, so a run into a fresh folder died on the first image it declined."""
    d = _tmp()
    try:
        src = os.path.join(d, "flat.jpg")
        cv2.imwrite(src, synth.flat_image())
        out = os.path.join(d, "does", "not", "exist", "flat_corr.jpg")
        r = process(src, out, Settings())
        assert r.status == SKIPPED, r.line()
        assert os.path.exists(out)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_mask_source_with_no_path_fails_once_not_per_file():
    """`--mask sam` without a checkpoint used to fail every image with what read
    as an internal fault. It is a configuration error and belongs before the
    run."""
    from bpc.cli import main as cli_main
    d = _tmp()
    try:
        cv2.imwrite(os.path.join(d, "a.jpg"), synth.Scene(pitch_deg=8, seed=61).img)
        assert cli_main([d, "--mask", "sam", "-o", os.path.join(d, "out")]) == 2
        assert cli_main([d, "--mask", "file", "-o", os.path.join(d, "out")]) == 2
        assert not os.path.exists(os.path.join(d, "out"))
    finally:
        shutil.rmtree(d, ignore_errors=True)
