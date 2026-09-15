"""Every symbol an audit report cites, checked against the module it names.

Three of three false findings died to this step. The trap is checking the leaf
alone: `deps.default_gdino_dir()` is a real function under a module that does
not define it, and a check that only looks for the name says "fine". So the
module has to be checked too -- and a `file.py:NNN` past the end of file.py is
a confabulation you can catch by counting lines.
"""
import pathlib
import re
import sys

REPO = pathlib.Path(r"D:\Coding\Perspective-Correction")
AUDIT = REPO / "analysis" / "audit"
SRC = REPO / "src" / "pc"

MODS = {p.stem: p.read_text(encoding="utf-8", errors="replace")
        for p in SRC.glob("*.py")}
LINES = {p.name: sum(1 for _ in p.open(encoding="utf-8", errors="replace"))
         for sub in ("src/pc", "tests", "tools") for p in (REPO / sub).glob("*.py")}

# `mod.thing` / `mod.thing()` -- only where `mod` is a real module of this package
_EXT = {"py", "md", "png", "jpg", "txt", "json", "tar", "pt", "bat", "log", "yaml"}
DOTTED = re.compile(r"`([a-z_][a-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\(?\)?`")
# `foo.py:123` or `foo.py:123-130`
CITE = re.compile(r"`?([A-Za-z_][A-Za-z0-9_]*\.py):(\d+)")


def check(text):
    bad = []
    for mod, name in set(DOTTED.findall(text)):
        # `lines.py` is a filename, not lines.py's attribute `py` -- the
        # regex cannot tell, so the extensions are named here.
        if mod not in MODS or mod == "settings" or name in _EXT:
            continue
        if not re.search(r"\b" + re.escape(name) + r"\b", MODS[mod]):
            bad.append(f"{mod}.{name} (no such name in {mod}.py)")
    # settings.X must be a field of Settings
    cfg = MODS.get("config", "")
    for mod, name in set(DOTTED.findall(text)):
        if mod == "settings" and name not in _EXT and not re.search(r"^\s+" + re.escape(name) + r"\s*:",
                                               cfg, re.M):
            bad.append(f"settings.{name} (not a Settings field)")
    for fname, num in set(CITE.findall(text)):
        n = LINES.get(fname)
        if n is not None and int(num) > n:
            bad.append(f"{fname}:{num} (file has {n} lines)")
    return sorted(bad)


names = [p.name for p in sorted(AUDIT.glob("*.md"))]
if len(sys.argv) > 1:
    names = [n if n.endswith(".md") else n + ".md" for n in sys.argv[1:]]
grand = 0
for name in names:
    text = (AUDIT / name).read_text(encoding="utf-8", errors="replace")
    text = text.split("## ARCHITECT'S VERDICT")[0]
    bad = check(text)
    if bad:
        grand += len(bad)
        print(f"--- {name}")
        for b in bad:
            print("      " + b)
print(f"\n{grand} citation(s) point at something that is not there.")
