"""ml/host/spec.py -- the IO shape contract (host-side mirror).

Single source: reference/ML_DESIGN.md; game-side twin: ml/game/spec.lua.
The hello frame carries the game's shape table; validate_hello() rejects a
drifted mirror at handshake instead of letting it silently mis-decode.
Data only -- no IO, no numpy.
"""

SPEC_VERSION = "0.1.0"  # bump with game/spec.lua, always together

DECISION_TICK_S = 1.0

MAX_FRIENDLY = 40
MAX_ENEMY = 40
MAX_STRUCT = 512  # [G] measure via bld scans
MAX_VEH = 32      # [G]

GLOBAL_DIM = 40
UNIT_FEAT_DIM = 224
STRUCT_DIM = 16
VEH_DIM = 12
TERRAIN_CH = 11
GRID = 128
TERRAIN_HIRES_CH = 6
HIRES = 2048
COARSE_LOC = 32   # 32x32 = 1024 coarse cells
LOC_PATCH = 64    # 64x64 fine patch per coarse cell

FACING_BINS = 24
WIDTH_BINS = 16
SHOT_VOCAB = 95
ABILITY_VOCAB = 48
STANCE_VOCAB = 11
FORMATION_VOCAB = 10
TYPE_VOCAB = 1146  # 1145 land unit types + pad id 0 (ids 1..1145)

# Observation array shapes (the INPUT block of ML_DESIGN).
SHAPES = {
    "global": (GLOBAL_DIM,),
    "friendly_type": (MAX_FRIENDLY,),
    "friendly_feat": (MAX_FRIENDLY, UNIT_FEAT_DIM),
    "enemy_type": (MAX_ENEMY,),
    "enemy_feat": (MAX_ENEMY, UNIT_FEAT_DIM),
    "structures": (MAX_STRUCT, STRUCT_DIM),
    "vehicles": (MAX_VEH, VEH_DIM),
    "terrain": (TERRAIN_CH, GRID, GRID),
    "terrain_hires": (TERRAIN_HIRES_CH, HIRES, HIRES),
}

# Unit-token feature blocks: [lo, hi) into feat[224] (mirrors game
# M.FEAT; masks.py and codec.py read feat columns through THIS table,
# never hand-typed offsets; validated via the hello handshake).
FEAT = {
    "STATIC": (0, 44),
    "LIVE": (44, 70),
    "ORDER": (70, 84),
    "STANCE": (84, 95),
    "FORMATION": (95, 115),   # current [95,105) / owned [105,115)
    "ABILITY": (115, 211),    # owned [115,163) / ready [163,211)
    "META": (211, 224),
}

# Vocab orders -- index order is load-bearing, mirrors game/spec.lua.
STANCES = [
    "fire_at_will", "melee_mode", "skirmish", "defend", "formed_attack",
    "loose_spacing", "dismount", "unlimber", "release_animals",
    "drop_siege_equipment", "abandon_artillery_engines",
]

FORMATIONS = [
    "form_diamond", "form_flying_wedge", "form_hoplite_phalanx",
    "form_pike_square", "form_pike_wall", "form_shield_screen",
    "form_shield_wall", "form_testudo", "form_testudo_defensive",
    "form_wedge",
]

UNIT_CLASSES = [
    "inf_mel", "inf_spr", "inf_pik", "inf_mis", "cav_mel",
    "cav_mis", "cav_shk", "art_fld", "art_siege", "elph",
]

STRUCT_CLASSES = ["wall_segment", "gate", "tower", "garrisonable", "barricade", "other"]
VEH_TYPES = ["ram", "siege_tower", "ladder", "other"]

# Raster channel orders (load-bearing: codec patches ch by index).
TERRAIN_CHANNELS = [
    "elevation", "slope", "forest_cover", "water", "ford_shallow",
    "road", "walkable", "vp_region", "wall_footprint", "open_passage",
    "enemy_centroid",
]

TERRAIN_HIRES_CHANNELS = [
    "impassable", "building_footprint", "wall_blocking",
    "open_passage",  # the ONE dynamic channel (breach/gate events)
    "water", "prop_obstacles",
]

# The 15 verbs, head-index order; args = arg heads per verb.
VERBS = [
    ("move", ("loc", "run")),
    ("move_formation", ("loc", "facing", "width", "run")),
    ("halt", ()),
    ("attack_unit", ("enemy", "run")),
    ("attack_ground", ("loc",)),
    ("attack_building", ("struct",)),
    ("change_shot_type", ("shot",)),
    ("set_stance", ("stance",)),          # joint (stance, on/off)
    ("set_formation", ("formation",)),
    ("use_unit_ability", ("ability",)),
    ("use_general_ability", ("ability",)),
    ("enter_structure", ("struct",)),
    ("leave_building", ()),
    ("occupy_vehicle", ("vehicle",)),
    ("no_change", ()),
]
VERB_INDEX = {key: i for i, (key, _args) in enumerate(VERBS)}

# Wire tokens per arg head -- the act-frame line grammar, mirrored from
# game/spec.lua M.ARG_TOKENS. All wire indices 0-based; x/y/width_m meters,
# deg degrees, run/on 0|1.
ARG_TOKENS = {
    "loc": ("x", "y"),
    "facing": ("deg",),
    "width": ("width_m",),
    "run": ("run",),
    "enemy": ("enemy",),
    "struct": ("struct",),
    "vehicle": ("vehicle",),
    "shot": ("shot",),
    "stance": ("stance", "on"),
    "formation": ("formation",),
    "ability": ("ability",),
}

# Mask shapes (built host-side by masks.legal -- see that module).
# Dims derive from constants above -- never literals (v1's hand-synced
# duplicates are the drift class this file exists to kill).
# NOTE enemy_target_mask: spec marks per-unit conditioning as an open
# [G]; if it lands, this becomes (MAX_FRIENDLY, MAX_ENEMY) + a
# SPEC_VERSION bump -- decide before masks.legal is written.
MASK_SHAPES = {
    "verb_mask": (MAX_FRIENDLY, len(VERBS)),
    "enemy_target_mask": (MAX_ENEMY,),
    "struct_target_mask": (MAX_FRIENDLY, MAX_STRUCT),
    "vehicle_target_mask": (MAX_VEH,),
    "loc_coarse_mask": (COARSE_LOC * COARSE_LOC,),
    "loc_fine_mask": (LOC_PATCH * LOC_PATCH,),  # per crop
    "shot_type_mask": (MAX_FRIENDLY, SHOT_VOCAB),
    "stance_mask": (MAX_FRIENDLY, 2 * STANCE_VOCAB),
    "formation_mask": (MAX_FRIENDLY, FORMATION_VOCAB),
    "ability_mask": (MAX_FRIENDLY, ABILITY_VOCAB),
}


def shapes_for_hello() -> dict:
    """The shape table this mirror expects the game's hello to carry."""
    return {
        "spec_version": SPEC_VERSION,
        "global": [GLOBAL_DIM],
        "friendly_type": [MAX_FRIENDLY],
        "friendly_feat": [MAX_FRIENDLY, UNIT_FEAT_DIM],
        "enemy_type": [MAX_ENEMY],
        "enemy_feat": [MAX_ENEMY, UNIT_FEAT_DIM],
        "structures": [MAX_STRUCT, STRUCT_DIM],
        "vehicles": [MAX_VEH, VEH_DIM],
        "terrain": [TERRAIN_CH, GRID, GRID],
        "terrain_hires": [TERRAIN_HIRES_CH, HIRES, HIRES],
        "verbs": len(VERBS),
        "feat_blocks": {k: list(v) for k, v in FEAT.items()},
        "terrain_channels": TERRAIN_CHANNELS,
        "terrain_hires_channels": TERRAIN_HIRES_CHANNELS,
    }


def validate_hello(hello: dict) -> None:
    """Raise ValueError naming every mismatched field; silence = compatible."""
    raise NotImplementedError("todo: field-by-field compare of hello['shapes'] vs shapes_for_hello()")
