#!/usr/bin/env python3
"""Standalone test runner -- no pytest required.

    python tests/run_tests.py            all tests
    python tests/run_tests.py geometry   only modules matching "geometry"
    python tests/run_tests.py -v         show every test name
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

MODULES = ["test_geometry", "test_lines", "test_warp", "test_estimation",
           "test_pipeline", "test_review", "test_masks", "test_reference",
           "test_assets"]


def main(argv):
    verbose = "-v" in argv
    picks = [a for a in argv if not a.startswith("-")]
    total = failed = skipped = 0
    t0 = time.time()
    failures = []

    for name in MODULES:
        if picks and not any(p in name for p in picks):
            continue
        try:
            mod = importlib.import_module(name)
        except Exception:
            print(f"!! cannot import {name}")
            traceback.print_exc()
            failed += 1
            continue
        tests = [k for k in sorted(vars(mod)) if k.startswith("test_")]
        if not tests:
            continue
        print(f"\n{name}")
        for t in tests:
            total += 1
            fn = getattr(mod, t)
            t1 = time.time()
            try:
                fn()
            except _Skip as exc:
                skipped += 1
                print(f"  -  {t}  ({exc})")
                continue
            except Exception:
                failed += 1
                failures.append((name, t, traceback.format_exc()))
                print(f"  FAIL {t}")
                continue
            if verbose:
                print(f"  ok {t}  ({time.time() - t1:.2f}s)")
            else:
                print(f"  ok {t}")

    for name, t, tb in failures:
        print(f"\n{'=' * 70}\n{name}.{t}\n{'-' * 70}\n{tb}")
    print(f"\n{total} test(s), {failed} failed, {skipped} skipped, "
          f"{time.time() - t0:.1f}s")
    return 1 if failed else 0


class _Skip(Exception):
    pass


# make the skip helper importable from the test modules
sys.modules[__name__].Skip = _Skip
import builtins  # noqa: E402
builtins.SkipTest = _Skip


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
