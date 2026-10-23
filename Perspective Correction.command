#!/bin/bash
# Double-click launcher for macOS, the twin of "Perspective Correction.bat".
# Uses the .venv that "Install (macOS).command" made; without one it says so.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "No .venv here yet -- double-click 'Install (macOS).command' first."
    read -n 1 -s -r -p "Press any key to close."
    exit 1
fi
# Failures land in pc_errors.log, as on Windows.
exec .venv/bin/python perspective_correction.py --gui "$@"
