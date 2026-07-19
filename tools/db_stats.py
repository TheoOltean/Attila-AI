#!/usr/bin/env python3
"""
db_stats.py -- Decode Total War: Attila DB tables and emit static per-unit combat stats.

Reads the game's data.pack (PFH4), decodes the binary DB rows for main_units,
land_units and the stat tables they reference, joins them, and writes a JSON map
keyed by the unit "type" string the game's unit:type() returns (e.g.
att_rom_palatina_guards) to a dict of combat stats.

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

DATA_PACK = "/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data/data.pack"
OUT_PATHS = [
    "/mnt/c/Program Files (x86)/Steam/steamapps/common/Total War Attila/data/aai_unit_stats.json",
    "/mnt/c/Users/theod/programming/Attila-AI/reference/aai_unit_stats.json",
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
    # string (a readable id like att_abil_shield_wall); no further table needed.
    "land_units_to_unit_abilites_junctions": (
        r"db\land_units_to_unit_abilites_junctions_tables\land_units_to_unit_abilites_junctions", None, [
        ("ability", "StringU8"), ("land_unit", "StringU8"),
    ]),
}

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
        junction   = load_table(f, index, "land_units_to_unit_abilites_junctions")

    abils_by_lu = {}
    for r in junction:
        abils_by_lu.setdefault(r["land_unit"], []).append(r["ability"])

    cov = {"armour": [0, 0], "shield": [0, 0], "entity": [0, 0],
           "melee_weapon": [0, 0], "missile_range": [0, 0], "abilities": [0, 0]}
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

        # --- armour (FK -> unit_armour_types.armour_value) ---
        cov["armour"][1] += 1
        av = armour.get(lu["armour"])
        if av is not None:
            s["armour"] = av["armour_value"]
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
        mount_key = lu["mount"]
        if mount_key and mount_key in mounts:
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
            s["weapon_armour_piercing"] = bool(mw["armour_piercing"])
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
                proj = projectile.get(mwpn["default_projectile"])
                if proj is not None:
                    s["missile_range"] = proj["effective_range"]
                    s["missile_damage"] = proj["damage"]
                    s["missile_ap_damage"] = proj["ap_damage"]
                    cov["missile_range"][0] += 1

        # --- campaign context ---
        s["recruitment_cost"] = mu["recruitment_cost"]
        s["upkeep_cost"] = mu["upkeep_cost"]
        s["caste"] = mu["caste"]
        s["tier"] = mu["tier"]

        # --- special-ability roster (from the land_unit<->ability junction) ---
        cov["abilities"][1] += 1
        abils = abils_by_lu.get(mu["land_unit"])
        if abils:
            s["abilities"] = sorted(set(abils))
            cov["abilities"][0] += 1

        out[utype] = s

    return out, cov

def main():
    pack = sys.argv[1] if len(sys.argv) > 1 else DATA_PACK
    out, cov = build(pack)
    payload = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True)
    for p in OUT_PATHS:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
        print(f"wrote {len(out)} units -> {p}")
    print("FK coverage (resolved/total):")
    for k, (a, b) in cov.items():
        print(f"  {k:14} {a}/{b}")

if __name__ == "__main__":
    main()
