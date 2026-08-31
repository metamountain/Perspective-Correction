# Line detectors: what was measured, and why the default did not change

The front end was the obvious suspect for the accuracy ceiling. LSD returns
*fragments*: on a real barn at 1600 px analysis resolution it produced 4823 raw
segments, 503 surviving the filters, with a **median length of 56 px** -- 3.5 %
of the long edge -- and only **29** longer than a tenth of the short edge.

M-LSD (Gu et al., AAAI 2022, Apache-2.0) is trained on wireframe data and
returns few, long, structural segments instead. The 512 large model is 6.1 MB
and is vendored in `models/`. It needs a TFLite runtime, which is optional:

    pip install ai-edge-litert        # or tflite-runtime on older setups
    python rectify.py "D:\Fotos" --detector mlsd

## What the detectors actually return

Same four real photographs, same 1600 px analysis grid:

| photo | detector | segments | median length | > 10 % of short edge | time |
|---|---|---|---|---|---|
| barn (4032 px) | LSD | 4823 | 16 px | 29 | 0.21 s |
| barn | M-LSD tiny | 106 | **318 px** | 103 of 106 | 0.10 s |
| barn | M-LSD large | 112 | 250 px | 99 of 112 | 0.57 s |
| pole barn | LSD | 1805 | 15 px | 157 | 0.09 s |
| pole barn | M-LSD large | 87 | 145 px | 85 of 87 | 1.64 s |

M-LSD's lines are roughly **20x longer**. On line length alone it is not close.

## Accuracy is not line length

Angular precision scales with length *and* with endpoint precision, and M-LSD
decodes its endpoints from a 256x256 displacement map. At 1400 px that is about
**5.5 px of endpoint quantisation**: on a 300 px line, roughly 1 degree. LSD's
endpoints are sub-pixel, so a 50 px fragment carries about 0.1 degrees. Long and
coarse loses to short and sharp.

### Synthetic scenes, exact ground truth (26 scenes)

| detector | pitch mean | p90 | max | roll mean | roll max |
|---|---|---|---|---|---|
| **LSD** | **0.21** | **0.34** | **1.76** | **0.060** | 0.402 |
| M-LSD tiny | 1.77 | 3.02 | 12.44 | 0.289 | 1.092 |
| M-LSD large | 1.42 | 2.56 | 4.93 | 0.267 | 0.597 |
| hybrid (LSD gated by M-LSD) | 1.37 | 2.51 | 18.39 | 0.357 | 7.869 |
| union (LSD + M-LSD) | 0.48 | 0.99 | 2.11 | 0.079 | 0.195 |

(focal length supplied, so the focal prior does not mask the effect)

**This benchmark is biased in LSD's favour** and it would be dishonest not to
say so: the scenes are rendered flat colour with clean thin lines, which is
exactly LSD's home turf and out of distribution for a network trained on
photographs.

### Real photographs, known applied rotation (24 measurements)

So the same question was asked a second way, on real imagery. A photograph's
true camera pose is unknown, but a rotation *we* apply is known exactly: if the
true world-up in camera coordinates is `u0` and the image is warped by `R_d`,
the warped copy's up must be `R_d @ u0`. Estimating from both and comparing
gives a real error in degrees on real texture, foliage and JPEG noise, without
ever knowing `u0`. `tools/benchmark_detectors.py` runs exactly this.

| detector | mean | p90 | max |
|---|---|---|---|
| LSD | 0.94 | 3.44 | **4.39** |
| M-LSD tiny | 1.59 | 3.46 | 3.90 |
| M-LSD large | 1.44 | 2.55 | 5.57 |
| **hybrid (LSD gated by M-LSD)** | **0.78** | 2.41 | 6.23 |
| union (LSD + M-LSD) | 1.25 | **2.23** | 4.84 |

The picture inverts. On real photographs the hybrid is best on mean and close to
best on p90 -- M-LSD's judgement of *which edge is structural* combined with
LSD's sub-pixel geometry.

## The decision

**LSD stays the default.** It wins decisively on synthetic ground truth and
loses only narrowly on 24 real samples, and the hybrid's 7.9 degree worst-case
roll on synthetic scenes shows it can gate away evidence it needed. Promoting a
detector on 24 measurements, against a benchmark where it loses badly, would be
exactly the mistake the rest of `accuracy.md` documents.

The alternatives ship as measured options:

| flag | when it is worth trying |
|---|---|
| `--detector hybrid` | real photographs, plenty of structure; best mean error measured on real input |
| `--detector union` | the safest addition: never discards anything, best p90 on real input, and unlike the hybrid it does not damage the synthetic case |
| `--detector mlsd` | heavy texture where LSD drowns in fragments; also the fastest way to see what the network considers structural |

## Deciding on your own photographs

Twenty-four measurements is not enough to promote a default. A few dozen of
your own is better evidence than any table here:

    python tools/benchmark_detectors.py "D:\Fotos" --focal-35mm 24

If the hybrid or union wins on your material by a clear margin, make it the
default with `--detector`, and please open an issue with the table -- that is
the evidence needed to change the shipped default.

## Not tried, and why

* **A bigger network.** 6.1 MB is the largest M-LSD there is, and capacity is
  not the limit here -- endpoint resolution is. A heavier wireframe model
  (LETR, HAWP, DeepLSD) would bring a torch dependency of hundreds of megabytes
  to address the wrong bottleneck. DeepLSD is the interesting one, because it
  refines network output with a classical optimiser at full resolution, which
  attacks the actual problem.
* **Tiling M-LSD** to raise its effective resolution. Plausible, unmeasured,
  and it multiplies the runtime.
* **ELSED**, faster than LSD with slightly better precision/recall, would be a
  drop-in for the classical path. Worth doing if runtime ever matters; it does
  not, at 0.2 s per image.
