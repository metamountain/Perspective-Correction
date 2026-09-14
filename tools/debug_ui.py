"""Sweep the whole window lifecycle and report what the layout gives each field.

Promoted out of analysis/scratch because the work packages in CLAUDE.md cite it
as a validation step, and a gitignored folder is the wrong home for something a
fresh clone has to be able to run.

    python tools/debug_ui.py

Ends "FAILURES: none" with bpc_errors.log clean, or names what broke.
"""
import os, sys, time, glob, types, traceback
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from bpc.gui import App

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
step("status has no failure", lambda: ("CLEAN" if "failed" not in r.status.get("1.0","end").lower() else "!! 'failed' in status"))
step("action row mapped", lambda: f"mapped={r._btns.winfo_ismapped()}" if hasattr(r,"_btns") else "no _btns attr")
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

print("== 5. planar corners ==")
def _planar_on():
    r.v_planar.set(True); r._on_planar_toggle(); pump(app, 15)
    return "on"
step("planar mode on", _planar_on)
def _place():
    for dx, dy in [(10, 10), (300, 14), (16, 250), (296, 246)]:
        r._on_planar_click(dx, dy)
    pump(app, 15)
    return f"corners={len(r.session.planar_quad)}"
step("place four corners", _place)
def _drag():
    before = r.session.planar_quad[0]
    r._on_planar_click(10, 10)              # click on corner 0 grabs it
    grabbed = "grabbed" if r._planar_drag == 0 else f"!! not grabbed ({r._planar_drag})"
    ev = types.SimpleNamespace(x=120 + r._before_off[0], y=130 + r._before_off[1])
    r._on_planar_drag(ev); r._on_planar_release(None)
    moved = "moved" if r.session.planar_quad[0] != before else "!! did not move"
    return f"{grabbed}, {moved}"
step("grab and drag corner 0", _drag)
def _planar_off():
    r.v_planar.set(False); r._on_planar_toggle(); pump(app, 15)
    return "off"
step("planar mode off", _planar_off)

print("== 6. clear back to empty ==")
def _clear():
    app.items = []
    app._refresh_items(); app._set_stage(); pump(app, 25)
    return f"canvas {r.c_before.winfo_width()}x{r.c_before.winfo_height()}"
step("clear", _clear)

app.destroy()
print("\nFAILURES:", fails if fails else "none")
print("bpc_errors.log:", "WROTE ERRORS" if os.path.exists("bpc_errors.log") else "clean")
