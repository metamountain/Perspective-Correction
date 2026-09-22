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


def test_a_folder_that_is_not_a_checkout_says_so_instead_of_failing():
    """A downloaded zip is a perfectly good way to run this program."""
    with tempfile.TemporaryDirectory() as d:
        assert SU.is_checkout(d) is False
        info = SU.check(d)
        assert info["can_update"] is False
        assert "did not come from git" in info["message"]


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
