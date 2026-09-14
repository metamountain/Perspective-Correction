# analysis (local only)

A scratch folder for diagnostic images -- overlays, before/after pairs, the
kind of picture you make to understand *why* a photograph behaves the way it
does. The whole folder is git-ignored and never pushed: the images are
regenerable from the assets and the pipeline, and this is private working space.

## How to make one

The pipeline's own debug output is the fastest route:

    python rectify.py "tests/assets" -o analysis --debug-dir analysis

That writes, per asset, `<name>_lines.jpg` (detected verticals, horizontals and
the implied horizon, with the decision in the banner), `<name>_compare.jpg`
(before/after) and `<name>_corr.jpg` (the corrected output) -- all into this
folder, leaving tests/assets untouched. Add `--dry-run` for only the overlays.

## What to read in one

Confidence is multiplicative, so one weak term vetoes the rest. The overlay's
colours say where the evidence came from: green = vertical inliers, yellow =
rejected vertical candidates, blue = horizontals, magenta = the implied horizon.
When a photograph sits near the gate, the diagnostics name the weakest term --
that is the thing to look at, not the lines that already agree.

Worked example: `wilsdruff-scheunen-6` scores share/count/spread/horizon all
high but **stability 0.47**, because an oblique row of barns offers few,
scattered verticals over a strongly foreshortened perspective -- so the RANSAC
solution wobbles and the confidence pendulums across seeds. That is why it is
kept as an asset but not marked `_skip`.
