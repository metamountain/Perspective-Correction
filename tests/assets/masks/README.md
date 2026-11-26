# Cached region masks for the test assets

One PNG per photograph in `tests/assets/`, matched by file stem.

They are a static fixture: the tool that generated them (`--mask-export`,
BiRefNet) is retired, so they are committed and not regenerated.

## Convention

**White means *ignore*.** That is what `masks.load` expects, so these work with
a plain `--mask file --mask-file tests/assets/masks` and **no `--mask-invert`**.
A segmenter returns the opposite (the subject); the writer inverts before
anything is stored.

Stored at the analysis resolution (long edge 1600), not at the photograph's
resolution; `masks.load` resamples. That is why six masks are 42 KB.

Produced by a now-retired segmenter (BiRefNet-HR); they are a committed
fixture, not regenerated — the numbers in `docs/masking.md` are tied to these.

## What the cache is for, and the one thing it must not be used for

It exists so the mask is read **once** instead of recomputed per run: the test
suite re-analyses this folder many times over, and a cached PNG keeps every
mask-dependent test runnable in an interpreter without torch.

**It must not be used for the round-trip test.** That test warps each photograph
by a known rotation and re-estimates, so the building is in a different place in
every warped copy while the cached mask still describes the original. Measured
on `Alte_Scheune.jpg`:

| | live mask vs cached mask |
|---|---|
| unwarped original | **IoU 1.000** — byte-identical, the cache is exact |
| warped copy | IoU 0.802 — the cache is stale |

Using it anyway does not fail loudly, it just quietly reports a worse estimator:
0.69° mean / 2.35° worst against the true 0.65° / 1.36°. A round-trip
measurement has to mask each warped copy live.
