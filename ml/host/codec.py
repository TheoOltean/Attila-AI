"""ml/host/codec.py -- frames <-> arrays (host side).

The mirror of ml/game/ipc/codec.lua. Wire form is defined there; this
module turns an obs JSON payload into the model-facing Observation
(numpy, spec.SHAPES) and an action list into act-frame text. The
0-based wire convention is native here; the game codec owns the +-1.

Feature FINISHING also lives here, per the read-layer split: the game
ships facts it alone can see; the host builds what is derivable --
- type keys -> vocab ids (the id table is a training asset, host-side);
- terrain rasters from per-map assets keyed by hello.map, ch 3 patched
  from aux.raster_events, ch 10 painted from aux.centroid (Gaussian,
  sigma 40 m [G]);
- enemy last-seen aging beyond what the game tracks, if any.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    """One decision tick, spec.SHAPES-exact (numpy arrays)."""

    battle_id: str
    seq: int
    tick: int
    phase: str
    arrays: dict[str, Any]  # name -> np.ndarray per spec.SHAPES
    aux: dict[str, Any] = field(default_factory=dict)


@dataclass
class Action:
    """One per-unit decision in wire form (only the verb's args set)."""

    u: int          # friendly slot, 0-based
    verb: str       # spec.VERBS key
    x: float | None = None
    y: float | None = None
    deg: float | None = None
    width_m: float | None = None
    run: bool | None = None
    enemy: int | None = None
    struct: int | None = None
    vehicle: int | None = None
    shot: int | None = None
    stance: int | None = None
    on: bool | None = None
    formation: int | None = None
    ability: int | None = None


class MapAssets:
    """Per-map static rasters (terrain / terrain_hires), keyed by map key.

    Built offline (capture tooling TBD); until assets exist, decode_obs
    zero-fills the raster arrays so the pipeline runs end to end.
    """

    def load(self, map_key: str):
        raise NotImplementedError("todo: asset file layout + loader")


def decode_obs(payload: dict, hello: dict, assets: MapAssets | None) -> Observation:
    """Obs JSON payload -> Observation (validate, arrayify, finish features)."""
    raise NotImplementedError("todo: shape-check vs spec, type-key -> id map, raster finishing")


def encode_act(seq: int, battle_id: str, for_tick: int, actions: list[Action]) -> str:
    """Action list -> act-frame text (grammar in game/ipc/codec.lua header).

    Line shapes derive from spec.VERBS + spec.ARG_TOKENS -- never a
    hand-maintained per-verb format list. Booleans (run/on) MUST emit as
    0|1 tokens (int(value)) -- Lua tonumber() rejects 'True'/'False'.
    """
    raise NotImplementedError("todo: frame/u/end lines per ARG_TOKENS")


# --- the two-way bin/continuous converter (ML_DESIGN: "the same converter
# runs both directions" -- model output -> order AND pro replay -> training
# targets; the future extraction pipeline imports THESE, never its own) ---

def facing_bin_to_deg(facing_bin: int) -> float:
    """deg = facing_bin * (360 / FACING_BINS)."""
    raise NotImplementedError("todo")


def deg_to_facing_bin(deg: float) -> int:
    raise NotImplementedError("todo")


def width_bin_to_m(width_bin: int, unit_min_m: float, unit_max_m: float) -> float:
    """width_m = unit_min + width_bin/(WIDTH_BINS-1) * (unit_max - unit_min).

    Per-unit min/max frontage source is OPEN (DB spacing fields? engine
    read?) -- ML_DESIGN iteration queue; decide before implementing.
    """
    raise NotImplementedError("todo")


def width_m_to_bin(width_m: float, unit_min_m: float, unit_max_m: float) -> int:
    raise NotImplementedError("todo")
