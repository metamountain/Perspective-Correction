"""Synthetic architecture scenes with an exactly known camera pose.

The high-frequency-mask notes carry a hard-won lesson: a synthetic fixture
misled that project on the one question that mattered, because it was being
asked a *statistical* question (how does a histogram behave on real texture)
that synthetic data answered wrongly.

Here the question is *geometric* -- given a camera rotated by a known amount,
does the estimator recover that amount -- and for geometry a rendered scene with
an exact ground truth is the strictly better instrument, because on a real photo
nobody knows the true answer to compare against.  Real photographs are still
needed, and belong in ``tests/assets``; what they test is the front end (does
LSD find the facade at all under real texture, JPEG noise and foliage), not the
maths.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

WORLD_UP = np.array([0.0, -1.0, 0.0])


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


class Scene:
    """A building rendered through a camera with a known rotation."""

    def __init__(self, w=1200, h=800, focal_35mm=28.0, pitch_deg=0.0, roll_deg=0.0,
                 yaw_deg=0.0, seed=0, clutter=40, noise=3.0, corner=False,
                 texture=True, half_timbered=False, brace_lean_deg=28.0,
                 occluders=0):
        self.w, self.h = w, h
        self.f = focal_35mm * math.hypot(w, h) / math.hypot(36.0, 24.0)
        self.K = np.array([[self.f, 0, w / 2.0], [0, self.f, h / 2.0], [0, 0, 1.0]])
        self.pitch = math.radians(pitch_deg)
        self.roll = math.radians(roll_deg)
        self.yaw = math.radians(yaw_deg)
        # camera rotation: world -> camera
        self.R = rot_x(-self.pitch) @ rot_z(-self.roll) @ rot_y(self.yaw)
        self.rng = np.random.default_rng(seed)
        self.half_timbered = half_timbered
        self.brace_lean = math.radians(brace_lean_deg)
        self.img = np.full((h, w, 3), 232, np.uint8)
        self._sky()
        if half_timbered:
            self._timber_facade(0.0)
        else:
            self._facade(0.0)
        if corner:
            (self._timber_facade if half_timbered else self._facade)(math.pi / 2.4, x0=6.0)
        self._ground()
        if clutter:
            self._clutter(clutter)
        if occluders:
            self._occluders(occluders)
        if texture:
            self._texture()
        if noise:
            n = self.rng.normal(0, noise, self.img.shape)
            self.img = np.clip(self.img.astype(float) + n, 0, 255).astype(np.uint8)

    # -- projection ------------------------------------------------------
    def project(self, P):
        p = self.K @ self.R @ np.asarray(P, dtype=float)
        if p[2] <= 1e-6:
            return None
        return float(p[0] / p[2]), float(p[1] / p[2])

    def seg(self, A, B, colour, thickness=2):
        a, b = self.project(A), self.project(B)
        if a is None or b is None:
            return
        cv2.line(self.img, (int(round(a[0])), int(round(a[1]))),
                 (int(round(b[0])), int(round(b[1]))), colour, thickness, cv2.LINE_AA)

    # -- content ---------------------------------------------------------
    def _sky(self):
        top = np.linspace(190, 235, self.h)[:, None]
        self.img[:] = np.dstack([np.repeat(top, self.w, 1) * 1.02,
                                 np.repeat(top, self.w, 1) * 0.99,
                                 np.repeat(top, self.w, 1) * 0.92]).clip(0, 255).astype(np.uint8)

    def _facade(self, yaw, x0=-6.0, width=12.0, top=-9.0, bottom=3.0, z=16.0):
        """A wall with a grid of windows, placed at an angle ``yaw`` about the
        world vertical so that corner views can be generated."""
        c, s = math.cos(yaw), math.sin(yaw)
        def P(u, v):
            return (x0 + u * c, v, z + u * s)
        wall = np.array([self.project(P(0, top)), self.project(P(width, top)),
                         self.project(P(width, bottom)), self.project(P(0, bottom))])
        if not np.any(wall == None):                      # noqa: E711
            cv2.fillPoly(self.img, [np.round(wall).astype(np.int32)], (176, 168, 158))
        for u in np.linspace(0, width, 9):
            self.seg(P(u, top), P(u, bottom), (86, 82, 78), 3)
        for v in np.linspace(top, bottom, 7):
            self.seg(P(0, v), P(width, v), (104, 100, 96), 2)
        # windows: extra short verticals and horizontals, like real facades
        for u in np.linspace(width / 16, width - width / 16, 8)[::1]:
            for v in np.linspace(top + 1.0, bottom - 1.2, 5):
                self.seg(P(u - 0.35, v), P(u + 0.35, v), (60, 58, 56), 2)
                self.seg(P(u - 0.35, v + 0.9), P(u + 0.35, v + 0.9), (60, 58, 56), 2)
                self.seg(P(u - 0.35, v), P(u - 0.35, v + 0.9), (52, 50, 48), 2)
                self.seg(P(u + 0.35, v), P(u + 0.35, v + 0.9), (52, 50, 48), 2)
        # gable: two strong diagonals, the classic false-vanishing-point trap
        self.seg(P(0, top), P(width / 2, top - 3.2), (48, 46, 44), 4)
        self.seg(P(width / 2, top - 3.2), P(width, top), (48, 46, 44), 4)

    def _timber_facade(self, yaw, x0=-6.0, width=12.0, top=-9.0, bottom=3.0, z=16.0):
        """A half-timbered wall: posts, rails, and a great many diagonal braces.

        This is the adversarial case for a vertical-lines method, and it is
        adversarial in a specific way rather than merely noisy.  A brace leaning
        25-30 deg off vertical sits *inside* the vertical candidate window, and
        the braces come in mirrored pairs at a consistent angle, so they form a
        strong, coherent false vanishing point rather than scattered noise.  A
        detector that merely down-weights outliers is not enough; the angular
        prior and the horizon cross-check have to earn their place here.
        """
        c, s_ = math.cos(yaw), math.sin(yaw)
        def P(u, v):
            return (x0 + u * c, v, z + u * s_)
        wall = [self.project(P(0, top)), self.project(P(width, top)),
                self.project(P(width, bottom)), self.project(P(0, bottom))]
        if all(p is not None for p in wall):
            cv2.fillPoly(self.img, [np.round(np.array(wall)).astype(np.int32)],
                         (206, 208, 212))
        timber = (58, 66, 92)
        posts = np.linspace(0, width, 9)
        rails = np.linspace(top, bottom, 5)
        for u in posts:
            self.seg(P(u, top), P(u, bottom), timber, 4)
        for v in rails:
            self.seg(P(0, v), P(width, v), timber, 4)
        # braces: one per bay, mirrored about the centre of each storey, plus a
        # St Andrew's cross in every other bay
        lean = math.tan(self.brace_lean)
        for storey in range(len(rails) - 1):
            v0, v1 = rails[storey], rails[storey + 1]
            dv = v1 - v0
            for k in range(len(posts) - 1):
                a, b = posts[k], posts[k + 1]
                # NOT clipped to the bay: a brace clipped to a narrow bay ends
                # up near 45 deg, safely outside the vertical window, and the
                # test stops testing anything.  Real "wilder Mann" bracing runs
                # across bays, and a brace at `brace_lean_deg` off vertical is
                # the case that actually lands inside the candidate window.
                run = dv * lean
                if k % 2 == 0:
                    self.seg(P(a, v1), P(min(a + run, width), v0), timber, 3)
                    self.seg(P(b, v1), P(max(b - run, 0.0), v0), timber, 3)
                else:
                    self.seg(P(a, v0), P(min(a + run, width), v1), timber, 3)
                    self.seg(P(b, v0), P(max(b - run, 0.0), v1), timber, 3)
                if k % 3 == 1:                       # Andreaskreuz
                    self.seg(P(a, v0), P(b, v1), timber, 3)
                    self.seg(P(b, v0), P(a, v1), timber, 3)
        # windows sit between the posts
        for k in range(0, len(posts) - 1, 2):
            for storey in range(len(rails) - 1):
                a = posts[k] + 0.25
                b = posts[k + 1] - 0.25
                v0 = rails[storey] + 0.35
                v1 = rails[storey + 1] - 0.35
                if b <= a or v1 <= v0:
                    continue
                for (p, q) in ((P(a, v0), P(b, v0)), (P(a, v1), P(b, v1)),
                               (P(a, v0), P(a, v1)), (P(b, v0), P(b, v1))):
                    self.seg(p, q, (70, 74, 84), 2)
        # a steep gable with rafters -- more long diagonals, near the top
        apex = top - 4.5
        self.seg(P(0, top), P(width / 2, apex), timber, 5)
        self.seg(P(width / 2, apex), P(width, top), timber, 5)
        for t in np.linspace(0.18, 0.82, 6):
            self.seg(P(width * t, top), P(width / 2, apex), timber, 2)

    def _ground(self):
        for z in np.linspace(9.0, 26.0, 7):
            self.seg((-14, 3.0, z), (14, 3.0, z), (150, 148, 145), 2)
        for x in np.linspace(-14, 14, 9):
            self.seg((x, 3.0, 9.0), (x, 3.0, 26.0), (150, 148, 145), 2)

    def _clutter(self, n):
        for _ in range(n):
            p = np.array([self.rng.uniform(-12, 12), self.rng.uniform(-8, 3),
                          self.rng.uniform(8, 24)])
            d = self.rng.normal(0, 0.8, 3)
            self.seg(p, p + d, (118, 136, 112), 2)

    def _occluders(self, n):
        """Trees in front of the building.

        This models the thing that actually breaks horizontal evidence on real
        photographs: an eaves line or a string course runs the width of the
        facade, a tree stands in front of it, and the detector returns two short
        fragments instead of one long line.  Short fragments locate a vanishing
        point badly, because the baseline is what fixes it.
        """
        for i in range(n):
            u = (i + 0.5) / n
            cx = int(self.w * (0.12 + 0.76 * u) + self.rng.normal(0, self.w * 0.02))
            cy = int(self.h * (0.55 + self.rng.normal(0, 0.05)))
            rx = int(self.w * self.rng.uniform(0.035, 0.065))
            ry = int(self.h * self.rng.uniform(0.28, 0.42))
            cv2.ellipse(self.img, (cx, cy), (rx, ry), 0, 0, 360, (74, 96, 66), -1)
            for _ in range(14):          # a few branches poking out
                a = self.rng.uniform(0, 2 * np.pi)
                r = self.rng.uniform(0.6, 1.35)
                cv2.line(self.img, (cx, cy),
                         (int(cx + rx * r * np.cos(a) * 1.6), int(cy + ry * r * np.sin(a))),
                         (66, 84, 60), 2, cv2.LINE_AA)

    def _texture(self):
        n = self.rng.normal(0, 5.0, self.img.shape[:2])
        self.img = np.clip(self.img.astype(float) + n[..., None], 0, 255).astype(np.uint8)

    # -- ground truth ----------------------------------------------------
    @property
    def up_camera(self):
        """World up expressed in camera coordinates -- what the estimator must find."""
        u = self.R @ WORLD_UP
        return u / np.linalg.norm(u)

    @property
    def vertical_vp(self):
        return self.K @ self.up_camera

    def true_roll_pitch(self):
        u = self.up_camera
        r = math.hypot(u[0], u[1])
        return math.atan2(u[0], -u[1]), math.atan2(u[2], r)

    def world_vertical_segments(self, n=7):
        """Image endpoints of lines that are vertical in the world -- used to
        measure the *effect* of a correction rather than its parameters."""
        out = []
        for x in np.linspace(-5, 5, n):
            a = self.project((x, -8.0, 16.0))
            b = self.project((x, 2.5, 16.0))
            if a and b:
                out.append([a[0], a[1], b[0], b[1]])
        return np.asarray(out)


def flat_image(w=1200, h=800, seed=0):
    """Texture with no straight lines at all: must be skipped, never corrected."""
    rng = np.random.default_rng(seed)
    img = rng.normal(140, 40, (h, w, 3))
    img = cv2.GaussianBlur(img, (0, 0), 6.0)
    img += rng.normal(0, 12, img.shape)
    return np.clip(img, 0, 255).astype(np.uint8)
