-------------------------------------------------------------------------
--	ml/game/spec.lua -- the IO shape contract (game-side copy).
--
--	Single source: reference/ML_DESIGN.md. This module is DATA ONLY --
--	constants, layouts, vocab orders. host/spec.py mirrors it; the hello
--	frame carries these shapes so the host can reject a drifted mirror
--	at handshake instead of silently mis-decoding.
--	No engine calls, no io, safe to require from any context.
-------------------------------------------------------------------------

local M = {};

-- Bump on ANY change to shapes/orders below (mirrors host/spec.py).
-- 0.2.0: battle-type one-hot cut 6 -> 3 (Theo 2026-08-09); global[40]
-- compacted from idx 6 down, so weather/counts/VP/spare all moved.
-- 0.3.0: clock semantics (layout unchanged) -- global 0/1 normalize by the
-- BATTLE'S OWN time limit (remaining_conflict_time at conflict start), not
-- a flat 1800; NORM.TIME_S is now only the fallback for unlimited-time
-- battles. Elapsed excludes the deployment phase.
-- 0.4.0: weather block [6,10) -> [6,11). The engine enum is None/Rain/Snow/
-- Dust with a separate severity 0..2 (RE 2026-08-09: weather-definition
-- +0x1c/+0x20) -- there is no fog, so that slot is now DUST, and severity
-- joins as a scalar. global[40] re-compacted from idx 10 down.
M.SPEC_VERSION = "0.4.0";

-- Decision cadence: one observation out + one action frame in per second.
M.DECISION_TICK_S = 1.0;

-- Serialization capacities (slot maxima; exists=0 slots are padding).
M.MAX_FRIENDLY = 40;
M.MAX_ENEMY = 40;
M.MAX_STRUCT = 512;			-- [G] measure via bld scans
M.MAX_VEH = 32;				-- [G]

-- Array shapes.
M.GLOBAL_DIM = 40;
M.UNIT_FEAT_DIM = 224;
M.STRUCT_DIM = 16;
M.VEH_DIM = 12;
M.TERRAIN_CH = 11;
M.GRID = 128;				-- strategic raster (terrain)
M.TERRAIN_HIRES_CH = 6;
M.HIRES = 2048;				-- collision raster (host-side asset, crops only)
M.COARSE_LOC = 32;			-- 32x32 = 1024 coarse location cells
M.LOC_PATCH = 64;			-- 64x64 fine patch under one coarse cell

-- Head/vocab sizes.
M.FACING_BINS = 24;			-- 15 deg
M.WIDTH_BINS = 16;
M.SHOT_VOCAB = 95;			-- DB projectile keys
M.ABILITY_VOCAB = 48;		-- cap; exact key list pending (DB join)
M.STANCE_VOCAB = 11;
M.FORMATION_VOCAB = 10;
M.TYPE_VOCAB = 1146;		-- 1145 land unit types + pad id 0 (ids 1..1145)

-- Unit-token feature blocks: [lo, hi) into feat[224].
M.FEAT = {
	STATIC = { 0, 44 };		-- DB card, fixed all battle
	LIVE = { 44, 70 };		-- position/facing/men/ammo/morale/fatigue/flags
	ORDER = { 70, 84 };		-- current order one-hot + dest/target offsets
	STANCE = { 84, 95 };	-- 11 stance bits (order = M.STANCES)
	FORMATION = { 95, 115 };	-- current one-hot [95,105) + owned multi-hot [105,115)
	ABILITY = { 115, 211 };	-- owned [115,163) + ready-now [163,211)
	META = { 211, 224 };	-- side/army/reinforcement/arrived + spare
};

-- global[40] section boundaries ([lo, hi); field-by-field layout in ML_DESIGN.md):
--	0 time elapsed - 1 time remaining (both / the battle's own time limit;
--	  NORM.TIME_S only when the battle is unlimited) - 2 we are attacker
--	[3,6) battle-type one-hot - [6,10) weather one-hot - 10 weather severity
--	11/12 men alive own/enemy (/initial) - 13/14 men initial (/6400)
--	15/16 units alive own/enemy (/40) - [17,32) victory points 3x5
--	[32,40) spare
M.GLOBAL = {
	BATTLE_TYPE = { 3, 6 };
	WEATHER = { 6, 11 };	-- [6,10) one-hot + 10 severity (/2)
	WEATHER_ONEHOT = { 6, 10 };
	WEATHER_SEVERITY = 10;
	VP = { 17, 32 };		-- 3 slots x (exists, owner ours/theirs/neutral, progress)
	SPARE = { 32, 40 };
};

-- Vocab orders. Index order is LOAD-BEARING (= one-hot / multi-hot bit order
-- and head output order); never reorder without a SPEC_VERSION bump.

-- Battle-type one-hot order (global[3..5]). All three are structure facts:
-- settlement-vs-field and walled-vs-unwalled both fall out of the structures
-- scan, so they unlock together, from one source.
M.BATTLE_TYPES = { "unwalled_settlement", "walled_settlement", "field_battle" };

-- Weather one-hot order (global[6..9]), then severity as a scalar at 10.
-- These are the ENGINE's categories, not a guess: the weather-definition
-- object carries type @+0x1c against the string list "None,Rain,Snow,Dust"
-- and severity @+0x20 registered over 0..2 (RE 2026-08-09). "clear" is the
-- engine's None. There is no fog category in Attila.
M.WEATHER_TYPES = { "clear", "rain", "snow", "dust" };

M.STANCES = {
	"fire_at_will", "melee_mode", "skirmish", "defend", "formed_attack",
	"loose_spacing", "dismount", "unlimber", "release_animals",
	"drop_siege_equipment", "abandon_artillery_engines",
};

M.FORMATIONS = {
	"form_diamond", "form_flying_wedge", "form_hoplite_phalanx",
	"form_pike_square", "form_pike_wall", "form_shield_screen",
	"form_shield_wall", "form_testudo", "form_testudo_defensive",
	"form_wedge",
};

M.UNIT_CLASSES = {
	"inf_mel", "inf_spr", "inf_pik", "inf_mis", "cav_mel",
	"cav_mis", "cav_shk", "art_fld", "art_siege", "elph",
};

-- Structure class one-hot order (structures[i][0..5], [G] taxonomy).
M.STRUCT_CLASSES = {
	"wall_segment", "gate", "tower", "garrisonable", "barricade", "other",
};

-- Vehicle type one-hot order (vehicles[i][0..3], [G] taxonomy).
M.VEH_TYPES = { "ram", "siege_tower", "ladder", "other" };

-- Raster channel orders (load-bearing: codecs/assets patch and paint by
-- index; drift must be caught at handshake, so these ride the hello).
M.TERRAIN_CHANNELS = {
	"elevation", "slope", "forest_cover", "water", "ford_shallow",
	"road", "walkable", "vp_region", "wall_footprint", "open_passage",
	"enemy_centroid",
};

M.TERRAIN_HIRES_CHANNELS = {
	"impassable", "building_footprint", "wall_blocking",
	"open_passage",		-- the ONE dynamic channel (breach/gate events)
	"water", "prop_obstacles",
};

-- The 15 verbs, index order = verb head output order. args names the arg
-- heads each verb decodes (the write surface consumes exactly these fields
-- from an action record; masks.py gates them host-side).
M.VERBS = {
	{ key = "move",					args = { "loc", "run" } },
	{ key = "move_formation",		args = { "loc", "facing", "width", "run" } },
	{ key = "halt",					args = {} },
	{ key = "attack_unit",			args = { "enemy", "run" } },
	{ key = "attack_ground",		args = { "loc" } },
	{ key = "attack_building",		args = { "struct" } },
	{ key = "change_shot_type",		args = { "shot" } },
	{ key = "set_stance",			args = { "stance" } },	-- joint (stance, on/off)
	{ key = "set_formation",		args = { "formation" } },
	{ key = "use_unit_ability",		args = { "ability" } },
	{ key = "use_general_ability",	args = { "ability" } },
	{ key = "enter_structure",		args = { "struct" } },	-- climb vs defend by struct class
	{ key = "leave_building",		args = {} },
	{ key = "occupy_vehicle",		args = { "vehicle" } },
	{ key = "no_change",			args = {} },
};

-- verb key -> 0-based head index.
M.VERB_INDEX = {};
for i, v in ipairs(M.VERBS) do
	M.VERB_INDEX[v.key] = i - 1;
end;

-- Wire tokens per arg head (act-frame line grammar; order is the token order
-- after "u <slot> <verb>"). Mirrored in host/spec.py -- the two codecs derive
-- their per-verb line shapes from this ONE table, never hand-synced lists
-- (v1 lesson: Python's verb whitelist drifted from Lua's verb tables).
-- All wire indices (slot, enemy, struct, vehicle, shot, stance, formation,
-- ability) are 0-BASED; x/y/width_m are meters; deg degrees; run/on are 0|1.
M.ARG_TOKENS = {
	loc = { "x", "y" };
	facing = { "deg" };
	width = { "width_m" };
	run = { "run" };
	enemy = { "enemy" };
	struct = { "struct" };
	vehicle = { "vehicle" };
	shot = { "shot" };
	stance = { "stance", "on" };
	formation = { "formation" };
	ability = { "ability" };
};

-- Normalization divisors (decided or DB-measured maxima; [G] = still a guess).
-- Positions/offsets divide by MAP_W/MAP_H (per-battle, from hello.map, NOT here).
-- Elevation = (z - map_min_z) / ELEV_M.
M.NORM = {
	-- global
	TIME_S = 1800,			-- [G] FALLBACK ONLY: used when a battle has no
							-- time limit. Limited battles normalize the clock
							-- by their own limit (see read/global.lua).
	MEN_TOTAL = 6400,		-- 40 units x 160 DB-max men
	WEATHER_SEVERITY = 2,	-- engine registers severity over 0..2
	-- live state
	ELEV_M = 100,			-- [G divisor]
	MORALE = 100,			-- [G scale unknown]
	LAST_SEEN_S = 60,		-- [G], cap 1
	FRONTAGE_M = 100,		-- [G]
	ORDER_AGE_S = 30,		-- [G], cap 1
	ARMY_IDX = 4,			-- [G] meta idx 212
	-- static card (DB maxima over all 1145 land types)
	melee_attack = 75, melee_defence = 60, charge_bonus = 300,
	armour = 70, shield_defence = 35, shield_armour = 50,
	missile_block_chance = 85, morale = 110, hp_per_man = 250,
	num_men = 160, missile_damage = 75, missile_ap_damage = 100,
	missile_range = 500, ammo = 27, accuracy = 25, reload = 75,
	min_range = 100,		-- [G source]
	walk_speed = 2, run_speed = 10, charge_speed = 12, mass = 225,
	acceleration = 6, turn_speed = 90, weapon_damage = 75,
	weapon_ap_damage = 75, bonus_v_cavalry = 75, bonus_v_infantry = 20,
	capture_power = 25, soldier_radius = 3,
};

-- Shape table shipped in the hello frame; host/spec.py validates it
-- field-for-field against its own copy before accepting any obs frame.
function M.shapes()
	return {
		spec_version = M.SPEC_VERSION,
		global = { M.GLOBAL_DIM },
		friendly_type = { M.MAX_FRIENDLY },
		friendly_feat = { M.MAX_FRIENDLY, M.UNIT_FEAT_DIM },
		enemy_type = { M.MAX_ENEMY },
		enemy_feat = { M.MAX_ENEMY, M.UNIT_FEAT_DIM },
		structures = { M.MAX_STRUCT, M.STRUCT_DIM },
		vehicles = { M.MAX_VEH, M.VEH_DIM },
		terrain = { M.TERRAIN_CH, M.GRID, M.GRID },
		terrain_hires = { M.TERRAIN_HIRES_CH, M.HIRES, M.HIRES },
		verbs = #M.VERBS,
		feat_blocks = M.FEAT,
		terrain_channels = M.TERRAIN_CHANNELS,
		terrain_hires_channels = M.TERRAIN_HIRES_CHANNELS,
	};
end;

return M;
