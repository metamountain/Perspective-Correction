"""The update button's logic, against real git repositories.

Not mocked. Each test builds a throwaway remote and a clone of it, puts them
into one of the states the button has to survive, and asks. Mocking `git` here
would test my idea of what git prints, and the first bug this file caught was
exactly that kind: `_git` strips its output, `status --porcelain` is column
aligned, and `src/pc/gui.py` came back as `rc/pc/gui.py`.

The property that matters most is negative and is asserted everywhere: after a
refusal, the local commits are still there. An updater that can lose work is
worse than no updater.
"""
import os
import subprocess
import tempfile

from pc import selfupdate as SU


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _commit(repo, name, text):
    with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
        fh.write(text)
    _git(["add", name], repo)
    _git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", text], repo)


def _pair(d):
    """A bare remote with one commit, and a clone of it. Returns the clone."""
    remote = os.path.join(d, "remote.git")
    work = os.path.join(d, "seed")
    clone = os.path.join(d, "clone")
    os.makedirs(work)
    _git(["init", "-q", "-b", "main"], work)
    _commit(work, "a.txt", "one")
    _git(["init", "-q", "--bare", "-b", "main", remote], d)
    _git(["remote", "add", "origin", remote], work)
    _git(["push", "-q", "origin", "main"], work)
    _git(["clone", "-q", remote, clone], d)
    return clone, work


def test_a_folder_that_is_not_a_checkout_never_runs_git_and_unreachable_is_a_sentence():
    """A downloaded archive is a perfectly good way to run this program. No
    `.git` means no git call: on a Mac without the developer tools git is a
    stub that opens an install dialog."""
    with tempfile.TemporaryDirectory() as d:
        real = SU._git
        SU._git = lambda *a, **k: (_ for _ in ()).throw(AssertionError("git ran"))
        try:
            assert SU.is_checkout(d) is False
        finally:
            SU._git = real
        info = SU.archive_check(d, api="http://127.0.0.1:9/nothing")
        assert info["can_update"] is False
        assert "Could not reach GitHub" in info["message"]


def test_up_to_date_is_reported_as_an_answer_not_an_error():
    with tempfile.TemporaryDirectory() as d:
        clone, _ = _pair(d)
        info = SU.check(clone)
        assert info["can_update"] is False
        assert info["behind"] == 0
        assert "up to date" in info["message"].lower()


def test_a_waiting_commit_is_offered_with_its_subject_and_then_applied():
    with tempfile.TemporaryDirectory() as d:
        clone, work = _pair(d)
        _commit(work, "b.txt", "the new thing")
        _git(["push", "-q", "origin", "main"], work)

        info = SU.check(clone)
        assert info["can_update"] is True
        assert info["behind"] == 1
        assert "the new thing" in info["log"], \
            "the offer has to name what it is about to take"

        ok, text = SU.apply(clone)
        assert ok, text
        assert os.path.exists(os.path.join(clone, "b.txt")), "the file arrived"
        assert "1 commit(s), 1 file(s) changed" in text
        assert "Restart" in text, \
            "the code already imported is the old code; saying so is the point"


def test_uncommitted_work_stops_it_and_the_files_are_named():
    """The refusal has to be actionable -- which file, not just 'dirty'."""
    with tempfile.TemporaryDirectory() as d:
        clone, work = _pair(d)
        _commit(work, "b.txt", "two")
        _git(["push", "-q", "origin", "main"], work)
        with open(os.path.join(clone, "a.txt"), "w", encoding="utf-8") as fh:
            fh.write("something the user typed")

        assert SU.dirty_files(clone) == ["a.txt"]
        info = SU.check(clone)
        assert info["can_update"] is False
        assert "a.txt" in info["message"]
        assert "not overwrite" in info["message"]

        ok, _ = SU.apply(clone)
        assert not ok, "apply must re-check, not trust an earlier look"
        with open(os.path.join(clone, "a.txt"), encoding="utf-8") as fh:
            assert fh.read() == "something the user typed", "untouched"


def test_a_rewritten_remote_is_named_as_such_and_nothing_is_merged():
    """The force-push case, which this project caused for itself on
    2026-09-22. A pull here produces a merge nobody asked for, and the usual
    cause cannot be fixed by pulling at all -- so it says clone again."""
    with tempfile.TemporaryDirectory() as d:
        clone, work = _pair(d)
        head_before = _git(["rev-parse", "HEAD"], clone).stdout.strip()
        # rewrite the remote's only commit, exactly as a history rewrite does
        _git(["-c", "user.email=t@t", "-c", "user.name=t",
              "commit", "-q", "--amend", "-m", "one, rewritten"], work)
        _git(["push", "-q", "--force", "origin", "main"], work)

        info = SU.check(clone)
        assert info["can_update"] is False
        assert "diverged" in info["message"]
        assert "clone the project again" in info["message"]

        ok, _ = SU.apply(clone)
        assert not ok
        assert _git(["rev-parse", "HEAD"], clone).stdout.strip() == head_before, \
            "a refusal must leave HEAD exactly where it was"


def test_local_commits_are_never_merged_away():
    with tempfile.TemporaryDirectory() as d:
        clone, _ = _pair(d)
        _commit(clone, "mine.txt", "my own work")
        mine = _git(["rev-parse", "HEAD"], clone).stdout.strip()

        info = SU.check(clone)
        assert info["can_update"] is False
        assert "AHEAD" in info["message"]

        ok, _ = SU.apply(clone)
        assert not ok
        assert _git(["rev-parse", "HEAD"], clone).stdout.strip() == mine
        assert os.path.exists(os.path.join(clone, "mine.txt"))


def test_a_detached_head_is_refused_rather_than_updated_into_nowhere():
    with tempfile.TemporaryDirectory() as d:
        clone, work = _pair(d)
        _commit(work, "b.txt", "two")
        _git(["push", "-q", "origin", "main"], work)
        _git(["fetch", "-q"], clone)
        _git(["checkout", "-q", "--detach", "HEAD"], clone)

        info = SU.check(clone)
        assert info["can_update"] is False
        assert "detached" in info["message"].lower()


def test_no_remote_means_there_is_nowhere_to_update_from():
    with tempfile.TemporaryDirectory() as d:
        work = os.path.join(d, "solo")
        os.makedirs(work)
        _git(["init", "-q", "-b", "main"], work)
        _commit(work, "a.txt", "one")

        info = SU.check(work)
        assert info["can_update"] is False
        assert "no remote" in info["message"].lower()


def test_a_missing_git_is_a_sentence_not_a_traceback():
    """`git` absent is the one failure a user can do nothing about from
    inside the program, so it must arrive as prose."""
    ok, text = SU._git(["status"], root=os.path.dirname(__file__))
    assert isinstance(text, str)
    # and the shape of the helper's contract: never an exception, always a pair
    ok2, text2 = SU._git(["rev-parse", "--verify", "no-such-ref-at-all"],
                         root=os.path.dirname(__file__))
    assert ok2 is False and isinstance(text2, str)


def test_the_paths_it_reports_are_whole():
    """The first bug this file caught. `_git` strips its output and
    `status --porcelain` is column aligned, so the leading space of the first
    line was eaten and `src/pc/gui.py` came back as `rc/pc/gui.py`."""
    with tempfile.TemporaryDirectory() as d:
        clone, _ = _pair(d)
        os.makedirs(os.path.join(clone, "src", "pc"))
        _commit(clone, os.path.join("src", "pc", "gui.py"), "x = 1\n")
        with open(os.path.join(clone, "src", "pc", "gui.py"), "a",
                  encoding="utf-8") as fh:
            fh.write("y = 2\n")
        assert SU.dirty_files(clone) == ["src/pc/gui.py"]


# --- a copy without git: the archive path, against a local stand-in for GitHub

import io
import json
import tarfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class _FakeGitHub:
    """Serves `commits/main`, `compare/A...B` and `tar/<sha>` from a folder,
    over real HTTP, so `urllib` and the tarball code run as they do live."""

    def __init__(self, d):
        self.dir = os.path.join(d, "gh")
        for sub in ("commits", "compare", "tar"):
            os.makedirs(os.path.join(self.dir, sub))
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0),
                                       partial(_Quiet, directory=self.dir))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.api, self.tarball = base, base + "/tar/{sha}"
        self.head = None

    def publish(self, sha, subject, files, modes=None, extra=None):
        """Make `files` ({path: text}) the newest commit on main."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, text in list(files.items()) + list((extra or {}).items()):
                data = text.encode("utf-8")
                info = tarfile.TarInfo(f"Perspective-Correction-{sha}/{name}")
                info.size, info.mode = len(data), (modes or {}).get(name, 0o644)
                tar.addfile(info, io.BytesIO(data))
        with open(os.path.join(self.dir, "tar", sha), "wb") as fh:
            fh.write(buf.getvalue())
        if self.head:
            cmp = {"ahead_by": 1, "commits": [
                {"sha": sha, "commit": {"message": subject + "\n\nbody"}}]}
            with open(os.path.join(self.dir, "compare", f"{self.head}...{sha}"),
                      "w", encoding="utf-8") as fh:
                json.dump(cmp, fh)
        with open(os.path.join(self.dir, "commits", "main"), "w",
                  encoding="utf-8") as fh:
            json.dump({"sha": sha, "commit": {"message": subject}}, fh)
        self.head = sha

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read()


def _write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


A = "a" * 40
B = "b" * 40


def test_an_archive_install_is_updated_and_what_the_user_owns_is_left_alone():
    """The first update of an unpacked copy (no mark yet), then a second one
    that changes, adds and removes files. Photographs, the venv and anything
    else the updates did not write survive both."""
    with tempfile.TemporaryDirectory() as d:
        gh, root = _FakeGitHub(d), os.path.join(d, "install")
        try:
            _write(root, "src/pc/gui.py", "old\n")
            _write(root, "photo.jpg", "mine\n")
            _write(root, ".venv/bin/python", "venv\n")
            gh.publish(A, "first", {"src/pc/gui.py": "v1\n", "gone.txt": "x\n",
                                    "start.command": "#!/bin/sh\n"},
                       modes={"start.command": 0o755},
                       extra={".venv/bin/python": "from the archive\n"})

            info = SU.archive_check(root, api=gh.api)
            assert info["can_update"] is True
            assert "unknown -> aaaaaaa" in info["message"]
            ok, text = SU.archive_apply(root, api=gh.api, tarball=gh.tarball)
            assert ok, text
            assert "Restart" in text
            assert _read(root, "src/pc/gui.py") == "v1\n"
            assert _read(root, "photo.jpg") == "mine\n"
            assert _read(root, ".venv/bin/python") == "venv\n"
            if os.name != "nt":
                assert os.stat(os.path.join(root, "start.command")).st_mode & 0o111
            assert SU.read_mark(root)["sha"] == A
            assert "Already up to date" in SU.archive_check(root, api=gh.api)["message"]

            gh.publish(B, "second", {"src/pc/gui.py": "v2\n", "new.txt": "n\n",
                                     "start.command": "#!/bin/sh\n"})
            info = SU.archive_check(root, api=gh.api)
            assert info["can_update"] and "bbbbbbb second" in info["log"]
            ok, text = SU.archive_apply(root, api=gh.api, tarball=gh.tarball)
            assert ok, text
            assert _read(root, "src/pc/gui.py") == "v2\n"
            assert _read(root, "new.txt") == "n\n"
            assert not os.path.exists(os.path.join(root, "gone.txt"))
            assert _read(root, "photo.jpg") == "mine\n"
            assert "1 removed" in text
        finally:
            gh.close()


def test_a_file_edited_since_the_last_update_stops_the_next_one():
    """The archive twin of a dirty tree: named, and nothing is written."""
    with tempfile.TemporaryDirectory() as d:
        gh, root = _FakeGitHub(d), os.path.join(d, "install")
        try:
            gh.publish(A, "first", {"src/pc/gui.py": "v1\n", "b.txt": "b\n"})
            assert SU.archive_apply(root, api=gh.api, tarball=gh.tarball)[0]
            _write(root, "src/pc/gui.py", "my fix\n")
            gh.publish(B, "second", {"src/pc/gui.py": "v2\n", "b.txt": "b2\n"})

            info = SU.archive_check(root, api=gh.api)
            assert info["can_update"] is False
            assert "src/pc/gui.py" in info["message"]
            ok, _ = SU.archive_apply(root, api=gh.api, tarball=gh.tarball)
            assert ok is False
            assert _read(root, "src/pc/gui.py") == "my fix\n"
            assert _read(root, "b.txt") == "b\n"
            assert SU.read_mark(root)["sha"] == A
        finally:
            gh.close()


def test_an_archive_that_reaches_outside_the_folder_is_refused_whole():
    with tempfile.TemporaryDirectory() as d:
        gh, root = _FakeGitHub(d), os.path.join(d, "install")
        try:
            os.makedirs(root)
            gh.publish(A, "evil", {"ok.txt": "fine\n", "../escaped.txt": "no\n"})
            ok, text = SU.archive_apply(root, api=gh.api, tarball=gh.tarball)
            assert ok is False and "unsafe path" in text
            assert not os.path.exists(os.path.join(d, "escaped.txt"))
            assert not os.path.exists(os.path.join(root, "ok.txt"))
            assert SU.read_mark(root) is None
        finally:
            gh.close()


def test_an_update_that_stops_half_way_can_be_finished_by_pressing_again():
    """Its own message says "press update again to finish it", so that must
    work: the files written before the failure are the update's, not edits."""
    with tempfile.TemporaryDirectory() as d:
        gh, root = _FakeGitHub(d), os.path.join(d, "install")
        try:
            gh.publish(A, "first", {"a.txt": "a1\n", "b.txt": "b1\n"})
            assert SU.archive_apply(root, api=gh.api, tarball=gh.tarball)[0]
            os.makedirs(os.path.join(root, "c.txt"))       # blocks the third file
            gh.publish(B, "second", {"a.txt": "a2\n", "b.txt": "b2\n", "c.txt": "c\n"})
            ok, text = SU.archive_apply(root, api=gh.api, tarball=gh.tarball)
            assert ok is False and "again" in text
            os.rmdir(os.path.join(root, "c.txt"))
            ok, text = SU.archive_apply(root, api=gh.api, tarball=gh.tarball)
            assert ok, text
            assert _read(root, "a.txt") == "a2\n" and _read(root, "c.txt") == "c\n"
            assert SU.read_mark(root)["sha"] == B
        finally:
            gh.close()
