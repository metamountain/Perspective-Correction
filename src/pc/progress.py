"""How long a stage will take, so the window can say so while it runs.

Measured, not guessed. A progress bar that lies teaches people to ignore
progress bars, so every constant below came off this project's own assets
(Platte_1.jpg scaled to 1, 4, 12 and 24 MPx, 2026-09-21) and the measurement
is repeatable with ``tools/measure_cost.py``.

    source   canvas    detect    warp     telea      lama
      1.0     9.0 MPx   0.07 s   0.00 s    8.78 s    4.16 s
      4.0    36.0       0.10     0.01      9.74      2.91
     12.0   108.0       0.11     0.03     13.07      4.51

Three things that table says, none of them obvious:

* **The fill dominates.** Warping a 108-megapixel canvas costs 30 ms; filling
  its band costs seconds. Any estimate that ignores the fill is noise.
* **telea is SLOWER than lama at full size**, by three to four times, which is
  the reverse of the preview: telea works on the real hole and grows with it,
  while lama generates at ``fill_max_edge`` and pastes back, so its cost is
  nearly flat. The "telea is the cheap one" rule holds only at preview size.
* **detect is flat**, because it runs on the analysis image, which is capped.

The numbers are this machine's. They are meant as an order of magnitude -- the
difference between "a moment" and "go and make tea" -- and the window says
"about", never a countdown.
"""

from __future__ import annotations

# seconds = BASE + PER_MPX * canvas megapixels
_COST = {
    "detect": (0.07, 0.002),
    "warp": (0.00, 0.0003),
    "fill:telea": (8.40, 0.043),
    "fill:lama": (3.50, 0.008),
    # The ComfyUI fill is a network round trip to another process; its cost is
    # that server's, not ours, and pretending to know it would be inventing.
    "fill:comfyui": (8.00, 0.010),
    "sam": (6.00, 0.0),
}


def estimate(stage: str, canvas_mpx: float, share: float = 1.0) -> float:
    """Seconds *stage* should take on a canvas of *canvas_mpx* megapixels.

    ``share`` is the fraction of the canvas the stage actually touches -- the
    hole, for a fill. It scales the size-dependent half only: loading a model
    costs the same whether it inpaints a sliver or half the frame.

    An unknown stage returns 0.0 rather than a guess. A caller that gets 0.0
    should show a working indicator with no time on it, which is honest; a
    made-up number is not.
    """
    base, per = _COST.get(stage, (0.0, 0.0))
    if base == 0.0 and per == 0.0:
        return 0.0
    return base + per * max(float(canvas_mpx), 0.0) * max(float(share), 0.0)


def total(stages, canvas_mpx: float, share: float = 1.0) -> float:
    """Seconds for a sequence of stages on one canvas."""
    return sum(estimate(s, canvas_mpx, share if s.startswith("fill:") else 1.0)
               for s in stages)


def humanise(seconds: float) -> str:
    """"about 4 s" / "about 1:20" / "" when there is nothing worth saying.

    Under two seconds there is no message: a bar that appears and vanishes is
    worse than no bar, because the flicker reads as a fault.
    """
    if seconds < 2.0:
        return ""
    if seconds < 90.0:
        return f"about {int(round(seconds))} s"
    m, s = divmod(int(round(seconds)), 60)
    return f"about {m}:{s:02d} min"
