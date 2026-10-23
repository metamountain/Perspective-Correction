"""Build ``fonts/``: the Google fonts the window ships with, cut to what it uses.

User, 2026-10-23: "lass uns Microsoft-Schriften durch Google Fonts ersetzen".
Segoe UI, Consolas, Verdana and Segoe's icon and emoji fonts exist on Windows
only and may not be redistributed; on the first Mac the icon keys crashed the
window. These four are free to ship and load on every OS:

    Inter              (OFL)        the UI face, regular weight, Latin
    JetBrains Mono     (OFL)        the retro themes' terminal face
    Material Symbols   (Apache 2.0) the add / folder / paste keys
    Noto Emoji         (OFL)        the Grounding DINO key's dinosaur

The variable sources are pinned to one instance (the window uses no bold) and
subset, so the folder stays a few hundred KB. Run once when a font changes;
the output is committed, this script is how it was made:

    python tools/build_fonts.py

Needs ``fonttools`` (``pip install fonttools``) and network access.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "fonts")
GF = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
MD = "https://raw.githubusercontent.com/google/material-design-icons/master/"

LATIN = "U+0020-007E,U+00A0-017F,U+2010-2027,U+2030-203A,U+20AC,U+2190-2193,U+2212,U+00D7"
# Material Symbols codepoints: add, folder, content_paste.
MATERIAL = "U+E145,U+E2C7,U+E14F"
DINOSAUR = "U+1F996"

FONTS = [
    # (output, source url, axis pins, unicodes, licence url, licence name)
    ("Inter-Regular.ttf", GF + "inter/Inter%5Bopsz,wght%5D.ttf",
     {"wght": 400, "opsz": 14}, LATIN, GF + "inter/OFL.txt", "OFL-Inter.txt"),
    ("JetBrainsMono-Regular.ttf", GF + "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
     {"wght": 400}, LATIN, GF + "jetbrainsmono/OFL.txt", "OFL-JetBrainsMono.txt"),
    ("MaterialSymbols-Subset.ttf",
     MD + "variablefont/MaterialSymbolsOutlined%5BFILL,GRAD,opsz,wght%5D.ttf",
     {"FILL": 0, "GRAD": 0, "opsz": 24, "wght": 300}, MATERIAL,
     MD + "LICENSE", "LICENSE-MaterialSymbols.txt"),
    ("NotoEmoji-Subset.ttf", GF + "notoemoji/NotoEmoji%5Bwght%5D.ttf",
     {"wght": 700}, DINOSAUR, GF + "notoemoji/OFL.txt", "OFL-NotoEmoji.txt"),  # bold: reads at key size
]


def fetch(url, path):
    print("  get", url.rsplit("/", 1)[-1])
    with urllib.request.urlopen(url, timeout=120) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f)


def main():
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
        from fontTools.varLib import instancer
    except ImportError:
        print("needs fonttools: pip install fonttools")
        return 2
    os.makedirs(OUT, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, url, pins, unicodes, lic_url, lic_name in FONTS:
            print(name)
            src = os.path.join(tmp, name + ".src")
            fetch(url, src)
            fetch(lic_url, os.path.join(OUT, lic_name))
            font = TTFont(src)
            if "fvar" in font:
                font = instancer.instantiateVariableFont(font, pins)
            pinned = os.path.join(tmp, name)
            font.save(pinned)
            dst = os.path.join(OUT, name)
            subset.main([pinned, f"--unicodes={unicodes}", f"--output-file={dst}",
                         "--layout-features=*", "--name-IDs=*", "--name-legacy",
                         "--name-languages=*", "--notdef-outline"])
            print(f"  -> {os.path.getsize(dst) // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
