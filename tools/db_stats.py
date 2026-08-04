#!/usr/bin/env python3
"""
db_stats.py -- Decode Total War: Attila DB tables and emit static per-unit combat stats.

Reads the game's data.pack (PFH4), decodes the binary DB rows for main_units,
land_units and the stat tables they reference, joins them, and writes a JSON map
keyed by the unit "type" string the game's unit:type() returns (e.g.
att_rom_palatina_guards) to a dict of combat stats.

Also writes aai_ability_stats.json: per-ability parameters + phases + stat
effects (unit_special_abilities and friends), the battle _kv_* rule constants,
the unit-experience thresholds/bonuses, and a top-level "names" map of
ability key -> on-screen button label (from local_en.pack).

Schemas (column order + types) come from RPFM's schema_att.ron and are embedded
below, pinned to the table VERSION numbers found in Attila's shipped data.pack.
If the game's tables ever change version, re-pull the matching field lists from
https://github.com/Frodo45127/rpfm-schemas (schema_att.ron) and update SCHEMAS.

DB binary row format (per RPFM):
  [optional GUID marker \\xFD\\xFE\\xFC\\xFF + u16-len UTF-16LE string]
  [optional version marker \\xFC\\xFD\\xFE\\xFF + u32 version]
  [1 "mysterious" byte]
  [u32 row_count]
  row_count * rows, each a sequence of typed fields in schema order:
    Boolean          = 1 byte
    I32              = 4 bytes LE signed
    I64              = 8 bytes LE signed
    F32              = 4 bytes LE float
    StringU8         = u16 length + that many UTF-8 bytes
    OptionalStringU8 = 1 present-flag byte + (if 1) StringU8
"""
import struct, sys, os, json

_GAME_DATA = (
    r"C:\Program Files (x86)\Steam\steamapps\common\Total War Attila\data"
    if os.name == "nt"
    else "/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data"
)
_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PACK = os.path.join(_GAME_DATA, "data.pack")
LOCAL_PACK = os.path.join(_GAME_DATA, "local_en.pack")

# Per-unit shot_types emission (the cockpit ammo picker's data source).
# Was paused in the 08-01 crash revert, re-enabled same evening at Theo's ask:
# db.lua PROVABLY never parses this file in-game (presence check only), so the
# emission cannot affect the game -- cockpit-only data.
EMIT_SHOT_TYPES = True
OUT_PATHS = [
    os.path.join(_GAME_DATA, "aai_unit_stats.json"),
    os.path.join(_PROJECT, "reference", "aai_unit_stats.json"),
]
ABILITY_OUT_PATHS = [
    os.path.join(_GAME_DATA, "aai_ability_stats.json"),
    os.path.join(_PROJECT, "reference", "aai_ability_stats.json"),
]

# ---------------------------------------------------------------------------
# Embedded schemas: (table_virtual_path, expected_version, [(field_name, type), ...])
# Field lists are the exact RPFM column order for the pinned version.
# ---------------------------------------------------------------------------
SCHEMAS = {
    "main_units": (r"db\main_units_tables\main_units", 17, [
        ("additional_building_requirement", "OptionalStringU8"), ("campaign_cap", "I32"),
        ("caste", "StringU8"), ("create_time", "I32"), ("is_naval", "Boolean"),
        ("land_unit", "StringU8"), ("num_men", "I32"), ("multiplayer_cap", "I32"),
        ("multiplayer_cost", "I32"), ("naval_unit", "OptionalStringU8"), ("num_ships", "I32"),
        ("min_men_per_ship", "I32"), ("max_men_per_ship", "I32"), ("prestige", "I32"),
        ("recruitment_cost", "I32"), ("recruitment_movie", "OptionalStringU8"),
        ("religion_requirement", "OptionalStringU8"), ("unit", "StringU8"),
        ("upkeep_cost", "I32"), ("weight", "OptionalStringU8"), ("campaign_total_cap", "I32"),
        ("resource_requirement", "OptionalStringU8"), ("world_leader_only", "Boolean"),
        ("can_trade", "Boolean"), ("special_edition_mask", "I32"), ("unique_index", "I64"),
        ("in_encyclopedia", "Boolean"), ("region_unit_resource_requirement", "OptionalStringU8"),
        ("voiceover", "StringU8"), ("ui_unit_group_land", "StringU8"),
        ("ui_unit_group_naval", "StringU8"), ("tier", "I32"),
    ]),
    "land_units": (r"db\land_units_tables\land_units", 30, [
        ("accuracy", "I32"), ("ammo", "I32"), ("armour", "StringU8"),
        ("campaign_action_points", "I32"), ("category", "StringU8"), ("charge_bonus", "I32"),
        ("class", "StringU8"), ("dismounted_charge_bonus", "I32"), ("dismounted_melee_attack", "I32"),
        ("dismounted_melee_defence", "I32"), ("historical_description_text", "StringU8"),
        ("key", "StringU8"), ("man_animation", "OptionalStringU8"), ("man_entity", "StringU8"),
        ("melee_attack", "I32"), ("melee_defence", "I32"), ("morale", "I32"),
        ("bonus_hit_points", "I32"), ("mount", "OptionalStringU8"), ("num_animals", "I32"),
        ("animal", "OptionalStringU8"), ("num_mounts", "I32"), ("primary_melee_weapon", "StringU8"),
        ("primary_missile_weapon", "OptionalStringU8"), ("rank_depth", "I32"), ("shield", "StringU8"),
        ("short_description_text", "StringU8"), ("spacing", "StringU8"),
        ("strengths_weaknesses_text", "StringU8"), ("supports_first_person", "Boolean"),
        ("training_level", "StringU8"), ("num_guns", "I32"), ("officers", "StringU8"),
        ("articulated_record", "OptionalStringU8"), ("engine", "OptionalStringU8"),
        ("is_male", "Boolean"), ("visibility_spotting_range_min", "F32"),
        ("visibility_spotting_range_max", "F32"), ("ability_global_recharge", "F32"),
        ("attribute_group", "OptionalStringU8"), ("spot_dist_tree", "F32"),
        ("spot_dist_scrub", "F32"), ("chariot", "OptionalStringU8"), ("num_chariots", "I32"),
        ("reload", "I32"), ("loose_spacing", "Boolean"), ("spotting_and_hiding", "OptionalStringU8"),
        ("selection_vo", "StringU8"), ("selected_vo_secondary", "StringU8"),
        ("selected_vo_tertiary", "StringU8"), ("hiding_scalar", "F32"), ("capture_power", "F32"),
    ]),
    "unit_armour_types": (r"db\unit_armour_types_tables\unit_armour_types", 1, [
        ("armour_value", "I32"), ("bonus_v_missiles", "OptionalStringU8"), ("key", "StringU8"),
        ("weak_v_missiles", "OptionalStringU8"), ("audio_material", "StringU8"),
    ]),
    "unit_shield_types": (r"db\unit_shield_types_tables\unit_shield_types", 3, [
        ("key", "StringU8"), ("shield_defence_value", "I32"), ("shield_armour_value", "I32"),
        ("audio_material", "StringU8"), ("missile_block_chance", "I32"),
    ]),
    "battle_entities": (r"db\battle_entities_tables\battle_entities", 5, [
        ("key", "StringU8"), ("type", "StringU8"), ("walk_speed", "F32"), ("run_speed", "F32"),
        ("acceleration", "F32"), ("deceleration", "F32"), ("charge_speed", "F32"),
        ("crawl_speed", "F32"), ("charge_distance_commence_run", "F32"),
        ("charge_distance_adopt_charge_pose", "F32"), ("charge_distance_pick_target", "F32"),
        ("radius", "F32"), ("shape", "StringU8"), ("radii_ratio", "F32"), ("mass", "F32"),
        ("height", "F32"), ("fire_arc_close", "F32"), ("fire_arc_loose", "F32"),
        ("turn_speed", "F32"), ("hit_points", "I32"), ("allow_turn_to_move_anim", "Boolean"),
        ("allow_static_turn_anim", "Boolean"), ("tracking_threshold", "F32"),
        ("min_turning_speed", "F32"), ("display_model_offset_z", "F32"),
    ]),
    "mounts": (r"db\mounts_tables\mounts", 3, [
        ("key", "StringU8"), ("animation", "StringU8"), ("entity", "StringU8"),
        ("mount_armour", "I32"), ("variant", "StringU8"), ("audio_armour_type", "StringU8"),
    ]),
    "melee_weapons": (r"db\melee_weapons_tables\melee_weapons", 7, [
        ("armour_penetrating", "Boolean"), ("armour_piercing", "Boolean"), ("bonus_v_cavalry", "I32"),
        ("bonus_v_elephants", "I32"), ("bonus_v_infantry", "I32"), ("key", "StringU8"),
        ("damage", "I32"), ("ap_damage", "I32"), ("first_strike", "I32"),
        ("shield_piercing", "Boolean"), ("weapon_length", "F32"), ("melee_weapon_type", "StringU8"),
        ("audio_material", "StringU8"), ("building_damage", "F32"),
    ]),
    # alternate shot types per missile weapon (the change_shot_type namespace
    # candidates: projectile keys like att_arrow_composite_heavy) -- no version
    # marker in the shipped table header, hence None.
    # NOTE 08-01 evening: shot_types emission is PAUSED (see EMIT_SHOT_TYPES) --
    # the first battle loaded after the 17:02 regen crashed at vanilla tick 4
    # (prime suspect in that revert); re-enable as its own isolated experiment.
    "missile_weapons_to_projectiles": (
        r"db\missile_weapons_to_projectiles_tables\missile_weapons_to_projectiles", None, [
        ("missile_weapon", "StringU8"), ("projectile", "StringU8")]),
    "missile_weapons": (r"db\missile_weapons_tables\missile_weapons", 6, [
        ("key", "StringU8"), ("precursor", "Boolean"), ("default_projectile", "StringU8"),
        ("can_fire_at_buildings", "Boolean"),
    ]),
    "projectiles": (r"db\projectiles_tables\projectiles", 25, [
        ("key", "StringU8"), ("category", "StringU8"), ("shot_type", "StringU8"),
        ("explosion_type", "OptionalStringU8"), ("spin_type", "StringU8"),
        ("projectile_number", "I32"), ("trajectory_sight", "StringU8"),
        ("effective_range", "I32"), ("minimum_range", "I32"), ("max_elevation", "I32"),
        ("muzzle_velocity", "F32"), ("marksmanship_bonus", "F32"), ("spread", "F32"),
        ("damage", "I32"), ("ap_damage", "I32"), ("penetration", "OptionalStringU8"),
        ("incendiary", "OptionalStringU8"), ("can_bounce", "Boolean"),
        ("high_air_resistance", "Boolean"), ("collision_radius", "F32"),
        ("base_reload_time", "F32"), ("below_waterline_damage_modifer", "F32"),
        ("calibration_distance", "F32"), ("calibration_area", "F32"),
        ("bonus_v_infantry", "I32"), ("bonus_v_cavalry", "I32"), ("bonus_v_elephant", "I32"),
        ("projectile_display", "OptionalStringU8"), ("overhead_stat_effect", "OptionalStringU8"),
        ("projectile_audio", "StringU8"), ("shockwave_radius", "F32"),
        ("can_damage_buildings", "Boolean"), ("contact_stat_effect", "OptionalStringU8"),
        ("is_grapple", "Boolean"), ("burst_size", "I32"), ("burst_shot_delay", "F32"),
        ("explosion_spread", "OptionalStringU8"), ("fire_damage", "F32"),
    ]),
    # land_unit <-> unit_ability junction. The "ability" cell is the ability KEY
    # string (a readable id like att_abil_shield_wall) and matches
    # unit_special_abilities.key directly (verified: all 30 rostered keys resolve).
    "land_units_to_unit_abilites_junctions": (
        r"db\land_units_to_unit_abilites_junctions_tables\land_units_to_unit_abilites_junctions", None, [
        ("ability", "StringU8"), ("land_unit", "StringU8"),
    ]),
    # --- ability parameter tables (join: junction.ability == unit_special_abilities.key;
    #     phases via special_ability_to_special_ability_phase_junctions, ordered;
    #     stat effects via special_ability_phase_stat_effects.phase == phases.id) ---
    "unit_special_abilities": (r"db\unit_special_abilities_tables\unit_special_abilities", 16, [
        ("key", "StringU8"), ("active_time", "F32"), ("recharge_time", "F32"),
        ("num_uses", "I32"), ("effect_range", "F32"), ("affect_self", "Boolean"),
        ("num_effected_friendly_units", "I32"), ("num_effected_enemy_units", "I32"),
        ("update_targets_every_frame", "Boolean"), ("initial_recharge", "F32"),
        ("activated_projectile", "OptionalStringU8"), ("can_autotrigger", "Boolean"),
        ("target_friends", "Boolean"), ("target_enemies", "Boolean"),
        ("target_ground", "Boolean"), ("target_intercept_range", "F32"),
        ("assume_specific_behaviour", "OptionalStringU8"), ("clear_current_order", "Boolean"),
        ("wind_up_time", "F32"), ("passive", "Boolean"), ("unique_id", "I32"),
    ]),
    "special_ability_phases": (r"db\special_ability_phases_tables\special_ability_phases", 13, [
        ("duration", "F32"), ("effect_type", "StringU8"), ("id", "StringU8"),
        ("requested_stance", "OptionalStringU8"), ("unbreakable", "Boolean"),
        ("cant_move", "Boolean"), ("kill_own_unit", "Boolean"), ("freeze_fatigue", "Boolean"),
        ("fatigue_change_ratio", "F32"), ("inspiration_aura_range_mod", "F32"),
        ("ability_recharge_change", "F32"), ("minor_casualties", "Boolean"),
        ("major_casualties", "Boolean"), ("ui_vfx", "OptionalStringU8"),
        ("rally_amount", "I32"), ("poison", "Boolean"),
    ]),
    "special_ability_phase_stat_effects": (
        r"db\special_ability_phase_stat_effects_tables\special_ability_phase_stat_effects", 1, [
        ("phase", "StringU8"), ("value", "F32"), ("stat", "StringU8"), ("how", "StringU8"),
    ]),
    "special_ability_to_special_ability_phase_junctions": (
        r"db\special_ability_to_special_ability_phase_junctions_tables\special_ability_to_special_ability_phase_junctions", 1, [
        ("order", "I32"), ("phase", "StringU8"), ("special_ability", "StringU8"),
    ]),
    "unit_abilities": (r"db\unit_abilities_tables\unit_abilities", 4, [
        ("key", "StringU8"), ("stationary_for_turn", "Boolean"),
        ("supersedes_ability", "OptionalStringU8"), ("requires_effect_enabling", "Boolean"),
        ("tooltip_text", "OptionalStringU8"),
    ]),
    # --- per-unit FK detail tables ---
    "unit_spacings": (r"db\unit_spacings_tables\unit_spacings", 2, [
        ("close_formation_spacing_horizontal", "F32"), ("close_formation_spacing_variation", "F32"),
        ("close_formation_spacing_vertical", "F32"),
        ("dismounted_close_formation_spacing_horizontal", "F32"),
        ("dismounted_close_formation_spacing_variation", "F32"),
        ("dismounted_close_formation_spacing_vertical", "F32"),
        ("dismounted_loose_formation_spacing_horizontal", "F32"),
        ("dismounted_loose_formation_spacing_variation", "F32"),
        ("dismounted_loose_formation_spacing_vertical", "F32"),
        ("horde", "Boolean"), ("key", "StringU8"),
        ("loose_formation_spacing_horizontal", "F32"),
        ("loose_formation_spacing_variation", "F32"),
        ("loose_formation_spacing_vertical", "F32"),
    ]),
    "unit_attributes_to_groups_junctions": (
        r"db\unit_attributes_to_groups_junctions_tables\unit_attributes_to_groups_junctions", 2, [
        ("attribute", "StringU8"), ("attribute_group", "OptionalStringU8"),
    ]),
    "land_units_officers": (r"db\land_units_officers_tables\land_units_officers", 2, [
        ("key", "StringU8"), ("officer_1", "OptionalStringU8"), ("officer_2", "OptionalStringU8"),
        ("standard_bearer_1", "OptionalStringU8"), ("standard_bearer_2", "OptionalStringU8"),
        ("musician_1", "OptionalStringU8"), ("musician_2", "OptionalStringU8"),
        ("personality_location", "OptionalStringU8"),
    ]),
    "battlefield_engines": (r"db\battlefield_engines_tables\battlefield_engines", 4, [
        ("destroyed_model", "OptionalStringU8"), ("destruction_animation", "OptionalStringU8"),
        ("engine_type", "StringU8"), ("gun_animation_table", "StringU8"), ("key", "StringU8"),
        ("missile_weapon", "OptionalStringU8"), ("model", "StringU8"),
        ("battle_entity", "StringU8"), ("can_move", "Boolean"),
    ]),
    # --- experience ---
    "unit_experience_thresholds": (
        r"db\unit_experience_thresholds_tables\unit_experience_thresholds", None, [
        ("key", "StringU8"), ("value", "I32"),
    ]),
    "unit_stats_land_experience_bonuses": (
        r"db\unit_stats_land_experience_bonuses_tables\unit_stats_land_experience_bonuses", None, [
        ("xp_level", "StringU8"), ("melee_attack", "I32"), ("melee_defence", "I32"),
        ("core_reloading_skill", "I32"), ("morale", "I32"), ("core_marksmanship", "I32"),
        ("fatigue", "I32"), ("mp_fixed_cost", "I32"), ("mp_experience_cost_multiplier", "F32"),
    ]),
    # --- naval (stretch: is_naval card via naval_units -> ship_dbs -> battle_entities) ---
    "naval_units": (r"db\naval_units_tables\naval_units", 20, [
        ("campaign_action_points", "I32"), ("category", "StringU8"), ("class", "StringU8"),
        ("historical_description_text", "StringU8"), ("key", "StringU8"), ("ship", "StringU8"),
        ("short_description_text", "StringU8"), ("strengths_weaknesses_text", "StringU8"),
        ("supports_first_person", "Boolean"), ("unit_type_icon", "OptionalStringU8"),
        ("primary_naval_weapon", "OptionalStringU8"), ("secondary_naval_weapon", "OptionalStringU8"),
        ("rank_depth", "I32"), ("attribute_groups", "OptionalStringU8"),
        ("can_board", "Boolean"), ("is_composite", "Boolean"), ("ignition_threshold", "I32"),
        ("can_ram", "Boolean"), ("weight", "OptionalStringU8"),
    ]),
    "ship_dbs": (r"db\ship_dbs_tables\ship_dbs", None, [
        ("entity", "StringU8"), ("key", "StringU8"), ("model", "StringU8"), ("spacing", "StringU8"),
    ]),
}

# Battle key-value rule tables: identical two-column shape, no version marker in pack.
KV_TABLES = ("rules", "morale", "fatigue", "fire_values", "key_buildings",
             "naval_morale", "naval_rules", "experience_bonuses")
for _kv in KV_TABLES:
    SCHEMAS["_kv_" + _kv] = (
        "db\\_kv_%s_tables\\_kv_%s" % (_kv, _kv), None,
        [("key", "StringU8"), ("value", "F32")])

# ---------------------------------------------------------------------------
# PFH4 pack index
# ---------------------------------------------------------------------------
def read_pack_index(f):
    hdr = f.read(28)
    magic, flags, pf_count, pf_size, file_count, file_index_size, ts = struct.unpack("<4sIIIIII", hdr)
    if magic != b"PFH4":
        raise SystemExit(f"unsupported pack magic {magic!r}")
    f.read(pf_size)
    index_data = f.read(file_index_size)
    data_offset = f.tell()

    def parse(with_ts):
        entries, pos = [], 0
        for _ in range(file_count):
            size = struct.unpack_from("<I", index_data, pos)[0]; pos += 4
            if with_ts:
                pos += 4
            end = index_data.index(b"\x00", pos)
            path = index_data[pos:end].decode("latin-1"); pos = end + 1
            entries.append((path, size))
        if pos != len(index_data):
            raise ValueError("index size mismatch")
        return entries

    entries = None
    for with_ts in (False, True):
        try:
            entries = parse(with_ts); break
        except (ValueError, IndexError, struct.error):
            entries = None
    if entries is None:
        raise SystemExit("could not parse file index")

    # accumulate offsets in index order (data blocks stored contiguously)
    out, off = {}, data_offset
    for path, size in entries:
        out[path] = (off, size)
        off += size
    return out

def extract(f, index, virtual_path):
    if virtual_path not in index:
        return None
    off, size = index[virtual_path]
    f.seek(off)
    return f.read(size)

# ---------------------------------------------------------------------------
# Localisation (.loc) -> ability display names
# ---------------------------------------------------------------------------
# TW .loc format: UTF-16LE BOM (FF FE), "LOC\0" magic, u32 version, u32 row
# count, then rows of [u16 char-count + UTF-16LE key][u16 char-count + UTF-16LE
# text][1 tooltip byte]. Battle-ability button labels live in local_en.pack
# text\db\unit_abilities.loc as unit_abilities_tooltip_text_<ability_key> =
# "Name||tooltip body" (verified: form_hoplite_phalanx -> "Spear Wall"); the
# unit_abilities table has no separate onscreen_name loc family.
LOC_ABILITY_FILE = r"text\db\unit_abilities.loc"
LOC_KEY_PREFIX = "unit_abilities_tooltip_text_"

def read_loc(data):
    if data[:2] != b"\xff\xfe" or data[2:6] != b"LOC\x00":
        raise ValueError(f"bad .loc header {data[:6]!r}")
    count = struct.unpack_from("<I", data, 10)[0]
    pos, rows = 14, []
    for _ in range(count):
        pair = []
        for _ in range(2):
            ln = struct.unpack_from("<H", data, pos)[0]; pos += 2
            pair.append(data[pos:pos + ln * 2].decode("utf-16-le")); pos += ln * 2
        pos += 1  # tooltip flag byte
        rows.append(pair)
    return rows

def build_ability_names(loc_pack_path, unit_abils):
    """(ability_key -> on-screen button label, ability_key -> tooltip body).
    Primary source: the localisation pack (what the player actually sees);
    fallback for keys it lacks: the same English text baked into
    unit_abilities.tooltip_text in data.pack."""
    texts = {r["key"]: r["tooltip_text"] for r in unit_abils if r["tooltip_text"]}
    try:
        with open(loc_pack_path, "rb") as f:
            data = extract(f, read_pack_index(f), LOC_ABILITY_FILE)
        if data is None:
            raise ValueError(f"{LOC_ABILITY_FILE} not in pack")
        for key, text in read_loc(data):
            if key.startswith(LOC_KEY_PREFIX) and text:
                texts[key[len(LOC_KEY_PREFIX):]] = text
    except (OSError, ValueError) as e:
        print(f"WARNING: could not read ability names from {loc_pack_path} "
              f"({e}); using data.pack tooltip_text only.", file=sys.stderr)
    # "Name||tooltip body" -> label before the "||", the game's own
    # buff/debuff description after it
    names, tips = {}, {}
    for k, t in texts.items():
        parts = t.split("||", 1)
        names[k] = parts[0].strip()
        if len(parts) > 1 and parts[1].strip():
            tips[k] = parts[1].strip()
    return names, tips

# ---------------------------------------------------------------------------
# DB row decoding
# ---------------------------------------------------------------------------
def read_field(data, pos, t):
    if t == "Boolean":
        return data[pos] != 0, pos + 1
    if t == "I32":
        return struct.unpack_from("<i", data, pos)[0], pos + 4
    if t == "I64":
        return struct.unpack_from("<q", data, pos)[0], pos + 8
    if t == "F32":
        return struct.unpack_from("<f", data, pos)[0], pos + 4
    if t == "StringU8":
        ln = struct.unpack_from("<H", data, pos)[0]; pos += 2
        return data[pos:pos + ln].decode("utf-8", "replace"), pos + ln
    if t == "OptionalStringU8":
        present = data[pos]; pos += 1
        if present == 0:
            return "", pos
        ln = struct.unpack_from("<H", data, pos)[0]; pos += 2
        return data[pos:pos + ln].decode("utf-8", "replace"), pos + ln
    raise ValueError(f"unknown field type {t}")

def read_db_header(data):
    """Return (version, row_count, body_offset)."""
    pos = 0
    if data[pos:pos + 4] == b"\xFD\xFE\xFC\xFF":
        pos += 4
        ln = struct.unpack_from("<H", data, pos)[0]; pos += 2 + ln * 2
    ver = None
    if data[pos:pos + 4] == b"\xFC\xFD\xFE\xFF":
        pos += 4
        ver = struct.unpack_from("<I", data, pos)[0]; pos += 4
    pos += 1  # mysterious byte
    rc = struct.unpack_from("<I", data, pos)[0]; pos += 4
    return ver, rc, pos

def decode_table(data, expected_version, fields, name):
    ver, rc, pos = read_db_header(data)
    if expected_version is not None and ver != expected_version:
        print(f"WARNING: table {name} version {ver} != expected {expected_version}; "
              f"field layout may be wrong.", file=sys.stderr)
    rows = []
    for _ in range(rc):
        row = {}
        for fn, ft in fields:
            v, pos = read_field(data, pos, ft)
            row[fn] = v
        rows.append(row)
    if pos != len(data):
        print(f"WARNING: table {name} did not consume cleanly "
              f"(pos={pos} len={len(data)}); decode may be misaligned.", file=sys.stderr)
    return ver, rows

def load_table(f, index, name):
    vpath, ver, fields = SCHEMAS[name]
    data = extract(f, index, vpath)
    if data is None:
        raise SystemExit(f"table not found in pack: {vpath}")
    _, rows = decode_table(data, ver, fields, name)
    return rows

def keyed(rows, key):
    return {r[key]: r for r in rows}

# ---------------------------------------------------------------------------
# Build joined stats
# ---------------------------------------------------------------------------
def rnd(x, n=1):
    return round(float(x), n)

def build_abilities(specials, phases, stat_fx, phase_junc, kv_tables, xp_thresh, xp_bonus):
    """aai_ability_stats.json payload: per-ability params + ordered phases +
    stat effects, the battle _kv_* constants, and experience data. Booleans and
    zero-valued modifiers are omitted to keep it small; -1 keeps the DB's
    "unlimited / not set" sentinel meaning."""
    fx_by_phase = {}
    for r in stat_fx:
        fx_by_phase.setdefault(r["phase"], []).append(
            {"stat": r["stat"], "how": r["how"], "value": rnd(r["value"], 3)})
    phases_by_abil = {}
    for r in sorted(phase_junc, key=lambda j: (j["special_ability"], j["order"])):
        phases_by_abil.setdefault(r["special_ability"], []).append(r["phase"])

    abilities = {}
    for r in specials:
        a = {"recharge": rnd(r["recharge_time"]), "active_time": rnd(r["active_time"]),
             "uses": r["num_uses"], "range": rnd(r["effect_range"]),
             "initial_recharge": rnd(r["initial_recharge"])}
        if r["wind_up_time"]:
            a["wind_up"] = rnd(r["wind_up_time"])
        for flag in ("passive", "affect_self", "target_friends", "target_enemies",
                     "target_ground", "can_autotrigger", "clear_current_order"):
            if r[flag]:
                a[flag] = True
        if r["num_effected_friendly_units"]:
            a["num_friendly_targets"] = r["num_effected_friendly_units"]
        if r["num_effected_enemy_units"]:
            a["num_enemy_targets"] = r["num_effected_enemy_units"]
        if r["target_intercept_range"]:
            a["target_intercept_range"] = rnd(r["target_intercept_range"])
        if r["assume_specific_behaviour"]:
            a["behaviour"] = r["assume_specific_behaviour"]
        if r["activated_projectile"]:
            a["projectile"] = r["activated_projectile"]
        ph_list = []
        for pid in phases_by_abil.get(r["key"], []):
            p = phases.get(pid)
            if p is None:
                continue
            d = {"id": pid, "duration": rnd(p["duration"]), "effect": p["effect_type"]}
            for flag in ("unbreakable", "cant_move", "kill_own_unit", "freeze_fatigue",
                         "minor_casualties", "major_casualties", "poison"):
                if p[flag]:
                    d[flag] = True
            if p["requested_stance"]:
                d["stance"] = p["requested_stance"]
            if p["fatigue_change_ratio"]:
                d["fatigue_change_ratio"] = rnd(p["fatigue_change_ratio"], 3)
            if p["inspiration_aura_range_mod"]:
                d["inspiration_aura_range_mod"] = rnd(p["inspiration_aura_range_mod"], 2)
            if p["ability_recharge_change"]:
                d["ability_recharge_change"] = rnd(p["ability_recharge_change"], 2)
            if p["rally_amount"]:
                d["rally_amount"] = p["rally_amount"]
            fx = fx_by_phase.get(pid)
            if fx:
                d["effects"] = fx
            ph_list.append(d)
        if ph_list:
            a["phases"] = ph_list
        abilities[r["key"]] = a

    kv = {name: {r["key"]: rnd(r["value"], 4) for r in rows}
          for name, rows in kv_tables.items()}
    exp = {"thresholds": {r["key"]: r["value"] for r in xp_thresh},
           "land_bonuses": {r["xp_level"]: {
               "melee_attack": r["melee_attack"], "melee_defence": r["melee_defence"],
               "reloading": r["core_reloading_skill"], "morale": r["morale"],
               "marksmanship": r["core_marksmanship"], "fatigue": r["fatigue"],
           } for r in xp_bonus}}
    return {"abilities": abilities, "kv": kv, "experience": exp}

def build(pack_path):
    with open(pack_path, "rb") as f:
        index = read_pack_index(f)
        main_units = load_table(f, index, "main_units")
        land_units = keyed(load_table(f, index, "land_units"), "key")
        armour     = keyed(load_table(f, index, "unit_armour_types"), "key")
        shields    = keyed(load_table(f, index, "unit_shield_types"), "key")
        entities   = keyed(load_table(f, index, "battle_entities"), "key")
        mounts     = keyed(load_table(f, index, "mounts"), "key")
        melee      = keyed(load_table(f, index, "melee_weapons"), "key")
        missile    = keyed(load_table(f, index, "missile_weapons"), "key")
        projectile = keyed(load_table(f, index, "projectiles"), "key")
        alt_shots  = {}      # missile_weapon -> [alternate projectile keys]
        if EMIT_SHOT_TYPES:
            for r in load_table(f, index, "missile_weapons_to_projectiles"):
                alt_shots.setdefault(r["missile_weapon"], []).append(r["projectile"])
        junction   = load_table(f, index, "land_units_to_unit_abilites_junctions")
        spacings   = keyed(load_table(f, index, "unit_spacings"), "key")
        attr_junc  = load_table(f, index, "unit_attributes_to_groups_junctions")
        officers   = keyed(load_table(f, index, "land_units_officers"), "key")
        engines    = keyed(load_table(f, index, "battlefield_engines"), "key")
        naval      = keyed(load_table(f, index, "naval_units"), "key")
        ship_dbs   = keyed(load_table(f, index, "ship_dbs"), "key")
        # ability parameter tables (for aai_ability_stats.json)
        specials   = load_table(f, index, "unit_special_abilities")
        unit_abils = load_table(f, index, "unit_abilities")
        phases     = keyed(load_table(f, index, "special_ability_phases"), "id")
        stat_fx    = load_table(f, index, "special_ability_phase_stat_effects")
        phase_junc = load_table(f, index, "special_ability_to_special_ability_phase_junctions")
        xp_thresh  = load_table(f, index, "unit_experience_thresholds")
        xp_bonus   = load_table(f, index, "unit_stats_land_experience_bonuses")
        kv_tables  = {name: load_table(f, index, "_kv_" + name) for name in KV_TABLES}

    abils_by_lu = {}
    for r in junction:
        abils_by_lu.setdefault(r["land_unit"], []).append(r["ability"])
    attrs_by_group = {}
    for r in attr_junc:
        attrs_by_group.setdefault(r["attribute_group"], []).append(r["attribute"])

    cov = {"armour": [0, 0], "shield": [0, 0], "entity": [0, 0],
           "melee_weapon": [0, 0], "missile_range": [0, 0], "abilities": [0, 0],
           "spacing": [0, 0], "attributes": [0, 0], "officers": [0, 0],
           "engine": [0, 0], "naval_ship": [0, 0]}
    out = {}
    for mu in main_units:
        utype = mu["unit"]
        lu = land_units.get(mu["land_unit"])
        if not lu:
            continue  # no land stats (e.g. naval-only); skip
        s = {}
        # --- direct land_units combat stats ---
        s["melee_attack"] = lu["melee_attack"]
        s["melee_defence"] = lu["melee_defence"]
        s["charge_bonus"] = lu["charge_bonus"]
        s["morale"] = lu["morale"]
        s["num_men"] = mu["num_men"]
        s["unit_class"] = lu["class"]
        s["category"] = lu["category"]
        s["training_level"] = lu["training_level"]
        mount_key = lu["mount"]
        # dismounted stats only matter for mounted units (or when explicitly nonzero)
        for k in ("dismounted_melee_attack", "dismounted_melee_defence",
                  "dismounted_charge_bonus"):
            if mount_key or lu[k]:
                s[k] = lu[k]
        s["rank_depth"] = lu["rank_depth"]
        s["loose_spacing"] = bool(lu["loose_spacing"])
        s["visibility_spotting_range_min"] = rnd(lu["visibility_spotting_range_min"])
        s["visibility_spotting_range_max"] = rnd(lu["visibility_spotting_range_max"])
        s["spot_dist_tree"] = rnd(lu["spot_dist_tree"])
        s["spot_dist_scrub"] = rnd(lu["spot_dist_scrub"])
        s["hiding_scalar"] = rnd(lu["hiding_scalar"], 2)
        s["capture_power"] = rnd(lu["capture_power"], 2)
        s["ability_global_recharge"] = rnd(lu["ability_global_recharge"])
        for k in ("num_mounts", "num_animals", "num_chariots", "num_guns"):
            if lu[k]:
                s[k] = lu[k]
        s["officers"] = lu["officers"]
        if lu["engine"]:
            s["engine"] = lu["engine"]
        if lu["attribute_group"]:
            s["attribute_group"] = lu["attribute_group"]

        # --- armour (FK -> unit_armour_types.armour_value) ---
        cov["armour"][1] += 1
        av = armour.get(lu["armour"])
        if av is not None:
            s["armour"] = av["armour_value"]
            # optional-string cells; every Attila row carries the placeholder "0"
            if av["bonus_v_missiles"] not in ("", "0"):
                s["armour_bonus_v_missiles"] = av["bonus_v_missiles"]
            if av["weak_v_missiles"] not in ("", "0"):
                s["armour_weak_v_missiles"] = av["weak_v_missiles"]
            cov["armour"][0] += 1

        # --- shield (FK -> unit_shield_types) ---
        if lu["shield"]:
            cov["shield"][1] += 1
            sh = shields.get(lu["shield"])
            if sh is not None:
                s["shield_defence"] = sh["shield_defence_value"]
                s["shield_armour"] = sh["shield_armour_value"]
                s["missile_block_chance"] = sh["missile_block_chance"]
                cov["shield"][0] += 1

        # --- man entity (rider) -> hit points; movement entity -> speed ---
        man_be = entities.get(lu["man_entity"])
        move_be = man_be
        if mount_key and mount_key in mounts:
            s["mount_armour"] = mounts[mount_key]["mount_armour"]
            ment = mounts[mount_key]["entity"]
            if ment in entities:
                move_be = entities[ment]
        cov["entity"][1] += 1
        if man_be is not None:
            s["hitpoints"] = man_be["hit_points"] + lu["bonus_hit_points"]
            s["mass"] = rnd(man_be["mass"])
            cov["entity"][0] += 1
        if move_be is not None:
            s["speed"] = rnd(move_be["run_speed"])
            s["walk_speed"] = rnd(move_be["walk_speed"])
            s["charge_speed"] = rnd(move_be["charge_speed"])
            s["acceleration"] = rnd(move_be["acceleration"], 2)
            s["deceleration"] = rnd(move_be["deceleration"], 2)
            s["turn_speed"] = rnd(move_be["turn_speed"])
            s["radius"] = rnd(move_be["radius"], 2)
            s["charge_distance_commence_run"] = rnd(move_be["charge_distance_commence_run"])
            s["charge_distance_adopt_charge_pose"] = rnd(move_be["charge_distance_adopt_charge_pose"])
            s["charge_distance_pick_target"] = rnd(move_be["charge_distance_pick_target"])
            s["fire_arc_close"] = rnd(move_be["fire_arc_close"])
            s["fire_arc_loose"] = rnd(move_be["fire_arc_loose"])
        if lu["bonus_hit_points"]:
            s["bonus_hit_points"] = lu["bonus_hit_points"]

        # --- primary melee weapon (FK -> melee_weapons) ---
        cov["melee_weapon"][1] += 1
        mw = melee.get(lu["primary_melee_weapon"])
        if mw is not None:
            s["weapon_damage"] = mw["damage"]
            s["weapon_ap_damage"] = mw["ap_damage"]
            s["weapon_bonus_v_cavalry"] = mw["bonus_v_cavalry"]
            s["weapon_bonus_v_infantry"] = mw["bonus_v_infantry"]
            s["weapon_bonus_v_elephants"] = mw["bonus_v_elephants"]
            s["weapon_armour_piercing"] = bool(mw["armour_piercing"])
            s["weapon_armour_penetrating"] = bool(mw["armour_penetrating"])
            s["weapon_shield_piercing"] = bool(mw["shield_piercing"])
            s["weapon_length"] = rnd(mw["weapon_length"], 2)
            if mw["first_strike"]:
                s["weapon_first_strike"] = mw["first_strike"]
            if mw["building_damage"]:
                s["weapon_building_damage"] = rnd(mw["building_damage"])
            cov["melee_weapon"][0] += 1

        # --- missile stats (only meaningful when the unit has a ranged weapon) ---
        if lu["primary_missile_weapon"]:
            s["has_missile"] = True
            s["ammo"] = lu["ammo"]
            s["accuracy"] = lu["accuracy"]
            s["reload"] = lu["reload"]
            s["primary_missile_weapon"] = lu["primary_missile_weapon"]
            # FK: missile_weapon -> default_projectile -> projectiles.effective_range / damage
            cov["missile_range"][1] += 1
            mwpn = missile.get(lu["primary_missile_weapon"])
            if mwpn is not None:
                # default first, then the weapon's alternates -- the harness
                # shot-type picker offers exactly this list
                if EMIT_SHOT_TYPES:
                    s["shot_types"] = ([mwpn["default_projectile"]] +
                                       alt_shots.get(lu["primary_missile_weapon"], []))
                proj = projectile.get(mwpn["default_projectile"])
                if proj is not None:
                    s["missile_range"] = proj["effective_range"]
                    s["missile_damage"] = proj["damage"]
                    s["missile_ap_damage"] = proj["ap_damage"]
                    s["missile_minimum_range"] = proj["minimum_range"]
                    s["missile_spread"] = rnd(proj["spread"], 2)
                    s["missile_marksmanship_bonus"] = rnd(proj["marksmanship_bonus"], 2)
                    s["missile_fire_damage"] = rnd(proj["fire_damage"])
                    s["missile_can_damage_buildings"] = bool(proj["can_damage_buildings"])
                    s["missile_bonus_v_infantry"] = proj["bonus_v_infantry"]
                    s["missile_bonus_v_cavalry"] = proj["bonus_v_cavalry"]
                    s["missile_bonus_v_elephant"] = proj["bonus_v_elephant"]
                    if proj["incendiary"]:
                        s["missile_incendiary"] = proj["incendiary"]
                    if proj["penetration"]:
                        s["missile_penetration"] = proj["penetration"]
                    cov["missile_range"][0] += 1

        # --- campaign context ---
        s["recruitment_cost"] = mu["recruitment_cost"]
        s["upkeep_cost"] = mu["upkeep_cost"]
        s["caste"] = mu["caste"]
        s["tier"] = mu["tier"]

        # --- formation spacing (FK -> unit_spacings) ---
        cov["spacing"][1] += 1
        sp = spacings.get(lu["spacing"])
        if sp is not None:
            s["spacing_horizontal"] = rnd(sp["close_formation_spacing_horizontal"], 2)
            s["spacing_vertical"] = rnd(sp["close_formation_spacing_vertical"], 2)
            s["spacing_loose_horizontal"] = rnd(sp["loose_formation_spacing_horizontal"], 2)
            s["spacing_loose_vertical"] = rnd(sp["loose_formation_spacing_vertical"], 2)
            if sp["horde"]:
                s["spacing_horde"] = True
            cov["spacing"][0] += 1

        # --- attributes (FK -> unit_attributes_to_groups_junctions) ---
        if lu["attribute_group"]:
            cov["attributes"][1] += 1
            attrs = attrs_by_group.get(lu["attribute_group"])
            if attrs:
                s["attributes"] = sorted(set(attrs))
                cov["attributes"][0] += 1

        # --- officer loadout (FK -> land_units_officers) ---
        cov["officers"][1] += 1
        off = officers.get(lu["officers"])
        if off is not None:
            n = sum(1 for k in ("officer_1", "officer_2") if off[k])
            if n:
                s["officer_count"] = n
            if off["standard_bearer_1"] or off["standard_bearer_2"]:
                s["has_standard_bearer"] = True
            if off["musician_1"] or off["musician_2"]:
                s["has_musician"] = True
            cov["officers"][0] += 1

        # --- siege engine loadout (FK -> battlefield_engines -> projectiles) ---
        if lu["engine"]:
            cov["engine"][1] += 1
            eng = engines.get(lu["engine"])
            if eng is not None:
                s["engine_type"] = eng["engine_type"]
                s["engine_can_move"] = bool(eng["can_move"])
                emw = missile.get(eng["missile_weapon"]) if eng["missile_weapon"] else None
                if emw is not None:
                    # artillery shot types live on the ENGINE's missile weapon,
                    # not land_units.primary_missile_weapon
                    if EMIT_SHOT_TYPES and "shot_types" not in s:
                        s["shot_types"] = ([emw["default_projectile"]] +
                                           alt_shots.get(eng["missile_weapon"], []))
                    eproj = projectile.get(emw["default_projectile"])
                    if eproj is not None:
                        s["engine_missile_range"] = eproj["effective_range"]
                        s["engine_missile_damage"] = eproj["damage"]
                        s["engine_missile_ap_damage"] = eproj["ap_damage"]
                cov["engine"][0] += 1

        # --- naval card (main_units flag; naval_unit -> ship_dbs -> hull entity) ---
        if mu["is_naval"]:
            s["is_naval"] = True
            if mu["num_ships"] > 0:
                s["num_ships"] = mu["num_ships"]
            if mu["naval_unit"]:
                cov["naval_ship"][1] += 1
                nu = naval.get(mu["naval_unit"])
                if nu is not None:
                    s["naval_class"] = nu["class"]
                    if nu["can_ram"]:
                        s["can_ram"] = True
                    if nu["can_board"]:
                        s["can_board"] = True
                    s["ignition_threshold"] = nu["ignition_threshold"]
                    sdb = ship_dbs.get(nu["ship"])
                    hull = entities.get(sdb["entity"]) if sdb is not None else None
                    if hull is not None:
                        s["ship_hitpoints"] = hull["hit_points"]
                        s["ship_mass"] = rnd(hull["mass"])
                        s["ship_speed"] = rnd(hull["run_speed"])
                        cov["naval_ship"][0] += 1

        # --- special-ability roster (from the land_unit<->ability junction) ---
        cov["abilities"][1] += 1
        abils = abils_by_lu.get(mu["land_unit"])
        if abils:
            s["abilities"] = sorted(set(abils))
            cov["abilities"][0] += 1

        out[utype] = s

    abil_out = build_abilities(specials, phases, stat_fx, phase_junc,
                               kv_tables, xp_thresh, xp_bonus)
    abil_out["names"], abil_out["tips"] = build_ability_names(LOCAL_PACK, unit_abils)
    # rostered ability keys (the junction) -> unit_special_abilities resolve rate
    rostered = set(r["ability"] for r in junction)
    cov["rostered_abils"] = [sum(1 for k in rostered if k in abil_out["abilities"]),
                             len(rostered)]
    cov["abil_names"] = [sum(1 for k in rostered if k in abil_out["names"]),
                         len(rostered)]
    return out, abil_out, cov

def main():
    pack = sys.argv[1] if len(sys.argv) > 1 else DATA_PACK
    out, abil_out, cov = build(pack)
    payload = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True)
    for p in OUT_PATHS:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
        print(f"wrote {len(out)} units -> {p}")
    abil_payload = json.dumps(abil_out, ensure_ascii=False, indent=1, sort_keys=True)
    for p in ABILITY_OUT_PATHS:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(abil_payload)
        print(f"wrote {len(abil_out['abilities'])} abilities -> {p}")
    print("FK coverage (resolved/total):")
    for k, (a, b) in cov.items():
        print(f"  {k:14} {a}/{b}")

if __name__ == "__main__":
    main()
