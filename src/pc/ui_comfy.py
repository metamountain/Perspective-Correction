"""The ComfyUI settings window and its controls.

The controls the ``App`` in :mod:`pc.gui` offers for ComfyUI -- the server
address, the workflow selector, the model lists and the connection test --
live here on a mixin.  This module never imports ``pc.gui``: it only needs
the standard library, ``prefs`` and the theme names, plus ``inpaint``'s URL
helper and lazy imports of ``inpaint`` inside the methods that use it.
"""
from __future__ import annotations

import os
import threading

import tkinter as tk
from tkinter import filedialog, ttk

from . import prefs
from .inpaint import join_url as _join_url
from .ui_theme import INK, _attach_tooltip


class _ComfyMixin:
    """The ComfyUI settings popup and its controls, mixed into ``App``.

    The host supplies the state the popup draws (``v_comfy_host``,
    ``v_comfy_port``, ``v_comfy_wf``, ``v_comfy_wf_pick``,
    ``v_comfy_models``, ``lbl_fill``), the bookkeeping it remembers
    (``_comfy_state``, ``_comfy_models_cache``, ``_comfy_listeners``,
    ``_w_comfy``) and the helpers it calls (``_settings``,
    ``_check_fill``); the methods only add the withdrawn settings window and
    its widgets on ``self``.
    """

    # -- the ComfyUI dock ------------------------------------------------
    def _build_comfy_popup(self):
        """The ComfyUI controls, in a small window that stays out of the way.

        They were docked along the bottom of this window, which cost four
        permanent rows for one of four fill modes -- visible to everyone who
        never fills a band, and gone from no one.  Before that they were a
        Toplevel, and before that a row inside the options panel; the panel is
        hidden until a folder is loaded, so the server could not be configured
        *before* the work -- which is the only time anyone wants to.

        The answer is a Toplevel built once at startup and kept withdrawn: it
        costs nothing on screen until opened, and closing hides rather than
        destroys, so the verdict and the model lists survive a round trip.
        The StringVars stay on the App, so `_comfy_open`, `_show_comfy_state`,
        `_fill_model_lists` and the queue path are untouched.
        """
        win = tk.Toplevel(self)
        win.configure(bg=INK["bg"])
        win.title("ComfyUI settings")
        win.transient(self)
        win.protocol("WM_DELETE_WINDOW", win.withdraw)
        self._w_comfy = win
        body = ttk.Frame(win, padding=(10, 8))
        body.pack(fill="both", expand=True)

        addr = ttk.Frame(body)
        addr.pack(fill="x")
        ttk.Label(addr, text="server").pack(side="left")
        self.ent_comfy_host = ttk.Entry(addr, textvariable=self.v_comfy_host, width=20)
        self.ent_comfy_host.pack(side="left", padx=(6, 2))
        ttk.Entry(addr, textvariable=self.v_comfy_port, width=6).pack(side="left")
        self.btn_comfy_test = ttk.Button(addr, text="Test connection",
                                         command=self._test_comfy)
        self.btn_comfy_test.pack(side="left", padx=8)
        _attach_tooltip(self.btn_comfy_test, "Check that the ComfyUI server is reachable and the workflow loads")
        self.lbl_comfy_state = tk.Label(body, text="not checked",
                                        background=INK["bg"], foreground=INK["dim"])
        self.lbl_comfy_state.pack(anchor="w")

        # A list, not a browse button.  The graph decides what the fill *is*,
        # and the unset case silently took the inpainting one and ran an edit
        # model through it, at a green light, because every checkpoint it named
        # was installed.  Naming the choice is the fix; see `inpaint.SHIPPED`.
        wfr = ttk.Frame(body)
        wfr.pack(fill="x", pady=(8, 0))
        ttk.Label(wfr, text="workflow").pack(anchor="w")
        self.cb_comfy_wf = ttk.Combobox(wfr, textvariable=self.v_comfy_wf_pick,
                                        width=46, state="readonly")
        self.cb_comfy_wf.pack(fill="x", pady=(2, 0))
        self.cb_comfy_wf.bind("<<ComboboxSelected>>",
                              lambda e: self._on_comfy_workflow_pick())
        self.lbl_comfy_wf = ttk.Label(wfr, text="", style="Dim.TLabel")
        self.lbl_comfy_wf.pack(anchor="w")

        # The three files a workflow names, chosen from what the server has.
        # `resolve_models` guesses well enough when one candidate is obviously
        # the same file under another name, and not at all when a machine has
        # forty-six text encoders installed -- which is the normal case, and the
        # reason these are a selector rather than a message.  Stacked, because
        # the popup is narrow where the dock was wide.
        mrow = ttk.Frame(body)
        mrow.pack(fill="x", pady=(8, 0))
        self.cb_comfy_models = {}
        for label, key in (("model", "comfy_unet"), ("clip", "comfy_clip"),
                           ("vae", "comfy_vae")):
            row = ttk.Frame(mrow)
            row.pack(fill="x")
            ttk.Label(row, text=label, width=6).pack(side="left")
            box = ttk.Combobox(row, textvariable=self.v_comfy_models[key],
                               width=40, state="readonly",
                               values=["(from the workflow)"])
            box.pack(side="left", fill="x", expand=True)
            self.cb_comfy_models[key] = box

        self.lbl_comfy_detail = ttk.Label(body, text="", style="Dim.TLabel",
                                          wraplength=440, justify="left")
        self.lbl_comfy_detail.pack(fill="x", pady=(8, 0))

        self._sync_comfy_workflow_label()
        self._fill_model_lists(self._comfy_models_cache)
        self._show_comfy_state(*self._comfy_state)
        win.withdraw()

    def _open_comfy(self):
        """Open the ComfyUI settings window.

        Kept as a name because three callers mean "let them at the ComfyUI
        settings": the menu, the fill selector, and a review window's button.
        The window is built once and kept withdrawn, so this only shows it --
        the verdict and model lists from the last test are still there.
        """
        try:
            self._w_comfy.deiconify()
            self._w_comfy.lift()
            self.ent_comfy_host.focus_set()
        except Exception:                     # a torn-down or headless window
            pass

    def _comfy_open(self):
        """Whether the controls exist to be drawn into.

        Always true once `_build` has run, and false during construction and
        teardown -- which is what the `_show_comfy_state` and
        `_sync_comfy_workflow_label` guards are actually asking.
        """
        return getattr(self, "lbl_comfy_state", None) is not None

    CHOOSE_A_FILE = "choose a file..."

    def _comfy_workflow_choices(self):
        """``([label, ...], {label: path})`` -- the shipped graphs, then a file.

        The path is absolute so the choice is a *choice*: once it is stored,
        `inpaint.workflow_path` reports ``chosen`` and the indicator stops
        saying nobody picked one.
        """
        from . import inpaint as FILL
        labels, paths = [], {}
        for name, what in FILL.SHIPPED:
            label = f"{what}  [{name}]"
            labels.append(label)
            paths[label] = os.path.join(FILL.WORKFLOWS, name)
        current = self.v_comfy_wf.get()
        if current and current not in paths.values():
            label = os.path.basename(current)
            labels.append(label)
            paths[label] = current
        labels.append(self.CHOOSE_A_FILE)
        return labels, paths

    def _sync_comfy_workflow_label(self):
        """Show which graph is in force -- by name, and never as 'shipped'.

        The old label said "shipped workflow" while two of them ship, which is
        the whole defect: it read as an answer and named nothing.
        """
        if not self._comfy_open():
            return
        labels, paths = self._comfy_workflow_choices()
        self.cb_comfy_wf.configure(values=labels)
        current = self.v_comfy_wf.get()
        from . import inpaint as FILL
        if not current:
            # Nobody chose. Say so where the choice is made, not only in the
            # detail line -- a blank box reads as "fine".
            self.v_comfy_wf_pick.set("")
            self.lbl_comfy_wf.configure(
                text=f"not chosen -- {os.path.basename(FILL.DEFAULT_WORKFLOW)} will run")
            return
        for label, path in paths.items():
            if path == current:
                self.v_comfy_wf_pick.set(label)
                break
        self.lbl_comfy_wf.configure(text="")

    def _on_comfy_workflow_pick(self):
        labels, paths = self._comfy_workflow_choices()
        picked = self.v_comfy_wf_pick.get()
        if picked == self.CHOOSE_A_FILE:
            # A cancelled dialog must not leave the box showing the sentinel.
            if not self._pick_comfy_workflow():
                self._sync_comfy_workflow_label()
            return
        self._use_comfy_workflow(paths.get(picked, ""))

    def _pick_comfy_workflow(self):
        """The API export, not the editor export.

        ``inpaint.load_workflow`` tells the two apart and says which one it got,
        because posting an editor export to ``/prompt`` is the mistake everyone
        makes once and the error it produces on its own is unreadable.
        """
        p = filedialog.askopenfilename(
            title="ComfyUI workflow (API format)", parent=self,
            initialdir=self._comfy_workflow_dir(),
            filetypes=[("ComfyUI API workflow", "*.json"), ("All files", "*.*")])
        if not p:
            return False
        self._use_comfy_workflow(p)
        return True

    def _comfy_workflow_dir(self):
        from . import inpaint as FILL
        current = self.v_comfy_wf.get()
        return os.path.dirname(current) if current else FILL.WORKFLOWS

    def _use_comfy_workflow(self, path):
        if not path:
            return
        self.v_comfy_wf.set(path)
        self._sync_comfy_workflow_label()
        prefs.save(comfy_workflow=path)    # an address, not a correction setting
        # The workflow is half of what "connected" means -- a reachable server
        # with an unusable graph is not a working fill -- so the verdict is
        # stale the moment it changes.
        self._show_comfy_state("unknown", "the workflow changed -- press Test connection")
        self._check_fill()

    def _comfy_url(self):
        return _join_url(self.v_comfy_host.get(), self.v_comfy_port.get())

    def _remember_model(self, key):
        """A chosen checkpoint is an address, like the mask folder."""
        value = self.v_comfy_models[key].get()
        if value and not value.startswith("("):
            prefs.save(**{key: value})

    def _fill_model_lists(self, models):
        """Offer what the server reported, keeping any choice still valid.

        The first entry is always "(from the workflow)" -- the default, and the
        only honest label for it. Naming a file the user did not pick would make
        the selector claim a decision nobody made, and the workflow's own value
        is what runs until they do.  Silently does nothing when the window is
        shut: the choices live on the App and outlive it.
        """
        if not self._comfy_open():
            return
        for key, box in self.cb_comfy_models.items():
            names = models.get(key) or []
            box.configure(values=["(from the workflow)"] + names)
            current = self.v_comfy_models[key].get()
            if current and not current.startswith("(") and current in names:
                continue                          # a still-valid choice survives
            self.v_comfy_models[key].set("(from the workflow)")

    def _show_comfy_state(self, state, text):
        """Remembered on the App, drawn only when the window is open."""
        self._comfy_state = (state, text)
        label, ink = self.COMFY_LIGHTS.get(state, self.COMFY_LIGHTS["down"])
        if self._comfy_open():
            self.lbl_comfy_state.configure(text=label, foreground=INK[ink])
            self.lbl_comfy_detail.configure(text=text)
        # A verdict is news; "not checked yet" (start-up, an address being
        # typed) is not, and on the picture it read as an error.
        if state != "unknown":
            self.lbl_fill.configure(text=f"ComfyUI: {label} -- {text}")
        for listen in list(self._comfy_listeners):
            try:
                listen(state, text)
            except Exception:     # a dead review window must not take this one down
                self._comfy_listeners.remove(listen)

    def _test_comfy(self):
        """Ask the server whether it is there, off the UI thread.

        ``describe`` does two network round trips with a three second timeout
        each, and doing that inline freezes the window mid-click -- which reads
        as a crash, not as a slow server.  The button says it is working and
        comes back either way; a check that cannot fail visibly is not a check.
        """
        if self._comfy_open():
            self.btn_comfy_test.configure(state="disabled")
            self.lbl_comfy_state.configure(text="checking...", foreground=INK["dim"])
        self.lbl_fill.configure(text="ComfyUI: asking...")
        settings = self._settings().replace(fill="comfyui")

        def work():
            from . import inpaint as FILL
            models = {}
            try:
                state, text = FILL.status(settings)
                if state != "down":
                    # Same round trip answers both questions, so ask once.
                    models = FILL.model_options(settings.comfy_url)
            except Exception as exc:                     # never take the window down
                state, text = "down", f"comfyui check failed: {exc}"
            # Through the queue the batch run already uses, not `after` from
            # here: Tk is not thread-safe, and registering a callback from a
            # worker raises "main thread is not in main loop" outright.
            self.queue.put(("comfy", (state, text, models)))

        threading.Thread(target=work, daemon=True).start()

    # Three lights, because "up but running on a guessed checkpoint" is neither
    # of the other two: green would hide it, red would refuse something that
    # works.
    COMFY_LIGHTS = {"ok":      ("connected", "ok"),
                    "models":  ("models missing", "warn"),
                    "down":    ("disconnected", "err"),
                    # Not a verdict.  "disconnected" for an address nobody has
                    # asked yet would be a claim, and a wrong one.
                    "unknown": ("not checked", "dim")}

    def _comfy_result(self, state, text, models=None):
        if self._comfy_open():
            self.btn_comfy_test.configure(state="normal")
        self._comfy_models_cache = models or self._comfy_models_cache
        self._fill_model_lists(self._comfy_models_cache)
        self._show_comfy_state(state, text)
        if state != "down":
            prefs.save(comfy_url=self._comfy_url())
