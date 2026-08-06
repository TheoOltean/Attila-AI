-------------------------------------------------------------------------
--	ml/game/read/global.lua -- the global[40] assembler.
--
--	Layout (spec.GLOBAL + ML_DESIGN.md): time elapsed/remaining (/1800),
--	attacker bit, battle-type one-hot [3,9), weather one-hot [9,13),
--	men+unit counts [13,19), victory points [19,34), spare [34,40).
--	Clock source law: remaining_conflict_time() is the authoritative
--	battle clock (tick-derived time drifts under speed changes).
--
--	Harvested fields are LIVE below; unharvested stay zero, each gap
--	carrying its provider seed. "Own" = the player alliance for now
--	(local_alliance(), read fresh per tick from pump context); when the
--	controlled-side decision lands it must be pinned in ONE place and
--	this module re-pointed at it.
-------------------------------------------------------------------------

local spec = require "ml/spec";
local util = require "ml/util";
local plua = require "ml/providers/lua";

local M = {};

local ML = nil;

-- Per-battle state: first-seen remaining time anchors elapsed (idx 0).
local init_remaining = nil;

function M.init(core)
	ML = core;
	init_remaining = nil;
end;

-- Sanity ceiling for remaining_conflict_time(): 3600 = full battle; the
-- no-limit sentinel on unlimited-time battles reads far above it (clock
-- fields stay zero rather than feeding garbage).
local REMAINING_MAX_S = 36000;

-- Full vector (Lua array of 40 numbers, array pos p = spec idx p-1;
-- zeros where a field is not yet readable).
function M.build()
	local v = util.zeros(spec.GLOBAL_DIM);
	local b = ML and ML.battle;
	if not b then return v; end;

	-- idx 0/1: the authoritative clock. elapsed = first-seen remaining
	-- minus current remaining (never the tick clock).
	local rem = plua.read(b, "remaining_conflict_time");
	if type(rem) == "number" and rem >= 0 and rem < REMAINING_MAX_S then
		if init_remaining == nil then init_remaining = rem; end;
		v[1] = util.clamp01((init_remaining - rem) / spec.NORM.TIME_S);
		v[2] = util.clamp01(rem / spec.NORM.TIME_S);
	end;

	-- idx 2: attacker bit -- not harvested (candidate: local_alliance()==1
	-- observed once on an attacker-side battle, UNVERIFIED as a rule).
	-- [3,9): battle-type one-hot -- not harvested (taxonomy [G], source open).
	-- [9,13): weather one-hot -- not harvested (T3 not yet readable).

	-- [13,19): men + unit counts via the T1 alliance walk
	-- (battle -> alliances -> armies -> units, 1-indexed item()).
	local la = plua.read(b, "local_alliance");
	local alliances = plua.read(b, "alliances");
	local an = plua.count(alliances);
	if type(la) == "number" and an > 0 then
		local own_alive, own_init, own_units = 0, 0, 0;
		local en_alive, en_init, en_units = 0, 0, 0;
		for a = 1, an do
			local alliance = plua.read(alliances, "item", a);
			local armies = plua.read(alliance, "armies");
			for m = 1, plua.count(armies) do
				local army = plua.read(armies, "item", m);
				local units = plua.read(army, "units");
				for i = 1, plua.count(units) do
					local unit = plua.read(units, "item", i);
					local alive = plua.read(unit, "number_of_men_alive");
					local init = plua.read(unit, "initial_number_of_men");
					if type(alive) ~= "number" then alive = 0; end;
					if type(init) ~= "number" then init = 0; end;
					if a == la then
						own_alive = own_alive + alive;
						own_init = own_init + init;
						if alive > 0 then own_units = own_units + 1; end;
					else
						en_alive = en_alive + alive;
						en_init = en_init + init;
						if alive > 0 then en_units = en_units + 1; end;
					end;
				end;
			end;
		end;
		if own_init > 0 then v[14] = util.clamp01(own_alive / own_init); end;
		if en_init > 0 then v[15] = util.clamp01(en_alive / en_init); end;
		v[16] = util.clamp01(own_init / spec.NORM.MEN_TOTAL);
		v[17] = util.clamp01(en_init / spec.NORM.MEN_TOTAL);
		v[18] = util.clamp01(own_units / spec.MAX_FRIENDLY);
		v[19] = util.clamp01(en_units / spec.MAX_ENEMY);
	end;

	-- [19,34): victory points -- not harvested (T5; events never deliver,
	-- Lua getters nil). [34,40): spare, zero by definition.
	return v;
end;

return M;
