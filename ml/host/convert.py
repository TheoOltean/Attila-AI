"""ml/host/convert.py -- the two-way bin/continuous converters (contract level).

ML_DESIGN law: "the same converter runs both directions" -- pro replay ->
training targets AND model output -> order. This is that one module; the
extraction pipeline, net/ (training + decode) and, when wiring lands,
codec.py's converter stubs all route HERE, never their own copy. Sits at
spec level (pure scalar math, spec-only import, no numpy) so every layer
may import it downward.

Raster orientation convention (defined HERE, once): row index <-> y,
column index <-> x, both increasing with the normalized coordinate.
Capture tooling for the terrain rasters must emit this orientation.

Location geometry: HIRES (2048^2) pixels tile the map; a coarse cell is
one 64x64 pixel patch (32x32 cells). A continuous position is (cell,
pixel-in-crop, sub-pixel offset); the offset is the model's tanh output
in [-1, 1], worth half a pixel of displacement from the pixel center.
"""

from __future__ import annotations

import spec

DEG_PER_BIN = 360.0 / spec.FACING_BINS
CELLS = spec.COARSE_LOC          # 32 -> 1024 coarse cells
PATCH = spec.LOC_PATCH           # 64 -> 4096 fine pixels per cell
HIRES = spec.HIRES               # 2048 = CELLS * PATCH


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


# --- facing -----------------------------------------------------------------

def facing_bin_to_deg(facing_bin: int) -> float:
    """deg = bin * 15 (bin 0 = 0 deg, increasing with deg)."""
    return facing_bin * DEG_PER_BIN


def deg_to_facing_bin(deg: float) -> int:
    return int(round((deg % 360.0) / DEG_PER_BIN)) % spec.FACING_BINS


# --- width ------------------------------------------------------------------
# Per-unit frontage range source is OPEN (ML_DESIGN iteration queue); both
# directions take it as arguments so this module never grows a DB dependency.

def width_bin_to_m(width_bin: int, unit_min_m: float, unit_max_m: float) -> float:
    """width_m = unit_min + bin/(WIDTH_BINS-1) * (unit_max - unit_min)."""
    return unit_min_m + width_bin / (spec.WIDTH_BINS - 1) * (unit_max_m - unit_min_m)


def width_m_to_bin(width_m: float, unit_min_m: float, unit_max_m: float) -> int:
    span = unit_max_m - unit_min_m
    if span <= 0.0:
        return 0
    frac = _clamp((width_m - unit_min_m) / span, 0.0, 1.0)
    return int(round(frac * (spec.WIDTH_BINS - 1)))


# --- location ---------------------------------------------------------------

def cell_crop_origin(cell: int) -> tuple[int, int]:
    """Coarse cell -> (row0, col0) of its 64x64 patch in HIRES pixels."""
    return (cell // CELLS) * PATCH, (cell % CELLS) * PATCH


def _axis_to_pixel(v: float, map_dim: float) -> tuple[int, float]:
    """Meters on one axis -> (hires pixel index, offset in [-1, 1])."""
    u = _clamp(v / map_dim, 0.0, 1.0) * HIRES  # continuous pixel coordinate
    i = min(int(u), HIRES - 1)
    off = _clamp(((u - i) - 0.5) / 0.5, -1.0, 1.0)
    return i, off


def xy_to_loc(x: float, y: float, map_w: float, map_h: float,
              ) -> tuple[int, int, tuple[float, float]]:
    """Meters -> (coarse cell [0,1024), fine pixel [0,4096), (dx, dy) in [-1,1]).

    The training-target direction: the pro's continuous x, y become the
    stage-A cell label, the stage-B pixel label and the offset regression
    target, exactly invertible by loc_to_xy up to sub-offset clamping.
    """
    col, dx = _axis_to_pixel(x, map_w)
    row, dy = _axis_to_pixel(y, map_h)
    cell = (row // PATCH) * CELLS + (col // PATCH)
    pixel = (row % PATCH) * PATCH + (col % PATCH)
    return cell, pixel, (dx, dy)


def loc_to_xy(cell: int, pixel: int, offset: tuple[float, float],
              map_w: float, map_h: float) -> tuple[float, float]:
    """(cell, pixel, tanh offset) -> continuous meters (the order direction)."""
    row0, col0 = cell_crop_origin(cell)
    row = row0 + pixel // PATCH
    col = col0 + pixel % PATCH
    dx, dy = offset
    x = (col + 0.5 + 0.5 * _clamp(dx, -1.0, 1.0)) * (map_w / HIRES)
    y = (row + 0.5 + 0.5 * _clamp(dy, -1.0, 1.0)) * (map_h / HIRES)
    return x, y
