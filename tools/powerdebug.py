"""Walk the worker through the code hunting CONTRADICTIONS, and collect them.

Two formats were measured against each other on this codebase today:

    "report the defect, rate it, and write the patch"   1 of 6 findings held
    "quote BOTH sides of the contradiction verbatim"    3 of 3 findings held

The second wins because a quotation is hard to invent and trivial to check: the
architect greps the two strings, and a finding whose quote is not in the file
dies in one second. A severity is an opinion, and an opinion cannot be checked
at all -- both HIGH-rated proposals from the first format would have broken
working code if applied unread. So this driver asks for neither severity nor
patch. It asks what disagrees with what.

The file goes INTO the package, numbered. The worker used to be told to fetch
its own lines; it fetched them and then wandered into three other modules,
repeated one grep three times and hit the server's context limit with the
reading done and nothing written down.

    python tools/powerdebug.py                # every module
    python tools/powerdebug.py review gui     # just these
    python tools/powerdebug.py --resume       # skip what is already reported

Output: analysis/powerdebug/<module>.md
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "analysis", "powerdebug")

CHUNK, OVERLAP = 650, 60

PACKAGE = """GOAL
INVESTIGATE AND REPORT ONLY. Change nothing. No str_replace, no run_tests.

Find CONTRADICTIONS in {name} and list them. A contradiction is two things in
this repository that cannot both be true. Nothing else counts -- not style, not
naming, not code you would have written differently.

THE SIX KINDS THAT HAVE COST THIS PROJECT REAL TIME

A. A COMMENT OR DOCSTRING THE CODE CONTRADICTS.
   A promised label never drawn, a claimed return shape the body does not
   return, a named widget or setting that is absent (or present when the
   comment says it is gone), a stated default that differs from the real one.

B. A UNIT OR FRAME MISMATCH.
   The same value in two units in two places: full-resolution pixels against
   analysis pixels, pixels against fractions, canvas coordinates against image
   coordinates, radians against degrees. Quote the two lines that disagree.

C. A SWALLOWED FAILURE.
   An `except` whose handler neither re-raises, nor returns something a caller
   tests, nor sets anything a user could see.

D. STATE WRITTEN AND NEVER READ, or a setting read nowhere.
   Check with grep over files="*" before claiming it: tests and tools count.

E. A BRANCH THAT CANNOT BE TAKEN given what the lines above it guarantee.

F. TWO PIECES OF CODE THAT DISAGREE ABOUT THE SAME FACT.
   Two readers of one field that treat it differently; a test whose NAME or
   docstring asserts the opposite of its own body; two functions that convert
   the same quantity in opposite directions.

FORMAT -- a numbered list, nothing else, and every entry in exactly this shape:

    N. `{name}:LINE`
       SAYS: "<quoted verbatim from the file>"
       BUT:  `file.py:LINE` "<quoted verbatim>"
       CONTRADICTION: <one sentence, no more>

BOTH QUOTES MUST BE COPY-EXACT. They will be grepped. A quote I cannot find in
the file discredits the whole entry and costs me more time than it saved.

DO NOT WRITE: a severity, a confidence, a rating, a patch, a suggested fix, or
the words "should" and "consider". You are finding the disagreement. Deciding
what to do about it is not your job in this run and a guess there is worse than
silence.

RULES
- THE FILE IS PRINTED BELOW, numbered. Do not call read_file on {path}: you
  have it. Use grep only to check whether a name is used ELSEWHERE, which is
  the one question the printed lines cannot answer.
- Cite only lines printed below, or lines a grep result showed you.
- AN EMPTY LIST IS A GOOD ANSWER. Most code is fine.
- At most 8 entries. If there are more, list the 8 whose contradiction would
  mislead a reader most.
- Stop and reply once you have been through the printed lines once.
"""


def slices(n):
    if n <= CHUNK:
        return [(1, n)]
    out, a = [], 1
    while a <= n:
        b = min(a + CHUNK - 1, n)
        out.append((a, b))
        if b >= n:
            break
        a = b - OVERLAP + 1
    return out


def numbered(path, a, b):
    text = open(path, encoding="utf-8").read().splitlines()
    return chr(10).join(f"{i:5d}  {t}" for i, t in enumerate(text[a - 1:b], a))


def run_one(pkg_path, header):
    r = subprocess.run([sys.executable, os.path.join(HERE, "worker_agent.py"),
                        pkg_path], cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    body = (r.stdout or "") + (r.stderr or "")
    mark = "=== worker finished"
    if mark not in body:
        tail = [t for t in body.strip().splitlines() if t.strip()][-3:]
        return (header + "**RUN DID NOT FINISH.** No findings were produced; "
                "this span is NOT cleared." + chr(10) * 2
                + chr(10).join("    " + t[:160] for t in tail))
    out = body.split(mark, 1)[1].strip()
    if out.startswith(": forced answer"):
        out = ("**FORCED ANSWER** -- cut short, answered from what it had; "
               "weigh the quotes accordingly." + chr(10) * 2 + out.lstrip(": "))
    return header + out


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    resume = "--resume" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    paths = sorted(glob.glob(os.path.join(REPO, "src", "pc", "*.py")))
    if args:
        want = {a if a.endswith(".py") else a + ".py" for a in args}
        paths = [p for p in paths if os.path.basename(p) in want]
    print(f"power-debugging {len(paths)} module(s)\n")
    for i, path in enumerate(paths, 1):
        name = os.path.basename(path)
        dest = os.path.join(OUT, name.replace(".py", ".md"))
        if resume and os.path.exists(dest):
            print(f"[{i}/{len(paths)}] {name:16s} already reported")
            continue
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        n = sum(1 for _ in open(path, encoding="utf-8"))
        parts = slices(n)
        t0, report = time.time(), ""
        for k, (a, b) in enumerate(parts, 1):
            span = (f"lines {a} to {b} (part {k} of {len(parts)})"
                    if len(parts) > 1 else f"the whole file, {n} lines")
            # Appended, not formatted in: source is full of braces and
            # str.format would choke on the first dict literal it met.
            pkg = (PACKAGE.format(path=rel, name=name)
                   + chr(10) * 2 + "THE FILE -- " + span + ":" + chr(10) * 2
                   + numbered(path, a, b) + chr(10))
            pkg_path = os.path.join(OUT, "_package.txt")
            with open(pkg_path, "w", encoding="utf-8") as fh:
                fh.write(pkg)
            head = f"\n\n## part {k}: lines {a}-{b}\n" if len(parts) > 1 else ""
            report += run_one(pkg_path, head)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(f"# {name} ({n} lines)\n\n{report.strip()}\n")
        bad = report.count("RUN DID NOT FINISH") + report.count("FORCED ANSWER")
        print(f"[{i}/{len(paths)}] {name:16s} {time.time()-t0:5.0f}s  "
              f"{'(' + str(bad) + ' incomplete)' if bad else ''}  "
              f"-> {os.path.relpath(dest, REPO)}")


if __name__ == "__main__":
    main()
