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

**A copy that did not come from git updates from the archive** (2026-10-23,
user: the macOS installer falls back to the GitHub tarball when there is no
git, so the friend it was written for had a button that only ever refused).
`check()` asks GitHub for the newest commit on main and `apply()` downloads
that commit's tarball and writes its files over this folder. The same rules
hold in their archive form: `INSTALL_MARK` records the commit and a hash of
every file it wrote, a file whose hash no longer matches is a local edit and
stops the update (the archive twin of a dirty tree), and only files the
previous update wrote are ever deleted. A copy with no mark yet (unpacked by
hand or by the installer) is offered the newest version as "unknown -> x".
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

# A fetch is a network call behind a button. Long enough for a slow line,
# short enough that a hung proxy gives the window back rather than wedging it.
FETCH_TIMEOUT = 60.0
LOCAL_TIMEOUT = 15.0

REPO = "metamountain/Perspective-Correction"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPO}"
# `{sha}` is filled in; codeload is where github.com/.../archive redirects.
TARBALL = f"https://codeload.github.com/{REPO}/tar.gz/{{sha}}"
# Written by `archive_apply` into the folder it updated: the commit, and the
# sha256 of every file it wrote. Untracked (.gitignore).
INSTALL_MARK = ".pc_install.json"
# Never written, never deleted, whatever an archive contains.
KEEP = (".git", ".venv", "venv", INSTALL_MARK)


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

    No `.git` here means no git call at all: on a Mac without the developer
    tools `/usr/bin/git` is a stub that opens an "install the tools" dialog.
    """
    if not os.path.exists(os.path.join(root or ROOT, ".git")):
        return False
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
        return archive_check(root)

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
    if not is_checkout(root):
        return archive_apply(root)
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


# --- a copy without git: update from the GitHub archive ---------------------

def _get(url, timeout=FETCH_TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": "perspective-correction-update",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_mark(root=None):
    """The last archive update's record, or None (never updated this way)."""
    try:
        with open(os.path.join(root or ROOT, INSTALL_MARK), encoding="utf-8") as fh:
            mark = json.load(fh)
        return mark if isinstance(mark.get("files"), dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def edited_files(root=None, mark=None):
    """Files the last update wrote whose content has changed since. A file
    that is gone is not listed: restoring it loses nothing."""
    root = root or ROOT
    mark = mark if mark is not None else read_mark(root)
    if not mark:
        return []
    out = []
    for rel, digest in sorted(mark["files"].items()):
        path = os.path.join(root, rel)
        if os.path.isfile(path) and _sha256(path) != digest:
            out.append(rel)
    return out


def _kept(rel):
    return rel.split("/", 1)[0] in KEEP


def archive_check(root=None, api=API) -> dict:
    """`check` for a copy without git. Never changes anything."""
    root = root or ROOT
    try:
        head = json.loads(_get(f"{api}/commits/{BRANCH}"))
        sha = head["sha"]
        subject = head["commit"]["message"].splitlines()[0]
    except Exception as exc:
        return {"can_update": False,
                "message": f"Could not reach GitHub: {exc}"}
    mark = read_mark(root)
    have = mark.get("sha") if mark else None
    if have == sha:
        return {"can_update": False, "behind": 0,
                "message": f"Already up to date ({sha[:7]})."}

    edited = edited_files(root, mark)
    if edited:
        shown = "\n".join("   " + e for e in edited[:12])
        more = f"\n   ... and {len(edited) - 12} more" if len(edited) > 12 else ""
        return {"can_update": False,
                "message": (f"A newer version ({sha[:7]}) is waiting, but "
                            f"these files were changed here since the last "
                            f"update:\n\n{shown}{more}\n\nCopy them somewhere "
                            f"else first. This button will not overwrite "
                            f"work you have not saved elsewhere.")}

    log, behind = f"{sha[:7]} {subject}", None
    if have:
        try:
            cmp = json.loads(_get(f"{api}/compare/{have}...{sha}"))
            commits = cmp.get("commits") or []
            behind = cmp.get("ahead_by", len(commits))
            if commits:
                log = "\n".join(c["sha"][:7] + " "
                                + c["commit"]["message"].splitlines()[0]
                                for c in reversed(commits))
        except Exception:
            pass                    # the newest subject alone still says enough
    count = f"{behind} update(s)" if behind else "A newer version"
    return {"can_update": True, "behind": behind or 1, "sha": sha, "log": log,
            "message": (f"{count} ready: {(have or 'unknown')[:7]} -> "
                        f"{sha[:7]}. The program's files in this folder are "
                        f"replaced; your photographs, settings and .venv "
                        f"are not touched.")}


def _safe_members(tar):
    """``(relative path, member)`` for every regular file under the archive's
    one top folder, that folder stripped. A path that would land outside the
    install refuses the whole archive; links are skipped, never followed."""
    out = []
    for m in tar.getmembers():
        parts = m.name.replace("\\", "/").split("/", 1)
        if len(parts) < 2 or not parts[1] or m.isdir():
            continue
        rel = parts[1].rstrip("/")
        if (rel.startswith("/") or ":" in rel
                or ".." in rel.split("/")):
            raise ValueError(f"unsafe path in the archive: {m.name}")
        if m.isfile():
            out.append((rel, m))
        elif not (m.issym() or m.islnk()):
            raise ValueError(f"unexpected entry in the archive: {m.name}")
    return out


def archive_apply(root=None, api=API, tarball=TARBALL) -> tuple:
    """`apply` for a copy without git. ``(ok, text)``.

    Checks again first (a file edited between the question and the answer
    still stops it), unpacks the whole archive into a temporary folder before
    writing anything here, writes each file through a temporary name and
    `os.replace`, then deletes what the previous update wrote and this one no
    longer has. Files it never wrote are never deleted.
    """
    root = root or ROOT
    info = archive_check(root, api)
    if not info.get("can_update"):
        return False, info["message"]
    sha = info["sha"]
    old = read_mark(root)
    written, changed = {}, 0
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, "new")
        try:
            tgz = os.path.join(tmp, "update.tar.gz")
            with open(tgz, "wb") as fh:
                fh.write(_get(tarball.format(sha=sha)))
            with tarfile.open(tgz, "r:gz") as tar:
                members = [(r, m) for r, m in _safe_members(tar) if not _kept(r)]
                for rel, m in members:
                    dst = os.path.join(staged, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with tar.extractfile(m) as src, open(dst, "wb") as fh:
                        shutil.copyfileobj(src, fh)
        except Exception as exc:
            return False, f"The update could not be downloaded: {exc}"
        if not members:
            return False, "The downloaded archive was empty; nothing changed."

        try:
            for rel, m in members:
                src = os.path.join(staged, rel)
                dst = os.path.join(root, rel)
                digest = _sha256(src)
                written[rel] = digest
                if os.path.isfile(dst) and _sha256(dst) == digest:
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                part = dst + ".pc_update"
                shutil.copyfile(src, part)
                if os.name != "nt":
                    # the .command starters and install_mac.sh must stay
                    # executable
                    os.chmod(part, (m.mode & 0o777) | 0o600)
                os.replace(part, dst)
                changed += 1
        except OSError as exc:
            return False, (f"The update stopped half way: {exc}\n\nPress "
                           f"update again to finish it.")

    removed = 0
    for rel in (old or {}).get("files", {}):
        if rel not in written and not _kept(rel):
            try:
                os.remove(os.path.join(root, rel))
                removed += 1
            except OSError:
                pass
    with open(os.path.join(root, INSTALL_MARK), "w", encoding="utf-8") as fh:
        json.dump({"sha": sha, "files": written}, fh, indent=0)

    lines = [f"Updated: {((old or {}).get('sha') or 'unknown')[:7]} -> {sha[:7]}",
             f"{changed} file(s) changed, {removed} removed."]
    if info.get("log"):
        lines += ["", "What came in:"]
        lines += ["   " + ln for ln in info["log"].splitlines()[:12]]
    lines += ["", "Restart the program for this to take effect."]
    return True, "\n".join(lines)
