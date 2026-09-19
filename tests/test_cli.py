"""CLI wiring: flags reach the pipeline, nothing more.

The roi_x seam itself (``pipeline.analyse(roi_x=)`` / ``review.set_roi_x``) is
covered in test_pipeline and test_review; what this module pins is that the
command line can actually switch it on -- a flag that parses to a pair and a
job tuple that carries it through to ``process``.
"""
import sys

import pc.cli as cli
from pc.config import Settings


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


class _NotATty:
    """A stand-in for sys.stdin when nothing interactive is attached --
    piped input, a double-clicked .bat, a CI runner. isatty() is the whole
    signal main() uses to tell that apart from someone at a keyboard."""

    def isatty(self):
        return False


def test_overwrite_without_yes_refuses_when_stdin_is_not_a_tty():
    """--overwrite replaces the user's original files, so a caller nobody
    can answer for must never be waved through. An audit once filed the
    isatty() check itself as the defect ("blocks piped double-click") --
    but that check *is* the safety: with no --yes, ``echo y | rectify
    --overwrite *.jpg`` correctly does not work either, because reading a
    piped "y" would let an accidental redirect authorise destroying
    originals just as easily as a deliberate one. See the comment above
    the check in cli.py for the full reasoning; this pins the property it
    defines.

    The defining property is not the wording of the message -- it is that
    the pipeline never runs. A "fix" that prints the warning and then
    proceeds anyway would still make a message-only assertion pass while
    destroying files, so this also asserts ``process`` was never called.
    """
    import contextlib
    import io

    calls = []

    def fake_process(src, dst, settings, debug_dir=None, dry_run=False, roi_x=None):
        calls.append(src)
        return "result"

    real_collect, real_stdin, real_process = cli.collect, sys.stdin, cli.process
    cli.collect = lambda inputs, recursive: ["fake.jpg"]
    cli.process = fake_process
    sys.stdin = _NotATty()
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["fake.jpg", "--overwrite"])
    finally:
        cli.collect, sys.stdin, cli.process = real_collect, real_stdin, real_process

    assert rc == 1, f"a non-tty --overwrite without --yes must refuse (got rc={rc})"
    assert not calls, "the pipeline ran even though the overwrite gate should have refused first"
    # Substring, not equality -- apply_prefs may print a leading "# using
    # remembered ..." line that depends on this machine's saved prefs.
    assert "--yes" in buf.getvalue(), "the refusal must name --yes as the way to mean it"


def test_overwrite_with_yes_skips_the_prompt_even_off_a_tty():
    """--yes ("do not ask before overwriting", build_parser) is the
    documented non-interactive escape hatch, so a .bat or CI caller that
    really does want to overwrite is not stuck -- it puts --yes in the
    command. This is the complement of the refusal test above: without it,
    a "fix" that refused --overwrite unconditionally would also pass that
    test.

    Uses a file that does not exist so a real, harmless read failure (not
    a destructive write) is what proves the gate let it through -- if the
    gate had refused, main() would return 1 with the "needs --yes" message
    before ever trying to read anything.
    """
    import contextlib
    import io

    real_collect, real_stdin = cli.collect, sys.stdin
    cli.collect = lambda inputs, recursive: ["definitely_missing_xyz.jpg"]
    sys.stdin = _NotATty()
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["definitely_missing_xyz.jpg", "--overwrite", "--yes"])
    finally:
        cli.collect, sys.stdin = real_collect, real_stdin

    out = buf.getvalue()
    assert "needs --yes" not in out, "--yes should skip the gate's refusal entirely"
    assert rc != 1, f"--yes must not be treated like a refused, unanswered prompt (rc={rc})"
