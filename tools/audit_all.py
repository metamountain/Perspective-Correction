"""Walk the worker through every module, one at a time, collecting defects.

One file per run, deliberately.  Two broad sweeps earlier today read half the
codebase and hit the turn limit with nothing to show; a single module fits in a
handful of turns and finishes.  The reports land in one file for the architect
to read -- the worker finds and cites, it does not decide.

    python tools/audit_all.py                 # every module
    python tools/audit_all.py review gui      # just these
    python tools/audit_all.py --resume        # skip modules already reported

Output: analysis/audit/<module>.md, plus a running index printed here.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(REPO, "analysis", "audit")

# The shapes that actually cost time in this project, named so the worker looks
# for defects rather than for style.  Every one of these has bitten here.
PACKAGE = """GOAL
INVESTIGATE AND REPORT ONLY. Change nothing. No str_replace, no run_tests.
ONE file: {path}

Read it and report DEFECTS. Not style, not naming, not "could be clearer".
Only these six shapes, each of which has cost this project real time:

A. A COMMENT OR DOCSTRING THE CODE CONTRADICTS.
   A claimed caller that does not exist, a described gesture with no binding, a
   stated default that differs from the real one. Cite both sides.

B. A SWALLOWED FAILURE.
   An `except` whose handler neither re-raises, nor returns something a caller
   checks, nor sets anything a user could see. Or an early `return` on a
   disabled condition that leaves earlier state in force.

C. A UNIT OR SCALE MISMATCH.
   A value converted one way and used as if converted the other: divided by a
   scale and then used in the space it was divided out of, pixels used where
   fractions are expected, analysis-resolution used where full-resolution is.
   Two of these shipped here today.

D. A HARDCODED PATH, VERSION OR MAGIC VALUE that can be wrong on another
   machine or after an upgrade, with no fallback and no error naming it.

E. A VALUE COMPUTED AND NOT USED, or state written and never read.

F. A BRANCH THAT CANNOT BE TAKEN given what the lines above it guarantee.

FOR EACH FINDING, THREE PARTS

1. `{name}:LINE` -- one sentence on what goes wrong, one on what a user or
   caller would see. Then HIGH, MEDIUM or LOW: HIGH means it silently produces
   a wrong result or a dead feature.

2. PROPOSAL. The exact change you would make, as two fenced blocks:

   ```old
   <the text exactly as it appears in the file, enough lines to be unique>
   ```
   ```new
   <what it should become>
   ```

   The `old` block must be copy-paste exact -- it will be matched verbatim, and
   a near-miss is worthless. Include a comment in `new` saying WHY, in the voice
   of the file around it. Keep the change minimal: fix the defect, do not tidy.

3. RISK. One sentence: what this could break, and which test would catch it.
   If you are not sure the change is safe, say so and propose nothing -- a
   finding with no patch is more useful than a patch that guesses.

DO NOT APPLY ANYTHING. You have no str_replace in this run. The architect reads
every proposal, checks it against the file, and applies the ones that hold.

RULES
- Read {span} first with read_file. Do not read past it: another run covers
  the rest, and reading the whole of a long file is what blew the context
  window on the first attempt.
- Cite only lines you actually read. Do not guess.
- Use grep with files="*" to check whether a name is used elsewhere before
  calling it unused -- tests and tools count as users.
- AN EMPTY REPORT IS A GOOD ANSWER. Most files are fine. Do not manufacture
  findings to look thorough.
- At most 6 findings. If there are more, report the 6 worst.
- Stop and reply once you have been through the file once.
"""


# A 4000-line file does not fit the worker's context: `read_file` on the whole
# of gui.py came back HTTP 400 and the run produced nothing at all.  Long files
# go in overlapping slices instead -- overlapping, because a defect that spans
# the cut would otherwise be invisible from both sides.
CHUNK = 700
OVERLAP = 60


def slices(n):
    """``[(start, end), ...]`` covering ``n`` lines, or one slice for a short file."""
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


def modules(names):
    paths = sorted(glob.glob(os.path.join(REPO, "src", "pc", "*.py")))
    if names:
        want = {n if n.endswith(".py") else n + ".py" for n in names}
        paths = [p for p in paths if os.path.basename(p) in want]
    return paths


def _run_one(pkg_path, header):
    """One worker run; returns just what it said, not its tool trace."""
    # utf-8 with replacement: Windows hands this process cp1252 and the worker
    # writes arrows like any model will.  The third time today that this exact
    # class of bug ate a run -- decoding its answer must never be able to fail.
    # 70, not the worker default of 40: asking for an exact patch costs turns,
    # and a run that stops mid-file produces no report at all.
    r = subprocess.run([sys.executable, os.path.join(HERE, "worker_agent.py"),
                        pkg_path], cwd=REPO, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       env={**os.environ, "WORKER_MAX_TURNS": "70"})
    body = (r.stdout or "") + (r.stderr or "")
    mark = "=== worker finished"
    if mark not in body:
        # No final answer: the run hit the turn limit or died. Writing the tool
        # TRACE here, as this used to, produces a file that looks like a report
        # and contains none -- the exact shape this audit hunts. Say so instead.
        tail = [t for t in body.strip().splitlines() if t.strip()][-3:]
        return (header + "**RUN DID NOT FINISH** (turn limit or error). No "
                "findings were produced; this module is NOT cleared." + chr(10) * 2
                + chr(10).join("    " + t[:160] for t in tail))
    return header + body.split(mark, 1)[1].strip()


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    resume = "--resume" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    paths = modules(args)
    print(f"auditing {len(paths)} module(s)\n")
    for i, path in enumerate(paths, 1):
        name = os.path.basename(path)
        dest = os.path.join(OUT, name.replace(".py", ".md"))
        if resume and os.path.exists(dest):
            print(f"[{i}/{len(paths)}] {name:16s} already reported, skipping")
            continue
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        n = sum(1 for _ in open(path, encoding="utf-8"))
        parts = slices(n)
        t0 = time.time()
        report = ""
        for k, (a, b) in enumerate(parts, 1):
            span = (f"lines {a} to {b} of {rel} (part {k} of {len(parts)})"
                    if len(parts) > 1 else f"the whole file, {n} lines")
            pkg = PACKAGE.format(path=rel, name=name, span=span)
            pkg_path = os.path.join(OUT, "_package.txt")
            with open(pkg_path, "w", encoding="utf-8") as fh:
                fh.write(pkg)
            head = ""
            if len(parts) > 1:
                head = f"\n\n## part {k}: lines {a}-{b}\n"
            report += _run_one(pkg_path, head)

        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(f"# {name} ({n} lines)\n\n{report.strip()}\n")
        hi = report.upper().count("HIGH")
        print(f"[{i}/{len(paths)}] {name:16s} {time.time()-t0:5.0f}s  "
              f"{'HIGH x%d' % hi if hi else '-'}  -> {os.path.relpath(dest, REPO)}")


if __name__ == "__main__":
    main()
