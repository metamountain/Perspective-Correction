#!/bin/bash
# Double-click installer for macOS: tools/install_mac.sh on this folder as it
# is (no download). That finds a Python with Tk 8.6, offers to install one if
# there is none, and runs tools/install_deps.py. The window stays open.
cd "$(dirname "$0")" || exit 1
PC_DIR="$PWD" PC_NO_FETCH=1 bash tools/install_mac.sh "$@"
echo
read -n 1 -s -r -p "Press any key to close."
echo
