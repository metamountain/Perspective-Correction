"""Remembered paths.

Paths only, and on purpose: a correction parameter that silently persists
between runs is one nobody can reason about, and this whole project turns on a
batch being reproducible from its command line. A checkpoint path is machine
configuration, not a decision about the photographs.
"""
import json
import os
import tempfile

from bpc import prefs


def _isolated(d):
    os.environ["XDG_CONFIG_HOME"] = d
    os.environ.pop("APPDATA", None)


def test_saving_and_loading_a_path():
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        assert prefs.load() == {}
        assert prefs.save(sam_model="/models/sam_vit_b.pth")
        assert prefs.load()["sam_model"] == "/models/sam_vit_b.pth"


def test_only_known_keys_survive():
    """A preferences file must not become a second place where behaviour is
    configured behind the user's back."""
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        prefs.save(sam_model="/a.pth")
        with open(prefs.path(), "w", encoding="utf-8") as fh:
            json.dump({"sam_model": "/a.pth", "min_confidence": 0.01,
                       "detector": "hough"}, fh)
        got = prefs.load()
        assert got == {"sam_model": "/a.pth"}


def test_saving_merges_rather_than_replaces():
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        prefs.save(sam_model="/a.pth")
        prefs.save(mask_file="/masks")
        got = prefs.load()
        assert got["sam_model"] == "/a.pth" and got["mask_file"] == "/masks"


def test_empty_values_are_not_stored():
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        assert not prefs.save(sam_model="", mask_file=None)
        assert prefs.load() == {}


def test_a_corrupt_file_is_ignored_not_fatal():
    """Failing to start because the preferences file is broken would be worse
    than forgetting."""
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        os.makedirs(os.path.dirname(prefs.path()), exist_ok=True)
        with open(prefs.path(), "w", encoding="utf-8") as fh:
            fh.write("{ not json at all")
        assert prefs.load() == {}
        assert prefs.save(sam_model="/a.pth")
        assert prefs.load()["sam_model"] == "/a.pth"


def test_an_explicit_flag_beats_a_remembered_one():
    from bpc.cli import apply_prefs, build_parser
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        prefs.save(sam_model="/remembered.pth", focal_35mm=28)
        p = build_parser()
        args = p.parse_args(["x", "--sam-model", "/typed.pth"])
        apply_prefs(args, p)
        assert args.sam_model == "/typed.pth"
        assert args.focal_35mm == 28.0          # unset, so the stored one fills in


def test_forget_removes_everything():
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        prefs.save(sam_model="/a.pth")
        assert prefs.forget()
        assert prefs.load() == {}
