#!/bin/bash
# Double-click installer for macOS. The work is in tools/install_deps.py; this
# only finds a Python with Tk 8.6 to run it with (python.org's first, then
# Homebrew's, then whatever python3 is on PATH) and keeps the window open.
cd "$(dirname "$0")" || exit 1

PY=""
for p in /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$p" >/dev/null 2>&1 &&
       "$p" -c 'import sys, tkinter; sys.exit(sys.version_info < (3, 9) or tkinter.TkVersion < 8.6)' 2>/dev/null; then
        PY="$p"
        break
    fi
done
# None qualifies: run it with any python3 anyway, so it explains what is missing.
if [ -z "$PY" ]; then PY=python3; fi

"$PY" tools/install_deps.py "$@"
echo
read -n 1 -s -r -p "Press any key to close."
echo
