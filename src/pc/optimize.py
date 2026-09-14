"""A small Nelder-Mead simplex.

darktable's ashift refines its correction the same way, for the same reason:
the cost surface over the correction parameters is cheap to evaluate, has no
useful derivative in closed form once robust weights are in play, and only ever
needs a couple of hundred evaluations.  Vendoring ~60 lines beats a scipy
dependency in a tool that is otherwise numpy + OpenCV.
"""
from __future__ import annotations

import numpy as np

ALPHA, BETA, GAMMA, DELTA = 1.0, 0.5, 2.0, 0.5


def nelder_mead(fn, x0, step, max_iter: int = 400, tol: float = 1e-9):
    """Minimise ``fn`` from ``x0``.  Returns ``(x, f(x), n_evals)``."""
    x0 = np.asarray(x0, dtype=float)
    n = len(x0)
    step = np.broadcast_to(np.asarray(step, dtype=float), (n,)).copy()

    simplex = np.tile(x0, (n + 1, 1))
    for i in range(n):
        simplex[i + 1, i] += step[i]
    fvals = np.array([fn(p) for p in simplex])
    evals = n + 1

    for _ in range(max_iter):
        order = np.argsort(fvals)
        simplex, fvals = simplex[order], fvals[order]
        if abs(fvals[-1] - fvals[0]) <= tol * (abs(fvals[0]) + abs(fvals[-1]) + tol):
            break
        centroid = simplex[:-1].mean(axis=0)

        xr = centroid + ALPHA * (centroid - simplex[-1])
        fr = fn(xr); evals += 1
        if fr < fvals[0]:
            xe = centroid + GAMMA * (xr - centroid)
            fe = fn(xe); evals += 1
            simplex[-1], fvals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < fvals[-2]:
            simplex[-1], fvals[-1] = xr, fr
        else:
            xc = centroid + BETA * (simplex[-1] - centroid)
            fc = fn(xc); evals += 1
            if fc < fvals[-1]:
                simplex[-1], fvals[-1] = xc, fc
            else:
                simplex[1:] = simplex[0] + DELTA * (simplex[1:] - simplex[0])
                fvals[1:] = [fn(p) for p in simplex[1:]]
                evals += n

    best = int(np.argmin(fvals))
    return simplex[best], float(fvals[best]), evals
