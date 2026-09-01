# ComfyUI workflows for `--fill comfyui`

`flux-klein-outpaint.json` is a starting point, not a guarantee. It is written
for **FLUX.2 [klein] 9B** and it names three files that almost certainly differ
from what your ComfyUI has installed:

| node | input | change it to |
|---|---|---|
| `UNETLoader` | `flux2-klein-9b.safetensors` | whatever your `models/unet` holds |
| `CLIPLoader` | `mistral_3_small_flux2_fp8_scaled.safetensors` | your FLUX.2 text encoder |
| `VAELoader` | `flux2_vae.safetensors` | your FLUX.2 VAE |

The fastest way to fix it is not to edit the JSON: open ComfyUI, build (or fix)
the graph there until it runs by hand, then **Save (API format)** and point
`--comfy-workflow` at the result. The editor's plain "Save" produces a different
file that `/prompt` cannot take -- BPC detects that one and says so.

## The contract is three node titles

BPC does not care what the graph does. It looks for nodes by their **title**
(right-click a node > Title) and fills them in:

| title | what BPC puts there | required |
|---|---|---|
| `BPC_IMAGE` | the corrected photograph, uploaded as a PNG | yes |
| `BPC_MASK` | white where the rotation opened a hole, black elsewhere | yes |
| `BPC_PROMPT` | the text from `--comfy-prompt`, when given | no |

Both image nodes must be `LoadImage`-shaped -- BPC writes the uploaded filename
into their `image` input. The mask arrives as an image, so the shipped graph
converts it with `ImageToMask` on the red channel; keep that, or use whatever
your inpainting nodes expect.

`--comfy-seed` overwrites every `seed` and `noise_seed` in the graph, which is
what makes a batch reproducible. Left at 0, the workflow's own seeds stand.

Any node that saves or previews an image ends the run; BPC takes the last image
the run produced.

## What BPC does with the result

It pastes it **into the hole and nowhere else**. The generated frame is resized
to the output, and the composite ramps its alpha inside the hole mask, so every
pixel that came off the sensor survives bit for bit no matter what the model
returned. A workflow that changes the whole image -- an upscaler, a relight, a
style transfer -- will therefore appear to do almost nothing. That is deliberate:
this is a perspective corrector, and the only pixels it is entitled to invent
are the ones the rotation left empty.
