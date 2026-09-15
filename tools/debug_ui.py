"""Sweep the whole window lifecycle and report what the layout gives each field.

Promoted out of analysis/scratch because the work packages in CLAUDE.md cite it
as a validation step, and a gitignored folder is the wrong home for something a
fresh clone has to be able to run.

    python tools/debug_ui.py

Ends "FAILURES: none" with pc_errors.log clean, or names what broke.
"""
import os, sys, time, glob, types, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from pc.gui import App

ASSET = sorted(glob.glob(os.path.join("tests", "assets", "*.jpg")))[1]
fails = []

def step(name, fn):
    try:
        r = fn()
        print(f"  ok   {name}" + (f"  {r}" if r else ""))
    except Exception as e:
        fails.append(name)
        print(f"  FAIL {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=2)

def pump(app, n=30):
    for _ in range(n):
        app.update(); time.sleep(0.02)

def check_previews_match(app):
    """Both previews must be the same height and each fill most of its field.

    Written by the local Qwen worker to its spec; applied by hand because that
    worker has no file-edit tool (see the Ledger). The 70 % floor is not a
    measurement, it is a tripwire: the failure it exists to catch is one pane
    silently rendering smaller than the other, which is what an inset applied to
    only one of them did.
    """
    r = app.review
    cb_w, cb_h = r.c_before.winfo_width(), r.c_before.winfo_height()
    b_w, b_h = r._ph_b.width(), r._ph_b.height()
    a_w, a_h = r._ph_a.width(), r._ph_a.height()
    same_h = (b_h == a_h)
    fill_b = 100.0 * b_w * b_h / max(1, cb_w * cb_h)
    fill_a = 100.0 * a_w * a_h / max(1, cb_w * cb_h)
    ok = same_h and fill_b >= 70.0 and fill_a >= 70.0
    print("  %s previews: before %dx%d (%.1f%%)  after %dx%d (%.1f%%)  same height: %s"
          % ("ok  " if ok else "FAIL", b_w, b_h, fill_b, a_w, a_h, fill_a, same_h))
    return ok


def _errlog_size():
    """Bytes in pc_errors.log, or 0 if absent.

    It watched `bpc_errors.log` until 2026-09-14 -- a file the code stopped
    writing when the package was renamed `bpc` -> `pc`. So it was reporting on a
    file nothing could touch, and every "clean" it printed was vacuous.

    Compared before and after the sweep. It used to be `os.path.exists`, which
    reports "WROTE ERRORS" for a log left behind hours ago by something else --
    a diagnostic that cries wolf, and one that cost real time here: it made a
    correct "that error is historical" reading look wrong twice in a row.
    """
    try:
        return os.path.getsize("pc_errors.log")
    except OSError:
        return 0


_LOG_BEFORE = _errlog_size()


print("== 1. construct empty, cross must build ==")
app = App(start_maximized=False)
app.geometry("1600x1000-4000+0")
pump(app)
r = app.review
step("review panel exists", lambda: type(r).__name__)
step("both canvases exist", lambda: f"before={r.c_before.winfo_width()}x{r.c_before.winfo_height()} after={r.c_after.winfo_width()}x{r.c_after.winfo_height()}")
step("session is None when empty", lambda: f"session={r.session}")
step("empty canvas has drawn items", lambda: f"items={len(r.c_before.find_all())}")
step("grey add icons in image corner", lambda: f"plus={r.add_btn.cget('text')!r} folder={'ok' if r.add_folder_btn.winfo_ismapped() else 'missing'}")
step("controls present with no photo", lambda: f"grid_var={app.review.v_grid.get()}")

print("== 2. load a photograph ==")
step("add", lambda: app._add([ASSET]))
pump(app, 60)
step("session set", lambda: os.path.basename(r.session.path))
step("canvas after load", lambda: f"{r.c_before.winfo_width()}x{r.c_before.winfo_height()}")
# The review panel's status Text was removed (see the Ledger); errors go to
# pc_errors.log instead, which this script already checks at the end.
step("action row mapped", lambda: f"mapped={r._btns.winfo_ismapped()}" if hasattr(r,"_btns") else "no _btns attr")
step("previews match", lambda: check_previews_match(app))
step("results tree height", lambda: f"{app._w_tree.winfo_height()}px")

print("== 3. resize sweep ==")
for w,h in [(1280,800),(1920,1080),(2560,1400)]:
    def _rs(w=w,h=h):
        app.geometry(f"{w}x{h}-4000+0"); pump(app, 25)
        return (f"{w}x{h} -> canvas {r.c_before.winfo_height()}px "
                f"tree {app._w_tree.winfo_height()}px "
                f"btns {r._btns.winfo_ismapped() if hasattr(r,'_btns') else '?'}")
    step(f"resize {w}x{h}", _rs)

print("== 4. toggles ==")
step("grid on", lambda: (app.review.v_grid.set(True), app.review._schedule_redraw(), pump(app,15), "on")[-1])
step("grid off", lambda: (app.review.v_grid.set(False), app.review._schedule_redraw(), pump(app,15), "off")[-1])

print("== 6. clear back to empty ==")
def _clear():
    app.items = []
    app._refresh_items(); app._set_stage(); pump(app, 25)
    return f"canvas {r.c_before.winfo_width()}x{r.c_before.winfo_height()}"
step("clear", _clear)

app.destroy()
print("\nFAILURES:", fails if fails else "none")
_log_after = _errlog_size()
if _log_after > _LOG_BEFORE:
    print("pc_errors.log: WROTE ERRORS (+%d bytes this run)" % (_log_after - _LOG_BEFORE))
else:
    print("pc_errors.log: clean (this run wrote nothing)")
