"""Filling the band a rotation opens up, with pixels nobody photographed.

Correcting converging verticals rotates the frame, and a rotated rectangle does
not cover the output rectangle.  `warp.plan` answers that by cropping, and when
cropping would cost too much it keeps the whole frame and pads the corners --
smeared edge pixels, or a flat colour.  Both are honest and both are visibly
not photograph.

This module is the third answer: **generate** the missing band.

Read that sentence against the first line of CLAUDE.md.  The metric here is how
many photographs the tool ruins, and a generated band is, by construction,
content the camera never saw.  So:

* the default is `none`, and it stays `none`;
* the fill only ever touches pixels `warp.filled_region` marks as having no
  source behind them -- never a pixel that came off the sensor;
* a backend that is missing, or a ComfyUI that is not running, is an **error
  for that image**, not a silent fall-back to the padded version.  A batch that
  quietly writes un-filled frames when the user asked for a fill is the failure
  this project is built to avoid.

Two backends, because they answer different questions:

``lama``      LaMa (Suvorov et al., WACV 2022) through `simple-lama-inpainting`.
              Fourier convolutions, trained for large masks, no prompt, ~0.5 s.
              It continues *structure* -- brickwork, a roofline, sky gradient --
              and is the right tool for a band a few percent wide, which is what
              a plausible correction actually opens up.
``comfyui``   a diffusion model in a running ComfyUI, over HTTP.  Slower, needs
              a server, and invents rather than continues -- worth it when the
              band is wide, or when the result is going somewhere that wants a
              plausible sky rather than a stretched one.

**One model per worker process.**  The batch runs on a `ProcessPoolExecutor`, so
`-j 8 --fill lama` holds eight copies of a 196 MB network.  Measured at `-j 4`
over twelve photographs it costs nothing noticeable in time (2 s per image,
model load included), but on a long batch on a small machine `-j` is the knob
to turn down, not `--fill-max-edge`.

**`simple-lama-inpainting` pins `pillow<10` and `numpy<2` and pip will happily
downgrade both when installing it.**  That breaks OpenCV in the same
interpreter.  Install it with `pip install --no-deps simple-lama-inpainting`;
the pins are stale, the package works against current Pillow.  This is the same
lesson as `ultralytics` replacing `cv2.imread` -- an optional dependency can
change a required one.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

import cv2
import numpy as np

MODES = ("none", "telea", "lama", "comfyui")

# Which of them the manual review window may run on every redraw.  The test is
# not "is it good" but "does a slider tick still feel like a slider tick":
# ``telea`` loads nothing and costs milliseconds at preview size, while the two
# learned backends load a model and take seconds.  A user choosing a fill should
# see it happen; a user dragging a slider should not wait for a model.
LIVE_MODES = ("telea",)

# And how large it may generate while doing so.  ``--fill-max-edge`` (2048) is
# the setting for the file being saved; on a 900 px preview it costs 211 ms per
# redraw, which is four frames a second while a slider is moving.  At 480 px the
# same band takes 38 ms and deviates 1.7/255 from the full-resolution fill --
# invisible, because what is being generated is low-frequency by construction.
# The saved file is never rendered through this.
PREVIEW_MAX_EDGE = 480

_LOCK = threading.Lock()
_LAMA = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
WORKFLOWS = os.path.join(ROOT, "workflows")
DEFAULT_WORKFLOW = os.path.join(WORKFLOWS, "flux-klein-outpaint.json")

SHIPPED = (("flux2-klein-edit-nomask.json", "edit model -- image + instruction"),
           ("flux-klein-outpaint.json", "inpainting -- image + mask"))
"""The two shipped graphs, for the two shapes a generator comes in.

One table, because the window lists them and `status` has to name whichever one
is actually in force.  That naming is the point: two workflows ship for two
*incompatible* model shapes, and an unnamed default is how a FLUX.2 [klein]
**edit** model gets run through an `InpaintModelConditioning` graph -- which
produces a plausible-looking wrong band and a **green** light, because every
checkpoint it named was installed.  Nothing was missing; the wrong graph was
running.  So `workflow_path` reports whether anybody chose, and every state of
the indicator says which file it is talking about.
"""


def workflow_path(settings=None) -> tuple:
    """``(path, chosen)`` -- which graph will run, and whether anyone picked it.

    Split out because "nobody chose this" is a fact the indicator has to carry
    and an empty string cannot.  It is deliberately *not* a fifth light: the
    default is a usable workflow, so refusing it would be wrong, and folding it
    into `models` would put a workflow problem behind a label that reads
    "models missing".  It is named instead.
    """
    p = (getattr(settings, "comfy_workflow", "") or "").strip() if settings else ""
    return (p, True) if p else (DEFAULT_WORKFLOW, False)

# titles the workflow marks its three sockets with, so a user can rebuild the
# graph any way they like and only has to keep the labels
TITLE_IMAGE = "BPC_IMAGE"
TITLE_MASK = "BPC_MASK"
TITLE_PROMPT = "BPC_PROMPT"


class FillUnavailable(RuntimeError):
    pass


# --------------------------------------------------------------------------
# geometry of the hole
# --------------------------------------------------------------------------
def _prepare(bgr: np.ndarray, hole: np.ndarray, max_edge: int):
    """Downscaled copy plus mask, and the scale needed to come back.

    The models are trained around 512-1024 px and a 6000 px facade is both slow
    and pointless here: the hole is a thin band of sky, wall or ground, so what
    is being invented is low-frequency.  Only the hole is scaled back up and
    pasted, so every photographed pixel stays at full resolution and untouched.
    """
    h, w = bgr.shape[:2]
    s = min(1.0, float(max_edge) / max(h, w)) if max_edge > 0 else 1.0
    if s < 1.0:
        small = cv2.resize(bgr, (max(1, int(w * s)), max(1, int(h * s))),
                           interpolation=cv2.INTER_AREA)
        m = cv2.resize(hole.astype(np.uint8) * 255,
                       (small.shape[1], small.shape[0]),
                       interpolation=cv2.INTER_NEAREST)
    else:
        small, m = bgr.copy(), hole.astype(np.uint8) * 255
    return small, m


def _composite(bgr: np.ndarray, filled_small: np.ndarray, hole: np.ndarray,
               feather: int = 2) -> np.ndarray:
    """Paste the generated pixels into the hole and nowhere else.

    Feathered by a couple of pixels along the boundary because the generated
    band and the photograph meet at a hard edge otherwise, and the resampler
    has already left a fringe there.  The blurred alpha is multiplied by the
    hole again afterwards, so the ramp lives *inside* the hole: outside it the
    weight is exactly zero and the photographed pixel comes through bit for
    bit.  That is the promise this module makes, and it is asserted by
    ``test_the_fill_touches_nothing_that_was_photographed``.
    """
    h, w = bgr.shape[:2]
    gen = cv2.resize(filled_small, (w, h), interpolation=cv2.INTER_LANCZOS4)
    a = hole.astype(np.float32)
    if feather > 0:
        k = 2 * feather + 1
        a = cv2.GaussianBlur(a, (k, k), 0) * hole.astype(np.float32)
    a = np.clip(a, 0.0, 1.0)[..., None]
    return (bgr.astype(np.float32) * (1 - a) + gen.astype(np.float32) * a
            ).clip(0, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# TELEA (pixel propagation, no model)
# --------------------------------------------------------------------------
def _fill_telea(bgr: np.ndarray, hole: np.ndarray, settings) -> np.ndarray:
    """cv2.inpaint TELEA: fast-marching pixel propagation from the boundary.

    No model, no download, no prompt.  It continues colour and gradient rather
    than structure, so it is the honest choice for a thin band of sky or road
    and the wrong one for anything a viewer would read as content.  Runs at
    ``--fill-max-edge`` like the learned backends, for the same reason: the
    band is low-frequency and only the hole is scaled back up.
    """
    small, m = _prepare(bgr, hole, int(getattr(settings, "fill_max_edge", 2048)))
    radius = max(3, int(min(small.shape[:2]) * 0.01))
    gen = cv2.inpaint(small, m, radius, cv2.INPAINT_TELEA)
    return _composite(bgr, gen, hole)


# --------------------------------------------------------------------------
# LaMa
# --------------------------------------------------------------------------
def _lama_model(device: str = ""):
    global _LAMA
    with _LOCK:
        if _LAMA is None:
            try:
                from simple_lama_inpainting import SimpleLama
            except ImportError as exc:
                raise FillUnavailable(
                    "LaMa needs simple-lama-inpainting: "
                    "pip install --no-deps simple-lama-inpainting  "
                    "(--no-deps matters: its pins downgrade Pillow and numpy "
                    "and break OpenCV)") from exc
            try:
                _LAMA = SimpleLama(device=device) if device else SimpleLama()
            except Exception as exc:
                raise FillUnavailable(f"LaMa failed to load: {exc}") from exc
        return _LAMA


def _fill_lama(bgr: np.ndarray, hole: np.ndarray, settings) -> np.ndarray:
    from PIL import Image
    model = _lama_model(getattr(settings, "fill_device", ""))
    small, m = _prepare(bgr, hole, int(getattr(settings, "fill_max_edge", 2048)))
    img = Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB))
    out = model(img, Image.fromarray(m))
    gen = cv2.cvtColor(np.array(out.convert("RGB")), cv2.COLOR_RGB2BGR)
    if gen.shape[:2] != small.shape[:2]:      # LaMa pads to a multiple of 8
        gen = gen[:small.shape[0], :small.shape[1]]
    return _composite(bgr, gen, hole)


# --------------------------------------------------------------------------
# ComfyUI
# --------------------------------------------------------------------------
def load_workflow(path: str = "") -> dict:
    p = path or DEFAULT_WORKFLOW
    if not os.path.isfile(p):
        raise FillUnavailable(f"ComfyUI workflow not found: {p}")
    with open(p, "r", encoding="utf-8") as fh:
        wf = json.load(fh)
    if "nodes" in wf and "last_node_id" in wf:
        raise FillUnavailable(
            f"{p} is a ComfyUI *editor* export. The API format is what /prompt "
            "takes: in ComfyUI enable 'Dev mode' in settings, then "
            "'Save (API format)'.")
    return wf


def _find(wf: dict, title: str):
    for nid, node in wf.items():
        if node.get("_meta", {}).get("title") == title:
            return nid
    return None


def _workflow_note(path: str, chosen: bool, wf: dict = None) -> str:
    """``workflow: <file>, <shape>`` -- and whether anybody picked it.

    The shape is read off the graph rather than the filename, by the same test
    `_fill_comfy` uses: no ``BPC_MASK`` node means the band itself is the
    signal, which is what an edit model wants.  It is the discriminating fact
    and it belongs in every line, not only the green one.
    """
    note = "workflow: " + os.path.basename(path)
    if wf is not None:
        note += ", edit-model" if _find(wf, TITLE_MASK) is None else ", inpainting"
    if not chosen:
        note += " (shipped default -- nobody chose it)"
    return note


def _http(url: str, data=None, headers=None, timeout=30.0):
    import urllib.request
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _upload(base: str, name: str, png: bytes) -> str:
    """POST one PNG to /upload/image and return the name ComfyUI stored it as."""
    boundary = "----bpc" + uuid.uuid4().hex
    parts = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
        f"filename=\"{name}\"\r\nContent-Type: image/png\r\n\r\n".encode(),
        png,
        f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\""
        f"\r\n\r\ntrue\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(parts)
    raw = _http(base + "/upload/image", body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    got = json.loads(raw.decode("utf-8"))
    sub = got.get("subfolder") or ""
    return f"{sub}/{got['name']}" if sub else got["name"]


def _png(arr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", arr)
    if not ok:
        raise FillUnavailable("could not encode a PNG for ComfyUI")
    return buf.tobytes()


DEFAULT_COMFY_URL = "http://127.0.0.1:8188"

PRIME_GREY = 128
PRIME_MIX = 0.5
"""How the band is primed before a generator sees it.

What arrives at ComfyUI would otherwise be the *padded* band -- either black, or
`BORDER_REPLICATE`'s long straight streaks.  Both are bad starting points, and
in opposite ways: black is an edge the sampler will happily treat as content,
and the streaks are exactly the artifact that once cost this project two wrong
conclusions (see the round-trip section of the working notes).

So the hole is primed with TELEA first -- boundary colour propagated inwards,
no model, milliseconds -- and then pulled halfway towards mid grey. The TELEA
half gives the sampler the right colours and rough gradient to continue; the
grey half destroys the smeared *structure* TELEA invents along with them, so
nothing in the band reads as an edge worth preserving. Only the hole is
touched, and the result is a starting point, not an answer: the generator
replaces it.
"""


def split_url(url):
    """``http://host:port`` -> ``("http://host", "port")``.

    Tolerant on purpose: a URL with no port, or one someone half-edited, still
    has to come back as two fields rather than an exception in a constructor.
    """
    url = (url or DEFAULT_COMFY_URL).strip().rstrip("/")
    head, sep, tail = url.rpartition(":")
    if sep and tail.isdigit():
        return head, tail
    return url, ""


def join_url(host, port):
    """The inverse, with the scheme filled in when someone typed a bare host."""
    host = (host or "").strip().rstrip("/") or DEFAULT_COMFY_URL
    if "://" not in host:
        host = "http://" + host
    port = (port or "").strip()
    return f"{host}:{port}" if port.isdigit() else host



def _prime_for_generation(small: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """TELEA the hole, then blend it halfway to grey.  ``mask`` is 0/255."""
    if not mask.any():
        return small
    radius = max(3, int(min(small.shape[:2]) * 0.01))
    primed = cv2.inpaint(small, mask, radius, cv2.INPAINT_TELEA).astype(np.float32)
    primed = primed * (1.0 - PRIME_MIX) + float(PRIME_GREY) * PRIME_MIX
    out = small.copy()
    inside = mask > 0
    out[inside] = primed[inside].clip(0, 255).astype(np.uint8)
    return out


def _match_sent_shape(gen: np.ndarray, small: np.ndarray) -> np.ndarray:
    """Put what came back on the grid it was sent on.

    A workflow is free to resize -- a `ImageScaleDownToSize` capping the long
    edge at 2048 is a common and sensible thing to have in one -- and a band
    that returns at a lower resolution is **fine**: it is sky, wall and road at
    the frame edge, it is low-frequency by construction, and only the hole is
    kept. `_composite` scales it back up and the photograph itself never leaves
    full resolution, so the visible cost is a slightly softer few per cent at
    the very edge.

    What is not fine is a different *shape*. `_composite` maps the returned
    image onto the frame corner to corner, so a returned aspect ratio that
    differs from the one sent slides the whole band sideways and pastes wall
    where sky was. Nothing about that looks like an error afterwards; it looks
    like a bad inpaint. So the aspect is checked here, a real mismatch is
    refused with both sizes named, and only the resolution is allowed to change.
    """
    gh, gw = gen.shape[:2]
    sh, sw = small.shape[:2]
    if (gh, gw) == (sh, sw):
        return gen
    sent, back = sw / float(sh), gw / float(gh)
    if abs(sent - back) > 0.01 * sent:
        raise FillUnavailable(
            f"ComfyUI returned {gw}x{gh} for a {sw}x{sh} image -- a different "
            f"shape, not just a different size. The band would be pasted in the "
            f"wrong place. Check the workflow for a crop or a fixed-size latent; "
            f"a node that only scales is fine.")
    return cv2.resize(gen, (sw, sh), interpolation=cv2.INTER_LANCZOS4)


def _combo_options(decl):
    """The choices a COMBO input offers, in either schema shape.

    The same server answers both ways: older nodes declare the list inline as
    ``[[...], {...}]``, newer ones say ``["COMBO", {"options": [...]}]``.
    """
    if not isinstance(decl, (list, tuple)) or not decl:
        return None
    head = decl[0]
    if isinstance(head, list):
        return head
    if head == "COMBO" and len(decl) > 1 and isinstance(decl[1], dict):
        return decl[1].get("options")
    return None


def _score(wanted, candidate):
    """How much two model filenames look like the same thing."""
    def toks(s):
        s = os.path.basename(str(s)).lower()
        s = s.rsplit(".", 1)[0]
        return {t for t in "".join(c if c.isalnum() else " " for c in s).split() if t}
    a, b = toks(wanted), toks(candidate)
    return len(a & b) / float(len(a | b)) if a | b else 0.0


def resolve_models(wf: dict, base: str, timeout: float = 30.0) -> list:
    """Point every loader at a file this server actually has.

    A workflow names checkpoints, and the names are the first thing that is
    wrong on somebody else's machine -- the shipped one says
    ``flux2-klein-9b.safetensors`` and a real install has
    ``flux-2-klein-9b-fp8.safetensors``.  ComfyUI answers that with three
    ``Value not in list`` errors and ignores the output, which is a correct
    message about the wrong problem.

    So each COMBO whose value the server does not offer is replaced by the
    closest name it does, by shared filename tokens.  Substitutions are
    **returned, not silent**: this is a guess about which of 46 text encoders was
    meant, and a guess that nobody is told about is how a batch quietly produces
    something else.  A value the server already has is never touched, and when
    nothing scores well enough the original is left in place for ComfyUI to
    reject with its own, better message.
    """
    try:
        info = json.loads(_http(base.rstrip("/") + "/object_info",
                                timeout=timeout).decode())
    except Exception:
        return [], []                  # unreachable is the caller's problem
    swapped, unresolved = [], []
    for node in wf.values():
        # Never the nodes BPC fills itself.  Their `image` is a COMBO too --
        # ComfyUI offers whatever is sitting in its input folder -- so an
        # unguarded resolver helpfully rewrites the photograph about to be
        # uploaded into somebody else's leftover PNG.
        if node.get("_meta", {}).get("title", "").startswith("BPC_"):
            continue
        if node.get("class_type") in ("LoadImage", "LoadImageMask", "LoadImageOutput"):
            continue
        spec = info.get(node.get("class_type"), {}).get("input", {})
        for section in ("required", "optional"):
            for name, decl in (spec.get(section) or {}).items():
                value = node.get("inputs", {}).get(name)
                if not isinstance(value, str):
                    continue
                options = _combo_options(decl)
                if not options or value in options:
                    continue
                best = max(options, key=lambda c: _score(value, c))
                if _score(value, best) < 0.34:      # too different to guess at
                    unresolved.append(f"{node['class_type']}.{name}: {value}")
                    continue
                node["inputs"][name] = best
                swapped.append(f"{node['class_type']}.{name}: {value} -> {best}")
    return swapped, unresolved


# Which loader input each explicit choice belongs to.  One place, because the
# GUI offers these three and `_fill_comfy` applies them.
MODEL_SLOTS = (("comfy_unet", "UNETLoader", "unet_name"),
               ("comfy_clip", "CLIPLoader", "clip_name"),
               ("comfy_vae", "VAELoader", "vae_name"))


def model_options(base: str, timeout: float = 30.0) -> dict:
    """``{"comfy_unet": [...], ...}`` -- what this server has installed.

    Asked of the server rather than read off a folder, because the server is
    the authority on what it will accept: it may be on another machine, and its
    `extra_model_paths.yaml` may point somewhere no local walk would find.
    """
    try:
        info = json.loads(_http(base.rstrip("/") + "/object_info",
                                timeout=timeout).decode())
    except Exception:
        return {}
    out = {}
    for key, cls, field in MODEL_SLOTS:
        decl = info.get(cls, {}).get("input", {}).get("required", {}).get(field)
        out[key] = list(_combo_options(decl) or [])
    return out


def apply_model_choices(wf: dict, settings) -> list:
    """Write the explicitly chosen files over whatever the workflow names.

    Applied *before* `resolve_models`, so a name the user picked from the
    server's own list is never second-guessed by the matcher.
    """
    used = []
    for key, cls, field in MODEL_SLOTS:
        value = (getattr(settings, key, "") or "").strip()
        if not value:
            continue
        for node in wf.values():
            if node.get("class_type") == cls and field in node.get("inputs", {}):
                node["inputs"][field] = value
                used.append(f"{cls}.{field} = {value}")
    return used


def status(settings) -> tuple:
    """``(state, line)`` for the window's indicator.  Three states, not two.

    ``down``    nothing will run: no answer from the server, or a workflow that
                cannot be posted -- an editor export, or one with no
                ``BPC_IMAGE``.
    ``models``  the server answered and the graph is sound, but the checkpoints
                it names are not the ones installed. The fill may still work,
                because the matcher substitutes what it can -- and that is
                exactly why this is its own colour rather than green. A guess is
                in force, and a guess about which of forty-six text encoders was
                meant deserves a look before a batch runs on it.
    ``ok``      every name resolves as written, nothing guessed.

    Two states would have to fold "running on a guess" into one of the other
    two, and both readings are wrong: green would hide it, red would refuse
    something that works.
    """
    base = getattr(settings, "comfy_url", DEFAULT_COMFY_URL)
    path, chosen = workflow_path(settings)
    try:
        stats = json.loads(_http(base.rstrip("/") + "/system_stats",
                                 timeout=5.0).decode())
    except Exception as exc:
        return "down", f"no answer from {base} ({exc}); {_workflow_note(path, chosen)}"
    version = stats.get("system", {}).get("comfyui_version", "?")
    try:
        wf = load_workflow(path)
    except FillUnavailable as exc:
        return "down", str(exc)
    if _find(wf, TITLE_IMAGE) is None:
        return "down", (f"{_workflow_note(path, chosen)} has no node titled "
                        f"{TITLE_IMAGE}, so BPC has nowhere to put the "
                        f"photograph (right-click the LoadImage node in "
                        f"ComfyUI > Title)")

    # Named in every state below, not just the green one.  A graph running the
    # wrong shape passes every check this function makes -- see `SHIPPED`.
    note = _workflow_note(path, chosen, wf)
    apply_model_choices(wf, settings)
    swapped, unresolved = resolve_models(wf, base)
    if unresolved:
        return "models", (f"ComfyUI {version} is up, {note}, but not installed: "
                          + "; ".join(unresolved))
    if swapped:
        return "models", (f"ComfyUI {version} is up, {note}; guessing at "
                          + "; ".join(swapped))
    return "ok", f"ComfyUI {version}, {note}, every model present"


def _fill_comfy(bgr: np.ndarray, hole: np.ndarray, settings) -> np.ndarray:
    base = getattr(settings, "comfy_url", "http://127.0.0.1:8188").rstrip("/")
    wf = load_workflow(workflow_path(settings)[0])
    # Chosen names first, then a guess for anything still absent.  Both happen
    # before the uploads, so nothing can rewrite the filename BPC just posted.
    apply_model_choices(wf, settings)
    resolve_models(wf, base)          # (swapped, unresolved) -- `status` reports them
    n_img, n_mask = _find(wf, TITLE_IMAGE), _find(wf, TITLE_MASK)
    if n_img is None:
        raise FillUnavailable(
            f"the workflow needs a node titled {TITLE_IMAGE} (rename it in "
            "ComfyUI: right-click > Title)")

    small, m = _prepare(bgr, hole, int(getattr(settings, "fill_max_edge", 2048)))
    small = _prime_for_generation(small, m)
    stamp = uuid.uuid4().hex[:12]
    try:
        wf[n_img]["inputs"]["image"] = _upload(base, f"bpc_{stamp}.png", _png(small))
        # `BPC_MASK` is optional, because a whole family of edit models takes an
        # image and an instruction and has nowhere to put a mask.  For those the
        # priming *is* the signal: the band arrives as flat grey-tinted colour
        # and the prompt says to replace it.  BPC's own guarantee does not rest
        # on the workflow honouring a mask in any case -- `_composite` puts the
        # result back through the hole and nowhere else, so a model that repaints
        # the whole frame still cannot touch a photographed pixel.
        if n_mask is not None:
            wf[n_mask]["inputs"]["image"] = _upload(base, f"bpc_{stamp}_mask.png",
                                                    _png(m))
    except Exception as exc:
        raise FillUnavailable(
            f"ComfyUI at {base} did not accept the upload ({exc}). Is it "
            "running, and is --comfy-url right?") from exc

    n_prompt = _find(wf, TITLE_PROMPT)
    text = getattr(settings, "comfy_prompt", "")
    if n_prompt is not None and text:
        wf[n_prompt]["inputs"]["text"] = text
    seed = int(getattr(settings, "comfy_seed", 0) or 0)
    if seed:
        for node in wf.values():                    # every sampler, one seed
            for key in ("seed", "noise_seed"):
                if key in node.get("inputs", {}):
                    node["inputs"][key] = seed

    client = "bpc-" + stamp
    body = json.dumps({"prompt": wf, "client_id": client}).encode("utf-8")
    try:
        got = json.loads(_http(base + "/prompt", body,
                               {"Content-Type": "application/json"}).decode())
    except Exception as exc:
        raise FillUnavailable(
            f"ComfyUI rejected the workflow ({exc}). The usual cause is a node "
            "or a model filename that does not exist on that server -- open "
            "the workflow in ComfyUI and run it once by hand.") from exc
    pid = got.get("prompt_id")
    if not pid:
        raise FillUnavailable(f"ComfyUI returned no prompt_id: {got}")

    deadline = time.time() + float(getattr(settings, "comfy_timeout", 300.0))
    while True:
        if time.time() > deadline:
            raise FillUnavailable(f"ComfyUI did not finish within the timeout ({pid})")
        time.sleep(0.7)
        try:
            hist = json.loads(_http(f"{base}/history/{pid}", timeout=15.0).decode())
        except Exception:
            continue
        entry = hist.get(pid)
        if not entry:
            continue
        status = entry.get("status", {})
        if status.get("status_str") == "error" or status.get("completed") is False:
            raise FillUnavailable(f"the ComfyUI run failed: {status}")
        images = [im for out in entry.get("outputs", {}).values()
                  for im in out.get("images", [])]
        if not images:
            if status.get("completed"):
                raise FillUnavailable(
                    "the workflow completed but saved no image -- it needs a "
                    "SaveImage or PreviewImage node")
            continue
        im = images[-1]
        import urllib.parse
        q = urllib.parse.urlencode({"filename": im["filename"],
                                    "subfolder": im.get("subfolder", ""),
                                    "type": im.get("type", "output")})
        raw = _http(f"{base}/view?{q}", timeout=60.0)
        gen = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if gen is None:
            raise FillUnavailable("could not decode the image ComfyUI returned")
        return _composite(bgr, _match_sent_shape(gen, small), hole)


# --------------------------------------------------------------------------
# the seam
# --------------------------------------------------------------------------
def fill(bgr: np.ndarray, hole: np.ndarray, settings) -> tuple:
    """``(image, note)``.  Raises ``FillUnavailable`` rather than falling back.

    ``hole`` is ``warp.filled_region``: True where the output has no source
    pixel behind it.  Nothing else is ever modified.
    """
    mode = getattr(settings, "fill", "none")
    if mode in ("", "none"):
        return bgr, ""
    if mode not in MODES:
        raise FillUnavailable(f"unknown fill mode: {mode}")
    if hole is None or not bool(np.any(hole)):
        return bgr, "nothing to fill"
    share = float(np.mean(hole))
    cap = float(getattr(settings, "fill_max_share", 0.35))
    if share > cap:
        raise FillUnavailable(
            f"the hole is {share:.0%} of the frame, over --fill-max-share "
            f"({cap:.0%}). That much invented content is a picture, not a "
            f"correction; crop instead")
    t0 = time.time()
    if mode == "telea":
        out = _fill_telea(bgr, hole, settings)
    elif mode == "lama":
        out = _fill_lama(bgr, hole, settings)
    else:
        out = _fill_comfy(bgr, hole, settings)
    return out, f"{mode} filled {share:.1%} in {time.time() - t0:.1f}s"


def available(mode: str, settings=None) -> bool:
    if mode in ("", "none"):
        return True
    if mode == "telea":
        return True
    if mode == "lama":
        try:
            import simple_lama_inpainting  # noqa: F401
            return True
        except Exception:
            return False
    if mode == "comfyui":
        base = getattr(settings, "comfy_url", "http://127.0.0.1:8188").rstrip("/")
        try:
            _http(base + "/system_stats", timeout=3.0)
            load_workflow(workflow_path(settings)[0])
            return True
        except Exception:
            return False
    return False


def describe(mode: str, settings=None) -> str:
    """One line for ``--fill-info`` and the diagnostics header."""
    if mode in ("", "none"):
        return "fill: off -- padded corners stay padded"
    if mode == "telea":
        return "telea: cv2.inpaint pixel propagation (no model, no download)"
    if mode == "lama":
        try:
            import simple_lama_inpainting  # noqa: F401
            return "lama: simple-lama-inpainting importable (weights download on first use)"
        except Exception as exc:
            return f"lama: unavailable ({exc})"
    base = getattr(settings, "comfy_url", "http://127.0.0.1:8188").rstrip("/")
    parts = []
    try:
        stats = json.loads(_http(base + "/system_stats", timeout=3.0).decode())
        sysinfo = stats.get("system", {})
        parts.append(f"comfyui at {base}: up (ComfyUI {sysinfo.get('comfyui_version', '?')})")
    except Exception as exc:
        parts.append(f"comfyui at {base}: unreachable ({exc})")
    wf_path, chosen = workflow_path(settings)
    try:
        wf = load_workflow(wf_path)
        have = [t for t in (TITLE_IMAGE, TITLE_MASK, TITLE_PROMPT) if _find(wf, t)]
        # The filename first: two graphs ship for two incompatible model shapes,
        # and a node count answers a question nobody asked while hiding the one
        # that decides whether the run means anything.
        note = _workflow_note(wf_path, chosen, wf)
        note += f", {len(wf)} nodes, sockets {have or 'none'}"
        if TITLE_IMAGE not in have:
            note += f" -- {TITLE_IMAGE} is REQUIRED and missing"
        elif TITLE_MASK not in have:
            note += (f" -- edit-model mode: no {TITLE_MASK}, so the primed band "
                     f"and the prompt do the work")
        parts.append(note)
    except FillUnavailable as exc:
        parts.append(str(exc))
    return "; ".join(parts)
