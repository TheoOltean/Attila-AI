"""ml/host/masks.py -- legal(observation) -> masks.

ONE pure, versioned function shared VERBATIM between training and
inference (ML_DESIGN masking law): False forces a head probability to
exactly 0. It lives host-side, next to the model, never in game Lua.

Universal rule: routing / shattered / nonexistent / ship units fail
every verb except no_change (verb_mask col 14 always True).

Mask table (shapes in spec.MASK_SHAPES, conditions per ML_DESIGN; feat
columns read via spec.FEAT, never hand-typed offsets):
  verb_mask            per unit x verb legality (verb table rules)
  enemy_target_mask    exists AND visible AND alive [G per-unit cond. --
                       if that lands, shape becomes (MAX_FRIENDLY,
                       MAX_ENEMY); decide before implementing legal()]
  struct_target_mask   per-unit (torches -> gates only, etc.)
  vehicle_target_mask  exists AND uncrewed AND our side may crew
  loc_coarse_mask      on-map AND any-walkable [G strictness]
  loc_fine_mask        HIRES impassable channel under the crop (per crop)
  shot_type_mask       roster (<=4) AND ammo > 0 AND != current
  stance_mask          joint (stance, on/off): roster AND would change
  formation_mask       owned
  ability_mask         owned AND ready

Self-test law: during extraction, any pro action this function forbids
is a MASK BUG -- log and count; that rate is this module's test suite.
"""

from __future__ import annotations

MASKS_VERSION = "0.1.0"


def legal(obs) -> dict:
    """Observation -> {name: bool ndarray} per spec.MASK_SHAPES."""
    raise NotImplementedError("todo: the one pure mask function (versioned)")


def fine_mask_for_crop(obs, coarse_cell: int):
    """loc_fine_mask [4096] for one coarse cell (stage-B decode support)."""
    raise NotImplementedError("todo: HIRES impassable crop under the cell")
