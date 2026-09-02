# ComfyUI workflows for `--fill comfyui`

One ships, and it is the edit-model shape:

| file | model shape | needs a mask |
|---|---|---|
| `flux2-klein-edit-nomask.json` | **edit model**: image + instruction | no |

There were two. The other, `flux-klein-outpaint.json`, was an inpainting graph
(image + mask + prompt) **and it was the unnamed default**, so an edit model
picked in the model selector was fed through its `InpaintModelConditioning`
node. The band came back wrong at a **green** light: every checkpoint the
workflow named was installed, so nothing was missing. The wrong graph was
running. It has been deleted rather than demoted.

Two things follow.

**The indicator names the file, in every state**, because `--comfy-workflow`
still takes any graph and "connected" without saying *to what* is that failure
waiting to happen again:

    ComfyUI 0.34.0, workflow: flux2-klein-edit-nomask.json, edit-model,
    every model present

and when nobody has picked, it says so rather than staying quiet:

    ... (shipped default -- nobody chose it) ...

**`BPC_MASK` still works and no longer has an example.** BPC uploads the hole
and writes it into that node whenever a graph has one, so an inpainting
workflow you build yourself behaves exactly as before -- there is just nothing
bundled to copy. The wiring is in "The contract is three node titles" below,
and `test_a_masked_workflow_still_gets_its_mask` builds its own graph so the
branch cannot rot unnoticed.

An edit model has nowhere to put a mask, so the *band itself* carries the
information. BPC primes it before uploading -- TELEA propagates the
boundary colour inwards and the result is pulled halfway to mid grey -- and the
prompt says `remove grey border`. `docs/outpaint-band-example.jpg` is what that
looks like before priming: a corrected frame whose rotation opened a flat grey
band at the edges.

Either way the guarantee is the same and does not depend on the workflow
honouring anything: BPC composites the returned image back through the hole and
nowhere else, so a model that repaints the whole frame still cannot move a
photographed pixel.

`flux2-klein-edit-nomask.json` was produced by flattening a ComfyUI *subgraph*
export -- the editor stores such a node as an opaque UUID type, and its 17 inner
nodes had to be inlined and their positional widget values named against the
running server's `/object_info`. Worth knowing if you ever need to do it again:
a widget promoted to a subgraph boundary appears as a socket **and** keeps its
slot in `widgets_values`, so dropping the linked names before zipping shifts
every later value by one. That is how a `UNETLoader` acquires a checkpoint
filename as its `weight_dtype`.

The two editor exports it was flattened from are kept as
`docs/comfyui-flux2-klein-edit-template*.json`. They are provenance, **not**
usable workflows -- `/prompt` cannot take an editor export -- which is why they
do not live in this folder.


**The shipped graph names three files your ComfyUI probably does not have:**

| node | input | change it to |
|---|---|---|
| `UNETLoader` | `flux-2-klein-9b-fp8.safetensors` | whatever your `models/unet` holds |
| `CLIPLoader` | `qwen_3_8b_fp8mixed.safetensors` | the encoder **that model** wants |
| `VAELoader` | `flux2-vae.safetensors` | your FLUX.2 VAE |

BPC checks these three against the server before it posts, substitutes the
closest name it finds, and lights the panel amber when it had to guess. **The
guess is by filename, and a filename does not know what a model is compatible
with.** The middle row is the example: a graph once named
`mistral_3_small_flux2_fp8_scaled.safetensors`, an install had
`mistral_3_small_flux2_fp8.safetensors`, and the substitution was a good match
and the wrong encoder -- Klein 9B wants Qwen3 here, and ComfyUI failed with

    mat1 and mat2 shapes cannot be multiplied (512x15360 and 12288x4096)

which is a text-embedding width, not the VAE it looks like. If the panel is
amber, pick the three explicitly in the selectors beside it.

The fastest way to fix a graph is not to edit the JSON: open ComfyUI, build (or
fix) it there until it runs by hand, then **Save (API format)** and point
`--comfy-workflow` at the result. The editor's plain "Save" produces a
different file that `/prompt` cannot take -- BPC detects that one and says so.

## The contract is three node titles

BPC does not care what the graph does. It looks for nodes by their **title**
(right-click a node > Title) and fills them in:

| title | what BPC puts there | required |
|---|---|---|
| `BPC_IMAGE` | the corrected photograph, uploaded as a PNG | yes |
| `BPC_MASK` | white where the rotation opened a hole, black elsewhere | no -- see below |
| `BPC_PROMPT` | the text from `--comfy-prompt`, when given | no |

Both image nodes must be `LoadImage`-shaped -- BPC writes the uploaded filename
into their `image` input. The mask arrives as an image, so an inpainting graph
needs `ImageToMask` on the red channel between the `BPC_MASK` node and whatever
takes a `MASK`; the shipped graph has no mask node at all, so there is nothing
in it to copy.

**A graph that runs perfectly by hand is still refused without these**, and the
refusal names the file: `workflow: outpaint.json has no node titled BPC_IMAGE`.
Nothing guesses which `LoadImage` was meant -- on a graph with two of them the
guess would be silent and wrong half the time -- so it is three right-clicks in
ComfyUI, then Save (API format) again.

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
