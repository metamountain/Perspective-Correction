"""Remembered paths.

Paths only, and on purpose: a correction parameter that silently persists
between runs is one nobody can reason about, and this whole project turns on a
batch being reproducible from its command line. A checkpoint path is machine
configuration, not a decision about the photographs.
"""
import json
import os
import tempfile

from pc import prefs


def _isolated(d):
    os.environ["XDG_CONFIG_HOME"] = d
    os.environ.pop("APPDATA", None)


def test_saving_and_loading_a_path():
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        assert prefs.load() == {}
        assert prefs.save(mask_file="/masks")
        assert prefs.load()["mask_file"] == "/masks"


def test_only_known_keys_survive():
    """A preferences file must not become a second place where behaviour is
    configured behind the user's back."""
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        prefs.save(mask_file="/a")
        with open(prefs.path(), "w", encoding="utf-8") as fh:
            json.dump({"mask_file": "/a", "min_confidence": 0.01,
                       "detector": "lsd"}, fh)
        got = prefs.load()
        assert got == {"mask_file": "/a"}


def test_saving_merges_rather_than_replaces():
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        prefs.save(mask_file="/a")
        prefs.save(mask_mode="off")
        got = prefs.load()
        assert got["mask_file"] == "/a" and got["mask_mode"] == "off"


def test_empty_values_are_not_stored():
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        assert not prefs.save(mask_file="", mask_mode=None)
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
        assert prefs.save(mask_file="/a")
        assert prefs.load()["mask_file"] == "/a"


def test_an_explicit_flag_beats_a_remembered_one():
    from pc.cli import apply_prefs, build_parser
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        prefs.save(mask_file="/remembered", focal_35mm=28)
        p = build_parser()
        args = p.parse_args(["x", "--mask-file", "/typed"])
        apply_prefs(args, p)
        assert args.mask_file == "/typed"
        assert args.focal_35mm == 28.0          # unset, so the stored one fills in


def test_forget_removes_everything():
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        prefs.save(mask_file="/a")
        assert prefs.forget()
        assert prefs.load() == {}


def test_the_comfyui_address_is_remembered_but_the_correction_is_not():
    """A server URL and a workflow file are *addresses*, like the checkpoint and
    the mask folder, so they are remembered.  How hard to correct is not: a
    setting that silently persists between runs is one nobody can reason about,
    and a batch must stay reproducible from its command line.
    """
    with tempfile.TemporaryDirectory() as d:
        _isolated(d)
        prefs.save(comfy_url="http://10.0.0.5:8188",
                   comfy_workflow=os.path.join("wf", "outpaint.json"),
                   pitch_strength=0.5, fill="comfyui")
        got = prefs.load()

    assert got["comfy_url"] == "http://10.0.0.5:8188"
    assert got["comfy_workflow"].endswith("outpaint.json")
    assert "pitch_strength" not in got, "a correction parameter must not persist"
    assert "fill" not in got, "which backend runs is a decision per run"
