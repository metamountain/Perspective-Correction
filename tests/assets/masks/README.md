# Cached region masks for the test assets

One PNG per photograph in `tests/assets/`, matched by file stem.

    python rectify.py tests/assets --mask-export tests/assets/masks

## Convention

**White means *ignore*.** That is what `masks.load` expects, so these work with
a plain `--mask file --mask-file tests/assets/masks` and **no `--mask-invert`**.
BiRefNet itself returns the opposite — the subject — and `birefnet.build_mask`
does the inversion before anything is written.

Stored at the analysis resolution (long edge 1600), not at the photograph's
resolution; `masks.load` resamples. That is why six masks are 42 KB.

Produced by `BiRefNet-HR.safetensors` (444 MB, 2048 px inference) at the default
threshold of 0.5. Regenerate with the command above if the model or the
threshold changes — the numbers in `docs/masking.md` are tied to these.

## What the cache is for, and the one thing it must not be used for

It exists so the mask is computed **once** instead of once per run: BiRefNet
needs torch and ~0.3 s per photograph, and the test suite re-analyses this
folder many times over. With the cache, every mask-dependent test runs in an
interpreter that has neither torch nor a 444 MB checkpoint.

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
