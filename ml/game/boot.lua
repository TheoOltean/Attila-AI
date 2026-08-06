-------------------------------------------------------------------------
--	ml/game/boot.lua -- the per-battle entry script (THE bridge).
--
--	This chunk is the ONLY context where the battle interface exists:
--	attached per battle via cm:add_custom_battlefield(...) from a
--	campaign-side hook (wiring into a pack + campaign shim is deferred --
--	see ml/README.md). Battle interface CLASSES resolve as bare globals
--	only here (environment law): interface acquisition + closure
--	creation happen HERE, published under the one namespace _G.aai_ml.
--	Modules then legally call engine methods from pump/timer context on
--	the published userdata. events.* context is different in kind:
--	engine calls there are FORBIDDEN -- a failing call returns garbage,
--	a SUCCEEDING one kills the timer dispatch (context law); event
--	handlers may only flip plain-Lua flags.
--
--	Timer law: never register engine script-timers (kills the entire
--	vanilla dispatch). The one legal driver: wrap vanilla's 100 ms
--	tick_increment_counter, kick the pump from tick >= 50, call the
--	original. The battle SIM clock pauses with the game -- by design.
-------------------------------------------------------------------------

local util = require "ml/util";
local log = util.log;

log("");
log("==== ml boot: battle entry ====");

-- Default-off self-gate: the pipeline arms only when Theo drops the flag.
if not util.flag("aai_ml_on.txt") then
	log("aai_ml_on.txt absent -- ml pipeline stays cold");
	return;
end;

-- The one global namespace this tree owns.
local ML = {
	battle_id = nil,	-- os.date stamp; on every per-battle artifact
	bm = nil,			-- battle_manager userdata
	battle = nil,		-- bm.battle raw interface
	api = {},			-- engine-call closures (entry-env), the T1 door
	tick = 0,			-- vanilla 100 ms tick counter (small int)
};
rawset(_G, "aai_ml", ML);

-- Stamp battle identity FIRST: every out-file carries it; the host drops
-- any payload whose battle_id differs from the live obs stream.
local function stamp_battle_id()
	local ok, stamp = pcall(os.date, "%Y%m%d_%H%M%S");
	ML.battle_id = (ok and stamp) or "unstamped";
end;

-- Acquire the battle interface (legal only from this chunk, at load):
-- require "lua_scripts.Battle_Script_Header"; get_bm(); publish bm + battle.
local function acquire_bm()
	local ok, err = pcall(require, "lua_scripts.Battle_Script_Header");
	if not ok then
		log("Battle_Script_Header require failed: " .. tostring(err));
	end;
	local ok2, bm = pcall(function() return get_bm(); end);
	if ok2 and bm then
		ML.bm = bm;
		local ok3, b = pcall(function() return bm.battle; end);
		if ok3 then ML.battle = b; end;
	end;
end;

-- Engine-call closures for the T1 provider. Signatures are the contract;
-- bodies are wiring TODOs. Write closures take the entry's unit_controller.
local function publish_api()
	local A = ML.api;
	-- identity / enumeration (pump-context reads walk ML.battle directly;
	-- these closures cover what needs the entry environment)
	A.unit_key = function(alliance_i, army_i, unit) end;	-- todo: "a:b:name" via unit:name()
	A.make_uc = function(army, unit) end;					-- todo: army:create_unit_controller() + uc:add_units(unit)
	-- movement / attack verbs (uc = unit_controller)
	A.move = function(uc, x, z, run) end;					-- todo: uc:goto_location(v(x, z), run)
	A.move_form = function(uc, x, z, deg, width, run) end;	-- todo: uc:goto_location_angle_width
	A.halt = function(uc) end;								-- todo: uc:halt()
	A.attack_unit = function(uc, target_unit, run) end;		-- todo: uc:attack_unit(u, run, true)
	A.attack_ground = function(uc, x, z) end;				-- todo: uc:attack_location(v(x, z), false) -- run pinned: the verb carries no run arg
	A.attack_building = function(uc, building) end;			-- todo: uc:attack_building(b) -- T1 unverified
	-- stances / formations / abilities
	A.fire_at_will = function(uc, on) end;					-- todo: uc:fire_at_will(on)
	A.melee = function(uc, on) end;							-- todo: uc:melee(on)
	A.behaviour = function(uc, name, on) end;				-- todo: uc:change_behaviour_active(name, on)
	A.shot_type = function(uc, name) end;					-- todo: uc:change_shot_type(name)
	A.ability = function(uc, name) end;						-- todo: uc:perform_special_ability(name) -- T1-attempt
	-- structures / vehicles
	A.climb = function(uc, building) end;					-- todo: uc:climb_building(b)
	A.defend = function(uc, building) end;					-- todo: uc:defend_building(b) -- T1 unverified
	A.leave = function(uc) end;								-- todo: uc:leave_building() -- T1 unverified
	A.occupy_vehicle = function(uc, vehicle) end;			-- todo: uc:occupy_vehicle / interact_with_deployable -- T1 unverified
	-- control ownership (not durable -- write surface re-takes per tick)
	A.take = function(uc) end;								-- todo: uc:take_control()
	A.release = function(uc) end;							-- todo: uc:release_control()
end;

-- Wrap vanilla's tick dispatch slot (registered when get_bm() built the
-- bm -- so this runs AFTER acquire). The wrapper body runs INSIDE engine
-- timer dispatch: fully pcall'd, kick from tick >= 50 (past the load
-- window), always call the original.
local function hook_vanilla_tick()
	local orig = rawget(_G, "tick_increment_counter");
	if type(orig) ~= "function" then
		log("tick_increment_counter absent -- pump has no driver");
		return;
	end;
	local pump = require "ml/ipc/pump";
	rawset(_G, "tick_increment_counter", function(...)
		ML.tick = ML.tick + 1;
		if ML.tick >= 50 then
			pcall(pump.kick);
		end;
		return orig(...);
	end);
end;

local function main()
	stamp_battle_id();
	acquire_bm();
	if not ML.bm then
		log("get_bm() failed -- ml pipeline inert this battle");
		return;
	end;
	publish_api();
	-- Load order = the layer stack (ml/README.md layering law): shared core,
	-- provider substrate, the two surfaces (pure), pump last (the driver).
	local steps = {
		{ "roster", function() require("ml/roster").reset(ML.battle_id); end },
		{ "providers", function()
			require("ml/providers/lua").init(ML);
			require("ml/providers/db").init(ML);
			require("ml/providers/native").init(ML);
		end },
		{ "read", function() require("ml/read/surface").init(ML); end },
		{ "write", function() require("ml/write/surface").init(ML); end },
		{ "pump", function() require("ml/ipc/pump").init(ML); end },
	};
	for i = 1, #steps do
		local name, fn = steps[i][1], steps[i][2];
		local ok, err = pcall(fn);
		log("boot step " .. name .. ": " .. (ok and "ok" or ("FAILED " .. tostring(err))));
		if not ok then return; end;
	end;
	hook_vanilla_tick();
	log("ml boot complete; pump armed on vanilla tick");
end;

local ok, err = pcall(main);
if not ok then
	log("BOOT ERROR: " .. tostring(err));
end;
