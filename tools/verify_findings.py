"""Grep every quote in a power-debug report back against the file it names.

The report format exists to be checkable: each finding quotes BOTH sides of the
contradiction verbatim, so a machine can ask the only question that matters
first -- is this text actually in that file? A finding whose quote is not there
is a confabulation, and it costs more time than it saves.

This does not judge whether a contradiction is real. It removes the entries
that cannot be real, so the architect spends judgement on the rest.

    python tools/verify_findings.py                  # every report
    python tools/verify_findings.py review gui       # just these
"""

from __future__ import annotations

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
REPORTS = os.path.join(REPO, "analysis", "powerdebug")

# `file.py:123` followed, on this or a later line, by a double-quoted span
CITE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*\.py):(\d+)(?:-\d+)?`")
QUOTED = re.compile(r'"([^"]{8,})"')
ENTRY = re.compile(r"^\s*(\d+)\.\s", re.M)

_cache: dict = {}


def source(name):
    if name not in _cache:
        for sub in ("src/pc", "tests", "tools", ""):
            p = os.path.join(REPO, sub, name)
            if os.path.isfile(p):
                _cache[name] = open(p, encoding="utf-8", errors="replace").read()
                break
        else:
            _cache[name] = None
    return _cache[name]


def squash(s):
    """Whitespace-insensitive, so a quote rewrapped across lines still matches."""
    return re.sub(r"\s+", " ", s).strip()


def check_entry(text):
    """(files_missing, quotes_missing, quotes_found) for one numbered entry."""
    miss_f, miss_q, found = [], [], 0
    files = [f for f, _ln in CITE.findall(text)]
    for f in dict.fromkeys(files):
        if source(f) is None:
            miss_f.append(f)
    bodies = [squash(source(f) or "") for f in dict.fromkeys(files)]
    for q in QUOTED.findall(text):
        sq = squash(q)
        if len(sq) < 8:
            continue
        if any(sq in b for b in bodies):
            found += 1
        else:
            miss_q.append(q[:70])
    return miss_f, miss_q, found


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    names = sorted(glob.glob(os.path.join(REPORTS, "*.md")))
    if args:
        want = {a if a.endswith(".md") else a + ".md" for a in args}
        names = [n for n in names if os.path.basename(n) in want]
    total = ok = 0
    for path in names:
        text = open(path, encoding="utf-8", errors="replace").read()
        cuts = [m.start() for m in ENTRY.finditer(text)]
        if not cuts:
            print(f"{os.path.basename(path):16s} no numbered entries")
            continue
        cuts.append(len(text))
        print(f"\n--- {os.path.basename(path)}")
        for i in range(len(cuts) - 1):
            body = text[cuts[i]:cuts[i + 1]]
            head = squash(body)[:72]
            mf, mq, nf = check_entry(body)
            total += 1
            if mf:
                print(f"   BAD  {head}\n        no such file: {', '.join(mf)}")
            elif mq:
                print(f"   BAD  {head}")
                for q in mq[:3]:
                    print(f'        quote not in file: "{q}"')
            elif nf == 0:
                print(f"   ??   {head}\n        nothing quoted -- cannot be checked")
            else:
                ok += 1
                print(f"   ok   ({nf} quotes) {head}")
    print(f"\n{ok} of {total} entries have every quote present in the file named.")


if __name__ == "__main__":
    main()
