#!/usr/bin/env python3
"""Run without installing:  python rectify.py "D:\\Fotos" --overwrite"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from pc.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
