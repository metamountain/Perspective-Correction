"""CLI wiring: flags reach the pipeline, nothing more.

The roi_x seam itself (``pipeline.analyse(roi_x=)`` / ``review.set_roi_x``) is
covered in test_pipeline and test_review; what this module pins is that the
command line can actually switch it on -- a flag that parses to a pair and a
job tuple that carries it through to ``process``.
"""
import bpc.cli as cli
from bpc.config import Settings


def test_roi_x_flag_parses_to_a_pair_and_defaults_off():
    p = cli.build_parser()
    a = p.parse_args(["x.jpg", "--roi-x", "100", "500"])
    assert list(a.roi_x) == [100.0, 500.0]
    b = p.parse_args(["x.jpg"])
    assert b.roi_x is None


def test_job_passes_roi_x_through_to_process():
    seen = {}

    def fake_process(src, dst, settings, debug_dir=None, dry_run=False, roi_x=None):
        seen["roi_x"] = roi_x
        return "result"

    real = cli.process
    cli.process = fake_process
    try:
        out = cli._job(("s.jpg", "d.jpg", Settings(), None, False, [100.0, 500.0]))
    finally:
        cli.process = real
    assert out == "result"
    assert seen["roi_x"] == [100.0, 500.0]


def test_job_leaves_roi_x_none_when_unset():
    seen = {}

    def fake_process(src, dst, settings, debug_dir=None, dry_run=False, roi_x=None):
        seen["roi_x"] = roi_x
        return "result"

    real = cli.process
    cli.process = fake_process
    try:
        cli._job(("s.jpg", "d.jpg", Settings(), None, False, None))
    finally:
        cli.process = real
    assert seen["roi_x"] is None
