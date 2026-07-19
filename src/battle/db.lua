-------------------------------------------------------------------------
--	DB provider (#3 of 3). Static, per-unit-TYPE combat data (stat card +
--	special-ability roster) extracted offline from the game's data.pack by
--	tools/db_stats.py -> data/aai_unit_stats.json, keyed by the exact string
--	unit:type() returns (e.g. att_rom_comes). This is the ZERO-RE source for
--	the stat card (armour / melee_attack / melee_defence / charge_bonus /
--	morale / hp / mass / speeds / weapon dmg+ap / vs cav+inf / missile
--	dmg+range+accuracy+reload+ammo / caste / tier / cost) and abilities[].
--
--	DELIVERY (important): the out-of-process cockpit reads THIS SAME json
--	directly (server route /unitstats) and joins it to live units by their
--	`type` client-side. So read_state() deliberately does NOT re-embed this
--	static data every tick -- that would bloat the snapshot with 40 constant
--	fields per unit. This module is therefore (a) the provider of record for
--	the DB field set (documented in the api.FIELDS catalog) and (b) a lazy
--	in-game accessor for any future Lua-side AI consumer.
--
--	No in-game JSON parse happens by default (the 850 KB file is only needed
--	out-of-process). M.stats()/M.abilities() are lazy stubs today; wiring a
--	tested decoder here is a small, isolated future add if an in-game
--	consumer ever needs the card. See reference/DB_DATA.md.
-------------------------------------------------------------------------
local M = {};

M.PATH = "data/aai_unit_stats.json";
M.ok = false;			-- true once the file is confirmed present in-game

-- the stat-card + roster field set this provider is authoritative for
-- (mirrors the keys tools/db_stats.py emits; consumed client-side by type)
M.FIELDS = {
	"melee_attack", "melee_defence", "charge_bonus", "morale", "num_men",
	"unit_class", "category", "training_level", "armour", "shield_defence",
	"shield_armour", "missile_block_chance", "hitpoints", "mass", "speed",
	"walk_speed", "charge_speed", "weapon_damage", "weapon_ap_damage",
	"weapon_bonus_v_cavalry", "weapon_bonus_v_infantry", "weapon_armour_piercing",
	"has_missile", "ammo", "accuracy", "reload", "missile_range", "missile_damage",
	"missile_ap_damage", "recruitment_cost", "upkeep_cost", "caste", "tier",
	"abilities",
};

function M.init(core)
	-- presence check only (no parse): confirms the extract shipped into data/
	local f = io.open(M.PATH, "r");
	if f then
		f:close();
		M.ok = true;
	end;
	if core then
		core.log("db: unit-stats extract " .. (M.ok and "present" or "MISSING") ..
			" (" .. M.PATH .. ") -- served to the cockpit via /unitstats, joined by type");
	end;
	return M.ok;
end;

-- Lazy in-game accessors. Static data is delivered out-of-process (see header),
-- so these return nil today; documented as a future tested add if needed.
function M.stats(unit_type)
	return nil;	-- consume via the client-side /unitstats join by `type`
end;

function M.abilities(unit_type)
	return nil;	-- roster is in the same /unitstats record, keyed by `type`
end;

return M;
