#!/usr/bin/env python3
"""Add a Wikimedia Commons photograph to ``tests/assets``, EXIF intact.

    python tools/fetch_commons_asset.py "https://commons.wikimedia.org/wiki/File:....jpg"
    python tools/fetch_commons_asset.py <url> <url> --dry-run

Why a tool rather than a few manual downloads: the value of these assets is that
their focal length is **verifiable**, and that is easy to lose. Commons
thumbnails are stripped of EXIF, so the original has to be fetched and resized
locally with the tags carried across; and the licence and author have to be
recorded at the same time or nobody can attribute them later.

What it does, in order:

1. resolves the Commons file page to its original upload,
2. reads ``FocalLengthIn35mmFilm`` from the API metadata and **refuses** the file
   if it has none -- an asset whose focal length is not machine-readable is the
   thing this is meant to avoid,
3. downloads the original, resizes to a long edge of 1800 px preserving EXIF,
   resetting the orientation tag and dropping the stale thumbnail,
4. re-reads the tag *from the written file* and fails if it did not survive,
5. prints the attribution row for ``tests/assets/README.md``.

It does not write the mask; run ``rectify.py <asset> --mask-export
tests/assets/masks`` afterwards, from the interpreter that has torch.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(HERE, "tests", "assets")
UA = {"User-Agent": "batch-perspective-correction/0.1 (test asset collector)"}
API = "https://commons.wikimedia.org/w/api.php?"
LONG_EDGE = 1800


def _api(params):
    req = urllib.request.Request(API + urllib.parse.urlencode(params), headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def title_from_url(url: str) -> str:
    """Accept a file page URL, a bare ``File:`` title, or a direct upload URL."""
    if url.startswith("File:"):
        return url
    path = urllib.parse.urlparse(url).path
    name = urllib.parse.unquote(path.rsplit("/", 1)[-1])
    if "/wiki/" in url:
        return name if name.startswith("File:") else "File:" + name
    return "File:" + name


def describe(title: str):
    d = _api({"action": "query", "format": "json", "prop": "imageinfo",
              "titles": title, "iiprop": "url|size|extmetadata|metadata"})
    for _, pg in d.get("query", {}).get("pages", {}).items():
        info = (pg.get("imageinfo") or [None])[0]
        if not info:
            return None
        md = {m["name"]: m["value"] for m in (info.get("metadata") or [])
              if isinstance(m, dict)}
        ex = info.get("extmetadata") or {}
        return {"title": pg["title"], "url": info["url"],
                "w": info.get("width"), "h": info.get("height"),
                "f35": md.get("FocalLengthIn35mmFilm"),
                "focal": md.get("FocalLength"), "model": md.get("Model", ""),
                "licence": (ex.get("LicenseShortName") or {}).get("value", ""),
                "author": _strip_tags((ex.get("Artist") or {}).get("value", "")),
                "page": "https://commons.wikimedia.org/wiki/" +
                        urllib.parse.quote(pg["title"].replace(" ", "_"))}
    return None


def _strip_tags(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html or "").strip()


def slug(title: str, model: str, f35) -> str:
    import re
    stem = os.path.splitext(title[5:])[0]
    stem = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    if len(stem) > 40:                       # cut at a word boundary, not mid-word
        stem = stem[:40].rsplit("-", 1)[0]
    stem = stem.strip("-")
    tag = re.sub(r"[^A-Za-z0-9]+", "", (model or "cam")).lower()[:10]
    return f"{stem}-{tag}-{int(float(f35))}mm.jpg"


def fetch(url: str, dry_run=False) -> int:
    import piexif
    from PIL import Image

    title = title_from_url(url)
    info = describe(title)
    if info is None:
        print("not found on Commons: " + title)
        return 1
    if not info["f35"]:
        print(f"REFUSED {info['title']}\n"
              f"        no FocalLengthIn35mmFilm -- the focal length would not be\n"
              f"        machine-readable, which is the whole point of these assets.\n"
              f"        (EXIF FocalLength is {info['focal']}, but the crop factor is\n"
              f"        not recoverable; use the _f<NN> name suffix instead.)")
        return 2
    name = slug(info["title"], info["model"], info["f35"])
    print(f"{info['title']}\n  {info['w']}x{info['h']}  f35={info['f35']}mm  "
          f"{info['model']}  {info['licence']}\n  -> tests/assets/{name}")
    if dry_run:
        return 0

    dst = os.path.join(ASSETS, name)
    tmp = dst + ".orig"
    req = urllib.request.Request(info["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    try:
        im = Image.open(tmp)
        ex = piexif.load(tmp)
        w, h = im.size
        s = min(1.0, LONG_EDGE / max(w, h))
        out = im.resize((int(w * s), int(h * s)), Image.LANCZOS) if s < 1.0 else im
        ex.setdefault("0th", {})[piexif.ImageIFD.Orientation] = 1
        ex.setdefault("Exif", {})[piexif.ExifIFD.PixelXDimension] = out.size[0]
        ex["Exif"][piexif.ExifIFD.PixelYDimension] = out.size[1]
        ex["thumbnail"] = None
        ex["1st"] = {}
        out.convert("RGB").save(dst, quality=92, subsampling=0, optimize=True,
                                exif=piexif.dump(ex))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    # the tag has to survive the rewrite, or the asset is not what it claims
    got = piexif.load(dst)["Exif"].get(piexif.ExifIFD.FocalLengthIn35mmFilm)
    if str(got) != str(info["f35"]):
        print(f"  FAILED: f35 did not survive the resize ({got} != {info['f35']})")
        os.remove(dst)
        return 3
    print(f"  written, {os.path.getsize(dst) / 1e6:.2f} MB, f35={got} verified in file")
    print(f"  attribution row:\n"
          f"| `{name}` | [Commons]({info['page']}) | {info['author']} | {info['licence']} |")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="+", help="Commons file page URLs or File: titles")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be added, download nothing")
    args = ap.parse_args(argv)
    rc = 0
    for u in args.urls:
        rc = fetch(u, args.dry_run) or rc
        print()
    return rc


if __name__ == "__main__":
    sys.exit(main())
