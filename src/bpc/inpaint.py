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


def _fill_comfy(bgr: np.ndarray, hole: np.ndarray, settings) -> np.ndarray:
    base = getattr(settings, "comfy_url", "http://127.0.0.1:8188").rstrip("/")
    wf = load_workflow(getattr(settings, "comfy_workflow", ""))
    n_img, n_mask = _find(wf, TITLE_IMAGE), _find(wf, TITLE_MASK)
    if n_img is None or n_mask is None:
        raise FillUnavailable(
            f"the workflow needs a node titled {TITLE_IMAGE} and one titled "
            f"{TITLE_MASK} (rename them in ComfyUI: right-click > Title)")

    small, m = _prepare(bgr, hole, int(getattr(settings, "fill_max_edge", 2048)))
    stamp = uuid.uuid4().hex[:12]
    try:
        wf[n_img]["inputs"]["image"] = _upload(base, f"bpc_{stamp}.png", _png(small))
        wf[n_mask]["inputs"]["image"] = _upload(base, f"bpc_{stamp}_mask.png", _png(m))
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
        return _composite(bgr, gen, hole)


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
            load_workflow(getattr(settings, "comfy_workflow", "") if settings else "")
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
    try:
        wf = load_workflow(getattr(settings, "comfy_workflow", "") if settings else "")
        have = [t for t in (TITLE_IMAGE, TITLE_MASK) if _find(wf, t)]
        parts.append(f"workflow: {len(wf)} nodes, sockets {have or 'MISSING'}")
    except FillUnavailable as exc:
        parts.append(str(exc))
    return "; ".join(parts)
