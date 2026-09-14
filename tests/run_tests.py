#!/usr/bin/env python3
"""Standalone test runner -- no pytest required.

    python tests/run_tests.py            all tests
    python tests/run_tests.py geometry   only modules matching "geometry"
    python tests/run_tests.py -v         show every test name
    python tests/run_tests.py -s         run sequentially, in MODULES order
    python tests/run_tests.py --full     include the slow modules (or PC_FULL=1)

Modules run in worker processes (up to one per core, capped at eight) because
the suite is bound by a handful of long asset sweeps and wall time should be
the slowest module, not the sum of all of them. Tests within a module still
run in order inside their process and share that module's caches; no test
reads another module's state, which is what makes the split safe. ``-s``
restores the old sequential run for debugging.
"""
import importlib
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, HERE)

# Real Tk windows and real photograph sweeps: correct, and far too slow to run
# after every edit. Deferred unless --full / PC_FULL / named explicitly.
SLOW = ("test_gui", "test_assets")

MODULES = ["test_geometry", "test_lines", "test_layout", "test_warp", "test_estimation",
            "test_pipeline", "test_cli", "test_review", "test_masks", "test_birefnet", "test_prefs",
             "test_inpaint",
            "test_detectors",
            "test_schemes",
            "test_planar",
           "test_reference",
           "test_assets",
           "test_gui",
           "test_deps",
           "test_distortion",
           "test_sam2seg"]

MAX_WORKERS = 8


class _Skip(Exception):
    pass


# At top level on purpose: worker processes re-import this file before running
# a module, and the test modules name SkipTest bare.
import builtins  # noqa: E402
builtins.SkipTest = _Skip
sys.modules[__name__].Skip = _Skip


def _unlisted():
    """Test files on disk that ``MODULES`` does not name.

    A test module that is not listed here runs nowhere, so it passes silently
    and forever -- worse than no test at all, because it reads as covered. The
    list stays explicit (ordering is deliberate: the fast modules first), so
    the cost of that choice is paid here rather than by whoever adds the next
    file and never sees it run.
    """
    import glob
    found = {os.path.splitext(os.path.basename(f))[0]
             for f in glob.glob(os.path.join(HERE, "test_*.py"))}
    return sorted(found - set(MODULES))


def _run_module(name):
    """Run one module's tests; return ``(name, results, import_error)``.

    Runs in a worker process, so everything that comes back must be plain
    data: ``results`` is a list of ``(test, status, seconds, detail)`` with
    status one of ok / skip / fail and detail the skip reason or traceback.
    """
    try:
        mod = importlib.import_module(name)
    except Exception:
        return name, None, traceback.format_exc()
    tests = [k for k in sorted(vars(mod)) if k.startswith("test_")]
    out = []
    for t in tests:
        t1 = time.time()
        try:
            getattr(mod, t)()
            out.append((t, "ok", time.time() - t1, None))
        except _Skip as exc:
            out.append((t, "skip", time.time() - t1, str(exc)))
        except Exception:
            out.append((t, "fail", time.time() - t1, traceback.format_exc()))
    return name, out, None


def main(argv):
    verbose = "-v" in argv
    sequential = "-s" in argv
    stray = _unlisted()
    if stray:
        print("!! not in MODULES, so never run: " + ", ".join(stray))
    picks = [a for a in argv if not a.startswith("-")]
    full = "--full" in argv or bool(os.environ.get("PC_FULL"))
    names = [n for n in MODULES if not picks or any(p in n for p in picks)]

    # A normal run is the one you make after every edit, so it has to be quick
    # enough that you actually make it (user, 2026-09-14: "reduce normal test to
    # 10s, do extensive testing only after finishing all"). These two are the
    # whole difference: `test_gui` builds real Tk windows and `test_assets`
    # sweeps real photographs, and together they were ~40 s of a ~48 s run.
    #
    # Naming either one explicitly still runs it -- the skip only applies to the
    # default sweep -- and the banner below is deliberately loud, because a
    # default that silently runs less than it says is how a suite stops meaning
    # anything.
    deferred = []
    if not full and not picks:
        deferred = [n for n in names if n in SLOW]
        names = [n for n in names if n not in SLOW]

    t0 = time.time()
    done = {}
    if sequential or len(names) <= 1:
        for n in names:
            name, out, err = _run_module(n)
            done[name] = (out, err)
    else:
        from concurrent.futures import ProcessPoolExecutor
        workers = min(MAX_WORKERS, os.cpu_count() or 1, len(names))
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for name, out, err in ex.map(_run_module, names):
                done[name] = (out, err)

    total = failed = skipped = 0
    failures = []
    for name in names:
        out, err = done[name]
        if out is None:
            print(f"!! cannot import {name}")
            print(err)
            failed += 1
            continue
        if not out:
            continue
        print(f"\n{name}")
        for t, status, secs, detail in out:
            total += 1
            if status == "skip":
                skipped += 1
                print(f"  -  {t}  ({detail})")
            elif status == "fail":
                failed += 1
                failures.append((name, t, detail))
                print(f"  FAIL {t}")
            elif verbose:
                print(f"  ok {t}  ({secs:.2f}s)")
            else:
                print(f"  ok {t}")

    for name, t, tb in failures:
        print(f"\n{'=' * 70}\n{name}.{t}\n{'-' * 70}\n{tb}")
    print(f"\n{total} test(s), {failed} failed, {skipped} skipped, "
          f"{time.time() - t0:.1f}s")
    if deferred:
        print("!! FAST RUN -- did NOT run: " + ", ".join(deferred))
        print("!! Green here does not mean green. Before claiming the suite passes:")
        print("!!   python tests/run_tests.py --full")
    return 1 if (failed or stray) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
