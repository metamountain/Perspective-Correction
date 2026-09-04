# analysis

A scratch folder for diagnostic images -- overlays, before/after pairs, the
kind of picture you make to understand *why* a photograph behaves the way it
does. Everything here except this file is git-ignored: the images are
regenerable from the assets and the pipeline, and they are large.

## How to make one

The pipeline's own debug output is the fastest route:

    python rectify.py "tests/assets/<asset>.jpg" --debug-dir analysis --dry-run

That writes `<asset>_lines.jpg` (the detected verticals, horizontals and the
implied horizon) and, without `--dry-run`, `<asset>_compare.jpg` (before/after).

For a side-by-side of the overlay and the correction at a chosen focal length,
call `preview.overlay` and `warp` directly -- see the session notes; a
known focal length is passed as an EXIF-equivalent so `f_source` reads `exif`
rather than a guess.

## What to read in one

The confidence is multiplicative, so one weak term vetoes the rest. The
overlay's colours say where the evidence came from (green = vertical inliers,
yellow = rejected vertical candidates, blue = horizontals, magenta = the
implied horizon). When a photograph sits near the gate, the diagnostics name
the weakest term -- that is the thing to look at, not the lines that already
agree.

Example already studied: `wilsdruff-scheunen-6` scores share/count/spread/
horizon all high but **stability 0.47**, because an oblique row of barns offers
few, scattered verticals over a strongly foreshortened perspective -- so the
RANSAC solution wobbles and the confidence pendulums across seeds. That is why
it is kept as an asset but not marked `_skip`.
