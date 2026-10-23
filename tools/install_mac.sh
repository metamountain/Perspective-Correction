#!/bin/bash
# One-line install for macOS, paste into Terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/metamountain/Perspective-Correction/main/tools/install_mac.sh | bash
#
# Add -s -- --lama after bash for the lama fill:  ... | bash -s -- --lama
#
# Why this exists (user, 2026-10-23): a ZIP from the browser is quarantined, so
# Gatekeeper refuses the .command files ("Apple cannot check it for malicious
# software"), and ~/Downloads, ~/Documents and ~/Desktop are privacy-protected,
# so Terminal gets "Operation not permitted" there. curl and git set no
# quarantine flag, and the home folder itself is not protected -- so this puts
# the code in ~/Perspective-Correction and every double-click works afterwards.
#
# Meant for people who have never opened Terminal (user: "ist für noobs"):
# without a usable Python it asks, then fetches and installs python.org's
# signed package itself (macOS asks for the login password once).
#
# git is used when it is really there (on a Mac /usr/bin/git is a stub that
# pops up the Command Line Tools installer, hence xcode-select), because a
# clone keeps the in-app update button working; otherwise a tarball via curl.
# Running it again updates the copy. "Install (macOS).command" runs this same
# file with PC_NO_FETCH=1 on the folder it sits in.
#
# Overrides, for trying it elsewhere: PC_DIR (target), PC_PYTHON (interpreter),
# PC_NO_GIT=1 (force the tarball), PC_NO_FETCH=1 (use PC_DIR as it is),
# PC_ASSUME=y|n (answer the Python question without a terminal).

PY_VERSION="3.12.10"         # last 3.12 with a python.org macOS installer; universal2
PY_PKG="https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-macos11.pkg"

find_python() {
    local p
    for p in /Library/Frameworks/Python.framework/Versions/3.1[0-9]/bin/python3 \
             /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
        if command -v "$p" >/dev/null 2>&1 &&
           "$p" -c 'import sys, tkinter; sys.exit(sys.version_info < (3, 9) or tkinter.TkVersion < 8.6)' 2>/dev/null; then
            echo "$p"
            return 0
        fi
    done
    return 1
}

ask() {
    # Under `curl | bash` stdin is the script itself, so the answer comes from
    # the terminal; with no terminal at all the default is no.
    local answer="$PC_ASSUME"
    if [ -z "$answer" ]; then
        if [ -r /dev/tty ]; then
            printf "%s [Y/n] " "$1" > /dev/tty
            read -r answer < /dev/tty || answer=n
            answer="${answer:-y}"
        else
            answer=n
        fi
    fi
    case "$answer" in [yYjJ]*) return 0 ;; *) return 1 ;; esac
}

install_python() {
    if [ "$(uname)" != "Darwin" ]; then
        echo "!! installing Python automatically only works on macOS"
        return 1
    fi
    local pkg
    pkg="$(mktemp -d)/python-$PY_VERSION.pkg"
    echo "== downloading Python $PY_VERSION from python.org (about 45 MB)"
    curl -fL --progress-bar -o "$pkg" "$PY_PKG"
    echo "== installing Python -- macOS asks for your login password"
    echo "   (nothing is shown while you type it; press Enter when done)"
    sudo installer -pkg "$pkg" -target / < /dev/tty
    rm -f "$pkg"
    # python.org's Python brings no root certificates of its own; without this
    # step every https download from Python (the lama model, for one) fails.
    local certs="/Applications/Python ${PY_VERSION%.*}/Install Certificates.command"
    if [ -f "$certs" ]; then
        bash "$certs" < /dev/null >/dev/null 2>&1 || true
    fi
}

main() {
    set -e
    local repo="https://github.com/metamountain/Perspective-Correction"
    local dir="${PC_DIR:-$HOME/Perspective-Correction}"

    if [ -n "$PC_NO_FETCH" ]; then
        :
    else
        local have_git=""
        if [ -z "$PC_NO_GIT" ] && command -v git >/dev/null 2>&1; then
            if [ "$(uname)" != "Darwin" ] || xcode-select -p >/dev/null 2>&1; then
                have_git=1
            fi
        fi
        if [ -d "$dir/.git" ] && [ -n "$have_git" ]; then
            echo "== updating $dir"
            git -C "$dir" pull --ff-only
        elif [ -n "$have_git" ] && [ ! -e "$dir" ]; then
            echo "== cloning into $dir"
            git clone "$repo.git" "$dir"
        else
            echo "== downloading into $dir"
            mkdir -p "$dir"
            curl -fsSL "$repo/archive/refs/heads/main.tar.gz" | tar xz -C "$dir" --strip-components 1
        fi
    fi
    cd "$dir"
    chmod +x ./*.command 2>/dev/null || true
    # Nothing fetched this way carries the flag; clear it in case an earlier
    # browser download left files behind in the same folder.
    if command -v xattr >/dev/null 2>&1; then
        xattr -dr com.apple.quarantine . 2>/dev/null || true
    fi

    local py="$PC_PYTHON"
    if [ -z "$py" ]; then
        py="$(find_python)" || py=""
    fi
    if [ -z "$py" ]; then
        echo
        echo "Python (3.9 or newer, with Tk 8.6) is not installed yet."
        if ask "Download and install Python $PY_VERSION from python.org now?"; then
            install_python
            py="$(find_python)" || py=""
        fi
    fi
    if [ -z "$py" ]; then
        echo
        echo "!! No Python yet. Install it from https://www.python.org/downloads/macos/"
        echo "   and paste the same command again -- the code is already in $dir."
        exit 1
    fi

    # </dev/null: under `curl | bash` stdin is this script; nothing below may read it.
    "$py" tools/install_deps.py "$@" </dev/null

    echo
    echo "Installed in $dir"
    echo "Start: double-click 'Perspective Correction.command' there"
    echo "       (Finder: Go > Home, folder Perspective-Correction), or paste:"
    echo "       ~/Perspective-Correction/.venv/bin/python ~/Perspective-Correction/perspective_correction.py --gui"
}

# Everything is inside main, so bash has read the whole script before running
# any of it -- a pipe cut short half-way runs nothing instead of half.
main "$@"
