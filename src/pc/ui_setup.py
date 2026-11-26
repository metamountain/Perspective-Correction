"""Setup and update dialogs for the window.

The three methods the ``App`` in :mod:`pc.gui` needs for the ``update`` and
``deps`` buttons live here on a mixin.  This module never imports ``pc.gui``:
it only needs the standard library and the theme names, plus lazy imports of
``selfupdate`` and ``depinstall`` inside the methods that use them.
"""
from __future__ import annotations

import threading

import tkinter as tk
from tkinter import messagebox, ttk

from .ui_theme import INK, _dark_titlebar


class _SetupMixin:
    """The setup and update methods, mixed into ``App``.

    The host supplies the widgets and helpers these methods drive (``queue``,
    ``btn_update``, ``btn_deps``, ``_notify``, ``_check_fill``) and the
    ``_AfterOwner`` scheduler; the methods only add the ``_w_deps`` window
    and the busy flags on ``self``.
    """

    def _check_for_update(self):
        """The `update` button beside the version number.

        A network call behind a button: the work happens on a daemon thread so
        the window keeps answering, the label carries the state instead of a
        second widget, and every return to Tk goes through `self.after(0, ...)`
        -- touching a widget from the worker thread is the bug this pattern
        exists to avoid.

        Two steps, and the user decides between them. `selfupdate.check` only
        looks; nothing moves until they say yes to what it found. A button that
        changes the program under you without showing you what it is taking is
        not something to press twice.
        """
        if getattr(self, "_upd_busy", False):
            return
        from . import selfupdate as SU
        self._upd_busy = True
        btn = self.btn_update
        # The label is the indicator: a second widget for two seconds of
        # network is not worth the width.
        btn.configure(text="checking")

        def done():
            btn.configure(text="update")
            self._upd_busy = False

        def found(info):
            done()
            if not info.get("can_update"):
                # Not an error: "already up to date" arrives here too, and so
                # does every refusal. They are all answers to what was asked.
                messagebox.showinfo("Update", info["message"], parent=self)
                return
            detail = info.get("log", "")
            ask = info["message"]
            if detail:
                ask += "\n\n" + "\n".join("   " + ln
                                          for ln in detail.splitlines()[:12])
            ask += "\n\nTake them now?"
            if not messagebox.askyesno("Update", ask, parent=self):
                return
            self._upd_busy = True
            btn.configure(text="updating")

            def pulled(ok, text):
                done()
                (messagebox.showinfo if ok else messagebox.showerror)(
                    "Update", text, parent=self)

            def pull():
                try:
                    ok, text = SU.apply()
                except Exception as exc:               # never lose the button
                    ok, text = False, str(exc)
                self._schedule_after(0, lambda: pulled(ok, text))

            threading.Thread(target=pull, daemon=True).start()

        def look():
            try:
                info = SU.check()
            except Exception as exc:
                info = {"can_update": False, "message": str(exc)}
            self._schedule_after(0, lambda: found(info))

        threading.Thread(target=look, daemon=True).start()

    def _install_deps(self):
        """The `deps` button: a menu of the optional packages.

        Every MISSING package is a row; what is installed is one line for
        reference and is never offered.  Telea is named as the built-in fill,
        nothing is ticked, and only what the user ticks is installed.  Ticking
        lama or SAM ticks torch too while torch is missing (neither can
        run without it).

        The work runs on a daemon thread with the button label as the
        indicator, back to Tk through `self.queue` (Tk refuses `after` from a
        thread whenever no mainloop runs -- found by the test, which pumps).
        Installs into the Python running the window.
        """
        if getattr(self, "_deps_busy", False):
            return
        from . import depinstall as DI
        old = getattr(self, "_w_deps", None)
        if old is not None:
            try:
                old.destroy()
            except tk.TclError:
                pass
        rows = DI.catalogue()
        # Asked of the WHOLE catalogue, before the filter drops the installed
        # rows: the tick handler must not auto-tick torch for a torch that is
        # not in the menu -- its checkbutton is never built, so the var lookup
        # raised KeyError 'torch' (measured: torch installed, lama missing,
        # tick "lama").
        torch_there = any(r["key"] == "torch" and r["installed"] for r in rows)
        # Only what is missing is offered; what is installed is one line,
        # for reference.
        have = [r for r in rows if r["installed"]]
        rows = [r for r in rows if not r["installed"]]
        if not rows:
            messagebox.showinfo("Optional packages",
                                "Everything optional is installed already:\n   "
                                + "\n   ".join(r["label"] for r in have), parent=self)
            return
        win = tk.Toplevel(self)
        win.configure(bg=INK["bg"])
        win.title("Optional packages")
        win.transient(self)
        self._w_deps = win
        body = ttk.Frame(win, padding=(10, 8))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Tick what to install. Nothing is installed unasked.").pack(anchor="w")
        self._deps_vars = {}
        boxes = {}

        def ticked(key):
            # lama/SAM without torch cannot run: take torch along, and
            # keep it while anything that needs it is ticked.
            v = self._deps_vars
            if key in DI.NEEDS_TORCH and v[key].get() and not torch_there:
                if "torch" in v:
                    v["torch"].set(True)
            if key == "torch" and not v["torch"].get():
                for k in DI.NEEDS_TORCH:
                    if k in v and not rows_by[k]["installed"]:
                        v[k].set(False)

        rows_by = {r["key"]: r for r in rows}
        for r in rows:
            v = tk.BooleanVar(value=False)
            self._deps_vars[r["key"]] = v
            cb = ttk.Checkbutton(body, variable=v, text=f"{r['label']}  ({r['size']})",
                                  command=lambda k=r["key"]: ticked(k))
            cb.pack(anchor="w", pady=(6, 0))
            ttk.Label(body, text="      " + r["purpose"], style="Dim.TLabel").pack(anchor="w")
            boxes[r["key"]] = cb
        self._deps_boxes = boxes
        if have:
            ttk.Label(body, text="already installed: " + ", ".join(r["key"] for r in have),
                      style="Dim.TLabel").pack(anchor="w", pady=(6, 0))
        ttk.Label(body, text="telea fill  (built in, always there)").pack(anchor="w", pady=(6, 0))
        ttk.Label(body, text="      fills the corners when lama is missing",
                  style="Dim.TLabel").pack(anchor="w")
        ttk.Label(body, text=f"Into the Python running this window:\n   {DI.pip_python()}",
                  style="Dim.TLabel").pack(anchor="w", pady=(8, 0))
        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(8, 0))

        def go():
            chosen = [dict(rows_by[k]) for k, v in self._deps_vars.items()
                      if v.get() and not rows_by[k]["installed"]]
            if not chosen:
                self._notify("nothing ticked -- nothing installed")
                return
            win.destroy()
            self._w_deps = None
            self._deps_run(DI.in_order(chosen))
        self._deps_go = go
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="Install ticked", style="Accent.TButton",
                   command=go).pack(side="right", padx=(0, 6))
        _dark_titlebar(win)

    def _deps_run(self, items):
        """Install ``items`` (ticked in the deps menu) on a thread."""
        from . import depinstall as DI
        self._deps_busy = True
        btn = self.btn_deps
        btn.configure(text="installing")
        self._notify("installing " + ", ".join(i["key"] for i in items)
                     + " -- the window stays usable")

        def step(i, n, label):
            self.queue.put(("call", lambda: btn.configure(text=f"{i + 1}/{n}")))

        def finished(results):
            btn.configure(text="deps")
            self._deps_busy = False
            ok, text = DI.summary(results)
            if any(item["key"] == "lama" and good for item, good, _r in results):
                text += "\n\nlama fills from the next preview or save on."
            (messagebox.showinfo if ok else messagebox.showwarning)(
                "Optional packages", text, parent=self)
            self._notify("optional packages installed" if ok
                         else "some optional packages could not be installed")
            if any(good for _i, good, _r in results):
                self._check_fill()

        def work():
            try:
                results = DI.install(items, on_step=step)
            except Exception as exc:                   # never lose the button
                results = [(items[0], False, str(exc))]
            self.queue.put(("call", lambda: finished(results)))

        threading.Thread(target=work, daemon=True).start()
