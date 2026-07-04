-------------------------------------------------------------------------
--	Battle probe v3.
--
--	Established (globals dump, 2026-07-03): the battle_scripted state
--	has NO battle interface classes (no empire_battle, no battle_vector
--	-- those exist only in the separate quest-battle script state), so
--	rosters cannot be read the vanilla-library way here.
--
--	What this state does have: events, plus unexplored engine tables.
--	This version digs into those:
--	- one-time dump of lookup / udata_lookup / defined / system / vfs
--	- deep probe of the FIRST context object of each event type
--	  (metatable, fields, candidate methods); repeats log one line
-------------------------------------------------------------------------

local M = {};

local EVENT_NAMES = {
	"BattleDeploymentPhaseCommenced",
	"BattleConflictPhaseCommenced",
	"BattleCompleted",
	"BattleUnitAttacksEnemyUnit",
	"BattleUnitRouts",
	"BattleCommandingUnitRouts",
	"BattleUnitAttacksBuilding",
	"BattleUnitCapturesBuilding",
	"BattleUnitDestroysBuilding",
	"BattleUnitUsingBuilding",
	"BattleUnitUsingWall",
	"BattleUnitAttacksWalls",
	"BattleFortPlazaCaptureCommenced",
	"BattleBoardingActionCommenced",
	"BattleShipRouts",
	"BattleShipSurrendered",
};

local ENGINE_TABLES = { "lookup", "udata_lookup", "defined", "system", "vfs", "bit" };

local CONTEXT_FIELDS  = { "string", "component" };
local CONTEXT_METHODS = { "unit", "target_unit", "attacker", "defender", "building",
                          "faction", "character", "pending_battle", "battle" };

local function dump_table_keys(core, label, t)
	if type(t) ~= "table" then
		core.log(label .. " is " .. type(t) .. " = " .. tostring(t));
		return;
	end;
	local names = {};
	for key, value in pairs(t) do
		names[#names + 1] = tostring(key) .. ":" .. type(value);
	end;
	table.sort(names);
	core.log(label .. " (" .. #names .. " entries):");
	local line = "";
	for i = 1, #names do
		line = line .. names[i] .. "  ";
		if #line > 140 then
			core.log("  " .. line);
			line = "";
		end;
	end;
	if line ~= "" then
		core.log("  " .. line);
	end;
end;

local probed = {};

local function deep_probe_context(core, name, context)
	core.log("context probe for " .. name .. ": type=" .. type(context));

	pcall(function()
		local mt = getmetatable(context);
		if mt then
			dump_table_keys(core, "  metatable", mt);
			if type(mt.__index) == "table" then
				dump_table_keys(core, "  __index", mt.__index);
			end;
		else
			core.log("  no metatable");
		end;
	end);

	for i = 1, #CONTEXT_FIELDS do
		local field = CONTEXT_FIELDS[i];
		pcall(function()
			core.log("  ." .. field .. " = " .. tostring(context[field]));
		end);
	end;

	for i = 1, #CONTEXT_METHODS do
		local method = CONTEXT_METHODS[i];
		local ok, value = pcall(function() return context[method](context); end);
		if ok then
			core.log("  :" .. method .. "() = " .. tostring(value));
		end;
	end;
end;

function M.init(core)
	core.log("VM decoda_name = " .. tostring(_G.decoda_name));

	-- one-time: what lives inside the engine-provided tables?
	for i = 1, #ENGINE_TABLES do
		local name = ENGINE_TABLES[i];
		pcall(dump_table_keys, core, "global " .. name, _G[name]);
	end;

	for i = 1, #EVENT_NAMES do
		local name = EVENT_NAMES[i];
		if events[name] then
			events[name][#events[name] + 1] = core.guarded(name, function(context)
				-- deep-probe the first occurrence only; repeats stay
				-- silent so the log is readable during melee
				if not probed[name] then
					probed[name] = true;
					deep_probe_context(core, name, context);
				end;
			end);
		else
			core.log("no such event table: " .. name);
		end;
	end;
	core.log("battle event listeners registered");
end;

return M;
