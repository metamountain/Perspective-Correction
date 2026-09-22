"""Bring this checkout up to date with its remote -- or refuse, and say why.

Kept out of `gui.py` so it can be tested without a window, and written as two
steps rather than one button press: `check()` looks, `apply()` moves. Nothing
here ever changes a file the user wrote.

The rules are narrow on purpose, because this runs behind a single button and
the person pressing it is not watching git:

* **Only ever a fast-forward.** No merge, no rebase, and above all no
  `reset --hard` or `clean`. If the local branch has commits the remote does
  not, this refuses and says so. An updater that can lose work is worse than
  no updater, and "it was only a stray edit" is not a judgement a button gets
  to make.
* **A dirty working tree stops it.** The files are named, so the answer to
  "why not" is on screen rather than in a git manual.
* **A rewritten history is its own case.** `git filter-repo` ran on this
  project on 2026-09-22 and the remote was force-pushed, which is exactly the
  situation where a pull produces an unholy merge or a wall of conflicts. The
  branches then share no recent ancestor, and the only honest answer is
  "clone again" -- so that is what it says, instead of trying.

Everything is reported as text for a message box. Nothing raises at the
caller; a failure is a sentence.
"""

from __future__ import annotations

import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

# A fetch is a network call behind a button. Long enough for a slow line,
# short enough that a hung proxy gives the window back rather than wedging it.
FETCH_TIMEOUT = 60.0
LOCAL_TIMEOUT = 15.0


def _git(args, root=None, timeout=LOCAL_TIMEOUT):
    """``(ok, text)``. A list, never a shell; decoded, never bytes.

    Explicit ``encoding="utf-8", errors="replace"`` rather than the bare
    ``text=True`` the rest of this repository uses, and the difference matters
    here: `text=True` decodes with the locale encoding, which is cp1252 on a
    German Windows, and what this reads back is commit subjects. This project's
    own history contains "Mariánské_Hory" and several German sentences, so the
    common case is the one that breaks. A mangled character in a report is a
    blemish; a `UnicodeDecodeError` behind a button is a bug. (The same trap,
    the other way round, is written up at `tools/worker_agent.py:33`.)
    """
    kwargs = {}
    if os.name == "nt":
        # Same flag `sam2seg.run_subprocess` uses: without it every one of
        # these calls flashes a console window over the picture, and `check`
        # makes six of them.
        kwargs["creationflags"] = 0x08000000        # CREATE_NO_WINDOW
    try:
        p = subprocess.run(["git"] + list(args), cwd=root or ROOT,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout, **kwargs)
    except FileNotFoundError:
        return False, "git is not installed, or not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"git {args[0]} gave up after {timeout:.0f} s"
    except OSError as exc:
        return False, f"git could not be run: {exc}"
    if p.returncode:
        msg = (p.stderr or p.stdout or "").strip()
        return False, msg.splitlines()[0] if msg else f"git {args[0]} failed"
    return True, (p.stdout or "").strip()


def is_checkout(root=None) -> bool:
    """Whether this copy came from git at all.

    A downloaded zip is a perfectly good way to run this program and there is
    nothing to update in it. The button says so rather than failing.
    """
    ok, out = _git(["rev-parse", "--is-inside-work-tree"], root)
    return ok and out == "true"


def dirty_files(root=None):
    """Paths with uncommitted changes, as git reports them. [] when clean.

    Untracked files are deliberately NOT included: this repository keeps 79
    test photographs and a whole `analysis/` folder outside git on purpose, and
    a fast-forward cannot touch an untracked file anyway.

    `diff --name-only HEAD` rather than `status --porcelain`, and not by taste:
    porcelain is COLUMN-ALIGNED -- two status characters, a space, the path --
    while `_git` strips the whole output, which eats the leading space of the
    first line and shifts it by one. That turned `src/pc/gui.py` into
    `rc/pc/gui.py` in the first thing this function ever printed. A generic
    strip and a fixed-column format do not mix; this asks for the paths
    directly instead. It covers staged and unstaged alike, because the
    comparison is against HEAD rather than against the index.
    """
    ok, out = _git(["diff", "--name-only", "HEAD"], root)
    if not ok:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def check(root=None) -> dict:
    """Look at the remote and decide. Never changes anything.

    Returns a dict with ``can_update`` (bool), ``message`` (one line for a
    box), and, when there is something to take, ``behind`` and ``log``.
    """
    if not is_checkout(root):
        return {"can_update": False,
                "message": "This copy did not come from git -- there is "
                           "nothing to pull. Download the current version "
                           "from the project page instead."}

    ok, branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    if not ok:
        return {"can_update": False, "message": branch}
    if branch == "HEAD":
        return {"can_update": False,
                "message": "This checkout is not on a branch (detached HEAD). "
                           "Check out a branch first -- updating from here "
                           "would leave the commits unreachable."}

    ok, remotes = _git(["remote"], root)
    if not ok or not remotes:
        return {"can_update": False,
                "message": "No remote is configured, so there is nowhere to "
                           "update from."}
    remote = "origin" if "origin" in remotes.split() else remotes.split()[0]

    ok, err = _git(["fetch", "--quiet", remote], root, timeout=FETCH_TIMEOUT)
    if not ok:
        return {"can_update": False, "message": f"Could not reach {remote}: {err}"}

    upstream = f"{remote}/{branch}"
    ok, _ = _git(["rev-parse", "--verify", "--quiet", upstream], root)
    if not ok:
        return {"can_update": False,
                "message": f"{upstream} does not exist -- this branch is only "
                           f"local, so there is no update to take."}

    ok, counts = _git(["rev-list", "--left-right", "--count",
                       f"{upstream}...HEAD"], root)
    if not ok:
        return {"can_update": False, "message": counts}
    parts = counts.split()
    behind, ahead = (int(parts[0]), int(parts[1])) if len(parts) == 2 else (0, 0)

    if behind == 0 and ahead == 0:
        return {"can_update": False, "behind": 0,
                "message": f"Already up to date with {upstream}."}

    if ahead and behind:
        # The force-push case, among others. A pull here would either refuse or
        # produce a merge nobody asked for, and the usual cause -- a rewritten
        # history -- cannot be fixed by pulling at all.
        return {"can_update": False, "behind": behind,
                "message": (
                    f"This checkout and {upstream} have diverged: {ahead} "
                    f"commit(s) here that are not there, {behind} there that "
                    f"are not here.\n\n"
                    f"If you did not commit anything yourself, the history was "
                    f"rewritten on the remote and a pull cannot repair that -- "
                    f"clone the project again into a new folder.\n\n"
                    f"If those {ahead} commits are yours, push or move them "
                    f"first; this button will not merge or discard them.")}

    if ahead:
        return {"can_update": False, "behind": 0,
                "message": (f"Nothing to take: this checkout is {ahead} "
                            f"commit(s) AHEAD of {upstream}.")}

    dirty = dirty_files(root)
    if dirty:
        shown = "\n".join("   " + d for d in dirty[:12])
        more = f"\n   ... and {len(dirty) - 12} more" if len(dirty) > 12 else ""
        return {"can_update": False, "behind": behind,
                "message": (f"{behind} update(s) are waiting, but this "
                            f"checkout has uncommitted changes:\n\n{shown}{more}"
                            f"\n\nCommit or stash them first. This button will "
                            f"not overwrite work you have not saved.")}

    ok, log = _git(["log", "--oneline", "--no-decorate", f"HEAD..{upstream}"], root)
    return {"can_update": True, "behind": behind, "upstream": upstream,
            "log": log if ok else "",
            "message": f"{behind} update(s) ready from {upstream}."}


def apply(root=None) -> tuple:
    """Take the updates. ``(ok, text)``.

    `merge --ff-only` and nothing else: if anything has changed between the
    `check` and this call such that a fast-forward is no longer possible, git
    refuses and so does this. That re-check is the point of using ff-only
    rather than trusting the count we just read.
    """
    info = check(root)
    if not info.get("can_update"):
        return False, info["message"]

    before = _git(["rev-parse", "--short", "HEAD"], root)[1]
    ok, err = _git(["merge", "--ff-only", info["upstream"]], root, timeout=FETCH_TIMEOUT)
    if not ok:
        return False, f"The update was refused: {err}"
    after = _git(["rev-parse", "--short", "HEAD"], root)[1]

    ok, names = _git(["diff", "--name-only", f"{before}..{after}"], root)
    files = [n for n in (names.splitlines() if ok else []) if n.strip()]
    head = _git(["log", "--oneline", "--no-decorate", "-1"], root)[1]

    lines = [f"Updated: {before} -> {after}",
             f"{info['behind']} commit(s), {len(files)} file(s) changed.",
             "", "Now at:", "   " + head]
    if info.get("log"):
        lines += ["", "What came in:"]
        lines += ["   " + ln for ln in info["log"].splitlines()[:12]]
        if len(info["log"].splitlines()) > 12:
            lines.append(f"   ... and {len(info['log'].splitlines()) - 12} more")
    # Python imported this code at start-up; the files on disk are new but the
    # ones running are not. Saying so is the difference between an update that
    # worked and an update that appears not to have.
    lines += ["", "Restart the program for this to take effect."]
    return True, "\n".join(lines)
