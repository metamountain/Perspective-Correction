---
name: camera-marker-direct-correction
description: How to compute a full perspective correction (roll, pitch, yaw) directly from hand-drawn marker lines without a vanishing-point model — the "diamond → rectangle" pattern.
source: auto-skill
extracted_at: '2026-09-17T12:13:46.821Z'
---

# Direct camera correction from hand-drawn markers

## When this applies

The user draws one line along a known-vertical edge and one line along a known-horizontal edge of a facade. The goal is to rotate the camera so those edges become truly vertical and horizontal in the output — **without** fitting a vanishing point or running the full estimator. The markers are the ground truth; no model, no RANSAC, no support threshold.

This is the "diamond → rectangle" case: a building photographed at an angle looks like a tilted diamond; two marks (one on each axis) pin down the rotation that makes it a rectangle again.

## The math

### Roll + pitch from the vertical marker

A world-vertical edge projects to a line in the image. Its direction encodes how the camera is tilted relative to that edge:

```python
def _marker_roll_pitch(f):
    """Roll and pitch from the first vertical control line's bearing."""
    seg = control_lines[0]          # (x0, y0, x1, y1) in image px
    dx, dy = x1 - x0, y1 - y0
    n = hypot(dx, dy)

    # Bearing from principal point to the line's midpoint (camera coords).
    cx, cy = w / 2.0, h / 2.0
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    b = normalize([(mx - cx) / f, (my - cy) / f, 1.0])

    # The line's in-plane image direction.
    d_img = [dx / n, dy / n, 0.0]

    # World vertical: cross of bearing and in-plane direction gives the
    # up-vector that contains both the edge direction and the optical axis.
    u = normalize(cross(b, d_img))
    if u[1] > 0:
        u = -u                       # ensure "up" in image (negative y)

    roll, pitch = roll_pitch_from_up(u)   # existing geometry helper
    return roll, pitch
```

**Why cross product:** The bearing `b` points from the camera to the edge's midpoint. The in-plane direction `d_img` says which way the edge runs on the sensor. Their cross gives a vector perpendicular to both — i.e., the component of the world-vertical that is *not* along the edge and *not* along the optical axis. That is exactly what `roll_pitch_from_up` expects: a unit vector in camera coordinates pointing "up" in the world.

### Yaw from the horizontal marker

After roll and pitch are known, rotate the horizontal line's bearing into the de-tilted frame and read off the remaining rotation about the vertical:

```python
def _marker_yaw(roll, pitch, f):
    seg = control_hlines[0]         # (x0, y0, x1, y1) in image px
    cx, cy = w / 2.0, h / 2.0
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    b = [(mx - cx) / f, (my - cy) / f, 1.0]

    # Rotate bearing into the frame where roll and pitch are removed.
    wv = rot_x(pitch) @ rot_z(-roll) @ b

    # The yaw that makes this vector lie in the x-z plane (y-component → 0).
    yaw = atan2(-wv[2], wv[0])
    return yaw
```

For **3+ horizontal lines**, use least-squares: stack all `wv_i` and compute `yaw = atan2(-sum(wv[:,2]), sum(wv[:,0]))`. This minimises the total residual tilt across all lines.

### Applying the correction

The standard `correction_rotation(roll, pitch, yaw)` from `geometry.py` composes the three rotations. No VP, no model — just the three angles derived above:

```python
H = K @ correction_rotation(roll, pitch, yaw) @ inv(K)
```

## Wiring into the review session

### State flags

```python
# On ReviewSession (review.py):
self._hmarker_active = False   # True when marker mode is in force
self._hmarker_yaw = None       # radians, set by the GUI toggle
```

### `current_angles()` — marker branch first

```python
def current_angles(self):
    if getattr(self, "_hmarker_active", False):
        f = (self.model.f if self.model and self.model.f
             else default_focal)
        roll, pitch = self._marker_roll_pitch(f)
        return roll, pitch, f, False
    # ... existing MANUAL / AUTO branches unchanged
```

The marker branch is **before** the MANUAL check because marker mode is a kind of manual correction that computes its own angles rather than reading sliders.

### `current_yaw()` — marker override

```python
def current_yaw(self):
    if getattr(self, "_hmarker_active", False):
        hm = getattr(self, "_hmarker_yaw", None)
        return hm if hm is not None else 0.0
    # ... existing MANUAL / AUTO branches unchanged
```

### GUI toggle (`_on_hmarker_toggle`)

Requirements before activation:
- **At least one vertical control line** (`control_lines`)
- **At least one horizontal control line** (`control_hlines`)

If either is missing, show a status message naming what's needed and refuse to activate.

On activation:
1. Turn off "horizontal auto" (mutually exclusive modes).
2. Compute `roll, pitch` via `_marker_roll_pitch`.
3. Compute `yaw` from the H-line bearing(s) using those roll/pitch values.
4. Set `_hmarker_yaw`, `_hmarker_active = True`.
5. Update the yaw slider to show the computed value (read-only display; the user can fine-tune).
6. Schedule redraw.

On deactivation:
1. Clear `_hmarker_yaw = None`, `_hmarker_active = False`.
2. Call `refit()` so the model-based path resumes.
3. Re-enable the yaw slider if "horizontal auto" is off.

## Mutual exclusion with auto mode

- **Manual → Auto:** turning on "horizontal auto" turns off H-Marker (clears `_hmarker_active`).
- **Auto → Manual:** turning on H-Marker turns off "horizontal auto" (sets `correct_horizontal=False`).
- **Markers in auto:** drawing 2+ horizontal control lines still sets `correct_horizontal=True` in `refit()` — this is the *auto* path and is independent of the manual marker toggle. The user said: "die implementation der marker in auto kann so bleiben."

## Pitfalls

- **Focal length source.** In marker mode there is no model, so `f` must come from somewhere. Use the model's focal if it exists (the estimator ran on load), otherwise fall back to `default_focal_35mm`. Do NOT try to estimate focal from the markers — two lines give rotation, not scale.

- **Line direction sign.** The cross product `cross(b, d_img)` can point down instead of up depending on which endpoint the user drew first. Always normalise: if `u[1] > 0`, flip `u`. Without this, roll and pitch are negated and the correction goes the wrong way.

- **Yaw fold.** `atan2` returns (-π, π]. A yaw near ±π means the camera is upside-down relative to the marker — physically impossible for a facade photo, but numerically possible if the user drew the line in the "wrong" direction. Fold into [-π/2, π/2]: `yaw = (yaw + π/2) % π - π/2`.

- **Do NOT call `refit()` inside the marker toggle.** The marker mode bypasses the model entirely. Calling `refit()` would re-run the estimator and potentially overwrite the settings with model-based values. Only call `refit()` when *deactivating* marker mode (to restore the model path).

- **Slider state.** When marker mode is active, the yaw slider should be **enabled** (so the user can see and fine-tune the value) even if "horizontal auto" is off. When marker mode is deactivated and "horizontal auto" is still off, the slider goes back to disabled.

- **`_marker_roll_pitch` uses `control_lines[0]`.** If the user draws multiple vertical lines, only the first is used for roll/pitch. This is intentional: one clean edge is better than an average of noisy ones. The H-lines get the least-squares treatment because horizontal evidence is typically weaker (more lines, less confidence each).

## Testing

The synthetic test pattern:
1. Create a white rectangle on a black background (the "true" facade).
2. Apply a known rotation (e.g., roll=8°, pitch=5°, yaw=12°) via `cv2.warpPerspective`.
3. Draw a V-line along the left edge and an H-line along the top edge of the rotated rectangle.
4. Call `_marker_roll_pitch` and `_marker_yaw`.
5. Assert: recovered angles are within 0.5° of the ground truth.

This is the "diamond → rectangle" test: the input looks like a tilted diamond, the output must be an axis-aligned rectangle.
