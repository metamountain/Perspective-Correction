"""Remembered paths, so they are typed once.

Deliberately tiny and deliberately limited to *paths*: model weights, a mask
folder, the last output folder. Correction parameters are not remembered,
because a setting that silently persists between runs is a setting nobody can
reason about -- the whole project turns on a batch being reproducible from its
command line. A path is different: it is machine configuration, not a decision
about the photographs.

Stored as JSON in the platform's usual place, and every failure is non-fatal.
A tool that cannot start because its preferences file is corrupt would be worse
than one that forgets.
"""
from __future__ import annotations

import json
import os

APP = "batch-perspective-correction"
REMEMBERED = ("birefnet_model", "mask_file", "output", "focal_35mm",
              "comfy_url", "comfy_workflow",
              "comfy_unet", "comfy_clip", "comfy_vae")


def path() -> str:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP, "settings.json")


def load() -> dict:
    try:
        with open(path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {k: v for k, v in data.items() if k in REMEMBERED}
    except Exception:
        return {}


def save(**values) -> bool:
    """Merge ``values`` into the stored preferences.  Never raises."""
    keep = {k: v for k, v in values.items() if k in REMEMBERED and v not in (None, "")}
    if not keep:
        return False
    try:
        p = path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        merged = load()
        merged.update(keep)
        tmp = p + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, indent=2)
        os.replace(tmp, p)
        return True
    except Exception:
        return False


def forget() -> bool:
    try:
        os.remove(path())
        return True
    except Exception:
        return False
